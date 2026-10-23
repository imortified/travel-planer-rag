"""Independent feasibility check. Does not import or trust any planning algorithm."""

from app.planning.models import Place, Request, ValidationReport, Violation, Visit


def validate_route(
    request: Request,
    visits: list[Visit],
    places: list[Place],
    travel: dict[tuple[str, str], int],
    reported_cost: int | None = None,
) -> ValidationReport:
    """Validate against reference data; monetary values are integer cents.

    Arrival may be later than the earliest reachable time (e.g. a break en route).
    Visiting longer than the minimum POI duration is allowed. Return travel is out of scope.
    Invalid reference data raises ValueError rather than producing a feasibility verdict.
    """
    by_id = {p.id: p for p in places}
    if (
        len(by_id) != len(places)
        or any(
            p.cost_per_person < 0 or p.duration <= 0 or not 0 <= p.opens < p.closes <= 1440
            for p in places
        )
        or any(t < 0 for t in travel.values())
    ):
        raise ValueError("Invalid reference data")
    if (
        not request.participants
        or request.budget < 0
        or not 0 <= request.start < request.end <= 1440
    ):
        raise ValueError("Invalid request")
    violations: list[Violation] = []

    def add(code, message, pid=None, index=None):
        violations.append(Violation(code, message, pid, index))

    for pid in sorted(request.mandatory & request.excluded):
        add("mandatory_excluded_conflict", "Place is both mandatory and excluded", pid)
    for pid in sorted(request.mandatory - by_id.keys()):
        add("unknown_mandatory_place", "Mandatory place is absent from reference data", pid)
    if not visits:
        add("no_feasible_visits", "Route has no visits")
    seen: set[str] = set()
    cost, previous_end, previous_id = 0, request.start, "start"
    for index, visit in enumerate(visits):
        pid = visit.place_id
        if pid in seen:
            add("duplicate_visit", "Place occurs more than once", pid, index)
        seen.add(pid)
        if pid in request.excluded:
            add("excluded_place", "Route includes an excluded place", pid, index)
        if not request.start <= visit.arrival <= visit.start < visit.end <= request.end:
            add(
                "invalid_visit_times",
                "Visit times violate ordering or the trip interval",
                pid,
                index,
            )
        minutes = travel.get((previous_id, pid))
        if minutes is None:
            add("missing_travel_time", "Transition has no reference travel time", pid, index)
        elif visit.arrival < previous_end + minutes:
            add("unreachable_arrival", "Arrival is earlier than physically reachable", pid, index)
        place = by_id.get(pid)
        if place is None:
            add("unknown_place", "Visited place is absent from reference data", pid, index)
        else:
            cost += place.cost_per_person * len(request.participants)
            if visit.start < place.opens or visit.end > place.closes:
                add("outside_opening_hours", "Visit does not fit opening hours", pid, index)
            if visit.end - visit.start < place.duration:
                add("insufficient_visit_duration", "Visit is shorter than required", pid, index)
        previous_id, previous_end = pid, visit.end
    for pid in sorted(request.mandatory - seen):
        add("mandatory_not_scheduled", "Mandatory place is missing from route", pid)
    if cost > request.budget:
        add("budget_exceeded", f"Computed cost {cost} exceeds group budget {request.budget}")
    if reported_cost is not None and reported_cost != cost:
        add("cost_mismatch", f"Reported cost {reported_cost} differs from computed cost {cost}")
    return ValidationReport(not violations, cost, violations)
