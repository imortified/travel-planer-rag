"""Small exact cosine index. Only public document vectors are cached, never user queries."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from app.core.config import ROOT
from app.integrations.embeddings import Embeddings, normalize

THRESHOLD = 0.55
MARGIN = 0.05
DOCUMENT_THRESHOLD = 0.40
CATEGORY_NAMES = {"history": "история", "nature": "природа", "food": "еда", "art": "искусство"}


def cosine(a, b):
    return max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b, strict=True))))


class Retrieval:
    def __init__(self, embedder=None, cache_dir=None, knowledge_path=None):
        self.embedder = embedder or Embeddings()
        self.cache_dir = Path(cache_dir or ROOT / ".local" / "semantic-cache")
        self.knowledge = json.loads(
            Path(knowledge_path or ROOT / "data" / "semantic-knowledge.json").read_text("utf-8")
        )

    def documents(self, pois):
        documents = []
        for poi in pois:
            extra = self.knowledge["places"].get(poi["id"])
            description = (
                extra["description"]
                if extra
                else (f"{poi['name']}. Категория каталога: {CATEGORY_NAMES[poi['category']]}.")
            )
            documents.append(
                {
                    "place_id": poi["id"],
                    "name": poi["name"],
                    "description": description,
                    "source_url": extra["source_url"] if extra else poi["source_url"],
                    "checked_on": self.knowledge["checked_on"] if extra else None,
                    "description_kind": "curated" if extra else "catalog_category",
                }
            )
        return documents

    def document_vectors(self, texts):
        key = hashlib.sha256(
            json.dumps(
                [self.embedder.identity, self.knowledge["version"], texts], ensure_ascii=False
            ).encode()
        ).hexdigest()
        path = self.cache_dir / f"{key}.json"
        try:
            saved = json.loads(path.read_text("utf-8"))
            if len(saved) == len(texts):
                return [normalize(v) for v in saved]
        except (OSError, TypeError, ValueError):
            pass
        vectors = self.embedder.encode(texts)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=self.cache_dir, delete=False) as file:
            json.dump(vectors, file)
            temporary = file.name
        os.replace(temporary, path)
        return vectors

    def search(self, text, pois):
        text = text.strip()
        documents = self.documents(pois)
        categories = list(self.knowledge["categories"])
        profiles = list(self.knowledge["categories"].values())
        vectors = self.document_vectors(profiles + [d["description"] for d in documents])
        query = self.embedder.encode([text], query=True)[0]
        scores = [cosine(query, vector) for vector in vectors]
        ranking = sorted(
            zip(categories, scores[: len(categories)], strict=True),
            key=lambda row: (-row[1], row[0]),
        )
        # A vector does not reliably encode negation, mixed preferences or commands.
        negative = bool(
            re.search(
                r"\bне\b|\bбез\b|\bкроме\b|\bненавиж\w*|\bизбега\w*|\bисключ\w*|\bnot\b|\bwithout\b",
                text.casefold(),
            )
        )
        accepted = (
            not negative
            and ranking[0][1] >= THRESHOLD
            and (ranking[0][1] - ranking[1][1] >= MARGIN)
        )
        matches = sorted(
            [
                {**doc, "score": round(score, 6)}
                for doc, score in zip(documents, scores[len(categories) :], strict=True)
            ],
            key=lambda item: (-item["score"], item["place_id"]),
        )
        return {
            "query": text,
            "model": self.embedder.identity,
            "knowledge_version": self.knowledge["version"],
            "threshold": THRESHOLD,
            "document_threshold": DOCUMENT_THRESHOLD,
            "margin": MARGIN,
            "category": ranking[0][0] if accepted else None,
            "category_scores": {category: round(score, 6) for category, score in ranking},
            "needs_clarification": not accepted,
            "reason": "negative_or_complex" if negative else "matched" if accepted else "uncertain",
            "matches": matches,
        }
