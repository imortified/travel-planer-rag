"""Attach date-specific reference prices to the saved presentation of a route."""

from dataclasses import replace


def with_visit_prices(result, inputs, participant_count):
    def priced(visits, data):
        places = {p.id: p for p in data.places}
        return [
            replace(
                visit,
                cost_per_person=places[visit.place_id].cost_per_person,
                group_cost=places[visit.place_id].cost_per_person * participant_count,
            )
            if visit.place_id in places
            else replace(visit, cost_per_person=None, group_cost=None)
            for visit in visits
        ]

    if result.days is not None:
        days = [
            replace(day, visits=priced(day.visits, data))
            for day, data in zip(result.days, inputs, strict=True)
        ]
        return replace(result, days=days, visits=[v for day in days for v in day.visits])
    if len(inputs) != 1:
        raise ValueError("Single-day result must have exactly one price snapshot")
    return replace(result, visits=priced(result.visits, inputs[0]))
