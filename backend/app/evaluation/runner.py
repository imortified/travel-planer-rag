"""A fixed public acceptance corpus, separate live/fault metrics and RAGAS-compatible export."""

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from time import perf_counter, sleep
from uuid import uuid4

from app.core.config import ROOT, settings
from app.core.logging import configure_logging, request_id
from app.evaluation.controls import run_controls
from app.evaluation.explanations import evaluate_fixture, fixtures
from app.evaluation.observer import ObservedLlama
from app.evaluation.scoring import grade, latency, summarize
from app.integrations.llama_cpp import EXPLANATION_MAX_TOKENS
from app.nlp import explanations, parser
from app.schemas.assistant import ExtractedTrip, ParseInput


def read_corpus(path):
    data = json.loads(path.read_text("utf-8"))
    date.fromisoformat(data["reference_date"])
    cases = data["cases"]
    if not 1 <= len(cases) <= 1000 or len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Corpus must contain unique IDs and 1-1000 cases")
    paths = {"group_budget", "effective_count", "names", "individual_interests"} | {
        "draft." + key for key in ExtractedTrip.model_fields
    }
    for case in cases:
        if not re.fullmatch(r"[a-z0-9-]{1,80}", case["id"]) or not case["expected"]:
            raise ValueError("Case requires a safe ID and expected fields")
        ParseInput(text=case["text"], member_names=case["members"])
        if not (set(case["expected"]) | set(case.get("nonempty", []))) <= paths:
            raise ValueError("Unknown expected field")
        if not set(case.get("questions_required", [])) <= set(case.get("questions_allowed", [])):
            raise ValueError("Required questions must be allowed")
    return data


def write_report(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", "utf-8"
    )
    for attempt in range(5):
        try:
            os.replace(temporary, path)
            break
        except PermissionError:
            # Windows readers/indexers can briefly deny delete-sharing during an atomic replace.
            # Keep the previous report intact; retry only this bounded sharing failure.
            if attempt == 4:
                raise
            sleep(0.05 * 2**attempt)


def metadata(corpus_path):
    paths = [
        corpus_path,
        ROOT / "data/llm-runtime.json",
        ROOT / "data/semantic-knowledge.json",
        *sorted((ROOT / "backend/app/evaluation").glob("*.py")),
        *[
            ROOT / "backend" / file
            for file in (
                "app/nlp/parser.py",
                "app/nlp/grounding.py",
                "app/nlp/explanations.py",
                "app/integrations/llama_cpp.py",
                "app/schemas/assistant.py",
            )
        ],
    ]
    hashes = {
        str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in paths
    }
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    return {
        "source_revision": revision,
        "input_sha256": hashes,
        "python": platform.python_version(),
        "model_requested": settings.llm_model,
        "runtime_manifest": json.loads((ROOT / "data/llm-runtime.json").read_text("utf-8")),
        "generation": {"temperature": 0, "seed": 42, "max_tokens": 1400, "enable_thinking": False},
        "timeout_seconds_per_http_operation": settings.llm_timeout_seconds,
        "explanation_generation": {
            "max_tokens": EXPLANATION_MAX_TOKENS,
            "timeout_seconds_including_repair": settings.llm_explanation_timeout_seconds,
        },
        "parser_prompt_version": parser.PROMPT_VERSION,
        "explanation_prompt_version": explanations.PROMPT_VERSION,
    }


