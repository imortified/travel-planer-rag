"""Attach date-specific reference prices to the saved presentation of a route."""

from dataclasses import replace

from app.planning.costs import age_at, group_cost


def with_visit_prices(result, inputs, participant_count, participants=None):
    def priced(visits, data):
        places = {p.id: p for p in data.places}
        metadata = {p["id"]: p for p in (data.data_context or {}).get("pois", []) if "id" in p}
        rows = []
        for visit in visits:
            place = places.get(visit.place_id)
            if place is None:
                rows.append(
                    replace(visit, cost_per_person=None, group_cost=None, participant_prices=None)
                )
                continue
            personal = metadata.get(visit.place_id, {}).get("participant_prices")
            if participants is not None:
                total = group_cost(place, participants)
                if personal is None:
                    personal = [
                        {
                            "participant_id": p.id,
                            "age": age_at(p, data.travel_date),
                            "cost": (place.participant_costs or {}).get(
                                p.id, place.cost_per_person
                            ),
                            "label": "Обычный тариф",
                            "reason": "regular_reference",
                            "source_url": None,
                        }
                        for p in participants
                    ]
                expected = {
                    p.id: (place.participant_costs or {}).get(p.id, place.cost_per_person)
                    for p in participants
                }
                if (
                    len(personal) != participant_count
                    or {row["participant_id"]: row["cost"] for row in personal} != expected
                ):
                    raise ValueError("Saved individual prices differ from reference vector")
                unique = {row["cost"] for row in personal}
                unit = next(iter(unique)) if len(unique) == 1 else None
            else:
                if place.participant_costs is not None:
                    raise ValueError("Individual prices require reference participants")
                unit, total = place.cost_per_person, place.cost_per_person * participant_count
            rows.append(
                replace(visit, cost_per_person=unit, group_cost=total, participant_prices=personal)
            )
        return rows

    if result.days is not None:
        days = [
            replace(day, visits=priced(day.visits, data))
            for day, data in zip(result.days, inputs, strict=True)
        ]
        return replace(result, days=days, visits=[v for day in days for v in day.visits])
    if len(inputs) != 1:
        raise ValueError("Single-day result must have exactly one price snapshot")
    return replace(result, visits=priced(result.visits, inputs[0]))
