from dataclasses import replace
from datetime import date, timedelta
from itertools import permutations

import pytest

from app.planning.models import DayInput, Pace, Participant, Place, Request, Visit
from app.planning.objective import PACE_VERSION, VERSION, Objective
from app.planning.pace import (
    PROFILES,
    coverage_upper_bound,
    planning_summary,
    schedule_order,
    spread_visits,
)
from app.planning.period import plan_period, validate_period
from app.planning.service import checked_plan
from app.planning.validator import validate_route


def fixture(count=6, days=1, duration=75, mode="balanced"):
    places = [Place(str(i), "history", 100, duration, 540, 1080) for i in range(count)]
    travel = {(a, b.id): 5 for a in ["start", *[p.id for p in places]] for b in places if a != b.id}
    request = Request([Participant("one", {"history": 1})], 100000, 540, 1080, pace=PROFILES[mode])
    inputs = [DayInput(date(2026, 10, 24) + timedelta(days=i), places, travel) for i in range(days)]
    return request, inputs


@pytest.mark.parametrize("algorithm", ["greedy", "beam", "cp-sat"])
@pytest.mark.parametrize("mode,expected", [("relaxed", 2), ("balanced", 3), ("intensive", 5)])
def test_all_algorithms_enforce_daily_count_and_breaks(algorithm, mode, expected):
    request, inputs = fixture(duration=45, mode=mode)
    data = inputs[0]
    result = checked_plan(request, data.places, data.travel, algorithm, time_limit=2)
    assert result.is_feasible and result.validation.is_valid
    assert len(result.visits) == expected
    for previous, current in zip(result.visits, result.visits[1:]):
        assert current.start >= previous.end + 5 + request.pace.break_minutes
    assert result.objective.version == PACE_VERSION
    if result.solver:
        assert (
            abs(result.objective.value - result.solver.model_objective)
            <= result.solver.objective_error_bound
        )
        assert result.solver.original_upper_bound >= result.objective.value - 1e-8


@pytest.mark.parametrize("algorithm", ["greedy", "beam", "cp-sat"])
def test_load_cap_is_separate_from_count_and_opening_waits(algorithm):
    request, inputs = fixture(duration=165)
    request = replace(request, pace=Pace("balanced", 10, 360, 20))
    data = inputs[0]
    # Two visits: 330 minutes + 10 driving + 20 rest = 360 exactly.
    result = checked_plan(request, data.places, data.travel, algorithm, time_limit=2)
    assert result.is_feasible and len(result.visits) == 2
    summary = planning_summary(request, result, inputs)
    assert summary["days"][0]["active_minutes"] == 360
    waiting_place = [Place("late", "history", 0, 60, 900, 1080)]
    late = checked_plan(request, waiting_place, {("start", "late"): 5}, algorithm, time_limit=2)
    assert late.is_feasible  # Opening wait does not count against active-time policy.
    late_summary = planning_summary(
        request, late, [replace(data, places=waiting_place, travel={("start", "late"): 5})]
    )
    assert late_summary["days"][0]["active_minutes"] == 65
    assert late_summary["days"][0]["opening_wait_minutes"] == 355


@pytest.mark.parametrize("algorithm", ["greedy", "beam", "cp-sat"])
def test_mandatory_conflict_with_pace_is_infeasible(algorithm):
    request, inputs = fixture(count=3, mode="relaxed")
    request = replace(request, mandatory=frozenset({"0", "1", "2"}))
    result = checked_plan(request, inputs[0].places, inputs[0].travel, algorithm, time_limit=2)
    assert not result.is_feasible
    assert any("mandatory_not_scheduled" in reason for reason in result.reasons)


def test_validator_rejects_independent_count_load_and_rest_violations():
    request, inputs = fixture(count=4)
    data = inputs[0]
    visits = [Visit(str(i), 540 + i * 80, 540 + i * 80, 615 + i * 80) for i in range(4)]
    request = replace(request, pace=Pace("balanced", 3, 240, 20))
    report = validate_route(request, visits, data.places, data.travel)
    assert {"daily_visit_limit", "daily_load_limit", "insufficient_rest"} <= {
        v.code for v in report.violations
    }


@pytest.mark.parametrize("algorithm", ["greedy", "beam", "cp-sat"])
def test_fourteen_days_are_spread_without_repeated_places(algorithm):
    request, inputs = fixture(count=14, days=14, duration=40)
    # Behavioural check, not a latency benchmark: allow CP-SAT presolve on busy hosts.
    result = plan_period(request, inputs, algorithm, time_limit=10)
    assert result.is_feasible
    assert len(result.visits) == 14
    assert len({v.place_id for v in result.visits}) == 14
    assert all(len(day.visits) == 1 for day in result.days)
    assert result.objective.contributions["spread"] == pytest.approx(0.12)


