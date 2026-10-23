from dataclasses import replace

from fastapi.testclient import TestClient

from app.integrations.demo import load_demo
from app.main import app
from app.planning.models import Participant, Place, Request
from app.planning.planner import plan


def request(**changes):
    base = Request(
        [Participant("a", {"history": 1}), Participant("b", {"nature": 1})], 10000, 540, 1080
    )
    return replace(base, **changes)


def test_demo_respects_schedule_and_budget():
    places, travel = load_demo()
    result = plan(request(), places, travel)
    assert result.is_feasible
    assert result.total_cost == 5400
    by_id = {p.id: p for p in places}
    previous, end = "start", 540
    for visit in result.visits:
        place = by_id[visit.place_id]
        assert visit.arrival == end + travel[previous, visit.place_id]
        assert visit.start >= max(visit.arrival, place.opens)
        assert visit.end == visit.start + place.duration
        assert visit.end <= min(place.closes, 1080)
        previous, end = visit.place_id, visit.end


def test_mandatory_place_over_budget():
    place = Place("museum", "history", 1200, 60, 540, 1080)
    result = plan(
        request(budget=2399, mandatory=frozenset({"museum"})), [place], {("start", "museum"): 10}
    )
    assert not result.is_feasible
    assert "mandatory_not_scheduled:museum" in result.reasons


def test_closed_place():
    place = Place("museum", "history", 0, 60, 480, 540)
    assert not plan(request(), [place], {("start", "museum"): 0}).is_feasible


def test_impossible_transfer_and_missing_routing():
    place = Place("museum", "history", 0, 60, 540, 1080)
    for travel in ({}, {("start", "museum"): 500}):
        assert not plan(request(), [place], travel).is_feasible


def test_conflicting_constraints():
    places, travel = load_demo()
    result = plan(
        request(mandatory=frozenset({"park"}), excluded=frozenset({"park"})), places, travel
    )
    assert result.reasons == ["mandatory_excluded_conflict"]


def test_api():
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}
    response = client.post(
        "/api/v1/plans/demo",
        json={
            "participants": [
                {"id": "a", "interests": {"history": 1}},
                {"id": "b", "interests": {"nature": 1}},
            ],
            "budget": 10000,
        },
    )
    assert response.status_code == 200
    assert response.json()["is_feasible"]
    assert (
        client.post(
            "/api/v1/plans/demo",
            json={
                "participants": [{"id": "a", "interests": {}}],
                "budget": 0,
                "start": 1080,
                "end": 540,
            },
        ).status_code
        == 422
    )
