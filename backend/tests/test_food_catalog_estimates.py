from copy import deepcopy
from dataclasses import replace
from datetime import date

import pytest
from catalog_helpers import NOW, snapshot
from test_catalog import reader
from test_meals_budget import LUNCH, fixture

from app.catalog.meals import meal_candidates, with_meal_estimates
from app.planning.budget import breakdown
from app.planning.meals import prepare_meals
from app.planning.models import Participant
from app.planning.period import plan_period
from app.planning.pricing import with_visit_prices


def test_unknown_menu_price_needs_explicit_estimate_and_does_not_override_access_or_hours():
    catalog = {"origin": {"latitude": 0, "longitude": 0}, "pois": [{
        "id": "osm:node:1", "name": "Cafe", "category": "food", "latitude": 0,
        "longitude": 0, "cost_per_person": None, "duration": 60, "window": [540, 1080],
        "issues": ["unknown_price"], "eligible": False,
    }]}
    original = deepcopy(catalog)
    assert with_meal_estimates(catalog, LUNCH) == catalog
    converted = with_meal_estimates(catalog, {**LUNCH, "mode": "estimate"})
    assert catalog == original
    poi = converted["pois"][0]
    assert poi["eligible"] and poi["cost_per_person"] == 1500
    assert poi["price_status"] == "user_estimate" and not poi.get("provenance")
    assert meal_candidates(converted, {**LUNCH, "mode": "estimate"}, frozenset()) == [poi]
    assert not meal_candidates(converted, {**LUNCH, "mode": "estimate"}, {poi["id"]})
    for issue in ["restricted_access", "unknown_closed_or_unsupported_hours", "catalog_expired"]:
        blocked = deepcopy(catalog)
        blocked["pois"][0]["issues"].append(issue)
        converted = with_meal_estimates(blocked, {**LUNCH, "mode": "estimate"})
        assert not converted["pois"][0]["eligible"]


def test_review_deadline_does_not_become_the_last_travel_date_but_stale_reviews_stay_blocked():
    data = snapshot(curated=True)
    fact = data.pois[0].model_copy(update={
        "provenance": data.pois[0].provenance.model_copy(update={"validity_kind": "review_window"})
    })
    data = data.model_copy(update={"pois": (fact,)})
    source = reader(data)
    result = source.catalog(date(2026, 12, 1))["pois"][0]
    assert result["eligible"] and result["price_status"] == "reference_estimate"
    assert result["provenance"]["valid_until"] == "2026-11-22"
    assert data.digest() == source.snapshot.data.digest()
    old = reader(data).catalog(date(2026, 9, 1))["pois"][0]
    assert "curation_outside_validity" in old["issues"]
    from datetime import timedelta
    stale = reader(data, now=NOW + timedelta(days=35)).catalog(date(2026, 12, 1))["pois"][0]
    assert "curation_review_expired" in stale["issues"]


@pytest.mark.parametrize("algorithm", ["greedy", "beam", "cp-sat"])
def test_estimated_meal_charges_each_person_once_and_keeps_real_road_legs(algorithm):
    request, inputs = fixture(days=2)
    request = replace(request, participants=[Participant("a", {"history": 1}),
                                             Participant("b", {"history": 1})])
    settings = {**LUNCH, "mode": "estimate"}
    request, prepared = prepare_meals(request, inputs, settings)
    result = plan_period(request, prepared, algorithm, time_limit=4)
    assert result.is_feasible and result.validation.is_valid
    result = with_visit_prices(result, prepared, 2, request.participants)
    meals = [v for v in result.visits if v.place_id in request.meal_ids]
    assert len(meals) == 2 and all(v.group_cost == 3000 for v in meals)
    assert all(r["reason"] == "user_meal_estimate" for v in meals for r in v.participant_prices)
    for data in prepared:
        meal = next(p for p in data.places if p.id in request.meal_ids)
        assert data.travel["start", meal.id] == 5
    from types import SimpleNamespace
    trip = SimpleNamespace(budget=request.budget, budget_settings=None,
                           participants_snapshot=[{"user_id": "a"}, {"user_id": "b"}],
                           meal_settings=settings)
    costs = breakdown(trip, result, prepared)
    assert costs["food"] == costs["food_estimate"] == 6000
    assert costs["food_reference"] == 0
    assert costs["known_total"] == result.total_cost


def test_two_meal_estimates_do_not_reuse_the_largest_slot_price():
    request, inputs = fixture(days=1)
    request = replace(request, end=1260, pace=None)
    settings = {"mode": "estimate", **LUNCH,
                "dinner": {"start": 1080, "end": 1260, "duration": 60, "max_cost_per_person": 2500}}
    request, prepared = prepare_meals(request, inputs, settings)
    costs = sorted(p.cost_per_person for p in prepared[0].places if p.id in request.meal_ids)
    assert costs == [1500, 2500]
    assert all(p.participant_costs == {"one": p.cost_per_person}
               for p in prepared[0].places if p.id in request.meal_ids)
