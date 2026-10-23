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
    assert saved["data_source"] == "osm-osrm-lisbon-v2"
    assert saved["result"]["data_context"] == context
    assert client.get(f"/api/v1/routes/{saved['id']}").json() == saved

    def fail(*args):
        raise SourceUnavailable("OSRM unavailable")

    monkeypatch.setattr("app.api.groups.CityProvider.dataset", fail)
    assert client.post(f"/api/v1/trips/{trip['id']}/routes").status_code == 503
    assert len(client.get(f"/api/v1/trips/{trip['id']}/routes").json()) == 1


def test_multiday_trip_is_persisted_without_repeats(client):
    gid, _ = create_group(client)
    created = client.post(
        f"/api/v1/groups/{gid}/trips",
        json={
            "travel_date": "2026-10-23",
            "end_date": "2026-10-25",
            "budget": 10000,
            "start": 600,
            "end": 720,
            "mandatory": ["museum", "market", "park"],
        },
    )
    assert created.status_code == 201
    trip = created.json()
    response = client.post(f"/api/v1/trips/{trip['id']}/routes?algorithm=beam")
    assert response.status_code == 201
    saved = response.json()
    result = saved["result"]
    assert result["is_feasible"]
    assert len(result["days"]) == 3
    assert [d["travel_date"] for d in result["days"]] == ["2026-10-23", "2026-10-24", "2026-10-25"]
    assert result["total_cost"] == sum(d["total_cost"] for d in result["days"]) == 5400
    assert len({v["place_id"] for v in result["visits"]}) == len(result["visits"]) == 3
    assert client.get(f"/api/v1/routes/{saved['id']}").json() == saved
    assert client.get(f"/api/v1/trips/{trip['id']}").json()["end_date"] == "2026-10-25"


def test_real_period_uses_date_specific_data_and_saves_empty_day(client, monkeypatch):
    from app.planning.models import Place

    gid, _ = create_group(client)
    trip = client.post(
        f"/api/v1/groups/{gid}/trips",
        json={
            "destination": "Lisbon",
            "travel_date": "2026-10-25",
            "end_date": "2026-10-26",
            "budget": 10000,
            "mandatory": ["osm:node:1"],
        },
    ).json()
    calls = []

    def dataset(self, day, mandatory, allow_empty=False):
        calls.append(str(day))
        assert allow_empty
        context = {"pois": [{"id": "osm:node:1", "name": "Date-specific museum"}]}
        if day.day == 25:
            return [], {}, context
        return (
            [Place("osm:node:1", "history", 0, 60, 600, 1080)],
            {
                ("start", "osm:node:1"): 10,
            },
            context,
        )

    monkeypatch.setattr("app.api.groups.CityProvider.dataset", dataset)
    response = client.post(f"/api/v1/trips/{trip['id']}/routes?algorithm=beam")
    assert response.status_code == 201
    result = response.json()["result"]
    assert result["is_feasible"]
    assert calls == ["2026-10-25", "2026-10-26"]
    assert not result["days"][0]["visits"]
    assert result["days"][1]["visits"][0]["place_id"] == "osm:node:1"
    assert result["days"][1]["data_context"]["pois"][0]["name"] == "Date-specific museum"


def test_invalid_trip_period_returns_422(client):
    gid, _ = create_group(client)
    for payload in (
        {"end_date": "2026-10-25"},
        {"travel_date": "2026-10-25", "end_date": "2026-10-23"},
        {"travel_date": "2026-10-01", "end_date": "2026-10-15"},
    ):
        assert (
            client.post(
                f"/api/v1/groups/{gid}/trips", json={"budget": 10000, **payload}
            ).status_code
            == 422
        )


def test_route_comparison_is_saved_together_and_reopened_without_recalculation(client):
    gid, ids = create_group(client)
    for uid, category in zip(ids, ["history", "nature"], strict=True):
        client.put(
            f"/api/v1/groups/{gid}/members/{uid}/preferences",
            json={"interests": {category: 1}},
        )
    trip = client.post(f"/api/v1/groups/{gid}/trips", json={"budget": 10000}).json()
    # Later preference edits must not change comparison metrics for this trip.
    client.put(
        f"/api/v1/groups/{gid}/members/{ids[0]}/preferences", json={"interests": {"food": 1}}
    )
    response = client.post(f"/api/v1/trips/{trip['id']}/route-sets?beam_width=8")
    assert response.status_code == 201
    saved = response.json()
    assert len(saved["options"]) == 3
    assert saved["diagnostic"] is None
    assert saved["algorithm"] == "diverse-v1:beam:w8"
    assert saved["options"][0]["comparison"]["participant_utility"] == {str(uid): 1 for uid in ids}
    for option in saved["options"]:
        assert option["route_set_id"] == saved["id"]
        assert option["result"]["is_feasible"] and option["result"]["validation"]["is_valid"]
        assert client.get(f"/api/v1/routes/{option['id']}").json() == option
    assert client.get(f"/api/v1/route-sets/{saved['id']}").json() == saved
    assert client.get(f"/api/v1/trips/{trip['id']}/route-sets").json() == [saved]
    assert client.get(f"/api/v1/trips/{trip['id']}/routes").json() == saved["options"]
    second = client.post(f"/api/v1/trips/{trip['id']}/route-sets?beam_width=8").json()
    assert second["id"] != saved["id"]
    assert len(client.get(f"/api/v1/trips/{trip['id']}/route-sets").json()) == 2
    assert client.get(f"/api/v1/route-sets/{saved['id']}").json() == saved


