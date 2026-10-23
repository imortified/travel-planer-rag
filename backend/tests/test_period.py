from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.planning.models import DayInput, Participant, Place, Request, Visit
from app.planning.period import plan_period, validate_period


def scenario():
    first = date(2026, 10, 23)
    req = Request(
        [Participant("a", {"history": 1})], 100, 540, 600, mandatory=frozenset({"museum", "park"})
    )
    museum = Place("museum", "history", 50, 60, 540, 600)
    park = Place("park", "nature", 50, 60, 540, 600)
    travel = {
        ("start", "museum"): 0,
        ("start", "park"): 0,
        ("museum", "park"): 0,
        ("park", "museum"): 0,
    }
    days = [
        DayInput(first, [museum, park], travel),
        DayInput(first + timedelta(days=1), [museum], travel),
    ]
    return req, days


def test_beam_moves_flexible_required_place_to_later_day():
    req, days = scenario()
    assert not plan_period(req, days, "greedy").is_feasible
    result = plan_period(req, days, "beam")
    assert result.is_feasible
    assert [[v.place_id for v in d.visits] for d in result.days] == [["park"], ["museum"]]
    assert result.total_cost == 100
    assert result.validation.is_valid
    assert result == plan_period(req, days, "beam")


@pytest.mark.parametrize("algorithm", ["beam", "greedy"])
def test_budget_is_shared_and_missing_required_is_reported(algorithm):
    req, days = scenario()
    result = plan_period(replace(req, budget=50), days, algorithm)
    assert result.total_cost <= 50
    assert not result.is_feasible
    assert "mandatory_not_scheduled" in result.reasons


def test_independent_period_validator_rejects_duplicates_and_total_overspend():
    req, days = scenario()
    visits = [[Visit("museum", 540, 540, 600)], [Visit("museum", 540, 540, 600)]]
    report, _ = validate_period(replace(req, budget=50), visits, days, 0)
    assert {v.code for v in report.violations} >= {
        "duplicate_visit_across_days",
        "budget_exceeded",
        "cost_mismatch",
        "mandatory_not_scheduled",
    }


def test_daily_schedule_and_daily_origin_are_validated():
    req, days = scenario()
    second = replace(
        days[1], places=[replace(days[1].places[0], opens=570)], travel={("start", "museum"): 10}
    )
    report, _ = validate_period(
        req,
        [[Visit("park", 540, 540, 600)], [Visit("museum", 540, 540, 600)]],
        [days[0], second],
        100,
    )
    assert {v.code for v in report.violations} >= {"outside_opening_hours", "unreachable_arrival"}


def test_closed_day_is_explicit_and_no_repeats_on_later_days():
    req, days = scenario()
    inputs = [
        replace(days[0], places=[]),
        days[1],
        replace(days[1], travel_date=days[1].travel_date + timedelta(days=1)),
    ]
    result = plan_period(replace(req, mandatory=frozenset({"museum"})), inputs)
    assert result.is_feasible
    assert len(result.days) == 3
    assert not result.days[0].visits
    assert len(result.visits) == 1
    assert result.total_cost == 50


def test_no_eligible_places_cannot_produce_feasible_period():
    req, days = scenario()
    result = plan_period(req, [replace(d, places=[]) for d in days])
    assert not result.is_feasible
    assert "no_feasible_visits" in result.reasons


def test_beam_balances_days_when_utility_and_cost_are_equal():
    req, days = scenario()
    inputs = [days[0], replace(days[0], travel_date=days[1].travel_date)]
    result = plan_period(req, inputs)
    assert result.is_feasible
    assert [len(d.visits) for d in result.days] == [1, 1]


def test_period_rejects_nonconsecutive_days():
    req, days = scenario()
    with pytest.raises(ValueError, match="consecutive"):
        plan_period(
            req, [days[0], replace(days[1], travel_date=days[1].travel_date + timedelta(days=1))]
        )


def test_larger_catalog_keeps_later_days_in_search():
    first = date(2026, 10, 23)
    places = [Place(f"p{i}", "history", 100, 45, 540, 1080) for i in range(9)]
    travel = {(a, b.id): 1 for a in ["start"] + [p.id for p in places] for b in places if a != b.id}
    inputs = [DayInput(first + timedelta(days=i), places, travel) for i in range(3)]
    req = Request([Participant("a", {"history": 1})], 10000, 540, 1080)
    result = plan_period(req, inputs)
    assert result.is_feasible
    assert len(result.visits) == 9
    assert all(day.visits for day in result.days)
