from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.integrations.llama_cpp import LlmUnavailable
from app.main import app
from app.nlp.explanations import explain
from app.nlp.parser import parse_request
from app.schemas.assistant import (
    Evidence,
    ExplanationClaim,
    ExplanationDraft,
    ExtractedTrip,
    ParseInput,
)


class FakeModel:
    def __init__(self, draft):
        self.draft = draft

    def structured(self, system, data, schema, verify=None):
        return self.draft.model_copy(deep=True), "test-qwen"


def test_extracts_group_budget_per_person_and_inclusive_period():
    text = (
        "Анна и Максим едут в Лиссабон 23 октября 2026 на 3 дня. "
        "По 50 евро на человека. Любим историю. С 9 до 18."
    )
    draft = ExtractedTrip(
        destination="Lisbon",
        travel_date=date(2026, 10, 23),
        duration_days=3,
        budget_eur=50,
        currency="EUR",
        budget_scope="per_person",
        participant_count=2,
        common_interests={"history": 1},
        start=540,
        end=1080,
        evidence=Evidence(
            destination="Лиссабон",
            dates="23 октября 2026 на 3 дня",
            budget="По 50 евро на человека",
            participants="Анна и Максим",
            interests="Любим историю",
            hours="С 9 до 18",
        ),
    )
    out = parse_request(
        ParseInput(text=text, member_names=["Анна", "Максим"]), FakeModel(draft), date(2026, 10, 23)
    )
    assert out.group_budget == 10000
    assert out.draft.end_date == date(2026, 10, 25)
    assert not out.questions
    assert out.draft.common_interests == {"history": 1}


def test_missing_evidence_does_not_fill_invented_values():
    draft = ExtractedTrip(
        destination="Lisbon",
        travel_date=date(2026, 10, 23),
        budget_eur=100,
        currency="EUR",
        budget_scope="group",
    )
    out = parse_request(ParseInput(text="Хочу в отпуск"), FakeModel(draft))
    assert out.group_budget is None
    assert out.draft.destination is None and out.draft.travel_date is None
    assert {q.field for q in out.questions} >= {
        "destination",
        "travel_date",
        "budget",
        "participants",
    }


def test_ambiguous_scope_currency_and_conflicting_places_require_clarification():
    text = "Лиссабон 23 октября 2026, 100 долларов. Обязательно Carmo, исключить Carmo."
    draft = ExtractedTrip(
        destination="Lisbon",
        travel_date=date(2026, 10, 23),
        budget_eur=100,
        currency="other",
        mandatory_names=["Carmo"],
        excluded_names=["Carmo"],
        evidence=Evidence(destination="Лиссабон", dates="23 октября 2026", budget="100 долларов"),
    )
    out = parse_request(ParseInput(text=text, member_names=["Анна"]), FakeModel(draft))
    assert out.group_budget is None
    assert {q.field for q in out.questions} >= {"currency", "budget_scope", "places"}


def test_invented_participant_and_place_names_are_not_silently_applied():
    text = "Мы вдвоём хотим в отпуск"
    draft = ExtractedTrip(
        participants=[{"name": "Выдуманное имя"}],
        mandatory_names=["Выдуманный музей"],
        evidence=Evidence(participants="Мы вдвоём"),
    )
    out = parse_request(ParseInput(text=text), FakeModel(draft))
    assert out.draft.participants == [] and out.draft.mandatory_names == []
    assert {q.field for q in out.questions} >= {"participants", "mandatory_names"}


def test_model_cannot_assume_budget_scope_or_convert_currency_without_text_evidence():
    draft = ExtractedTrip(
        budget_eur=100,
        currency="EUR",
        budget_scope="group",
        evidence=Evidence(budget="Бюджет 100 долларов"),
    )
    out = parse_request(
        ParseInput(text="Бюджет 100 долларов", member_names=["Анна"]), FakeModel(draft)
    )
    assert out.group_budget is None
    assert {q.field for q in out.questions} >= {"currency", "budget_scope"}


def test_period_hours_and_budget_precision_are_checked():
    text = "Лиссабон, 23 октября 2026 на 20 дней, общий бюджет 1.001 евро, с 18 до 9"
    draft = ExtractedTrip(
        destination="Lisbon",
        travel_date=date(2026, 10, 23),
        duration_days=20,
        budget_eur=1.001,
        currency="EUR",
        budget_scope="group",
        start=1080,
        end=540,
        evidence=Evidence(
            destination="Лиссабон",
            dates="23 октября 2026 на 20 дней",
            budget="общий бюджет 1.001 евро",
            hours="с 18 до 9",
        ),
    )
    out = parse_request(ParseInput(text=text, member_names=["Анна"]), FakeModel(draft))
    assert {q.field for q in out.questions} >= {"end_date", "duration_days", "hours", "budget"}
    assert out.group_budget is None


