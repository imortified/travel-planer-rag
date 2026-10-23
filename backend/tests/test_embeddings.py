import json
import math

import httpx
import pytest

from app.integrations.embeddings import DIMENSIONS, Embeddings, EmbeddingUnavailable, normalize


def vector(axis=0):
    return [float(i == axis) for i in range(DIMENSIONS)]


def test_embedding_adapter_reorders_normalizes_and_instructs_queries():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "Qwen3-Embedding-0.6B-Q8_0",
                "data": [
                    {"index": 1, "embedding": vector(1)},
                    {"index": 0, "embedding": [2 * x for x in vector()]},
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert Embeddings(client).encode(["first", "second"], query=True) == [vector(), vector(1)]
    assert requests[0]["input"][0].startswith("Instruct: ")
    assert requests[0]["input"][0].endswith("Query: first")


@pytest.mark.parametrize(
    "bad",
    [[], [0] * DIMENSIONS, [True] * DIMENSIONS, [math.nan] * DIMENSIONS, [math.inf] * DIMENSIONS],
)
def test_invalid_vectors_cannot_enter_cosine_index(bad):
    with pytest.raises(ValueError):
        normalize(bad)


@pytest.mark.parametrize(
    "model,indexes",
    [
        ("wrong", [0]),
        ("Qwen3-Embedding-0.6B-Q8_0", [1]),
        ("Qwen3-Embedding-0.6B-Q8_0", [False]),
        ("Qwen3-Embedding-0.6B-Q8_0", [0, 0]),
    ],
)
def test_wrong_model_duplicate_or_invalid_indexes_are_rejected(model, indexes):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "model": model,
                "data": [{"index": index, "embedding": vector()} for index in indexes],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EmbeddingUnavailable):
            Embeddings(client).encode(["text"])


def test_unavailable_embedding_server_is_explicit():
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client:
        with pytest.raises(EmbeddingUnavailable):
            Embeddings(client).encode(["text"])
