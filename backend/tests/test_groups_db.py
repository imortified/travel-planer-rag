"""Opt-in PostgreSQL tests. Each test is rolled back, including API commits."""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.session import engine, get_session
from app.main import app

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1", reason="Set RUN_DB_TESTS=1 after applying migrations"
)


@pytest.fixture
def client():
    with engine.connect() as connection:
        transaction = connection.begin()

        def test_session():
            with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
                yield session

        app.dependency_overrides[get_session] = test_session
        try:
            with TestClient(app) as http:
                yield http
        finally:
            app.dependency_overrides.pop(get_session, None)
            transaction.rollback()


def create_group(client, count=2):
    group = client.post("/api/v1/groups", json={"name": "Test group"})
    assert group.status_code == 201
    gid = group.json()["id"]
    ids = []
    for i in range(count):
        user = client.post("/api/v1/users", json={"name": f"Member {i}"})
        assert user.status_code == 201
        uid = user.json()["id"]
        assert (
            client.post(f"/api/v1/groups/{gid}/members", json={"user_id": uid}).status_code == 201
        )
        ids.append(uid)
    return gid, ids


def test_saved_trip_and_route_with_preference_snapshot(client):
    gid, ids = create_group(client)
    for uid, category in zip(ids, ["history", "nature"], strict=True):
        response = client.put(
            f"/api/v1/groups/{gid}/members/{uid}/preferences", json={"interests": {category: 1}}
        )
        assert response.status_code == 200
    trip = client.post(f"/api/v1/groups/{gid}/trips", json={"budget": 10000})
    assert trip.status_code == 201
    tid = trip.json()["id"]
    assert len(client.get(f"/api/v1/groups/{gid}").json()["members"]) == 2
    assert client.get(f"/api/v1/groups/{gid}/trips").json() == [trip.json()]
    assert client.get(f"/api/v1/trips/{tid}").json() == trip.json()
    client.put(
        f"/api/v1/groups/{gid}/members/{ids[0]}/preferences", json={"interests": {"food": 1}}
    )
    assert client.get(f"/api/v1/trips/{tid}").json()["participants_snapshot"][0]["interests"] == {
        "history": 1,
    }
    route = client.post(f"/api/v1/trips/{tid}/routes")
    assert route.status_code == 201
    saved = route.json()
    assert saved["result"]["is_feasible"]
    assert saved["result"]["total_cost"] == 5400
    assert saved["result"]["validation"] == {
        "is_valid": True,
        "computed_cost": 5400,
        "violations": [],
    }
    assert client.get(f"/api/v1/routes/{saved['id']}").json() == saved
    assert client.get(f"/api/v1/trips/{tid}/routes").json() == [saved]


def test_membership_limits_and_missing_resources(client):
    gid, ids = create_group(client, 6)
    assert client.post(f"/api/v1/groups/{gid}/members", json={"user_id": ids[0]}).status_code == 409
    extra = client.post("/api/v1/users", json={"name": "Extra"}).json()["id"]
    assert client.post(f"/api/v1/groups/{gid}/members", json={"user_id": extra}).status_code == 409
    assert (
        client.put(
            f"/api/v1/groups/{gid}/members/{extra}/preferences", json={"interests": {}}
        ).status_code
        == 404
    )
    assert client.get("/api/v1/trips/2147483647").status_code == 404
    assert client.get("/api/v1/groups/2147483647").status_code == 404


def test_empty_group_and_invalid_input(client):
    gid, _ = create_group(client, 0)
    assert client.post(f"/api/v1/groups/{gid}/trips", json={"budget": 10000}).status_code == 409
    assert client.post("/api/v1/users", json={"name": "   "}).status_code == 422
    gid, ids = create_group(client, 1)
    assert (
        client.put(
            f"/api/v1/groups/{gid}/members/{ids[0]}/preferences", json={"interests": {"history": 2}}
        ).status_code
        == 422
    )
    for payload in (
        {"budget": -1},
        {"budget": 100, "end": 1},
        {"budget": 100, "destination": "Other city"},
    ):
        assert client.post(f"/api/v1/groups/{gid}/trips", json=payload).status_code == 422


def test_infeasible_result_is_saved_as_infeasible(client):
    gid, _ = create_group(client)
    trip = client.post(
        f"/api/v1/groups/{gid}/trips",
        json={
            "budget": 10000,
            "mandatory": ["museum"],
            "excluded": ["museum"],
        },
    ).json()
    response = client.post(f"/api/v1/trips/{trip['id']}/routes")
    assert response.status_code == 201
    result = response.json()["result"]
    assert not result["is_feasible"]
    assert "mandatory_excluded_conflict" in result["reasons"]
    assert not result["validation"]["is_valid"]


def test_buggy_algorithm_cannot_persist_a_feasible_route(client, monkeypatch):
    from app.planning.models import Result, Visit

    gid, _ = create_group(client)
    trip = client.post(f"/api/v1/groups/{gid}/trips", json={"budget": 10000}).json()
    monkeypatch.setattr(
        "app.planning.service.plan",
        lambda *_: Result(True, [Visit("museum", 540, 540, 541)], 0, {}, []),
    )
    response = client.post(f"/api/v1/trips/{trip['id']}/routes")
    assert response.status_code == 201
    saved = client.get(f"/api/v1/routes/{response.json()['id']}").json()
    assert not saved["result"]["is_feasible"]
    assert "cost_mismatch" in saved["result"]["reasons"]


def test_beam_algorithm_and_report_are_saved(client):
    gid, _ = create_group(client)
    trip = client.post(f"/api/v1/groups/{gid}/trips", json={"budget": 10000}).json()
    response = client.post(f"/api/v1/trips/{trip['id']}/routes?algorithm=beam&beam_width=8")
    assert response.status_code == 201
    saved = client.get(f"/api/v1/routes/{response.json()['id']}").json()
    assert saved["algorithm"] == "beam-v1:w8"
    assert saved["result"]["validation"]["is_valid"]


def test_lisbon_snapshot_saved_and_source_failure_does_not_create_route(client, monkeypatch):
    from app.integrations.city import SourceUnavailable
    from app.planning.models import Place

    gid, _ = create_group(client)
    assert (
        client.post(
            f"/api/v1/groups/{gid}/trips",
            json={
                "destination": "Lisbon",
                "budget": 10000,
            },
        ).status_code
        == 422
    )
    trip = client.post(
        f"/api/v1/groups/{gid}/trips",
        json={
            "destination": "Lisbon",
            "travel_date": "2026-10-23",
            "budget": 10000,
        },
    ).json()
    assert trip["travel_date"] == "2026-10-23"
    context = {"source": "test-osm", "pois": [{"name": "Real fixture museum"}]}
    monkeypatch.setattr(
        "app.api.groups.CityProvider.dataset",
        lambda *_: (
            [Place("osm:node:1", "history", 0, 60, 600, 1080)],
            {("start", "osm:node:1"): 2},
            context,
        ),
    )
    saved = client.post(f"/api/v1/trips/{trip['id']}/routes?algorithm=beam").json()
    assert saved["data_source"] == "osm-osrm-lisbon-v1"
    assert saved["result"]["data_context"] == context
    assert client.get(f"/api/v1/routes/{saved['id']}").json() == saved

    def fail(*args):
        raise SourceUnavailable("OSRM unavailable")

    monkeypatch.setattr("app.api.groups.CityProvider.dataset", fail)
    assert client.post(f"/api/v1/trips/{trip['id']}/routes").status_code == 503
    assert len(client.get(f"/api/v1/trips/{trip['id']}/routes").json()) == 1
