from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.planning.budget import activity_budget, breakdown
from app.planning.meals import prepare_meals
from app.planning.models import DayInput, Participant, Place, Request, Visit
from app.planning.pace import PROFILES, planning_summary
from app.planning.period import plan_period, validate_period
from app.planning.service import checked_plan
from app.planning.validator import validate_route
from app.schemas.groups import TripInput


def fixture(days=1, count=6):
    places = [Place(f"museum:{i}", "history", 100, 45, 540, 1080) for i in range(count)]
    places.append(Place("cafe", "food", 1000, 60, 540, 1260))
    points = ["start", *[p.id for p in places]]
    travel = {(a, b): 5 for a in points for b in points if a != b and b != "start"}
    context = {
        "pois": [
            {
                "id": p.id,
                "name": p.id,
                "meal_types": ["breakfast", "lunch", "dinner"],
                "provenance": {"checked_on": "2026-10-25"},
            }
            for p in places
        ]
    }
    inputs = [
        DayInput(date(2026, 10, 26) + timedelta(days=i), places, travel, context)
        for i in range(days)
    ]
    request = Request(
        [Participant("one", {"history": 1, "food": 0.5})],
        100000,
        540,
        1080,
        pace=PROFILES["balanced"],
    )
    return request, inputs


LUNCH = {"lunch": {"start": 720, "end": 900, "duration": 60, "max_cost_per_person": 1500}}


@pytest.mark.parametrize("algorithm", ["greedy", "beam", "cp-sat"])
def test_required_lunch_has_real_road_legs_price_and_does_not_replace_visit_limit(algorithm):
    request, inputs = prepare_meals(*fixture(), LUNCH)
    data = inputs[0]
    result = checked_plan(request, data.places, data.travel, algorithm, time_limit=4)
    assert result.is_feasible and result.validation.is_valid
    lunch = next(v for v in result.visits if v.place_id in request.meal_ids)
    assert 720 <= lunch.start < lunch.end <= 900
    assert sum(v.place_id not in request.meal_ids for v in result.visits) == 3
    assert result.total_cost == 1300
    assert any(v.start < 720 for v in result.visits)
    assert data.data_context["meal_selection"]["policy"] == "reviewed-meal-venues-v1"
    assert data.travel["start", lunch.place_id] == 5
    assert result.objective.version == "normalized-group-objective-v3-meals"


@pytest.mark.parametrize("algorithm", ["greedy", "beam", "cp-sat"])
def test_restaurant_can_repeat_for_dated_meals_but_attractions_do_not_repeat(algorithm):
    request, inputs = prepare_meals(*fixture(days=3), LUNCH)
    result = plan_period(request, inputs, algorithm, time_limit=8)
    assert result.is_feasible, result.reasons
    assert len(request.meal_ids) == 3
    attractions = [v.place_id for v in result.visits if v.place_id not in request.meal_ids]
    assert len(attractions) == len(set(attractions))
    assert all(any(v.place_id in request.meal_ids for v in day.visits) for day in result.days)
    assert all(any(v.place_id not in request.meal_ids for v in day.visits) for day in result.days)
    report, _ = validate_period(
        request, [day.visits for day in result.days], inputs, result.total_cost
    )
    assert report.is_valid


def test_meals_alone_do_not_claim_excursion_coverage():
    request, inputs = prepare_meals(*fixture(days=2, count=0), LUNCH)
    result = plan_period(request, inputs, "greedy")
    summary = planning_summary(request, result, inputs)
    assert result.is_feasible
    assert summary["attraction_days"] == summary["coverage_upper_bound"] == 0
    assert result.objective.day_coverage == 0
    assert "unscheduled_days" in summary["warnings"]


def test_meal_minutes_still_count_against_load_and_meals_cannot_be_omitted():
    request, inputs = prepare_meals(*fixture(), LUNCH)
    data = inputs[0]
    meal = next(p for p in data.places if p.id in request.meal_ids)
    request = replace(request, pace=replace(request.pace, max_active_minutes=50))
    report = validate_route(request, [Visit(meal.id, 545, 720, 780)], data.places, data.travel)
    assert "daily_load_limit" in {v.code for v in report.violations}
    report = validate_route(request, [], data.places, data.travel)
    assert "mandatory_not_scheduled" in {v.code for v in report.violations}


def test_unsupported_unreviewed_excluded_or_too_expensive_food_never_becomes_a_meal():
    request, inputs = fixture()
    for change in ("unreviewed", "wrong_type", "excluded", "too_expensive"):
        context = {"pois": [{**p} for p in inputs[0].data_context["pois"]]}
        cafe = next(p for p in context["pois"] if p["id"] == "cafe")
        local = request
        settings = LUNCH
        if change == "unreviewed":
            cafe.pop("provenance")
        elif change == "wrong_type":
            cafe["meal_types"] = ["breakfast"]
        elif change == "excluded":
            local = replace(local, excluded=frozenset({"cafe"}))
        else:
            settings = {"lunch": {**LUNCH["lunch"], "max_cost_per_person": 900}}
        with pytest.raises(ValueError, match="нет проверенного"):
            prepare_meals(local, [replace(inputs[0], data_context=context)], settings)
    with pytest.raises(ValueError, match="На выбранное питание"):
        prepare_meals(replace(request, budget=999), inputs, LUNCH)


def test_budget_reserves_estimates_once_and_distinguishes_unknown_from_zero():
    request, inputs = prepare_meals(*fixture(), LUNCH)
    data = inputs[0]
    result = checked_plan(request, data.places, data.travel)
    trip = SimpleNamespace(
        budget=20000,
        budget_settings={"lodging": 10000, "transport": None, "reserve": 2000},
        participants_snapshot=[{}],
        meal_settings=LUNCH,
    )
    assert activity_budget(trip) == 8000
    report = breakdown(trip, result, inputs)
    assert report["food"] == 1000 and report["admissions"] == 300
    assert report["known_total"] == 11300
    assert report["unallocated_after_reserve"] == 6700
    assert report["unknown_categories"] == ["transport"]
    trip.budget_settings["transport"] = 0
    assert breakdown(trip, result, inputs)["unknown_categories"] == []


def test_api_settings_validate_total_and_day_windows():
    with pytest.raises(ValueError, match="превышают"):
        TripInput(budget=100, budget_settings={"lodging": 80, "reserve": 30})
    with pytest.raises(ValueError, match="реального города"):
        TripInput(budget=10000, meal_settings=LUNCH)
    with pytest.raises(ValueError, match="время дня"):
        TripInput(
            destination="Lisbon",
            travel_date="2026-10-26",
            budget=10000,
            start=540,
            end=660,
            meal_settings=LUNCH,
        )
