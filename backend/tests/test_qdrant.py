import json
import math

import httpx
import pytest
from test_embeddings import vector

from app.integrations.qdrant import QdrantStore, VectorStoreUnavailable, point_for

DOCUMENT = {"key": "Lisbon:one", "text": "A museum", "metadata": {"city": "Lisbon"}}


class UnusedEmbedder:
    identity = "test-model"

    def encode(self, texts):
        raise AssertionError("Unchanged documents must not be re-embedded")


def test_fingerprint_separates_text_model_and_logical_id_but_not_metadata():
    point = point_for(DOCUMENT, "model-a")
    assert point["id"] != point_for({**DOCUMENT, "text": "changed"}, "model-a")["id"]
    assert point["id"] != point_for(DOCUMENT, "model-b")["id"]
    assert point["id"] != point_for({**DOCUMENT, "key": "Porto:one"}, "model-a")["id"]
    changed = point_for({**DOCUMENT, "metadata": {"city": "Lisbon", "source": "new"}}, "model-a")
    assert point["id"] == changed["id"]
    assert point["payload"]["payload_sha256"] != changed["payload"]["payload_sha256"]


@pytest.mark.parametrize("mode", ["missing", "foreign", "duplicate", "nan", "bool", "large"])
def test_invalid_query_response_cannot_be_used_for_retrieval(mode):
    expected = point_for(DOCUMENT, "test-model")
    rows = [{"id": expected["id"], "score": 1.0}]
    if mode == "missing":
        rows = []
    elif mode == "foreign":
        rows[0]["id"] = "foreign"
    elif mode == "duplicate":
        rows *= 2
    else:
        rows[0]["score"] = {"nan": math.nan, "bool": True, "large": 2.0}[mode]

    def handler(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "result": {
                        "config": {"params": {"vectors": {"size": 1024, "distance": "Cosine"}}},
                        "payload_schema": {"embedding_identity": {"data_type": "keyword"}},
                    }
                },
            )
        if request.url.path.endswith("/points/query"):
            body = json.loads(request.content)
            assert body["filter"]["must"][0] == {"has_id": [expected["id"]]}
            assert body["params"] == {"exact": True}
            assert body["with_vector"] is False
            return httpx.Response(200, json={"result": {"points": rows}})
        return httpx.Response(200, json={"result": [expected]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(VectorStoreUnavailable):
            QdrantStore(client).search("places", [DOCUMENT], UnusedEmbedder(), vector())


@pytest.mark.parametrize("status", [401, 503])
def test_service_errors_are_explicit_and_never_use_file_fallback(status):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status))) as client:
        with pytest.raises(VectorStoreUnavailable):
            QdrantStore(client).search("places", [DOCUMENT], UnusedEmbedder(), vector())


def test_connection_timeout_is_unavailable():
    def fail(request):
        raise httpx.ConnectTimeout("offline", request=request)

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(VectorStoreUnavailable):
            QdrantStore(client).index("places", [DOCUMENT], UnusedEmbedder())


def test_collection_create_conflict_waits_for_transient_shard_readiness(monkeypatch):
    monkeypatch.setattr("app.integrations.qdrant.time.sleep", lambda _: None)
    statuses = [404, 409, 500, 200]
    requests = []

    def handler(request):
        requests.append(request.method)
        status = statuses.pop(0)
        return httpx.Response(
            status,
            json={
                "result": {
                    "config": {"params": {"vectors": {"size": 1024, "distance": "Cosine"}}},
                    "payload_schema": {"embedding_identity": {"data_type": "keyword"}},
                }
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        QdrantStore(client).ensure_collection("test_places_v1")
    assert requests == ["GET", "PUT", "GET", "GET"] and not statuses