def test_multiday_comparison_with_only_one_distinct_option(client):
    gid, _ = create_group(client)
    trip = client.post(
        f"/api/v1/groups/{gid}/trips",
        json={
            "travel_date": "2026-10-23",
            "end_date": "2026-10-25",
            "budget": 10000,
            "start": 600,
            "end": 720,
            "mandatory": ["museum", "market", "park"],
        },
    ).json()
    response = client.post(f"/api/v1/trips/{trip['id']}/route-sets")
    assert response.status_code == 201
    saved = response.json()
    assert len(saved["options"]) == 1
    assert len(saved["options"][0]["result"]["days"]) == 3
    assert saved["search_info"]["note_codes"] == ["fewer_distinct_options_found"]
    assert client.get(f"/api/v1/route-sets/{saved['id']}").json() == saved


def test_empty_comparison_preserves_diagnostic_without_claiming_invalid_alternatives(client):
    gid, _ = create_group(client)
    trip = client.post(
        f"/api/v1/groups/{gid}/trips",
        json={"budget": 0, "mandatory": ["museum"]},
    ).json()
    response = client.post(f"/api/v1/trips/{trip['id']}/route-sets")
    assert response.status_code == 201
    saved = response.json()
    assert not saved["options"]
    assert not saved["diagnostic"]["is_feasible"]
    assert "mandatory_not_scheduled" in saved["diagnostic"]["reasons"]
    assert not client.get(f"/api/v1/trips/{trip['id']}/routes").json()
    assert client.get(f"/api/v1/route-sets/{saved['id']}").json() == saved


def test_comparison_uses_one_source_snapshot_per_day_and_keeps_sets_on_source_failure(
    client, monkeypatch
):
    from app.integrations.city import SourceUnavailable
    from app.integrations.demo import load_demo

    gid, _ = create_group(client)
    trip = client.post(
        f"/api/v1/groups/{gid}/trips",
        json={
            "destination": "Lisbon",
            "travel_date": "2026-10-23",
            "end_date": "2026-10-24",
            "budget": 10000,
        },
    ).json()
    calls = []

    def dataset(self, day, mandatory, allow_empty=False):
        calls.append(str(day))
        places, travel = load_demo()
        return (
            places,
            travel,
            {"pois": [{"id": p.id, "name": p.id} for p in places], "day": str(day)},
        )

    monkeypatch.setattr("app.api.groups.CityProvider.dataset", dataset)
    saved = client.post(f"/api/v1/trips/{trip['id']}/route-sets").json()
    assert calls == ["2026-10-23", "2026-10-24"]
    assert len(saved["options"]) == 3
    for option in saved["options"]:
        assert option["data_source"] == "osm-osrm-lisbon-v2"
        assert [d["data_context"]["day"] for d in option["result"]["days"]] == calls

    def fail(*args, **kwargs):
        raise SourceUnavailable("OSRM unavailable")

    monkeypatch.setattr("app.api.groups.CityProvider.dataset", fail)
    assert client.post(f"/api/v1/trips/{trip['id']}/route-sets").status_code == 503
    assert client.get(f"/api/v1/trips/{trip['id']}/route-sets").json() == [saved]
    assert client.get("/api/v1/route-sets/2147483647").status_code == 404
    assert client.post(f"/api/v1/trips/{trip['id']}/route-sets?beam_width=129").status_code == 422


def test_verified_explanation_is_saved_with_option_and_cached(client, monkeypatch):
    from app.schemas.assistant import ExplanationClaim, ExplanationDraft

    gid, _ = create_group(client)
    trip = client.post(f"/api/v1/groups/{gid}/trips", json={"budget": 10000}).json()
    saved = client.post(f"/api/v1/trips/{trip['id']}/route-sets").json()
    option = saved["options"][1]
    calls = []

    def structured(self, system, data, schema, verify=None):
        calls.append(data)
        draft = ExplanationDraft(
            claims=[
                ExplanationClaim(fact_id=key, sentence=data["facts"][key])
                for key in ("summary", "budget", "choice", "limitations")
            ]
        )
        verify(draft)
        return draft, "test-qwen"

    monkeypatch.setattr("app.integrations.llama_cpp.LlamaCpp.structured", structured)
    response = client.post(f"/api/v1/routes/{option['id']}/explanation")
    assert response.status_code == 200
    explanation = response.json()
    assert explanation["mode"] == "llm" and explanation["verified"]
    assert client.post(f"/api/v1/routes/{option['id']}/explanation").json() == explanation
    assert len(calls) == 1
    reopened = client.get(f"/api/v1/routes/{option['id']}").json()
    assert reopened["explanation"] == explanation
    assert reopened["result"] == option["result"]
    set_again = client.get(f"/api/v1/route-sets/{saved['id']}").json()
    assert set_again["options"][1]["explanation"] == explanation
    assert set_again["options"][0]["explanation"] is None


