from dataclasses import replace

import pytest

from app.planning.models import Participant, Place, Request, Result, Visit
from app.planning.service import checked_plan
from app.planning.validator import validate_route


@pytest.fixture
def scenario():
    request = Request([Participant("a", {}), Participant("b", {})], 4000, 540, 1080)
    places = [Place("museum", "history", 1200, 60, 600, 1000)]
    travel = {("start", "museum"): 15}
    return request, places, travel


def test_waiting_and_exact_budget_are_valid(scenario):
    request, places, travel = scenario
    report = validate_route(
        replace(request, budget=2400), [Visit("museum", 555, 600, 660)], places, travel, 2400
    )
    assert report.is_valid
    assert report.computed_cost == 2400
    assert report.violations == []


@pytest.mark.parametrize(
    ("visit", "code"),
    [
        (Visit("museum", 550, 600, 660), "unreachable_arrival"),
        (Visit("museum", 555, 570, 630), "outside_opening_hours"),
        (Visit("museum", 990, 990, 1050), "outside_opening_hours"),
        (Visit("museum", 555, 600, 650), "insufficient_visit_duration"),
        (Visit("museum", 555, 540, 660), "invalid_visit_times"),
        (Visit("museum", 1050, 1050, 1110), "invalid_visit_times"),
        (Visit("unknown", 555, 600, 660), "unknown_place"),
    ],
)
def test_invalid_visits(scenario, visit, code):
    request, places, travel = scenario
    report = validate_route(request, [visit], places, travel)
    assert not report.is_valid
    violation = next(v for v in report.violations if v.code == code)
    assert violation.visit_index == 0
    assert violation.place_id == visit.place_id


def test_budget_and_cost_are_recomputed(scenario):
    request, places, travel = scenario
    report = validate_route(
        replace(request, budget=2000),
        [Visit("museum", 555, 600, 660)],
        places,
        travel,
        reported_cost=0,
    )
    assert report.computed_cost == 2400
    assert {v.code for v in report.violations} == {"budget_exceeded", "cost_mismatch"}


def test_missing_routing_duplicate_and_overlap(scenario):
    request, places, travel = scenario
    visits = [Visit("museum", 555, 600, 660), Visit("museum", 650, 650, 710)]
    report = validate_route(request, visits, places, {**travel, ("museum", "museum"): 0})
    assert {v.code for v in report.violations} >= {"duplicate_visit", "unreachable_arrival"}
    report = validate_route(request, visits[:1], places, {})
    assert {v.code for v in report.violations} == {"missing_travel_time"}


def test_required_and_excluded_places(scenario):
    request, places, travel = scenario
    report = validate_route(
        replace(
            request, mandatory=frozenset({"museum", "missing"}), excluded=frozenset({"museum"})
        ),
        [],
        places,
        travel,
    )
    assert {v.code for v in report.violations} == {
        "mandatory_excluded_conflict",
        "unknown_mandatory_place",
        "mandatory_not_scheduled",
        "no_feasible_visits",
    }
    report = validate_route(
        replace(request, excluded=frozenset({"museum"})),
        [Visit("museum", 555, 600, 660)],
        places,
        travel,
    )
    assert {v.code for v in report.violations} == {"excluded_place"}


def test_guard_rejects_a_buggy_algorithm(scenario, monkeypatch):
    request, places, travel = scenario
    fake = Result(True, [Visit("museum", 540, 540, 550)], 0, {}, [])
    monkeypatch.setattr("app.planning.service.plan", lambda *_: fake)
    result = checked_plan(request, places, travel)
    assert not result.is_feasible
    assert not result.validation.is_valid
    assert {v.code for v in result.validation.violations} >= {
        "unreachable_arrival",
        "outside_opening_hours",
        "insufficient_visit_duration",
        "cost_mismatch",
    }


def test_invalid_reference_data_is_not_a_route_verdict(scenario):
    request, places, travel = scenario
    with pytest.raises(ValueError, match="reference data"):
        validate_route(request, [], places, {("start", "museum"): -10})
