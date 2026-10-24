"""Opt-in frozen-catalog evaluation with live embeddings/Qdrant and explicit denominators."""

import argparse
import hashlib
import json
import math
import subprocess
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean
from time import perf_counter
from types import SimpleNamespace

from app.core.config import ROOT
from app.core.logging import configure_logging
from app.evaluation.observer import ObservedLlama
from app.evaluation.runner import write_report
from app.evaluation.scoring import latency
from app.integrations.city import ORIGIN, CityProvider
from app.integrations.qdrant import QdrantStore
from app.nlp.explanations import REQUIRED, explain, facts_for, retrieved_sources
from app.nlp.retrieval import DOCUMENT_THRESHOLD, MARGIN, THRESHOLD, Retrieval
from app.planning.models import Participant, Request
from app.planning.service import checked_plan


def read_json(path):
    return Path(path).read_text("utf-8")


def load_dataset(path):
    corpus = json.loads(read_json(path))
    snapshot_path = path.parent / corpus["snapshot"]
    snapshot = json.loads(read_json(snapshot_path))
    ids = {p["id"] for p in snapshot["pois"]}
    cases = corpus["cases"]
    if len(ids) != len(snapshot["pois"]) or not ids:
        raise ValueError("Snapshot must contain unique nonempty POI IDs")
    if not cases or len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Cases must have unique IDs")
    for case in cases:
        if not case["query"].strip() or not set(case["relevance"]) <= ids:
            raise ValueError("Unknown reference POI or empty query")
        if any(type(v) is not int or v not in (1, 2) for v in case["relevance"].values()):
            raise ValueError("Only positive relevance grades 1/2 may be listed")
        if bool(case["relevance"]) != bool(case["reference"].strip()):
            raise ValueError("Reference text required exactly for supported queries")
    date.fromisoformat(snapshot["travel_date"])
    return corpus, snapshot, snapshot_path


def rank_metrics(ranked_ids, relevance, k):
    if k < 1 or len(ranked_ids) != len(set(ranked_ids)):
        raise ValueError("Positive k and unique ranking required")
    if not relevance:
        return {name: None for name in ("precision", "recall", "ndcg", "mrr")}
    top = ranked_ids[:k]
    hits = sum(pid in relevance for pid in top)

    def dcg(grades):
        return sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(grades))

    ideal = dcg(sorted(relevance.values(), reverse=True)[:k])
    return {
        # Fixed k: an empty/short result cannot receive artificially perfect precision.
        "precision": hits / k,
        "recall": hits / len(relevance),
        "ndcg": dcg([relevance.get(pid, 0) for pid in top]) / ideal,
        "mrr": next((1 / (i + 1) for i, pid in enumerate(top) if pid in relevance), 0),
    }


def grade_search(case, output, ks=(1, 3, 5)):
    matches = output["matches"]
    ids = [m["place_id"] for m in matches]
    selected = [m for m in matches if m["score"] >= output["document_threshold"]][:3]
    return {
        "category_correct": output["category"] == case["expected_category"],
        "ranking": {str(k): rank_metrics(ids, case["relevance"], k) for k in ks},
        "thresholded_top3": rank_metrics([m["place_id"] for m in selected], case["relevance"], 3),
        "no_evidence": not case["relevance"],
        "context_returned_without_evidence": not case["relevance"] and bool(selected),
        "selected_contexts": selected,
    }


def summarize_search(rows):
    successful = [r for r in rows if "grade" in r]
    supported = [r for r in successful if not r["grade"]["no_evidence"]]
    absent = [r for r in successful if r["grade"]["no_evidence"]]
    return {
        "runs": len(rows),
        "successful": len(successful),
        "errors": len(rows) - len(successful),
        "category_correct": sum(r["grade"]["category_correct"] for r in successful),
        "supported_queries": len(supported),
        "no_evidence_queries": len(absent),
        "false_context_on_no_evidence": sum(
            r["grade"]["context_returned_without_evidence"] for r in absent
        ),
        "macro_ranking": {
            k: {
                metric: mean(r["grade"]["ranking"][k][metric] for r in supported)
                if supported
                else None
                for metric in ("precision", "recall", "ndcg", "mrr")
            }
            for k in ("1", "3", "5")
        },
        "latency": latency(rows),
    }


def manifest(paths):
    paths = [
        *paths,
        ROOT / "data/semantic-knowledge.json",
        ROOT / "data/embedding-runtime.json",
        ROOT / "data/llm-runtime.json",
        ROOT / "backend/uv.lock",
        *sorted((ROOT / "backend/app/evaluation").glob("*.py")),
        *[
            ROOT / "backend/app" / name
            for name in (
                "nlp/retrieval.py",
                "nlp/explanations.py",
                "integrations/qdrant.py",
                "integrations/embeddings.py",
                "integrations/city.py",
                "integrations/llama_cpp.py",
                "core/logging.py",
                "planning/service.py",
                "planning/planner.py",
                "planning/objective.py",
                "planning/validator.py",
            )
        ],
    ]
    return {
        str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in paths
    }


class FrozenCatalog:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def catalog(self, travel_date):
        if str(travel_date) != self.snapshot["travel_date"]:
            raise ValueError("Frozen snapshot assessed for a different date")
        return {
            **self.snapshot,
            "origin": ORIGIN,
            "destination": "Lisbon",
            "currency": "EUR",
            "timezone": "Europe/Lisbon",
            "source": "frozen_published_catalog",
            "fetched_at": self.snapshot["catalog_snapshot"]["fetched_at"],
        }


