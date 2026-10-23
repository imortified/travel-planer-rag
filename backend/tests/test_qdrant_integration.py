"""Opt-in Qdrant tests use unique collections and remove only those test collections."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from test_embeddings import vector

from app.integrations.qdrant import QdrantStore, VectorStoreUnavailable

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_QDRANT_TESTS") != "1", reason="Set RUN_QDRANT_TESTS=1 with Qdrant running"
)


class CountingEmbeddings:
    identity = "test-model-v1"

    def __init__(self):
        self.encoded = []

    def encode(self, texts):
        self.encoded.extend(texts)
        return [vector(0 if text == "matching" else 1) for text in texts]


def doc(key, text, **metadata):
    return {"key": key, "text": text, "metadata": metadata}


@pytest.fixture
def store():
    prefix = f"test_travel_{uuid4().hex}"
    with httpx.Client(timeout=10) as client:
        result = QdrantStore(client, prefix)
        try:
            yield result
        finally:
            for kind in ("places", "interests"):
                # Only this fixture's random namespace can be removed.
                name = result.collection(kind)
                assert name.startswith(prefix + "_")
                response = result.request("DELETE", f"/collections/{name}")
                assert response.status_code in (200, 404)


def test_idempotence_single_document_update_metadata_and_model_versions(store):
    embedding = CountingEmbeddings()
    documents = [doc("Lisbon:a", "matching", source="old"), doc("Lisbon:b", "other")]
    original = store.index("places", documents, embedding)
    assert embedding.encoded == ["matching", "other"]
    assert store.index("places", documents, embedding) == original
    assert len(embedding.encoded) == 2
    documents[0]["metadata"]["source"] = "new"
    assert store.index("places", documents, embedding) == original
    assert len(embedding.encoded) == 2
    saved = store.request(
        "POST",
        f"/collections/{store.collection('places')}/points",
        {"ids": original, "with_payload": True},
    ).json()["result"]
    assert next(p for p in saved if p["id"] == original[0])["payload"]["source"] == "new"
    documents[0]["text"] = "changed"
    revised = store.index("places", documents, embedding)
    assert revised[0] != original[0] and revised[1] == original[1]
    assert embedding.encoded == ["matching", "other", "changed"]
    assert store.search("places", documents, embedding, vector()) == pytest.approx([0, 0])
    # An older in-flight request can still use its own text revision.
    assert store.search(
        "places", [doc("Lisbon:a", "matching")], embedding, vector()
    ) == pytest.approx([1])
    assert len(embedding.encoded) == 3
    embedding.identity = "test-model-v2"
    assert store.index("places", documents, embedding) != revised
    assert embedding.encoded[-2:] == ["changed", "other"]


def test_candidate_ids_isolate_closed_excluded_foreign_and_stale_records(store):
    embedding = CountingEmbeddings()
    active = doc("Lisbon:active", "other")
    store.index(
        "places",
        [
            active,
            doc("Lisbon:closed", "matching"),
            doc("Lisbon:excluded", "matching"),
            doc("Porto:foreign", "matching"),
        ],
        embedding,
    )
    scores = store.search("places", [active], embedding, vector())
    assert scores == pytest.approx([0])  # Higher-scoring outsiders are never returned.


def test_incompatible_collection_is_preserved(store):
    name = store.collection("places")
    response = store.request(
        "PUT", f"/collections/{name}", {"vectors": {"size": 3, "distance": "Dot"}}
    )
    response.raise_for_status()
    with pytest.raises(VectorStoreUnavailable):
        store.index("places", [doc("one", "matching")], CountingEmbeddings())
    config = store.request("GET", f"/collections/{name}").json()["result"]["config"]["params"][
        "vectors"
    ]
    assert config == {"size": 3, "distance": "Dot"}


def test_concurrent_first_search_can_create_one_collection_and_share_documents(store):
    def search(_):
        return store.search("places", [doc("shared", "matching")], CountingEmbeddings(), vector())

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(search, range(2)))
    assert results == [[1.0], [1.0]]
    info = store.request("GET", f"/collections/{store.collection('places')}").json()["result"]
    assert info["points_count"] == 1
