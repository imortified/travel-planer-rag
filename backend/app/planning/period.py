"""Search across day boundaries, then independently validate the whole period."""

from dataclasses import dataclass, replace

from app.planning.models import (
    DayInput,
    DayResult,
    Request,
    Result,
    ValidationReport,
    Violation,
    Visit,
)
from app.planning.objective import Objective
from app.planning.pace import fits_pace, spread_visits
from app.planning.planner import plan
from app.planning.validator import validate_route


def validate_period(request, visits_by_day, inputs, reported_cost):
    if len(visits_by_day) != len(inputs):
        raise ValueError("Day count does not match input period")
    violations, days, seen = [], [], set()
    total = 0
    for visits, data in zip(visits_by_day, inputs, strict=True):
        local = replace(request, mandatory=frozenset())
        report = validate_route(local, visits, data.places, data.travel)
        issues = [v for v in report.violations if v.code != "no_feasible_visits"]
        previous_days = set(seen)
        for index, visit in enumerate(visits):
            if visit.place_id in previous_days:
                issues.append(
                    Violation(
                        "duplicate_visit_across_days",
                        "Place repeats across days",
                        visit.place_id,
                        index,
                    )
                )
            seen.add(visit.place_id)
        day_report = ValidationReport(not issues, report.computed_cost, issues)
        days.append(
            DayResult(
                str(data.travel_date), visits, report.computed_cost, day_report, data.data_context
            )
        )
        violations.extend(replace(v, message=f"{data.travel_date}: {v.message}") for v in issues)
        total += report.computed_cost
    for pid in sorted(request.mandatory - seen):
        violations.append(
            Violation("mandatory_not_scheduled", "Required place missing in period", pid)
        )
    for pid in sorted(request.mandatory & request.excluded):
        violations.append(
            Violation("mandatory_excluded_conflict", "Required place is excluded", pid)
        )
    if not seen:
        violations.append(Violation("no_feasible_visits", "Period contains no visits"))
    if total > request.budget:
        violations.append(Violation("budget_exceeded", "Total period cost exceeds group budget"))
    if reported_cost != total:
        violations.append(Violation("cost_mismatch", "Reported total differs from period cost"))
    return ValidationReport(not violations, total, violations), days


@dataclass(frozen=True)
class State:
    day: int
    visits: tuple[tuple[Visit, ...], ...]
    cost: int


def plan_period(
    request: Request,
    inputs: list[DayInput],
    algorithm="beam",
    beam_width=32,
    objective=None,
    time_limit=5.0,
):
    if algorithm == "cp-sat":
        from app.planning.cp_sat import cp_sat_plan

        return cp_sat_plan(request, inputs, objective, time_limit)
    if not 1 <= len(inputs) <= 14 or not 1 <= beam_width <= 128:
        raise ValueError("Expected 1-14 days and beam width 1-128")
    if algorithm not in ("greedy", "beam"):
        raise ValueError("Unknown algorithm")
    for previous, current in zip(inputs, inputs[1:]):
        if (current.travel_date - previous.travel_date).days != 1:
            raise ValueError("Input dates must be consecutive")
    for data in inputs:
        plan(replace(request, mandatory=frozenset()), data.places, data.travel)
    by_id = {p.id: p for data in inputs for p in data.places}
    if len(by_id) > 100:
        raise ValueError("Period is limited to 100 distinct candidate POI")

    def visited(state):
        return {v.place_id for day in state.visits for v in day}

    objective = objective or Objective(request, inputs)

    def rank(state):
        return objective.key(state.visits)

    # Greedy consumes a shared remaining budget and never repeats a POI.
    initial = State(0, tuple(() for _ in inputs), 0)
    greedy = initial
    for index, data in enumerate(inputs):
        seen = visited(greedy)
        local = replace(
            request,
            budget=request.budget - greedy.cost,
            mandatory=request.mandatory & {p.id for p in data.places} - seen,
            excluded=request.excluded | seen,
        )
        outcome = plan(local, data.places, data.travel, objective, index, greedy.visits)
        visits = list(greedy.visits)
        visits[index] = tuple(outcome.visits)
        greedy = State(index, tuple(visits), greedy.cost + outcome.total_cost)
    spread = spread_visits(request, greedy.visits, inputs, objective)
    spread_cost = sum(
        objective.places[i][v.place_id].cost_per_person * len(request.participants)
        for i, day in enumerate(spread)
        for v in day
    )
    best = State(greedy.day, tuple(tuple(day) for day in spread), spread_cost)
    if algorithm == "beam" and not request.mandatory & request.excluded:
        frontier = [initial]
        for _ in range(len(by_id) + len(inputs)):
            expanded = []
            for state in frontier:
                data = inputs[state.day]
                seen = visited(state)
                today = state.visits[state.day]
                now = today[-1].end if today else request.start
                previous = today[-1].place_id if today else "start"
                for place in sorted(data.places, key=lambda p: p.id):
                    if place.id in seen or place.id in request.excluded:
                        continue
                    travel = data.travel.get((previous, place.id))
                    if travel is None:
                        continue
                    arrival = now + travel
                    rest = request.pace.break_minutes if request.pace and today else 0
                    begins = max(arrival + rest, place.opens)
                    ends = begins + place.duration
                    cost = state.cost + place.cost_per_person * len(request.participants)
                    if (
                        ends > min(place.closes, request.end)
                        or cost > request.budget
                        or not fits_pace(
                            request, [*today, Visit(place.id, arrival, begins, ends)], data.travel
                        )
                    ):
                        continue
                    days = list(state.visits)
                    days[state.day] = today + (Visit(place.id, arrival, begins, ends),)
                    expanded.append(State(state.day, tuple(days), cost))
                if state.day + 1 < len(inputs):
                    expanded.append(replace(state, day=state.day + 1))
            if not expanded:
                break
            unique = {(s.day, s.visits, s.cost): s for s in expanded}
            ordered = sorted(unique.values(), key=rank)
            # A transition to a later day consumes a search step but adds no utility.
            # Reserve slots per day so early-day high-utility prefixes do not erase
            # every postponed schedule before it can acquire further visits.
            quota = max(1, beam_width // len(inputs))
            counts = {}
            frontier = []
            for candidate in ordered:
                if counts.get(candidate.day, 0) < quota and len(frontier) < beam_width:
                    frontier.append(candidate)
                    counts[candidate.day] = counts.get(candidate.day, 0) + 1
            selected = set(frontier)
            frontier.extend(s for s in ordered if s not in selected)
            frontier = frontier[:beam_width]
            best = min([best, *frontier], key=rank)
    visits = spread_visits(request, best.visits, inputs, objective)
    cost = sum(
        objective.places[i][v.place_id].cost_per_person * len(request.participants)
        for i, day in enumerate(visits)
        for v in day
    )
    report, days = validate_period(request, visits, inputs, cost)
    flat = [v for day in visits for v in day]
    score = objective.evaluate(visits)
    satisfaction = {
        p.id: score.participant_utility[p.id] / len(flat) if flat else 0
        for p in request.participants
    }
    return Result(
        report.is_valid,
        flat,
        cost,
        satisfaction,
        list(dict.fromkeys(v.code for v in report.violations)),
        report,
        days=days,
        objective=score if report.is_valid else None,
    )
