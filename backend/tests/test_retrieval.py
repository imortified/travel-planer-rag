from datetime import date

import httpx
import pytest
from cache_helpers import MemoryRedis
from fastapi.testclient import TestClient
from test_embeddings import vector

from app.integrations.city import CityProvider
from app.integrations.embeddings import EmbeddingUnavailable, normalize
from app.integrations.http_cache import RedisHttpCache
from app.integrations.qdrant import VectorStoreUnavailable
from app.main import app
from app.nlp.retrieval import Retrieval
from app.planning.models import Participant, Request
from app.planning.service import checked_plan


class FakeEmbeddings:
    identity = "test-1024-v1"

    def __init__(self):
        self.document_calls, self.query_calls = 0, 0

    def encode(self, texts, query=False):
        if query:
            self.query_calls += 1
            if texts[0] == "ambiguous":
                return [normalize([a + b for a, b in zip(vector(), vector(1), strict=True)])]
            return [vector(4 if texts[0] == "unknown" else 0)]
        self.document_calls += 1
        return [
            vector(
                next(
                    (
                        i
                        for i, prefix in enumerate(["История:", "Природа:", "Еда:", "Искусство:"])
                        if text.startswith(prefix)
                    ),
                    0,
                )
            )
            for text in texts
        ]


@pytest.mark.parametrize(
    "query,category",
    [
        ("archaeology", "history"),
        ("ambiguous", None),
        ("unknown", None),
        ("не люблю музеи", None),
        ("without museums", None),
    ],
)
def test_threshold_margin_and_negation_require_clarification(query, category):
    result = Retrieval(FakeEmbeddings(), MemoryStore()).search(query, [])
    assert result["category"] == category
    assert result["needs_clarification"] == (category is None)


class MemoryStore:
    """Unit-test double only; production retrieval always requires Qdrant."""

    def collection(self, kind):
        return f"test_{kind}"

    def search(self, kind, records, embedder, query):
        return [
            sum(x * y for x, y in zip(query, vector, strict=True))
            for vector in embedder.encode([r["text"] for r in records])
        ]


def test_unenriched_place_does_not_inherit_detailed_category_claims():
    index = Retrieval(FakeEmbeddings(), MemoryStore())
    document = index.documents(
        [
            {
                "id": "unknown",
                "name": "Museum",
                "category": "history",
                "source_url": "https://example.org/source",
            }
        ]
    )[0]
    assert "археология" not in document["description"]
    assert document["description_kind"] == "catalog_category"
    assert document["checked_on"] is None


def test_ranked_candidate_cap_keeps_mandatory_excludes_blocked_and_preserves_validator(
    tmp_path, monkeypatch
):
    pois = [
        {
            "id": str(i),
            "name": str(i),
            "eligible": i != 15,
            "category": "history",
            "latitude": 38.71,
            "longitude": -9.14,
            "cost_per_person": 100,
            "duration": 60,
            "window": [600, 1080],
        }
        for i in range(16)
    ]
    monkeypatch.setattr(
        CityProvider,
        "catalog",
        lambda self, day: {
            "pois": pois,
            "origin": {"id": "start", "longitude": -9.1395, "latitude": 38.7135},
        },
    )

    def search(self, query, candidates):
        assert "15" not in {p["id"] for p in candidates}
        assert "14" not in {p["id"] for p in candidates}
        return {
            "query": query,
            "matches": [{"place_id": p["id"], "score": int(p["id"]) / 20} for p in candidates],
        }

    monkeypatch.setattr(Retrieval, "search", search)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"code": "Ok", "durations": [[0] * 15 for _ in range(15)]}
            )
        )
    ) as client:
        places, travel, context = CityProvider(client, RedisHttpCache(MemoryRedis())).dataset(
            date(2026, 10, 23), frozenset({"0"}), semantic_query="history", excluded={"14"}
        )
    ids = [p.id for p in places]
    assert ids[0] == "0" and ids[1] == "13" and len(ids) == 14
    assert "1" in ids and "14" not in ids and "15" not in ids
    request = Request(
        [Participant("a", {"history": 1})], 100, 540, 1080, frozenset({"0"}), frozenset({"14"})
    )
    result = checked_plan(request, places, travel, "beam")
    assert result.is_feasible and result.validation.is_valid
    assert result.total_cost == 100 and result.visits[0].place_id == "0"
    assert context["retrieval"]["candidate_policy"] == "category-rotation-v1"


@pytest.mark.parametrize("failure", [EmbeddingUnavailable, VectorStoreUnavailable])
def test_search_api_uses_period_eligibility_and_returns_explicit_unavailability(
    monkeypatch, failure
):
    monkeypatch.setattr(
        "app.api.assistant.lisbon_pois",
        lambda first, last: {
            "pois": [{"id": "open", "eligible": True}, {"id": "closed", "eligible": False}]
        },
    )

    def search(self, text, pois):
        assert [p["id"] for p in pois] == ["open"]
        return {"matches": []}

    monkeypatch.setattr(Retrieval, "search", search)
    payload = {"text": "history", "travel_date": "2026-10-23", "end_date": "2026-10-25"}
    with TestClient(app) as client:
        response = client.post("/api/v1/assistant/search", json=payload)
        assert response.status_code == 200 and response.json()["end_date"] == "2026-10-25"
        monkeypatch.setattr(Retrieval, "search", lambda *args: (_ for _ in ()).throw(failure()))
        assert client.post("/api/v1/assistant/search", json=payload).status_code == 503
        assert (
            client.post("/api/v1/assistant/search", json={**payload, "text": " "}).status_code
            == 422
        )