@pytest.mark.parametrize("algorithm", ["greedy", "beam", "cp-sat"])
def test_dates_without_candidates_stay_empty_and_are_reported(algorithm):
    request, inputs = fixture(count=4, days=4)
    inputs[0] = replace(inputs[0], places=[], travel={})
    result = plan_period(request, inputs, algorithm, time_limit=2)
    assert result.is_feasible
    assert not result.days[0].visits
    assert len(result.visits) == 4
    summary = planning_summary(request, result, inputs)
    assert summary["active_days"] == 3
    assert {"dates_without_candidates", "unscheduled_days", "all_candidates_scheduled"} <= set(
        summary["warnings"]
    )


def test_relocation_respects_date_prices_and_missing_donor_edges():
    request, inputs = fixture(count=3, days=2)
    request = replace(request, budget=300)
    visits = schedule_order(request, ["0", "1", "2"], inputs[0])
    inputs[1] = replace(
        inputs[1], places=[replace(p, cost_per_person=1000) for p in inputs[1].places]
    )
    objective = Objective(request, inputs)
    assert spread_visits(request, [visits, []], inputs, objective) == [visits, []]
    inputs[1] = replace(inputs[1], places=[inputs[0].places[1]])
    # Only POI 1 can move; removing it destroys the only route from 0 to 2.
    inputs[0] = replace(
        inputs[0], travel={k: v for k, v in inputs[0].travel.items() if k != ("0", "2")}
    )
    assert spread_visits(request, [visits, []], inputs, Objective(request, inputs)) == [visits, []]


def test_zero_budget_relocation_never_scores_a_paid_date():
    request, inputs = fixture(count=2, days=2)
    request = replace(request, budget=0)
    inputs[0] = replace(inputs[0], places=[replace(p, cost_per_person=0) for p in inputs[0].places])
    visits = schedule_order(request, ["0", "1"], inputs[0])
    assert spread_visits(request, [visits, []], inputs, Objective(request, inputs)) == [visits, []]


def test_cp_sat_matches_exhaustive_paced_objective_on_date_specific_inputs():
    request, inputs = fixture(count=4, days=2, duration=95, mode="relaxed")
    inputs[1] = replace(
        inputs[1], places=[replace(p, cost_per_person=350) for p in inputs[1].places[:3]]
    )
    objective = Objective(request, inputs)
    best = float("-inf")
    for count in range(1, 5):
        for order in permutations(["0", "1", "2", "3"], count):
            for cut in range(count + 1):
                schedule = [
                    schedule_order(request, order[:cut], inputs[0]),
                    schedule_order(request, order[cut:], inputs[1]),
                ]
                if any(day is None for day in schedule):
                    continue
                cost = sum(
                    objective.places[i][v.place_id].cost_per_person
                    for i, day in enumerate(schedule)
                    for v in day
                )
                report, _ = validate_period(request, schedule, inputs, cost)
                if report.is_valid:
                    best = max(best, objective.evaluate(schedule).value)
    result = plan_period(request, inputs, "cp-sat", time_limit=3)
    assert result.is_feasible and result.solver.integer_model_optimal
    assert result.objective.value == pytest.approx(
        best, abs=result.solver.objective_error_bound * 2
    )


def test_legacy_default_keeps_original_formula_and_has_no_pace_caps():
    request, inputs = fixture(count=6, duration=45)
    request = replace(request, pace=None)
    result = plan_period(request, inputs, "greedy")
    assert len(result.visits) == 6
    assert result.objective.version == VERSION
    assert "spread" not in result.objective.contributions


def test_date_matching_bound_detects_shared_early_candidates():
    request, inputs = fixture(count=14, days=14)
    for i in range(9):
        inputs[i] = replace(inputs[i], places=inputs[i].places[:2])
    assert coverage_upper_bound(request, inputs) == 7
    assert coverage_upper_bound(replace(request, excluded=frozenset({"0", "1"})), inputs) == 5


def test_saved_fourteen_day_case_keeps_prices_and_spreads_within_date_bound():
    from pathlib import Path

    from app.evaluation.pace import load_fixture

    request, inputs, original = load_fixture(
        Path(__file__).resolve().parents[2] / "data/evaluation/pace-14-day-snapshot.json"
    )
    assert coverage_upper_bound(request, inputs) == 7
    legacy = plan_period(request, inputs, "greedy")
    assert legacy.total_cost == original["total_cost"] == 8350
    assert [len(day.visits) for day in legacy.days] == original["daily_visits"]
    paced = replace(request, pace=PROFILES["balanced"])
    result = plan_period(paced, inputs, "greedy")
    assert result.is_feasible and len(result.visits) == 14 and result.total_cost == 8350
    summary = planning_summary(paced, result, inputs)
    assert summary["active_days"] == summary["coverage_upper_bound"] == 7
    assert max(day["visit_count"] for day in summary["days"]) <= 3
    assert max(day["active_minutes"] for day in summary["days"]) <= 360


@pytest.mark.parametrize(
    "change",
    [
        {"max_visits": 0},
        {"max_active_minutes": -1},
        {"break_minutes": -1},
        {"version": "future"},
        {"max_visits": True},
    ],
)
def test_bad_or_unknown_snapshot_is_never_silently_interpreted(change):
    with pytest.raises(ValueError):
        replace(PROFILES["balanced"], **change)