def run(corpus_path, output_path, ragas_path, selected=None, repeat=1, skip_explanations=False):
    corpus = read_corpus(corpus_path)
    cases = [c for c in corpus["cases"] if not selected or c["id"] in selected]
    if not cases or (selected and set(selected) - {c["id"] for c in cases}):
        raise ValueError("Unknown or empty case selection")
    before = metadata(corpus_path)
    report = {
        "schema_version": 1,
        "completed": False,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "dataset_version": corpus["version"],
        "reference_date": corpus["reference_date"],
        "metadata": before,
        "parser": [],
        "explanations": [],
        "fault_controls": [],
    }
    for repetition in range(1, repeat + 1):
        for case in cases:
            started = perf_counter()
            model = ObservedLlama()
            token = request_id.set(str(uuid4()))
            row = {"id": case["id"], "repeat": repetition, "tags": case.get("tags", [])}
            try:
                result = parser.parse_request(
                    ParseInput(text=case["text"], member_names=case["members"]),
                    model,
                    date.fromisoformat(corpus["reference_date"]),
                )
                row["output"] = result.model_dump(mode="json")
            except Exception as exc:
                result = None
                row["error"] = type(exc).__name__
            finally:
                request_id.reset(token)
            checks = grade(case, result)
            row.update(
                checks=checks,
                passed=all(c["passed"] for c in checks),
                attempts=model.attempts,
                seconds=round(perf_counter() - started, 4),
            )
            report["parser"].append(row)
            report["parser_summary"] = summarize(report["parser"])
            write_report(output_path, report)
            print(
                json.dumps(
                    {
                        "id": case["id"],
                        "repeat": repetition,
                        "passed": row["passed"],
                        "seconds": row["seconds"],
                    }
                ),
                flush=True,
            )

    samples = []
    if not skip_explanations:
        for fixture in fixtures():
            started = perf_counter()
            model = ObservedLlama()
            row = evaluate_fixture(fixture, model)
            sample = row.pop("ragas_sample")
            samples.append({"case_id": fixture[0], "sample": sample, "mode": row["output"]["mode"]})
            row.update(seconds=round(perf_counter() - started, 4), attempts=model.attempts)
            report["explanations"].append(row)
            write_report(output_path, report)
            print(
                json.dumps(
                    {"id": fixture[0], "mode": row["output"]["mode"], "passed": row["passed"]}
                ),
                flush=True,
            )
    report["fault_controls"] = run_controls()
    rows = report["explanations"]
    report["explanation_summary"] = {
        "runs": len(rows),
        "grounding_passed": sum(r["passed"] for r in rows),
        "llm_mode": sum(r["output"]["mode"] == "llm" for r in rows),
        "fallback_mode": sum(r["output"]["mode"] == "template" for r in rows),
        "latency": latency(rows),
    }
    ragas_path.parent.mkdir(parents=True, exist_ok=True)
    ragas_path.write_text(
        "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in samples), "utf-8"
    )
    report.update(
        completed=True,
        finished_at=datetime.now(timezone.utc).isoformat(),
        inputs_unchanged=before["input_sha256"] == metadata(corpus_path)["input_sha256"],
        ragas={
            "export_path": str(ragas_path.relative_to(ROOT)),
            "samples": len(samples),
            "scores_computed": False,
            "scope": "Synthetic explanation contexts; does not measure live retrieval quality",
        },
    )
    write_report(output_path, report)
    return report


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--dataset", type=Path, default=ROOT / "data/evaluation/llm-parser.json")
    cli.add_argument("--output", type=Path, default=ROOT / ".local/evaluations/llm-quality.json")
    cli.add_argument(
        "--ragas-output", type=Path, default=ROOT / ".local/evaluations/ragas-explanations.jsonl"
    )
    cli.add_argument("--case", action="append", dest="selected")
    cli.add_argument("--repeat", type=int, choices=range(1, 6), default=1)
    cli.add_argument("--skip-explanations", action="store_true")
    args = cli.parse_args()
    configure_logging(file_tag="llm-evaluation")
    report = run(
        args.dataset.resolve(),
        args.output.resolve(),
        args.ragas_output.resolve(),
        args.selected,
        args.repeat,
        args.skip_explanations,
    )
    print(
        json.dumps(
            {
                "completed": report["completed"],
                "parser_summary": report["parser_summary"],
                "explanation_summary": report["explanation_summary"],
            }
        ),
        flush=True,
    )
    return (
        0
        if report["inputs_unchanged"]
        and all(
            r["passed"]
            for r in report["parser"] + report["explanations"] + report["fault_controls"]
        )
        else 1
    )
