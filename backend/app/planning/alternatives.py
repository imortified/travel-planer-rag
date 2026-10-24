"""Bounded deterministic diversification, checked against the original request."""

from collections import deque
from dataclasses import dataclass, replace
from math import ceil
from time import perf_counter

from app.planning.models import DayInput, Request, Result
from app.planning.objective import Objective
from app.planning.period import plan_period, validate_period
from app.planning.service import checked_plan
from app.planning.validator import validate_route

MIN_DISTANCE = 0.25
ATTEMPT_LIMIT = 13


@dataclass(frozen=True)
class ComparisonMetrics:
    visit_count: int
    participant_utility: dict[str, float]
    mean_utility: float
    min_utility: float
    utility_gap: float


@dataclass(frozen=True)
class Candidate:
    result: Result
    comparison: ComparisonMetrics


@dataclass(frozen=True)
class SearchOutcome:
    options: list[Candidate]
    diagnostic: Result | None
    search_info: dict


def place_ids(result):
    return frozenset(v.place_id for v in result.visits)


def distance(left, right):
    """Jaccard distance: order, time and date changes alone do not count."""
    a, b = place_ids(left), place_ids(right)
    return len(a ^ b) / len(a | b) if a or b else 0.0


def metrics(request, result, inputs):
    # Use each day's reference facts, including any date-specific category.
    visits = (
        [(d.visits, data.places) for d, data in zip(result.days, inputs, strict=True)]
        if result.days is not None
        else [(result.visits, inputs[0].places)]
    )
    totals = {p.id: 0.0 for p in request.participants}
    for day_visits, places in visits:
        by_id = {p.id: p for p in places}
        for visit in day_visits:
            for participant in request.participants:
                totals[participant.id] += participant.interests.get(
                    by_id[visit.place_id].category, 0
                )
    values = list(totals.values())
    return ComparisonMetrics(
        len(result.visits),
        totals,
        sum(values) / len(values),
        min(values),
        max(values) - min(values),
    )


def alternatives(
    request: Request,
    inputs: list[DayInput],
    algorithm="beam",
    beam_width=32,
    multi_day=False,
    time_limit=5.0,
) -> SearchOutcome:
    if not inputs or (not multi_day and len(inputs) != 1):
        raise ValueError("Expected one input for single-day planning")
    pool, queue, seen = [], deque([frozenset()]), {frozenset()}
    attempts, diagnostic = 0, None
    objective = Objective(request, inputs)
    started = perf_counter()
    timed_out = False

    def schedule(result):
        return [day.visits for day in result.days] if result.days is not None else [result.visits]

    def ranked_options():
        ordered = sorted(
            pool,
            key=lambda c: objective.key(schedule(c.result)),
        )
        selected = []
        for candidate in ordered:
            if all(distance(candidate.result, other.result) >= MIN_DISTANCE for other in selected):
                selected.append(candidate)
            if len(selected) == 3:
                break
        return selected

    while queue and attempts < ATTEMPT_LIMIT:
        remaining = max(0.0, time_limit - (perf_counter() - started))
        if algorithm == "cp-sat" and attempts and remaining == 0:
            timed_out = True
            break
        bans = queue.popleft()
        local = replace(request, excluded=request.excluded | bans)
        extra = {"time_limit": remaining} if algorithm == "cp-sat" else {}
        if multi_day:
            result = plan_period(local, inputs, algorithm, beam_width, objective, **extra)
            report, days = validate_period(
                request, [d.visits for d in result.days], inputs, result.total_cost
            )
            result = replace(result, validation=report, days=days)
        else:
            data = inputs[0]
            result = checked_plan(
                local, data.places, data.travel, algorithm, beam_width, objective, **extra
            )
            report = validate_route(
                request, result.visits, data.places, data.travel, result.total_cost
            )
            result = replace(result, validation=report, data_context=data.data_context)
        result = replace(
            result,
            is_feasible=result.is_feasible and report.is_valid,
            reasons=list(dict.fromkeys(result.reasons + [v.code for v in report.violations])),
            objective=objective.evaluate(schedule(result))
            if result.is_feasible and report.is_valid
            else None,
        )
        attempts += 1
        if diagnostic is None:
            diagnostic = result
        if result.solver and result.solver.status == "UNKNOWN":
            timed_out = True
            break
        if result.is_feasible and report.is_valid:
            pool.append(Candidate(result, metrics(request, result, inputs)))
            if len(ranked_options()) == 3:
                break
        # Exclude optional places only. Cyclic blocks change enough of the set to
        # avoid returning near-identical plans when the original visits many POI.
        optional = sorted(place_ids(result) - request.mandatory)
        if optional:
            size = min(len(optional), max(1, ceil(len(place_ids(result)) * MIN_DISTANCE)))
            for offset in range(min(6, len(optional))):
                block = {optional[(offset + i) % len(optional)] for i in range(size)}
                excluded = bans | block
                if excluded not in seen:
                    seen.add(excluded)
                    queue.append(excluded)
    selected = ranked_options()
    notes = []
    if timed_out:
        notes.append("cp_sat_time_budget_exhausted")
    if not selected:
        notes.append("no_feasible_options_found")
    elif len(selected) < 3:
        notes.append("fewer_distinct_options_found")
    return SearchOutcome(
        selected,
        None if selected else diagnostic,
        {
            "method": "bounded-exclusions-v2",
            "objective_version": objective.version,
            "attempts": attempts,
            "attempt_limit": ATTEMPT_LIMIT,
            "min_jaccard_distance": MIN_DISTANCE,
            "note_codes": notes,
        },
    )
