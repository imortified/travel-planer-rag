"""MVP §3.3: HTTP → PostgreSQL → planner → validation → saved snapshots.

POI and LLM adapters are controlled fixtures. These tests do not measure live-source
accuracy or model quality; see docs/acceptance.md for separate browser/live checks.
"""

import os
from copy import deepcopy
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.session import engine, get_session
from app.integrations.demo import load_demo
from app.integrations.llama_cpp import LlmUnavailable
from app.main import app
from app.schemas.assistant import Evidence, ExplanationClaim, ExplanationDraft, ExtractedTrip

pytestmark = [
    pytest.mark.acceptance,
    pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="Requires migrated PostgreSQL"),
]


@pytest.fixture
def client(monkeypatch):
    def dataset(self, travel_date, *args, **kwargs):
        places, travel = load_demo()
        # Synthetic geography for contract checks, never published as real POI evidence.
        pois = [
            {
                "id": p.id,
                "name": f"Fixture {p.id}",
                "longitude": -9.14 + i * 0.001,
                "latitude": 38.72 + (travel_date.day % 2) * 0.001,
            }
            for i, p in enumerate(places)
        ]
        return (
            places,
            travel,
            {
                "pois": pois,
                "origin": {
                    "id": "start",
                    "name": "Fixture start",
                    "longitude": -9.14,
                    "latitude": 38.71,
                },
                "transport": "driving",
                "fetched_at": "2026-10-24T00:00:00Z",
                "attribution": "Synthetic acceptance fixture",
            },
        )

    monkeypatch.setattr("app.api.groups.CityProvider.dataset", dataset)
    monkeypatch.setattr("app.visa.reference_date", lambda: date(2026, 10, 24))
    with engine.connect() as connection:
        transaction = connection.begin()

        def session():
            with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
                yield db

        app.dependency_overrides[get_session] = session
        try:
            with TestClient(app) as http:
                yield http
        finally:
            app.dependency_overrides.pop(get_session, None)
            transaction.rollback()


def group(client, count=2):
    gid = client.post("/api/v1/groups", json={"name": "Acceptance"}).json()["id"]
    ids = []
    for i in range(count):
        uid = client.post("/api/v1/users", json={"name": f"Member {i}"}).json()["id"]
        assert (
            client.post(f"/api/v1/groups/{gid}/members", json={"user_id": uid}).status_code == 201
        )
        interests = {"history": 1, "nature": 0} if i % 2 == 0 else {"nature": 1, "history": 0}
        assert (
            client.put(
                f"/api/v1/groups/{gid}/members/{uid}/preferences", json={"interests": interests}
            ).status_code
            == 200
        )
        ids.append(uid)
    return gid, ids


