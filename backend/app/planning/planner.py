"""Pure Python greedy baseline; times are local minutes, costs are integer cents."""

from app.planning.models import Place, Request, Result, Visit


def plan(request: Request, places: list[Place], travel: dict[tuple[str, str], int]) -> Result:
    if not request.participants or len({p.id for p in request.participants}) != len(
        request.participants
    ):
        raise ValueError("Participants must be nonempty and have unique IDs")
    if request.budget < 0 or not 0 <= request.start < request.end <= 1440:
        raise ValueError("Invalid budget or day interval")
    ids = {p.id for p in places}
    if len(ids) != len(places):
        raise ValueError("Duplicate place IDs")
    if any(
        p.cost_per_person < 0 or p.duration <= 0 or not 0 <= p.opens < p.closes <= 1440
        for p in places
    ) or any(t < 0 for t in travel.values()):
        raise ValueError("Invalid place or travel duration")
    if any(not 0 <= w <= 1 for p in request.participants for w in p.interests.values()):
        raise ValueError("Interest weights must be between 0 and 1")
    if request.mandatory & request.excluded:
        return Result(False, [], 0, {}, ["mandatory_excluded_conflict"])
    if request.mandatory - ids:
        return Result(False, [], 0, {}, ["unknown_mandatory_place"])

    def score(place: Place) -> float:
        return sum(p.interests.get(place.category, 0) for p in request.participants) / len(
            request.participants
        )

    remaining = [p for p in places if p.id not in request.excluded]
    visits: list[Visit] = []
    cost, now, previous = 0, request.start, "start"
    totals = {p.id: 0.0 for p in request.participants}
    while remaining:
        candidates = []
        for place in remaining:
            minutes = travel.get((previous, place.id))
            if minutes is None:
                continue  # Missing routing data must never imply zero travel time.
            arrival = now + minutes
            begins = max(arrival, place.opens)
            ends = begins + place.duration
            price = place.cost_per_person * len(request.participants)
            if ends <= min(place.closes, request.end) and cost + price <= request.budget:
                candidates.append((place, arrival, begins, ends, price))
        if not candidates:
            break
        place, arrival, begins, ends, price = min(
            candidates,
            key=lambda c: (c[0].id not in request.mandatory, -score(c[0]), c[0].id),
        )
        visits.append(Visit(place.id, arrival, begins, ends))
        cost += price
        now, previous = ends, place.id
        for participant in request.participants:
            totals[participant.id] += participant.interests.get(place.category, 0)
        remaining.remove(place)
    missing = request.mandatory - {v.place_id for v in visits}
    reasons = [f"mandatory_not_scheduled:{pid}" for pid in sorted(missing)]
    if not visits:
        reasons.append("no_feasible_visits")
    satisfaction = {pid: value / len(visits) if visits else 0 for pid, value in totals.items()}
    return Result(bool(visits) and not missing, visits, cost, satisfaction, reasons)
