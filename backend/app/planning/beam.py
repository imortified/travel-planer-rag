"""Deterministic bounded search; greedy is retained as a fallback candidate."""

from app.planning.models import Place, Request, Result, Visit
from app.planning.planner import plan


def route_key(request: Request, places: list[Place], result: Result) -> tuple:
    """Lower is better: feasibility, mandatory coverage, utility, balance, cost, finish, IDs."""
    by_id = {p.id: p for p in places}
    totals = [
        sum(p.interests.get(by_id[v.place_id].category, 0) for v in result.visits)
        for p in request.participants
    ]
    return (
        not result.is_feasible,
        -len(request.mandatory & {v.place_id for v in result.visits}),
        -sum(totals) / len(totals),
        -min(totals),
        result.total_cost,
        result.visits[-1].end if result.visits else request.start,
        tuple(v.place_id for v in result.visits),
    )


def beam_plan(
    request: Request, places: list[Place], travel: dict[tuple[str, str], int], beam_width: int = 32
) -> Result:
    if not 1 <= beam_width <= 128:
        raise ValueError("beam_width must be between 1 and 128")
    if len(places) > 100:
        raise ValueError("Beam search is limited to 100 candidate places")
    baseline = plan(request, places, travel)  # Shared input validation and fallback.
    if request.mandatory & request.excluded or request.mandatory - {p.id for p in places}:
        return baseline
    by_id = {p.id: p for p in places}

    def result(visits, cost):
        missing = request.mandatory - {v.place_id for v in visits}
        reasons = [f"mandatory_not_scheduled:{pid}" for pid in sorted(missing)]
        if not visits:
            reasons.append("no_feasible_visits")
        satisfaction = {
            p.id: sum(p.interests.get(by_id[v.place_id].category, 0) for v in visits) / len(visits)
            if visits
            else 0
            for p in request.participants
        }
        return Result(bool(visits) and not missing, visits, cost, satisfaction, reasons)

    best = baseline
    frontier = [result([], 0)]
    for _ in range(len(places)):
        expanded = []
        for state in frontier:
            seen = {v.place_id for v in state.visits}
            previous = state.visits[-1].place_id if state.visits else "start"
            now = state.visits[-1].end if state.visits else request.start
            for place in sorted(places, key=lambda p: p.id):
                if place.id in seen or place.id in request.excluded:
                    continue
                minutes = travel.get((previous, place.id))
                if minutes is None:
                    continue
                arrival = now + minutes
                begins = max(arrival, place.opens)
                ends = begins + place.duration
                cost = state.total_cost + place.cost_per_person * len(request.participants)
                if ends > min(place.closes, request.end) or cost > request.budget:
                    continue
                candidate = result(state.visits + [Visit(place.id, arrival, begins, ends)], cost)
                if route_key(request, places, candidate) < route_key(request, places, best):
                    best = candidate
                expanded.append(candidate)
        if not expanded:
            break
        frontier = sorted(expanded, key=lambda r: route_key(request, places, r))[:beam_width]
    return best