def test_unavailable_llm_saves_fallback_but_invalid_route_cannot_get_explanation(
    client, monkeypatch
):
    from app.integrations.llama_cpp import LlmUnavailable

    def fail(*args, **kwargs):
        raise LlmUnavailable("offline")

    monkeypatch.setattr("app.integrations.llama_cpp.LlamaCpp.structured", fail)
    gid, _ = create_group(client)
    trip = client.post(f"/api/v1/groups/{gid}/trips", json={"budget": 10000}).json()
    route = client.post(f"/api/v1/trips/{trip['id']}/routes").json()
    response = client.post(f"/api/v1/routes/{route['id']}/explanation")
    assert response.status_code == 200
    assert response.json()["mode"] == "template"
    assert response.json()["fallback_reason"] == "llm_unavailable"
    assert client.get(f"/api/v1/routes/{route['id']}").json()["explanation"] == response.json()
    invalid = client.post(
        f"/api/v1/groups/{gid}/trips", json={"budget": 0, "mandatory": ["museum"]}
    ).json()
    route = client.post(f"/api/v1/trips/{invalid['id']}/routes").json()
    assert client.post(f"/api/v1/routes/{route['id']}/explanation").status_code == 409


def test_semantic_snapshots_and_rag_citations_are_saved_without_changing_route(client, monkeypatch):
    from dataclasses import asdict

    from app.integrations.llama_cpp import LlmUnavailable
    from app.planning.models import Place

    place = Place("carmo", "history", 100, 60, 600, 1080)
    document = {
        "place_id": "carmo",
        "name": "Carmo",
        "description": "Сохранённое описание.",
        "source_url": "https://www.museuarqueologicodocarmo.pt/index.html",
        "checked_on": "2026-10-23",
        "description_kind": "curated",
        "score": 0.8,
    }

    def dataset(self, day, mandatory, allow_empty=False, semantic_query=None, excluded=frozenset()):
        assert semantic_query == "археология"
        return (
            [place],
            {("start", "carmo"): 1},
            {
                "pois": [{"id": "carmo", "name": "Carmo"}],
                "places_snapshot": [asdict(place)],
                "retrieval": {
                    "query": semantic_query,
                    "model": "test-model",
                    "knowledge_version": "v1",
                    "document_threshold": 0.4,
                    "matches": [document, {**document, "place_id": "unvisited", "score": 0.99}],
                },
            },
        )

    monkeypatch.setattr("app.api.groups.CityProvider.dataset", dataset)
    gid, _ = create_group(client, 1)
    trip = client.post(
        f"/api/v1/groups/{gid}/trips",
        json={
            "destination": "Lisbon",
            "travel_date": "2026-10-23",
            "budget": 100,
            "mandatory": ["carmo"],
            "semantic_query": "археология",
        },
    ).json()
    assert trip["semantic_query"] == "археология"
    route = client.post(f"/api/v1/trips/{trip['id']}/routes").json()
    assert route["result"]["validation"]["is_valid"]
    # Future source/model changes do not rewrite the snapshot or trigger embeddings on read.
    document["description"] = "Новое описание"
    assert client.get(f"/api/v1/routes/{route['id']}").json() == route

    def fail(*args, **kwargs):
        raise LlmUnavailable()

    monkeypatch.setattr("app.integrations.llama_cpp.LlamaCpp.structured", fail)
    explanation = client.post(f"/api/v1/routes/{route['id']}/explanation").json()
    assert explanation["mode"] == "template" and explanation["verified"]
    assert list(explanation["sources"]) == ["source_1"]
    assert explanation["sources"]["source_1"]["place_id"] == "carmo"
    assert explanation["facts"]["source_1"] == "Carmo: Сохранённое описание."
    reopened = client.get(f"/api/v1/routes/{route['id']}").json()
    assert reopened["result"] == route["result"] and reopened["explanation"] == explanation


def test_semantic_model_outage_is_explicit_but_manual_planning_remains_available(
    client, monkeypatch
):
    from app.integrations.embeddings import EmbeddingUnavailable

    def fail(*args, **kwargs):
        raise EmbeddingUnavailable()

    monkeypatch.setattr("app.api.groups.CityProvider.dataset", fail)
    gid, _ = create_group(client, 1)
    trip = client.post(
        f"/api/v1/groups/{gid}/trips",
        json={
            "destination": "Lisbon",
            "travel_date": "2026-10-23",
            "budget": 100,
            "semantic_query": "археология",
        },
    ).json()
    assert client.post(f"/api/v1/trips/{trip['id']}/routes").status_code == 503
    manual = client.post(f"/api/v1/groups/{gid}/trips", json={"budget": 10000}).json()
    assert client.post(f"/api/v1/trips/{manual['id']}/routes").status_code == 201
    assert (
        client.post(
            f"/api/v1/groups/{gid}/trips",
            json={"budget": 100, "semantic_query": "invalid for demo"},
        ).status_code
        == 422
    )
