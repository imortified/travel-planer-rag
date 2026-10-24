"""Select reviewed meal venues, then schedule dated mandatory stops with every solver.

Venue selection is a deterministic preprocessing policy, not joint venue optimization.
Repeated restaurant use is represented by different dated meal instances; attractions
retain their original identity and the no-repeat rule. No prices or road legs invented.
"""

from dataclasses import asdict, replace

from app.planning.candidates import PERIOD_POOL_LIMIT, select_candidates
from app.planning.costs import group_cost
from app.schemas.trip_costs import MealSettings

LABELS = {"breakfast": "Завтрак", "lunch": "Обед", "dinner": "Ужин"}
POLICY = "reviewed-meal-venues-v1"


def prepare_meals(request, inputs, settings):
    if settings is None:
        return request, inputs
    settings = MealSettings.model_validate(settings)
    slots = []
    food_ids = {p.id for data in inputs for p in data.places if p.category == "food"}
    required_food = request.mandatory & food_ids
    for index, data in enumerate(inputs):
        metadata = {p["id"]: p for p in (data.data_context or {}).get("pois", [])}
        for kind, slot in settings.slots():
            choices = []
            for place in data.places:
                poi = metadata.get(place.id, {})
                duration = max(slot.duration, place.duration)
                opens, closes = (
                    max(request.start, slot.start, place.opens),
                    min(request.end, slot.end, place.closes),
                )
                if (
                    place.category == "food"
                    and place.id not in request.excluded
                    and poi.get("provenance")
                    and kind in (poi.get("meal_types") or [])
                    and place.cost_per_person <= slot.max_cost_per_person
                    and opens + duration <= closes
                    and ("start", place.id) in data.travel
                ):
                    choices.append(replace(place, opens=opens, closes=closes, duration=duration))
            if not choices:
                raise ValueError(
                    f"{data.travel_date}: для «{LABELS[kind]}» нет проверенного заведения "
                    "с подходящими ценой, временем и переездом. Измените окно/лимит "
                    "или отключите этот приём пищи."
                )
            slots.append((index, kind, choices))
    minimum = sum(
        min(group_cost(p, request.participants) for p in options) for _, _, options in slots
    )
    if minimum > request.budget:
        raise ValueError(
            f"На выбранное питание по доступным кандидатам нужно от {minimum / 100:.2f} EUR; "
            f"после жилья, транспорта и резерва осталось {request.budget / 100:.2f} EUR."
        )
    selected, covered, meal_ids = {}, set(), set()
    spent = 0
    for index, kind, options in slots:
        data = inputs[index]
        minimum -= min(group_cost(p, request.participants) for p in options)
        affordable = [
            p
            for p in options
            if spent + group_cost(p, request.participants) + minimum <= request.budget
        ]
        if not affordable:
            raise ValueError("Не удалось распределить бюджет питания между днями")
        chosen = min(
            affordable,
            key=lambda p: (
                p.id not in required_food - covered,
                group_cost(p, request.participants),
                data.travel["start", p.id],
                p.id,
            ),
        )
        covered.add(chosen.id)
        spent += group_cost(chosen, request.participants)
        identifier = f"meal:{data.travel_date}:{kind}:{chosen.id}"
        meal_ids.add(identifier)
        selected.setdefault(index, []).append((kind, chosen, identifier))
    if required_food - covered:
        raise ValueError(
            "Не удалось назначить обязательное кафе на выбранные приёмы пищи; "
            "измените окно, лимит или ограничения заведения"
        )
    mandatory = (request.mandatory - food_ids) | meal_ids
    # Reserve room for dated meals under the common 100-instance solver bound.
    ordinary = {
        p.id: p
        for data in inputs
        for p in data.places
        if p.category != "food" and p.id not in request.excluded
    }
    capacity = PERIOD_POOL_LIMIT - len(meal_ids)
    if len(mandatory - meal_ids) > capacity:
        raise ValueError("Слишком много обязательных мест вместе с приёмами пищи")
    preferences = {
        key: sum(p.interests.get(key, 0) for p in request.participants)
        for key in ("art", "history", "nature", "food")
    }
    kept = {
        p["id"]
        for p in select_candidates(
            [asdict(p) for p in ordinary.values()],
            mandatory - meal_ids,
            preferences,
            limit=capacity,
        )
    }
    prepared = []
    for index, data in enumerate(inputs):
        context = data.data_context or {}
        metadata = {p["id"]: p for p in context.get("pois", [])}
        places = [p for p in data.places if p.id in kept]
        mapping = {p.id: p.id for p in places}
        pois = [metadata[p.id] for p in places if p.id in metadata]
        for kind, chosen, identifier in selected.get(index, []):
            places.append(replace(chosen, id=identifier))
            mapping[identifier] = chosen.id
            pois.append(
                {
                    **metadata[chosen.id],
                    "id": identifier,
                    "name": f"{LABELS[kind]} · {metadata[chosen.id]['name']}",
                    "venue_id": chosen.id,
                    "meal_type": kind,
                    "cost_per_person": chosen.cost_per_person,
                    "duration": chosen.duration,
                    "duration_source": "meal_policy_and_catalog_minimum",
                    "window": [chosen.opens, chosen.closes],
                }
            )
        points = {"start": "start", **mapping}
        travel = {
            (a, b): data.travel[original_a, original_b]
            for a, original_a in points.items()
            for b, original_b in mapping.items()
            if a != b and (original_a, original_b) in data.travel
        }
        # Same venue may serve two different meals; zero-distance self legs are explicit.
        for a, original_a in mapping.items():
            for b, original_b in mapping.items():
                if a != b and original_a == original_b:
                    travel[a, b] = 0
        context = {
            **context,
            "pois": pois,
            "meal_selection": {
                "policy": POLICY,
                "settings": settings.model_dump(mode="json"),
                "meal_ids": sorted(p.id for p in places if p.id in meal_ids),
            },
            "places_snapshot": [asdict(p) for p in places],
            "candidate_limit": context.get("candidate_limit", 24) + len(settings.slots()),
            "period_pool_limit": PERIOD_POOL_LIMIT,
            "travel_minutes": [
                {"from": a, "to": b, "minutes": minutes}
                for (a, b), minutes in sorted(travel.items())
            ],
        }
        prepared.append(replace(data, places=places, travel=travel, data_context=context))
    return replace(request, mandatory=frozenset(mandatory), meal_ids=frozenset(meal_ids)), prepared
