"""Project source-reviewed day tariffs onto an immutable per-participant cost vector."""

from dataclasses import asdict, replace
from datetime import datetime
from zoneinfo import ZoneInfo

from app.catalog.age_rules import AgeRules
from app.planning.costs import age_at

POLICY = "individual-age-pricing-v1"


def assessment_date():
    return datetime.now(ZoneInfo("Europe/Lisbon")).date()


def quote(place, poi, participants, day, assessed_on):
    ages = {p.id: age_at(p, day) for p in participants}
    rules = AgeRules.model_validate(poi["age_rules"]) if poi.get("age_rules") else None
    valid = (
        rules
        and rules.checked_on <= day <= rules.valid_until
        and (rules.checked_on <= assessed_on <= rules.valid_until)
    )
    access_rule = rules and any(
        value is not None for value in (rules.min_age, rules.max_age, rules.accompanied_under)
    )
    if access_rule:
        if not valid:
            return None, "age_access_review_expired"
        if any(age is None for age in ages.values()):
            return None, "age_required_for_access_check"
        if any(
            (rules.min_age is not None and age < rules.min_age)
            or (rules.max_age is not None and age > rules.max_age)
            for age in ages.values()
        ):
            return None, "age_admission_restricted"
        if (
            rules.accompanied_under
            and any(age < rules.accompanied_under for age in ages.values())
            and not any(age >= rules.companion_min_age for age in ages.values())
        ):
            return None, "adult_companion_required"
    rows = []
    for person in participants:
        age = ages[person.id]
        amount = place.cost_per_person
        source = (poi.get("provenance") or {}).get("price_source") or poi.get("source_url")
        reason, label = "regular_fallback_no_age_rule", "Обычный тариф; льгота не проверена"
        if place.category == "food":
            reason, label = "explicit_menu_order", "Выбранный заказ; детское меню не подключено"
        elif poi.get("date_free_admission"):
            amount, reason, label = 0, "date_free_admission", "Бесплатный вход на выбранную дату"
        elif age is None:
            reason, label = "regular_fallback_unknown_age", "Обычный тариф; возраст не указан"
        elif rules and not valid:
            reason, label = (
                "regular_fallback_expired_rule",
                "Обычный тариф; льгота требует проверки",
            )
        elif valid:
            band = next((band for band in rules.bands if band.min_age <= age <= band.max_age), None)
            if band:
                amount, reason, label = band.cost, "verified_age_tariff", band.label
                source = str(rules.source_url)
            else:
                reason = "regular_fallback_no_age_band"
        rows.append(
            {
                "participant_id": person.id,
                "age": age,
                "cost": amount,
                "label": label,
                "reason": reason,
                "source_url": source,
            }
        )
    return rows, None


def prepare_age_pricing(request, inputs, assessed_on=None):
    assessed_on = assessed_on or assessment_date()
    prepared = []
    exclusions = []
    for data in inputs:
        metadata = {p["id"]: p for p in (data.data_context or {}).get("pois", []) if "id" in p}
        places, pois, blocked = [], [], []
        for place in data.places:
            poi = metadata.get(place.id, {})
            rows, reason = quote(place, poi, request.participants, data.travel_date, assessed_on)
            if reason:
                blocked.append(
                    {
                        "id": place.id,
                        "name": poi.get("name", place.id),
                        "reason": reason,
                        "travel_date": str(data.travel_date),
                    }
                )
                continue
            places.append(
                replace(place, participant_costs={r["participant_id"]: r["cost"] for r in rows})
            )
            if poi:
                pois.append(
                    {**poi, "participant_prices": rows, "group_cost": sum(r["cost"] for r in rows)}
                )
        exclusions.extend(blocked)
        context = data.data_context
        if context is not None:
            context = {
                **context,
                "pois": pois,
                "age_exclusions": blocked,
                "pricing_policy": POLICY,
                "pricing_assessed_on": str(assessed_on),
                "places_snapshot": [asdict(p) for p in places],
            }
        prepared.append(replace(data, places=places, data_context=context))
    available = {p.id for data in prepared for p in data.places}
    missing = request.mandatory - available
    if missing and any(row["id"] in missing for row in exclusions):
        raise ValueError(
            "Обязательное место не подходит возрастному составу группы "
            "или требует указать возраст/подтвердить сопровождение"
        )
    if exclusions and not available:
        raise ValueError(
            "На выбранные даты не осталось мест с подтверждёнными возрастными условиями"
        )
    return prepared
