"""Explicit user meal estimates applied only to real venues with usable hours and access."""
import math

from app.schemas.trip_costs import MealSettings


def with_meal_estimates(catalog, settings):
    if settings is None:
        return catalog
    settings = MealSettings.model_validate(settings)
    if settings.mode != "estimate":
        return catalog
    prices = {kind: slot.max_cost_per_person for kind, slot in settings.slots()}
    pois = []
    for poi in catalog["pois"]:
        if poi["category"] != "food":
            pois.append(poi)
            continue
        issues = [i for i in poi["issues"] if i != "unknown_price"]
        pois.append({
            **poi, "cost_per_person": max(prices.values()),
            "name": poi["name"].split(" — ")[0] if poi.get("provenance") else poi["name"],
            "price_status": "user_estimate", "estimated_meal_prices": prices,
            "meal_types": list(prices), "issues": issues, "eligible": not issues,
            "price_notice": (
                "Ваша оценка расходов; меню, цена заказа и наличие столиков не подтверждены."
            ),
        })
    return {**catalog, "pois": pois}


def meal_candidates(catalog, settings, excluded):
    """Reserve a few nearby usable venues for each meal before the generic POI cap."""
    if settings is None:
        return []
    settings = MealSettings.model_validate(settings)
    origin = catalog["origin"]
    selected = {}
    for kind, slot in settings.slots():
        candidates = []
        for poi in catalog["pois"]:
            if poi["category"] != "food" or not poi["eligible"] or poi["id"] in excluded:
                continue
            if kind not in (poi.get("meal_types") or []):
                continue
            if settings.mode == "reviewed" and not poi.get("provenance"):
                continue
            cost = (poi.get("estimated_meal_prices") or {}).get(kind, poi["cost_per_person"])
            if cost > slot.max_cost_per_person or (
                max(poi["window"][0], slot.start) + max(poi["duration"], slot.duration)
                > min(poi["window"][1], slot.end)
            ):
                continue
            candidates.append(poi)
        candidates.sort(key=lambda p: (
            math.hypot(p["latitude"] - origin["latitude"], p["longitude"] - origin["longitude"]),
            p["id"],
        ))
        for poi in candidates[:3]:
            selected[poi["id"]] = poi
    return list(selected.values())
