from dataclasses import replace
from datetime import date, timedelta
from itertools import permutations
from random import Random

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.planning.alternatives import alternatives
from app.planning.cp_sat import cp_sat_plan
from app.planning.models import DayInput, Participant, Place, Request, Visit
from app.planning.objective import Objective
from app.planning.period import plan_period, validate_period
from app.planning.scenarios import scenarios
from app.planning.service import checked_plan


def route(order, data, request):
    visits, previous, now = [], "start", request.start
    for pid in order:
        place = next(p for p in data.places if p.id == pid)
        if (previous, pid) not in data.travel:
            return None
        arrival = now + data.travel[previous, pid]
        begins = max(arrival, place.opens)
        now = begins + place.duration
        visits.append(Visit(pid, arrival, begins, now))
        previous = pid
    return visits


def exhaustive(request, inputs):
    objective = Objective(request, inputs)
    ids = sorted({p.id for d in inputs for p in d.places})
    best = None
    for count in range(1, len(ids) + 1):
        for order in permutations(ids, count):
            cuts = range(count + 1) if len(inputs) == 2 else [count]
            for cut in cuts:
                chunks = [order[:cut], order[cut:]] if len(inputs) == 2 else [order]
                schedule = []
                for chunk, data in zip(chunks, inputs, strict=True):
                    if set(chunk) - {p.id for p in data.places}:
                        break
                    visits = route(chunk, data, request)
                    if visits is None:
                        break
                    schedule.append(visits)
                if len(schedule) != len(inputs):
                    continue
                cost = sum(
                    objective.places[d][v.place_id].cost_per_person * len(request.participants)
                    for d, visits in enumerate(schedule)
                    for v in visits
                )
                report, _ = validate_period(request, schedule, inputs, cost)
                if report.is_valid:
                    value = objective.evaluate(schedule).value
                    best = max(best, value) if best is not None else value
    return best


@pytest.mark.parametrize("name,trip_request,places,travel", list(scenarios()))
def test_reference_cases_have_honest_solver_status(name, trip_request, places, travel):
    result = checked_plan(trip_request, places, travel, "cp-sat")
    if name in {"constraint_conflict", "missing_routing", "empty_catalog"}:
        assert not result.is_feasible
        assert result.solver.status == "INFEASIBLE"
        assert result.objective is None
    else:
        assert result.is_feasible and result.validation.is_valid
        assert result.solver.status == "OPTIMAL"
        assert result.solver.integer_model_optimal
        assert result.solver.original_upper_bound >= result.objective.value - 1e-9


@pytest.mark.parametrize("seed", [17, 43, 71, 103, 151])
def test_small_random_instances_match_exhaustive_bound(seed):
    rng = Random(seed)
    request = Request(
        [Participant("a", {"h": 1, "n": 0.1}), Participant("b", {"n": 1, "h": 0.2})], 1400, 540, 760
    )
    places = [
        Place(
            f"p{i}",
            rng.choice(["h", "n"]),
            rng.choice([0, 100, 250, 500]),
            rng.choice([30, 45, 60]),
            rng.choice([540, 570, 600]),
            rng.choice([660, 710, 760]),
        )
        for i in range(5)
    ]
    travel = {
        (a, b.id): rng.randint(0, 30)
        for a in ["start"] + [p.id for p in places]
        for b in places
        if a != b.id and rng.random() > 0.1
    }
    inputs = [DayInput(date(2026, 10, 24), places, travel)]
    expected = exhaustive(request, inputs)
    result = cp_sat_plan(request, inputs)
    assert result.solver.status == "OPTIMAL"
    assert abs(result.objective.value - expected) <= 2 * result.solver.objective_error_bound
    assert expected <= result.solver.original_upper_bound + 1e-9


def test_multiday_exact_constraints_and_date_specific_scoring():
    request = Request(
        [Participant("a", {"h": 1}), Participant("b", {"n": 1})],
        1200,
        540,
        670,
        mandatory=frozenset({"p0", "p1"}),
        excluded=frozenset({"p3"}),
    )
    places = [Place(f"p{i}", "h" if i % 2 else "n", 100, 60, 540, 670) for i in range(4)]
    travel = {(a, b.id): 5 for a in ["start"] + [p.id for p in places] for b in places if a != b.id}
    first = DayInput(date(2026, 10, 24), [places[0], places[2], places[3]], travel)
    second = replace(
        first,
        travel_date=first.travel_date + timedelta(days=1),
        places=[replace(places[0], category="h", cost_per_person=150), places[1]],
    )
    inputs = [first, second]
    result = plan_period(request, inputs, "cp-sat")
    expected = exhaustive(request, inputs)
    assert result.validation.is_valid
    assert abs(expected - result.objective.value) <= 2 * result.solver.objective_error_bound
    ids = [v.place_id for v in result.visits]
    assert len(ids) == len(set(ids)) and not set(ids) & request.excluded
    assert request.mandatory <= set(ids)
    assert any(v.place_id == "p1" for v in result.days[1].visits)
    assert result.total_cost <= request.budget