def capture_explanation(case, snapshot):
    """Exercise actual candidate selection, OSRM, planner, validator and explanation composer."""
    travel_date = date.fromisoformat(snapshot["travel_date"])
    places, travel, context = CityProvider(catalog_source=FrozenCatalog(snapshot)).dataset(
        travel_date, semantic_query=case["query"]
    )
    interests = {case["expected_category"]: 1}
    members = [Participant(str(i), interests.copy()) for i in (1, 2)]
    request = Request(members, 10000, 540, 1080)
    result = checked_plan(request, places, travel, "greedy")
    if not result.is_feasible or not result.validation.is_valid:
        raise ValueError("Cannot evaluate explanation of an invalid route")
    route = SimpleNamespace(
        id=1,
        route_set_id=None,
        visa_report=None,
        comparison={},
        result={**asdict(result), "data_context": context},
    )
    trip = SimpleNamespace(
        travel_date=travel_date,
        end_date=None,
        budget=10000,
        mandatory=[],
        excluded=[],
        participants_snapshot=[{"user_id": i, "name": f"Участник {i}"} for i in (1, 2)],
    )
    facts = facts_for(route, trip)
    llm = ObservedLlama()
    output = explain(route, trip, llm=llm)
    required = REQUIRED | set(retrieved_sources(route)) | {"objective"}
    checks = {
        "route_valid": result.validation.is_valid,
        "required_facts": required <= set(output.selected_ids),
        "exact_fact_copy": output.paragraphs == [facts[k] for k in output.selected_ids],
    }
    return {
        "id": case["id"],
        "mode": output.mode,
        "checks": checks,
        "request": {"budget": 10000, "start": 540, "end": 1080, "interests": interests},
        "route": route.result,
        "output": output.model_dump(mode="json"),
        "attempts": llm.attempts,
        "sample": {
            "user_input": "Объясни маршрут по интересу «"
            + case["query"]
            + "», расходы и ограничения.",
            "response": "\n".join(output.paragraphs),
            "retrieved_contexts": list(facts.values()),
        },
    }


def run(dataset, output_path, samples_path, *, explanations=False):
    corpus, snapshot, snapshot_path = load_dataset(dataset)
    index = Retrieval(store=QdrantStore(prefix="travel_eval_rag_20261024"))
    if corpus["knowledge_version"] != index.knowledge["version"]:
        raise ValueError("Knowledge changed; create and review a new corpus version")
    hashes = manifest([dataset, snapshot_path])
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    report = {
        "schema_version": 1,
        "completed": False,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "dataset_version": corpus["version"],
        "source_revision": revision,
        "input_sha256": hashes,
        "scope": "Frozen catalog; live embeddings/Qdrant; authored regression labels.",
        "embedding_identity": index.embedder.identity,
        "catalog_snapshot": snapshot["catalog_snapshot"],
        "candidate_count": len(snapshot["pois"]),
        "rows": [],
        "explanations": [],
        "thresholds": {"category": THRESHOLD, "margin": MARGIN, "document": DOCUMENT_THRESHOLD},
    }
    samples = []
    for case in corpus["cases"]:
        started = perf_counter()
        row = {"id": case["id"], "query": case["query"]}
        try:
            result = index.search(case["query"], snapshot["pois"])
            row.update(output=result, grade=grade_search(case, result))
            samples.append(
                {
                    "case_id": case["id"],
                    "kind": "retrieval",
                    "mode": "live_qdrant",
                    "sample": {
                        "user_input": case["query"],
                        "reference": case["reference"],
                        "retrieved_contexts": [
                            m["name"] + ": " + m["description"]
                            for m in row["grade"]["selected_contexts"]
                        ],
                    },
                }
            )
        except Exception as exc:
            row["error"] = type(exc).__name__
        row["seconds"] = round(perf_counter() - started, 4)
        report["rows"].append(row)
        report["summary"] = summarize_search(report["rows"])
        write_report(output_path, report)
        print(f"retrieval {case['id']} {'ERROR' if 'error' in row else 'done'}", flush=True)
    if explanations:
        # Four predefined representative cases, one per category, never selected by observed scores.
        for case in (corpus["cases"][i] for i in (0, 8, 16, 24)):
            started = perf_counter()
            try:
                capture = capture_explanation(case, snapshot)
                report["explanations"].append(capture)
                samples.append(
                    {
                        "case_id": case["id"],
                        "kind": "explanation",
                        "mode": capture["mode"],
                        "sample": capture["sample"],
                    }
                )
            except Exception as exc:
                report["explanations"].append({"id": case["id"], "error": type(exc).__name__})
            report["explanations"][-1]["seconds"] = round(perf_counter() - started, 4)
            write_report(output_path, report)
            print(f"explanation {case['id']} done", flush=True)
    samples_path.parent.mkdir(parents=True, exist_ok=True)
    samples_path.write_text(
        "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in samples), "utf-8"
    )
    report.update(
        completed=True,
        inputs_unchanged=hashes == manifest([dataset, snapshot_path]),
        finished_at=datetime.now(timezone.utc).isoformat(),
        samples_sha256=hashlib.sha256(samples_path.read_bytes()).hexdigest(),
    )
    write_report(output_path, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "data/evaluation/rag-queries.json")
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".local/evaluations/rag-retrieval.json"
    )
    parser.add_argument(
        "--samples", type=Path, default=ROOT / ".local/evaluations/rag-samples.jsonl"
    )
    parser.add_argument(
        "--explanations", action="store_true", help="Also call live OSRM and chat model"
    )
    args = parser.parse_args()
    configure_logging(file_tag="rag-evaluation")
    report = run(
        args.dataset.resolve(),
        args.output.resolve(),
        args.samples.resolve(),
        explanations=args.explanations,
    )
    if (
        report["summary"]["errors"]
        or not report["inputs_unchanged"]
        or any("error" in r or not all(r["checks"].values()) for r in report["explanations"])
    ):
        raise SystemExit(1)
