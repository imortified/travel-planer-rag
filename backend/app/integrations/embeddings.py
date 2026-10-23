"""Validated, normalized embeddings from the separate local llama.cpp server."""

import math

import httpx

from app.core.config import settings

DIMENSIONS = 1024
REVISION = "370f27d7550e0def9b39c1f16d3fbaa13aa67728"
RECIPE = "qwen3-last-instruct-v1"
TASK = "Given a travel interest, retrieve relevant tourism descriptions and interest categories."


class EmbeddingUnavailable(Exception):
    pass


def normalize(vector, dimensions=DIMENSIONS):
    if not isinstance(vector, list) or len(vector) != dimensions:
        raise ValueError("Unexpected embedding dimensions")
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in vector):
        raise ValueError("Invalid embedding value")
    norm = math.sqrt(sum(v * v for v in vector))
    if not math.isfinite(norm) or norm <= 0:
        raise ValueError("Invalid embedding norm")
    return [v / norm for v in vector]


class Embeddings:
    def __init__(self, client=None):
        self.client = client

    @property
    def identity(self):
        return f"{settings.embedding_model}:{REVISION}:{DIMENSIONS}:{RECIPE}"

    def encode(self, texts, query=False):
        if not texts:
            return []
        if self.client is None:
            with httpx.Client(
                timeout=httpx.Timeout(settings.embedding_timeout_seconds, connect=3)
            ) as client:
                return Embeddings(client).encode(texts, query)
        inputs = [f"Instruct: {TASK}\nQuery: {t}" if query else t for t in texts]
        try:
            # Bounded batches avoid overflowing the CPU server's token batch.
            vectors = []
            for offset in range(0, len(inputs), 4):
                batch = inputs[offset : offset + 4]
                response = self.client.post(
                    f"{settings.embedding_base_url.rstrip('/')}/v1/embeddings",
                    json={"model": settings.embedding_model, "input": batch},
                )
                response.raise_for_status()
                body = response.json()
                if body.get("model") != settings.embedding_model:
                    raise ValueError("Unexpected embedding model")
                rows = body["data"]
                if any(type(r["index"]) is not int for r in rows):
                    raise ValueError("Invalid embedding index type")
                if len(rows) != len(batch) or sorted(r["index"] for r in rows) != list(
                    range(len(batch))
                ):
                    raise ValueError("Invalid embedding indexes")
                vectors.extend(
                    normalize(r["embedding"]) for r in sorted(rows, key=lambda r: r["index"])
                )
            return vectors
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise EmbeddingUnavailable("Local embeddings unavailable or invalid") from exc
