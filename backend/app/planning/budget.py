"""Account once for reference-priced stops and separately labelled user estimates."""

from app.planning.costs import group_cost
from app.schemas.trip_costs import BudgetSettings


def settings_for(trip):
    data = getattr(trip, "budget_settings", None)
    return BudgetSettings.model_validate(data) if data is not None else None


def activity_budget(trip):
    settings = settings_for(trip)
    return trip.budget - (settings.committed if settings else 0)


def breakdown(trip, result, inputs):
    settings = settings_for(trip)
    schedule = [day.visits for day in result.days] if result.days else [result.visits]
    food = 0
    participants = [
        str(row.get("user_id", index)) for index, row in enumerate(trip.participants_snapshot)
    ]
    for visits, data in zip(schedule, inputs, strict=True):
        by_id = {p.id: p for p in data.places}
        food += sum(
            group_cost(by_id[v.place_id], participants)
            for v in visits
            if v.place_id in by_id and by_id[v.place_id].category == "food"
        )
    lodging = settings.lodging if settings else None
    transport = settings.transport if settings else None
    flights = settings.flights if settings else None
    transport_scope = settings.transport_scope if settings else "all_transport"
    reserve = settings.reserve if settings else 0
    estimated = (lodging or 0) + (transport or 0) + (flights or 0)
    total = result.total_cost + estimated
    snapshot = getattr(trip, "lodging_snapshot", None) or {}
    provider_estimate = snapshot.get("price_status") == "provider_base_estimate"
    return {
        "version": "trip-budget-v1",
        "scope": "trip_estimate" if settings else "stops_only",
        "budget": trip.budget,
        "admissions": result.total_cost - food,
        "food": food,
        "stops_total": result.total_cost,
        "lodging_estimate": lodging,
        "lodging_price_basis": "xotelo_base_estimate" if provider_estimate else "user_estimate",
        "transport_estimate": transport,
        "flights_estimate": flights,
        "transport_scope": transport_scope,
        "reserve": reserve,
        "known_total": total,
        "available_for_stops": activity_budget(trip),
        "unallocated_after_reserve": trip.budget - total - reserve,
        "unknown_categories": (
            [
                key
                for key, value in (("lodging", lodging), ("transport", transport))
                if value is None
            ]
            + (["lodging_taxes_and_fees"] if provider_estimate else [])
            + (["flights"] if transport_scope == "ground_only" and flights is None else [])
        ),
        "within_budget": total + reserve <= trip.budget,
        "meal_policy": getattr(trip, "meal_settings", None),
        "limitations": [
            "User estimates are not booking quotes",
            "Food covers selected orders only; other meals and extras excluded",
            "Reserve is unspent money, not an expense",
        ],
    }