def test_missing_start_edge_can_be_reached_via_another_place_and_no_return_is_required():
    request = Request([Participant("a", {"h": 1})], 0, 540, 700, mandatory=frozenset({"a", "b"}))
    places = [Place("a", "h", 0, 60, 540, 700), Place("b", "h", 0, 60, 600, 700)]
    result = checked_plan(request, places, {("start", "a"): 0, ("a", "b"): 10}, "cp-sat")
    assert [v.place_id for v in result.visits] == ["a", "b"]
    assert result.objective.travel_minutes == 10


def test_zero_budget_empty_days_and_missing_interests_are_supported():
    request = Request([Participant("a", {})], 0, 540, 600)
    first = DayInput(date(2026, 10, 24), [], {})
    second = DayInput(
        first.travel_date + timedelta(days=1),
        [Place("p", "h", 0, 60, 540, 600)],
        {("start", "p"): 0},
    )
    result = cp_sat_plan(request, [first, second])
    assert result.is_feasible and not result.days[0].visits
    assert result.objective.value == 0


def test_unknown_is_not_reported_as_infeasible_or_optimal():
    name, request, places, travel = next(scenarios())
    result = cp_sat_plan(request, [DayInput(date(2026, 10, 24), places, travel)], time_limit=0)
    assert result.solver.status == "UNKNOWN"
    assert not result.solver.integer_model_optimal
    assert result.solver.original_upper_bound is None
    assert "cp_sat_unknown" in result.reasons
    assert "cp_sat_infeasible" not in result.reasons


def test_feasible_status_never_claims_a_proven_integer_optimum(monkeypatch):
    from ortools.sat.python import cp_model

    original = cp_model.CpSolver

    class LimitedSolver(original):
        def solve(self, model):
            super().solve(model)
            return cp_model.FEASIBLE

    monkeypatch.setattr("app.planning.cp_sat.cp_model.CpSolver", LimitedSolver)
    _, request, places, travel = next(scenarios())
    result = checked_plan(request, places, travel, "cp-sat")
    assert result.is_feasible and result.solver.status == "FEASIBLE"
    assert not result.solver.integer_model_optimal


def test_coarse_scaling_has_an_explicit_bound_and_alternatives_keep_shared_scales():
    _, request, places, travel = list(scenarios())[2]
    inputs = [DayInput(date(2026, 10, 24), places, travel)]
    result = cp_sat_plan(request, inputs, scale=100)
    assert exhaustive(request, inputs) <= result.solver.original_upper_bound + 1e-9
    output = alternatives(request, inputs, "cp-sat")
    assert output.options
    for candidate in output.options:
        assert candidate.result.objective.utility_bounds == Objective(request, inputs).bounds
    timed = alternatives(request, inputs, "cp-sat", time_limit=0)
    assert not timed.options
    assert "cp_sat_time_budget_exhausted" in timed.search_info["note_codes"]


def test_cp_sat_api_limits_and_status():
    client = TestClient(app)
    payload = {"participants": [{"id": "a", "interests": {"history": 1}}], "budget": 10000}
    response = client.post("/api/v1/plans/demo?algorithm=cp-sat&time_limit_seconds=1", json=payload)
    assert response.status_code == 200
    assert response.json()["solver"]["status"] == "OPTIMAL"
    assert response.json()["validation"]["is_valid"]
    for query in ["time_limit_seconds=0", "time_limit_seconds=31", "time_limit_seconds=nan"]:
        assert (
            client.post(f"/api/v1/plans/demo?algorithm=cp-sat&{query}", json=payload).status_code
            == 422
        )


def test_first_incumbent_fairness_matches_shared_score_without_lin_max_relaxation(monkeypatch):
    from ortools.sat.python import cp_model

    from app.planning.benchmark import generated

    original = cp_model.CpSolver

    class FirstSolutionSolver(original):
        def __init__(self):
            super().__init__()
            self.parameters.stop_after_first_solution = True

    monkeypatch.setattr("app.planning.cp_sat.cp_model.CpSolver", FirstSolutionSolver)
    instance = generated(30, 3, True, 17)
    result = cp_sat_plan(instance.request, instance.inputs, time_limit=3)
    assert result.is_feasible
    assert (
        abs(result.objective.value - result.solver.model_objective)
        <= result.solver.objective_error_bound
    )
