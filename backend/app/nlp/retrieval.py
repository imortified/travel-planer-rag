"""Semantic retrieval via Qdrant; every search is restricted to current candidate IDs."""

import json
from pathlib import Path

from app.core.config import ROOT
from app.integrations.embeddings import Embeddings
from app.integrations.qdrant import QdrantStore
from app.nlp.retrieval_policy import assess

THRESHOLD = 0.55
MARGIN = 0.05
DOCUMENT_THRESHOLD = 0.40
CATEGORY_NAMES = {"history": "история", "nature": "природа", "food": "еда", "art": "искусство"}


class Retrieval:
    def __init__(self, embedder=None, store=None, knowledge_path=None, destination="Lisbon"):
        from app.core.destinations import city_for

        self.destination = city_for(destination).id
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
                    "checked_on": extra.get("checked_on", self.knowledge["checked_on"])
                    if extra
                    else None,
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
                "key": f"{self.destination}:{doc['place_id']}",
                "text": doc["description"],
                "metadata": {
                    **doc,
                    "city": self.destination,
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
        matches = sorted(
            [
                {**doc, "score": round(score, 6)}
                for doc, score in zip(documents, place_scores, strict=True)
            ],
            key=lambda item: (-item["score"], item["place_id"]),
        )
        policy = assess(
            text,
            ranking,
            matches,
            {p["id"]: p["category"] for p in pois},
            THRESHOLD,
            MARGIN,
            DOCUMENT_THRESHOLD,
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
            "category_scores": {category: round(score, 6) for category, score in ranking},
            **policy,
            "matches": matches if policy["can_apply"] else [],
        }