def trip(client, gid, **overrides):
    response = client.post(
        f"/api/v1/groups/{gid}/trips",
        json={"destination": "Lisbon", "travel_date": "2026-11-02", "budget": 30000, **overrides},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("count", [1, 2, 6])
@pytest.mark.parametrize("days", [1, 3])
@pytest.mark.parametrize("algorithm", ["greedy", "beam", "cp-sat"])
def test_a01_to_a05_profiles_period_constraints_history_and_map_snapshot(
    client, count, days, algorithm
):
    gid, ids = group(client, count)
    saved = trip(
        client, gid, end_date=f"2026-11-{days + 1:02}", mandatory=["museum"], excluded=["market"]
    )
    snapshot = deepcopy(saved["participants_snapshot"])
    client.put(f"/api/v1/groups/{gid}/members/{ids[0]}/preferences", json={"interests": {}})
    response = client.post(f"/api/v1/trips/{saved['id']}/route-sets?algorithm={algorithm}")
    assert response.status_code == 201, response.text
    outcome = response.json()
    assert outcome["options"]
    for route in outcome["options"]:
        result = route["result"]
        assert result["is_feasible"] and result["validation"]["is_valid"]
        assert result["total_cost"] <= saved["budget"]
        visits = [v["place_id"] for v in result["visits"]]
        assert "museum" in visits and "market" not in visits and len(visits) == len(set(visits))
        assert set(result["objective"]["participant_utility"]) == {str(uid) for uid in ids}
        schedule = result["days"] or [result]
        assert len(schedule) == days
        for day in schedule:
            assert day["data_context"]["origin"]["name"] == "Fixture start"
            assert all("latitude" in p and "longitude" in p for p in day["data_context"]["pois"])
        assert client.get(f"/api/v1/routes/{route['id']}").json()["result"] == result
    assert client.get(f"/api/v1/route-sets/{outcome['id']}").json() == outcome
    assert client.get(f"/api/v1/trips/{saved['id']}").json()["participants_snapshot"] == snapshot
    assert client.get(f"/api/v1/groups/{gid}/trips").json()[0]["id"] == saved["id"]


def test_a06_three_alternatives_have_different_places_and_equal_normalization(client):
    gid, _ = group(client)
    saved = trip(client, gid)
    outcome = client.post(f"/api/v1/trips/{saved['id']}/route-sets?algorithm=beam").json()
    assert len(outcome["options"]) == 3
    sets = {frozenset(v["place_id"] for v in r["result"]["visits"]) for r in outcome["options"]}
    assert len(sets) == 3
    bounds = [r["result"]["objective"]["utility_bounds"] for r in outcome["options"]]
    assert all(b == bounds[0] for b in bounds)


@pytest.mark.parametrize("algorithm", ["greedy", "beam", "cp-sat"])
def test_a07_impossible_required_ticket_never_becomes_a_feasible_route(client, algorithm):
    gid, _ = group(client)
    saved = trip(client, gid, budget=0, mandatory=["museum"])
    outcome = client.post(f"/api/v1/trips/{saved['id']}/route-sets?algorithm={algorithm}").json()
    assert not outcome["options"] and not outcome["diagnostic"]["is_feasible"]
    assert outcome["diagnostic"]["reasons"]
    if algorithm == "cp-sat":
        assert outcome["diagnostic"]["solver"]["status"] == "INFEASIBLE"


def test_a08_llm_preview_does_not_create_a_trip_and_explanations_use_only_saved_facts(
    client, monkeypatch
):
    gid, _ = group(client)

    def structured(self, system, data, schema, verify=None):
        if schema is ExtractedTrip:
            draft = ExtractedTrip(
                destination="Lisbon",
                travel_date=date(2026, 11, 2),
                budget_eur=100,
                currency="EUR",
                budget_scope="group",
                participant_count=2,
                evidence=Evidence(
                    destination="Лиссабон",
                    dates="2 ноября 2026",
                    budget="100 евро на группу",
                    participants="двое",
                ),
            )
        else:
            # Include all facts required by the server-side explanation verifier.
            required = [
                k
                for k in ("summary", "budget", "limitations", "choice", "objective", "visa")
                if k in data["facts"]
            ]
            draft = ExplanationDraft(
                claims=[ExplanationClaim(fact_id=k, sentence=data["facts"][k]) for k in required]
            )
        return draft, "controlled-fixture"

    monkeypatch.setattr("app.integrations.llama_cpp.LlamaCpp.structured", structured)
    preview = client.post(
        "/api/v1/assistant/parse",
        json={"text": "Нас двое, Лиссабон 2 ноября 2026, 100 евро на группу."},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["group_budget"] == 10000
    assert client.get(f"/api/v1/groups/{gid}/trips").json() == []
    saved = trip(client, gid, budget=preview.json()["group_budget"])
    route = client.post(f"/api/v1/trips/{saved['id']}/route-sets").json()["options"][0]
    explanation = client.post(f"/api/v1/routes/{route['id']}/explanation").json()
    assert explanation["mode"] == "llm" and explanation["verified"]
    assert explanation["paragraphs"] == [
        explanation["facts"][k] for k in explanation["selected_ids"]
    ]
    assert client.get(f"/api/v1/routes/{route['id']}").json()["explanation"] == explanation


def test_a09_llm_outage_keeps_manual_planning_and_verified_template(client, monkeypatch):
    def unavailable(*args, **kwargs):
        raise LlmUnavailable("acceptance fixture")

    monkeypatch.setattr("app.integrations.llama_cpp.LlamaCpp.structured", unavailable)
    assert (
        client.post("/api/v1/assistant/parse", json={"text": "Хочу в Лиссабон"}).status_code == 503
    )
    gid, _ = group(client)
    saved = trip(client, gid)
    route = client.post(f"/api/v1/trips/{saved['id']}/route-sets").json()["options"][0]
    output = client.post(f"/api/v1/routes/{route['id']}/explanation").json()
    assert output["mode"] == "template" and output["verified"]
    assert output["fallback_reason"] == "llm_unavailable"


def test_a10_visa_profiles_and_assessment_are_frozen_with_route(client):
    gid, ids = group(client)
    path = f"/api/v1/groups/{gid}/members/{ids[0]}/travel-profile"
    profile = {
        "citizenship": "BY",
        "passport_kind": "ordinary",
        "visa_status": "not_held",
        "special_entry_basis": False,
    }
    assert client.put(path, json=profile).status_code == 200
    saved = trip(client, gid)
    client.put(path, json={"citizenship": "DE", "passport_kind": "ordinary"})
    outcome = client.post(f"/api/v1/trips/{saved['id']}/route-sets").json()
    report = outcome["visa_report"]
    assert report["participants"][0]["citizenship_name"] == "Беларусь"
    assert report["participants"][0]["sources"] and report["checked_on"] and report["review_by"]
    assert report["participants"][0]["timing"]
    assert client.get(f"/api/v1/route-sets/{outcome['id']}").json()["visa_report"] == report


def test_a11_seventh_member_and_unsupported_destination_are_rejected(client):
    gid, _ = group(client, 6)
    uid = client.post("/api/v1/users", json={"name": "Seventh"}).json()["id"]
    assert client.post(f"/api/v1/groups/{gid}/members", json={"user_id": uid}).status_code == 409
    assert (
        client.post(
            f"/api/v1/groups/{gid}/trips", json={"destination": "Unknown", "budget": 100}
        ).status_code
        == 422
    )
