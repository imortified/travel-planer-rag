"""Opt-in live smoke evaluation; no LLM, synthetic vectors or private user text."""

import json
import time
from datetime import date

from app.core.config import ROOT
from app.integrations.city import CityProvider
from app.nlp.retrieval import Retrieval

CASES = [
    ("археология и древние цивилизации", "history", "osm:node:5068486965"),
    ("живопись и скульптура", "art", "osm:node:11047643391"),
    ("ботанический сад и коллекция растений", "nature", "curated:lisbon:ajuda-botanical-garden"),
    ("гастрономия и местная кухня", "food", None),
    ("не люблю музеи", None, None),
    ("квантовые вычисления", None, None),
    ("хорошо провести время", None, None),
]


def main():
    catalog = CityProvider().catalog(date(2026, 10, 23))
    pois = [p for p in catalog["pois"] if p["eligible"]]
    index, results = Retrieval(), []
    for query, category, place_id in CASES:
        started = time.monotonic()
        output = index.search(query, pois)
        top = output["matches"][:3]
        passed = output["category"] == category and (
            place_id is None or place_id in {m["place_id"] for m in top}
        )
        results.append(
            {
                "query": query,
                "expected_category": category,
                "category": output["category"],
                "category_scores": output["category_scores"],
                "top_three": top,
                "passed": passed,
                "seconds": round(time.monotonic() - started, 3),
            }
        )
        print(
            f"{'PASS' if passed else 'FAIL'} {query.encode('ascii', 'backslashreplace').decode()}",
            flush=True,
        )
    report = {
        "evaluated_on": "2026-10-23",
        "model": index.embedder.identity,
        "knowledge_version": index.knowledge["version"],
        "eligible_poi_count": len(pois),
        "cases": results,
    }
    (ROOT / "docs" / "retrieval-evaluation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if not all(case["passed"] for case in results):
        raise SystemExit("Retrieval evaluation failed")


if __name__ == "__main__":
    main()
