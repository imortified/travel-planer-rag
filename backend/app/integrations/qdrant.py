"""Qdrant REST adapter: immutable text revisions and request-scoped exact cosine search."""

import hashlib
import json
import logging
import math
import re
import time
from uuid import NAMESPACE_URL, uuid5

import httpx

from app.core.config import settings
from app.core.logging import emit, error_kind, request_id
from app.integrations.embeddings import DIMENSIONS

SCHEMA_VERSION = "v1"
logger = logging.getLogger(__name__)


class VectorStoreUnavailable(Exception):
    pass


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def point_for(document, model):
    text_hash = fingerprint(document["text"])
    # Keep previous text versions for concurrent searches and model changes. Query IDs
    # restrict each search to the current request; stale records can never leak into it.
    identifier = str(
        uuid5(NAMESPACE_URL, json.dumps([SCHEMA_VERSION, document["key"], model, text_hash]))
    )
    payload = {
        **document["metadata"],
        "document_key": document["key"],
        "text": document["text"],
        "embedding_identity": model,
        "text_sha256": text_hash,
    }
    payload["payload_sha256"] = fingerprint(payload)
    return {"id": identifier, "payload": payload}


class QdrantStore:
    def __init__(self, client=None, prefix=None):
        self.client = client
        self.prefix = prefix or settings.qdrant_collection_prefix
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", self.prefix):
            raise ValueError("Invalid Qdrant collection prefix")

    def request(self, method, path, body=None):
        headers = {"api-key": settings.qdrant_api_key} if settings.qdrant_api_key else {}
        if request_id.get():
            headers["X-Request-ID"] = request_id.get()
        started = time.perf_counter()
        try:
            response = self.client.request(
                method, f"{settings.qdrant_url.rstrip('/')}{path}", json=body, headers=headers
            )
        except httpx.HTTPError as exc:
            emit(
                logger,
                "qdrant.request.failed",
                logging.ERROR,
                exception=exc,
                provider="qdrant",
                method=method,
                reason=error_kind(exc),
                duration_ms=(time.perf_counter() - started) * 1000,
            )
            raise
        emit(
            logger,
            "qdrant.request.completed",
            logging.ERROR if response.status_code >= 500 else logging.INFO,
            provider="qdrant",
            method=method,
            status_code=response.status_code,
            duration_ms=(time.perf_counter() - started) * 1000,
        )
        return response

    def collection(self, kind):
        if kind not in ("places", "interests"):
            raise ValueError("Unknown collection kind")
        return f"{self.prefix}_{kind}_{SCHEMA_VERSION}"

    def ensure_collection(self, name):
        path = f"/collections/{name}"
        response = self.request("GET", path)
        if response.status_code == 404:
            created = self.request(
                "PUT", path, {"vectors": {"size": DIMENSIONS, "distance": "Cosine"}}
            )
            # Another worker may have created it between GET and PUT.
            if created.status_code not in (400, 409):
                created.raise_for_status()
            response = self.request("GET", path)
        # Qdrant can briefly return 500 while another worker creates the shards,
        # even after the conflicting create has returned 409. Retry readiness reads
        # only, with a fixed bound; never retry arbitrary writes or recreate data.
        for delay in (0.05, 0.1, 0.2):
            if response.status_code not in (404, 500, 503):
                break
            time.sleep(delay)
            response = self.request("GET", path)
        response.raise_for_status()
        info = response.json()["result"]
        config = info["config"]["params"]["vectors"]
        if (
            not isinstance(config, dict)
            or config.get("size") != DIMENSIONS
            or config.get("distance") != "Cosine"
        ):
            raise ValueError("Incompatible Qdrant vector schema; existing collection preserved")
        if "embedding_identity" not in info.get("payload_schema", {}):
            response = self.request(
                "PUT",
                f"{path}/index?wait=true",
                {"field_name": "embedding_identity", "field_schema": "keyword"},
            )
            response.raise_for_status()

    def sync(self, name, documents, embedder):
        self.ensure_collection(name)
        points = [point_for(document, embedder.identity) for document in documents]
        if not points:
            return []
        if len({d["key"] for d in documents}) != len(documents):
            raise ValueError("Duplicate document keys")
        existing = {}
        for offset in range(0, len(points), 128):
            response = self.request(
                "POST",
                f"/collections/{name}/points",
                {
                    "ids": [p["id"] for p in points[offset : offset + 128]],
                    "with_payload": True,
                    "with_vector": False,
                },
            )
            response.raise_for_status()
            expected = {p["id"] for p in points[offset : offset + 128]}
            for point in response.json()["result"]:
                if (
                    point["id"] not in expected
                    or point["id"] in existing
                    or not isinstance(point.get("payload"), dict)
                ):
                    raise ValueError("Invalid retrieved point metadata")
                existing[point["id"]] = point
        missing = [p for p in points if p["id"] not in existing]
        if missing:
            vectors = embedder.encode([p["payload"]["text"] for p in missing])
            for offset in range(0, len(missing), 64):
                batch = missing[offset : offset + 64]
                response = self.request(
                    "PUT",
                    f"/collections/{name}/points?wait=true",
                    {"points": [{**p, "vector": vectors[offset + i]} for i, p in enumerate(batch)]},
                )
                response.raise_for_status()
        for point in points:
            saved = existing.get(point["id"])
            if (
                saved
                and saved["payload"].get("payload_sha256") != point["payload"]["payload_sha256"]
            ):
                # A source/name/version change alone does not require model inference.
                response = self.request(
                    "PUT",
                    f"/collections/{name}/points/payload?wait=true",
                    {"points": [point["id"]], "payload": point["payload"]},
                )
                response.raise_for_status()
        return [p["id"] for p in points]

    def search(self, kind, documents, embedder, query):
        if self.client is None:
            with httpx.Client(
                timeout=httpx.Timeout(settings.qdrant_timeout_seconds, connect=2)
            ) as client:
                return QdrantStore(client, self.prefix).search(kind, documents, embedder, query)
        try:
            name = self.collection(kind)
            ids = self.sync(name, documents, embedder)
            if not ids:
                return []
            response = self.request(
                "POST",
                f"/collections/{name}/points/query",
                {
                    "query": query,
                    "filter": {
                        "must": [
                            {"has_id": ids},
                            {"key": "embedding_identity", "match": {"value": embedder.identity}},
                        ]
                    },
                    "params": {"exact": True},
                    "limit": len(ids),
                    "with_payload": False,
                    "with_vector": False,
                },
            )
            response.raise_for_status()
            rows = response.json()["result"]["points"]
            scores = {}
            for row in rows:
                score = row["score"]
                if (
                    row["id"] not in ids
                    or type(score) not in (int, float)
                    or not math.isfinite(score)
                ):
                    raise ValueError("Invalid vector search result")
                if row["id"] in scores or not -1.00001 <= score <= 1.00001:
                    raise ValueError("Duplicate or out-of-range vector score")
                scores[row["id"]] = max(-1.0, min(1.0, score))
            if set(scores) != set(ids):
                raise ValueError("Incomplete vector search result")
            return [scores[identifier] for identifier in ids]
        except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError) as exc:
            emit(
                logger,
                "qdrant.search.failed",
                logging.ERROR,
                exception=exc,
                provider="qdrant",
                operation="search",
                reason=error_kind(exc),
            )
            raise VectorStoreUnavailable(
                "Qdrant unavailable or incompatible response/schema"
            ) from exc

    def index(self, kind, documents, embedder):
        if self.client is None:
            with httpx.Client(
                timeout=httpx.Timeout(settings.qdrant_timeout_seconds, connect=2)
            ) as client:
                return QdrantStore(client, self.prefix).index(kind, documents, embedder)
        try:
            return self.sync(self.collection(kind), documents, embedder)
        except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError) as exc:
            emit(
                logger,
                "qdrant.index.failed",
                logging.ERROR,
                exception=exc,
                provider="qdrant",
                operation="index",
                reason=error_kind(exc),
            )
            raise VectorStoreUnavailable("Qdrant indexing failed; existing data preserved") from exc
