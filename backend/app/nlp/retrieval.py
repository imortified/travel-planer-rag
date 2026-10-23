"""Semantic retrieval via Qdrant; every search is restricted to current candidate IDs."""

import json
import re
from pathlib import Path

from app.core.config import ROOT
from app.integrations.embeddings import Embeddings
from app.integrations.qdrant import QdrantStore

THRESHOLD = 0.55
MARGIN = 0.05
DOCUMENT_THRESHOLD = 0.40
CATEGORY_NAMES = {"history": "история", "nature": "природа", "food": "еда", "art": "искусство"}


class Retrieval:
    def __init__(self, embedder=None, store=None, knowledge_path=None):
        self.embedder = embedder or Embeddings()
        self.store = store or QdrantStore()
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

    def records(self, pois):
        version = self.knowledge["version"]
        profiles = [
            {
                "key": f"category:{category}",
                "text": text,
                "metadata": {"category": category, "knowledge_version": version},
            }
            for category, text in self.knowledge["categories"].items()
        ]
        documents = self.documents(pois)
        categories = {p["id"]: p["category"] for p in pois}
        places = [
            {
                "key": f"Lisbon:{doc['place_id']}",
                "text": doc["description"],
                "metadata": {
                    **doc,
                    "city": "Lisbon",
                    "category": categories[doc["place_id"]],
                    "knowledge_version": version,
                },
            }
            for doc in documents
        ]
        return profiles, places, documents

    def index(self, pois):
        profiles, places, _ = self.records(pois)
        return {
            kind: len(self.store.index(kind, records, self.embedder))
            for kind, records in [("interests", profiles), ("places", places)]
        }

    def search(self, text, pois):
        text = text.strip()
        profiles, places, documents = self.records(pois)
        categories = list(self.knowledge["categories"])
        query = self.embedder.encode([text], query=True)[0]
        category_scores = self.store.search("interests", profiles, self.embedder, query)
        place_scores = self.store.search("places", places, self.embedder, query) if places else []
        ranking = sorted(
            zip(categories, category_scores, strict=True),
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
                for doc, score in zip(documents, place_scores, strict=True)
            ],
            key=lambda item: (-item["score"], item["place_id"]),
        )
        return {
            "query": text,
            "model": self.embedder.identity,
            "knowledge_version": self.knowledge["version"],
            "vector_store": {
                "engine": "qdrant",
                "exact": True,
                "collections": {
                    kind: self.store.collection(kind) for kind in ("interests", "places")
                },
            },
            "threshold": THRESHOLD,
            "document_threshold": DOCUMENT_THRESHOLD,
            "margin": MARGIN,
            "category": ranking[0][0] if accepted else None,
            "category_scores": {category: round(score, 6) for category, score in ranking},
            "needs_clarification": not accepted,
            "reason": "negative_or_complex" if negative else "matched" if accepted else "uncertain",
            "matches": matches,
        }
