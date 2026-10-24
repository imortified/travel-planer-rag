import json
from datetime import date
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from test_retrieval import FakeEmbeddings, MemoryStore

from app.core.config import ROOT
from app.integrations.city import CityProvider, IncompleteData
from app.main import app
from app.nlp.explanations import retrieved_sources
from app.nlp.retrieval import Retrieval
from app.nlp.retrieval_policy import assess

CORPUS = json.loads((ROOT / "data/evaluation/rag-queries.json").read_text("utf-8"))
BASELINE = json.loads((ROOT / "docs/rag-retrieval-evaluation.json").read_text("utf-8"))
SNAPSHOT = json.loads((ROOT / "data/evaluation/rag-catalog.json").read_text("utf-8"))


@pytest.mark.parametrize(
    "case,row",
    list(zip(CORPUS["cases"], BASELINE["rows"], strict=True)),
    ids=[c["id"] for c in CORPUS["cases"]],
)
def test_recorded_scores_never_promote_an_incorrect_category(case, row):
    output = row["output"]
    result = assess(
        case["query"],
        sorted(output["category_scores"].items(), key=lambda pair: -pair[1]),
        output["matches"],
        {p["id"]: p["category"] for p in SNAPSHOT["pois"]},
        0.55,
        0.05,
        0.4,
    )
    assert result["category"] is None or result["category"] == case["expected_category"]
    if not case["relevance"]:
        assert not result["can_apply"]


def decide(text, category="history", score=0.7):
    return assess(
        text,
        [("history", 0.41), ("art", 0.3), ("nature", 0.2), ("food", 0.1)],
        [{"place_id": "p", "score": score}],
        {"p": category},
        0.55,
        0.05,
        0.4,
    )


@pytest.mark.parametrize(
    "text,category",
    [
        ("Археологическая коллекция", "history"),
        ("Хочу португальскую живопись", "art"),
        ("Ищу историческую кондитерскую", "food"),
        ("Прогулка по ботаническому саду", "nature"),
    ],
)
def test_explicit_single_topic_requires_a_matching_candidate(text, category):
    result = decide(text, category)
    assert result["category"] == category and result["can_apply"]
    assert result["category_source"] == "explicit_topic_and_place"
    assert decide(text, category, 0.39)["category"] is None
    other = "art" if category != "art" else "nature"
    assert decide(text, other)["category"] is None


@pytest.mark.parametrize(
    "text,reason",
    [
        ("Не хочу живопись", "negative_or_complex"),
        ("История неинтересна", "negative_or_complex"),
        ("Без галерей, но с природой", "negative_or_complex"),
        ("Люблю историю и искусство", "multiple_interests"),
        ("Веганская кухня", "unsupported_condition"),
        ("Кафе без орехов", "unsupported_condition"),
        ("Кухня с подтверждёнными аллергенами", "unsupported_condition"),
        ("Кафе без следов орехов", "unsupported_condition"),
        ("Нужны парки, доступные на велосипеде", "unsupported_condition"),
        ("Какая погода в парке?", "unsupported_condition"),
    ],
)
def test_constraints_are_not_confirmed_by_even_perfect_similarity(text, reason):
    result = decide(text, score=1)
    assert result["reason"] == reason
    assert not result["can_apply"] and result["category"] is None


def test_generic_museum_or_no_evidence_does_not_invent_history():
    result = decide("Хочу посетить музей")
    assert result["category"] is None and result["reason"] == "uncertain"
    assert decide("Нечто неизвестное", score=0.1)["reason"] == "no_evidence"


def test_recorded_development_mistakes_are_blocked_without_changing_gold():
    corpus = json.loads((ROOT / "data/evaluation/rag-queries.json").read_text("utf-8"))
    report = json.loads((ROOT / "docs/rag-retrieval-evaluation.json").read_text("utf-8"))
    snapshot = json.loads((ROOT / "data/evaluation/rag-catalog.json").read_text("utf-8"))
    cats = {p["id"]: p["category"] for p in snapshot["pois"]}
    for case, row in zip(corpus["cases"], report["rows"], strict=True):
        if case["id"] not in {"rag-36", "rag-37"}:
            continue
        out = row["output"]
        decision = assess(
            case["query"],
            sorted(out["category_scores"].items(), key=lambda pair: -pair[1]),
            out["matches"],
            cats,
            0.55,
            0.05,
            0.4,
        )
        assert not decision["can_apply"] and decision["category"] is None


def test_blocked_search_api_never_returns_ranked_places(monkeypatch):
    pois = [
        {"id": "p", "name": "Museum", "category": "history", "source_url": "https://example.org"}
    ]
    search = Retrieval(FakeEmbeddings(), MemoryStore()).search
    monkeypatch.setattr(
        Retrieval, "search", lambda self, text, candidates: search(text, candidates)
    )
    monkeypatch.setattr(
        "app.api.assistant.lisbon_pois",
        lambda *args: {"pois": [{**p, "eligible": True} for p in pois]},
    )
    with TestClient(app) as client:
        r = client.post(
            "/api/v1/assistant/search",
            json={"text": "Только веганская кухня", "travel_date": "2026-10-27"},
        )
    assert r.status_code == 200
    assert r.json()["matches"] == [] and not r.json()["can_apply"]
    assert r.json()["warnings"] and r.json()["message"]


def test_direct_planning_cannot_bypass_search_policy_or_call_routing(monkeypatch):
    poi = {
        "id": "p",
        "name": "Museum",
        "category": "history",
        "source_url": "https://example.org",
        "eligible": True,
    }
    search = Retrieval(FakeEmbeddings(), MemoryStore()).search
    monkeypatch.setattr(
        Retrieval, "search", lambda self, text, candidates: search(text, candidates)
    )
    monkeypatch.setattr(CityProvider, "catalog", lambda *args: {"pois": [poi]})
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("No routing request allowed"))
    ) as client:
        with pytest.raises(IncompleteData, match="не подтверждает"):
            CityProvider(client).dataset(date(2026, 10, 27), semantic_query="Веганская кухня")


def test_blocked_snapshot_cannot_supply_explanation_sources():
    route = SimpleNamespace(
        result={
            "visits": [{"place_id": "p"}],
            "data_context": {
                "retrieval": {
                    "can_apply": False,
                    "matches": [{"place_id": "p", "score": 1, "description_kind": "curated"}],
                }
            },
        }
    )
    assert retrieved_sources(route) == {}
