from itertools import permutations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.planning.beam import route_key
from app.planning.models import Result, Visit
from app.planning.scenarios import scenarios
from app.planning.service import checked_plan


@pytest.mark.parametrize("name,trip_request,places,travel", list(scenarios()))
def test_search_preserves_baseline_and_is_deterministic(name, trip_request, places, travel):
    greedy = checked_plan(trip_request, places, travel)
    beam = checked_plan(trip_request, places, travel, "beam")
    assert route_key(trip_request, places, beam) <= route_key(trip_request, places, greedy)
    assert beam == checked_plan(trip_request, list(reversed(places)), travel, "beam")
    if name == "time_window_trap":
        assert not greedy.is_feasible
        assert beam.is_feasible
        assert [v.place_id for v in beam.visits] == ["b_early", "a_late"]
    if name in ("optional_window_trap", "budget_trap"):
        assert route_key(trip_request, places, beam) < route_key(trip_request, places, greedy)
    if beam.is_feasible:
        assert beam.validation.is_valid


def test_small_case_matches_exhaustive_optimum():
    _, trip_request, places, travel = next(scenarios())
    candidates = []
    for length in range(1, len(places) + 1):
        for order in permutations(places, length):
            now, previous, cost, visits = trip_request.start, "start", 0, []
            for place in order:
                arrival = now + travel[previous, place.id]
                begins = max(arrival, place.opens)
                now = begins + place.duration
                cost += place.cost_per_person * len(trip_request.participants)
                visits.append(Visit(place.id, arrival, begins, now))
                previous = place.id
            from app.planning.validator import validate_route

            report = validate_route(trip_request, visits, places, travel, cost)
            if report.is_valid:
                candidates.append(Result(True, visits, cost, {}, []))
    expected = min(candidates, key=lambda r: route_key(trip_request, places, r))
    actual = checked_plan(trip_request, places, travel, "beam")
    assert route_key(trip_request, places, actual) == route_key(trip_request, places, expected)


def test_algorithm_query_validation():
    client = TestClient(app)
    payload = {"participants": [{"id": "a", "interests": {"history": 1}}], "budget": 10000}
    response = client.post("/api/v1/plans/demo?algorithm=beam&beam_width=8", json=payload)
    assert response.status_code == 200
    assert response.json()["validation"]["is_valid"]
    for query in ("algorithm=invalid", "algorithm=beam&beam_width=0", "beam_width=129"):
        assert client.post(f"/api/v1/plans/demo?{query}", json=payload).status_code == 422
