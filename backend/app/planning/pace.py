"""Versioned product policies and bounded improvements on the fixed objective."""

from dataclasses import asdict

from app.planning.models import Pace, Visit

PROFILES = {
    "relaxed": Pace("relaxed", 2, 240, 30),
    "balanced": Pace("balanced", 3, 360, 20),
    "intensive": Pace("intensive", 5, 480, 10),
}


def profile_snapshot(mode):
    return None if mode == "unrestricted" else asdict(PROFILES[mode])


def load_minutes(request, visits, travel):
    """Visits + reference driving + required inter-visit rests; opening waits separate."""
    previous = "start"
    drive = 0
    for visit in visits:
        drive += travel.get((previous, visit.place_id), 0)
        previous = visit.place_id
    rest = max(0, len(visits) - 1) * (request.pace.break_minutes if request.pace else 0)
    activity = sum(v.end - v.start for v in visits)
    return activity, drive, rest


def fits_pace(request, visits, travel):
    return request.pace is None or (
        len(visits) <= request.pace.max_visits
        and sum(load_minutes(request, visits, travel)) <= request.pace.max_active_minutes
    )


def schedule_order(request, ids, data):
    places = {p.id: p for p in data.places}
    result, previous, now = [], "start", request.start
    for pid in ids:
        if pid not in places or (previous, pid) not in data.travel:
            return None
        place = places[pid]
        arrival = now + data.travel[previous, pid]
        rest = request.pace.break_minutes if request.pace and result else 0
        begins = max(arrival + rest, place.opens)
        now = begins + place.duration
        result.append(Visit(pid, arrival, begins, now))
        if now > min(request.end, place.closes) or not fits_pace(request, result, data.travel):
            return None
        previous = pid
    return result


def spread_visits(request, schedule, inputs, objective):
    """At most 14 improving relocations to empty days; never change the selected POI set.

    Rebuild both days using their own prices, windows and directed travel matrix.
    An unavailable date, broken donor route or excessive period cost rejects a move.
    """
    if request.pace is None or len(inputs) == 1:
        return schedule
    current = [list(day) for day in schedule]
    for _ in range(len(inputs)):
        best, best_key = current, objective.key(current)
        for source, visits in enumerate(current):
            if len(visits) < 2:
                continue
            for removed in visits:
                donor = schedule_order(
                    request, [v.place_id for v in visits if v != removed], inputs[source]
                )
                if donor is None:
                    continue
                for target, day in enumerate(current):
                    if day:
                        continue
                    receiver = schedule_order(request, [removed.place_id], inputs[target])
                    if receiver is None:
                        continue
                    candidate = list(current)
                    candidate[source], candidate[target] = donor, receiver
                    cost = sum(
                        objective.places[i][v.place_id].cost_per_person * len(request.participants)
                        for i, day in enumerate(candidate)
                        for v in day
                    )
                    if cost > request.budget:
                        continue
                    key = objective.key(candidate)
                    if key < best_key:
                        best, best_key = candidate, key
        if best is current:
            break
        current = best
    return current


def coverage_upper_bound(request, inputs):
    """Maximum matching: one distinct POI per occupied day, ignoring other constraints.

    It is an upper bound, never a promise of a feasible schedule. Merely counting
    distinct POIs misses periods whose many early dates share only two candidates.
    """
    candidates = [sorted({p.id for p in day.places} - request.excluded) for day in inputs]
    assigned = {}

    def augment(day, seen):
        for pid in candidates[day]:
            if pid in seen:
                continue
            seen.add(pid)
            if pid not in assigned or augment(assigned[pid], seen):
                assigned[pid] = day
                return True
        return False

    return sum(augment(day, set()) for day in range(len(inputs)))


def planning_summary(request, result, inputs):
    schedule = [d.visits for d in result.days] if result.days else [result.visits]
    candidates = {p.id for day in inputs for p in day.places} - request.excluded
    selected = {v.place_id for day in schedule for v in day}
    days = []
    for visits, data in zip(schedule, inputs, strict=True):
        activity, drive, rest = load_minutes(request, visits, data.travel)
        days.append(
            {
                "travel_date": str(data.travel_date),
                "visit_count": len(visits),
                "activity_minutes": activity,
                "drive_minutes": drive,
                "rest_minutes": rest,
                "active_minutes": activity + drive + rest,
                "opening_wait_minutes": sum(
                    max(
                        0,
                        v.start
                        - v.arrival
                        - (request.pace.break_minutes if request.pace and i else 0),
                    )
                    for i, v in enumerate(visits)
                ),
                "eligible_count": len({p.id for p in data.places} - request.excluded),
                "catalog_eligible_count": (data.data_context or {}).get("eligible_count"),
            }
        )
    warnings = []
    coverage_bound = coverage_upper_bound(request, inputs)
    if coverage_bound < len(inputs):
        warnings.append("availability_limits_coverage")
    if len(candidates) < len(inputs):
        warnings.append("catalog_smaller_than_period")
    if any(not day["eligible_count"] for day in days):
        warnings.append("dates_without_candidates")
    if any(not day["visit_count"] for day in days):
        warnings.append("unscheduled_days")
    if candidates and selected == candidates:
        warnings.append("all_candidates_scheduled")
    if any(
        day["catalog_eligible_count"] is not None
        and day["catalog_eligible_count"] > day["eligible_count"]
        for day in days
    ):
        warnings.append("candidate_subset")
    return {
        "pace": asdict(request.pace) if request.pace else None,
        "total_days": len(inputs),
        "active_days": sum(bool(d["visit_count"]) for d in days),
        "candidate_count": len(candidates),
        "coverage_upper_bound": coverage_bound,
        "scheduled_count": len(selected),
        "cost_scope": "admissions_and_explicit_cafe_orders",
        "days": days,
        "warnings": warnings,
    }