def fixture_route():
    route = SimpleNamespace(
        id=1,
        route_set_id=1,
        comparison={"participant_utility": {"1": 1}, "utility_gap": 0},
        result={"visits": [{"place_id": "museum"}], "total_cost": 1500, "data_context": None},
    )
    trip = SimpleNamespace(
        budget=5000,
        participants_snapshot=[{"user_id": 1, "name": "Анна"}],
        travel_date=date(2026, 10, 23),
        end_date=None,
        mandatory=["museum"],
        excluded=[],
    )
    return route, trip


class NarrativeModel:
    def __init__(self, fault=None):
        self.fault = fault

    def structured(self, system, data, schema, verify=None):
        claims = [
            ExplanationClaim(fact_id=k, sentence=data["facts"][k])
            for k in ("summary", "budget", "limitations", "interests", "choice")
        ]
        if self.fault == "fabrication":
            claims[0].sentence += " Виза гарантирована."
        elif self.fault == "unknown":
            claims[0].fact_id = "unknown"
        elif self.fault == "missing":
            claims[1] = claims[0].model_copy()
        return ExplanationDraft(claims=claims), "test-qwen"


def test_grounded_explanation_contains_only_verified_saved_facts():
    route, trip = fixture_route()
    output = explain(route, trip, route, 3, NarrativeModel())
    assert output.mode == "llm" and output.verified
    assert output.paragraphs == [output.facts[key] for key in output.selected_ids]
    assert "15,00 EUR" in output.facts["budget"]
    assert "35,00 EUR" in output.facts["budget"]
    assert route.result["total_cost"] == 1500


@pytest.mark.parametrize("fault", ["fabrication", "unknown", "missing"])
def test_changed_claims_are_replaced_by_verified_template(fault):
    route, trip = fixture_route()
    output = explain(route, trip, route, 3, NarrativeModel(fault))
    assert output.mode == "template" and output.fallback_reason == "unverified_output"
    assert not any("Виза гарантирована" in text for text in output.paragraphs)


def test_unavailable_model_uses_template_and_parse_api_keeps_manual_planner_available(monkeypatch):
    def fail(*args, **kwargs):
        raise LlmUnavailable("offline")

    monkeypatch.setattr("app.integrations.llama_cpp.LlamaCpp.structured", fail)
    route, trip = fixture_route()
    assert explain(route, trip).mode == "template"
    with TestClient(app) as client:
        assert (
            client.post("/api/v1/assistant/parse", json={"text": "Хочу в отпуск"}).status_code
            == 503
        )
        assert client.post("/api/v1/assistant/parse", json={"text": " "}).status_code == 422
        assert (
            client.post(
                "/api/v1/plans/demo",
                json={"participants": [{"id": "a", "interests": {}}], "budget": 10000},
            ).status_code
            == 200
        )


def test_unsupported_categories_and_extra_data_fail_schema_validation():
    with pytest.raises(ValidationError):
        ExtractedTrip(common_interests={"invented": 1})
    with pytest.raises(ValidationError):
        ExtractedTrip(price_for_museum=1)


@pytest.mark.parametrize("fault", [None, "modified", "omitted"])
def test_rag_requires_exact_saved_description_and_server_attaches_citation(fault):
    route, trip = fixture_route()
    route.result["data_context"] = {
        "retrieval": {
            "document_threshold": 0.4,
            "matches": [
                {
                    "place_id": "museum",
                    "name": "Музей",
                    "description": "Археологическая коллекция.",
                    "description_kind": "curated",
                    "score": 0.8,
                    "source_url": "https://example.org/museum",
                    "checked_on": "2026-10-23",
                },
                {
                    "place_id": "park",
                    "name": "Парк",
                    "description": "Не посещается.",
                    "description_kind": "curated",
                    "score": 0.99,
                    "source_url": "https://example.org/park",
                },
            ],
        }
    }

    class Model:
        def structured(self, system, data, schema, verify=None):
            keys = ["summary", "budget", "limitations", "choice", "source_1"]
            if fault == "omitted":
                keys.remove("source_1")
            draft = ExplanationDraft(
                claims=[ExplanationClaim(fact_id=key, sentence=data["facts"][key]) for key in keys]
            )
            if fault == "modified":
                draft.claims[-1].sentence += " Билет бесплатный."
            return draft, "test-model"

    output = explain(route, trip, route, 3, Model())
    assert output.mode == ("llm" if fault is None else "template")
    assert output.sources["source_1"]["place_id"] == "museum"
    assert output.facts["source_1"] == "Музей: Археологическая коллекция."
    assert "source_2" not in output.facts
    assert not any("Билет бесплатный" in text for text in output.paragraphs)
