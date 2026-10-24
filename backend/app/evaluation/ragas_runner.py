"""Optional RAGAS scoring. No default judge, no implicit cloud client, no invented scores."""

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import math
import os
import platform
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from time import perf_counter

from app.core.config import ROOT, settings
from app.evaluation.runner import write_report


def read_samples(path, calibration=False):
    if calibration:
        data = json.loads(path.read_text("utf-8"))
        rows = [
            {**c, "case_id": c["id"], "kind": "calibration", "mode": "authored_control"}
            for c in data["cases"]
        ]
    else:
        rows = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]
    if not rows or len({(r["case_id"], r["kind"]) for r in rows}) != len(rows):
        raise ValueError("Samples require unique (case_id, kind)")
    for row in rows:
        sample = row["sample"]
        if not isinstance(sample["user_input"], str) or not sample["user_input"].strip():
            raise ValueError("Nonempty user_input required")
        contexts = sample["retrieved_contexts"]
        if not isinstance(contexts, list) or any(
            not isinstance(c, str) or not c.strip() for c in contexts
        ):
            raise ValueError("Contexts must be a list of nonempty strings")
        if row["kind"] not in {"calibration", "explanation", "retrieval"}:
            raise ValueError("Unknown sample kind")
        field = "reference" if row["kind"] == "retrieval" else "response"
        if not isinstance(sample.get(field), str):
            raise ValueError("Missing reference/response")
    return rows


def metric_inputs(row):
    sample = row["sample"]
    if not sample["retrieved_contexts"]:
        return [], "empty_context"
    if row["kind"] == "retrieval":
        if not sample["reference"].strip():
            return [], "no_reference_evidence"
        return [
            (name, {key: sample[key] for key in ("user_input", "reference", "retrieved_contexts")})
            for name in ("context_precision", "context_recall")
        ], None
    if not sample["response"].strip():
        return [], "empty_response"
    return [
        (
            "faithfulness",
            {key: sample[key] for key in ("user_input", "response", "retrieved_contexts")},
        )
    ], None


def summarize(rows):
    groups = {}
    for row in rows:
        for metric, result in row.get("metrics", {}).items():
            key = f"{row['kind']}:{row['mode']}:{metric}"
            group = groups.setdefault(key, {"values": [], "errors": 0})
            if result["status"] == "ok":
                group["values"].append(result["value"])
            else:
                group["errors"] += 1
    return {
        key: {
            "scored": len(g["values"]),
            "errors": g["errors"],
            "mean": mean(g["values"]) if g["values"] else None,
        }
        for key, g in groups.items()
    }


async def score_rows(rows, metrics, output, report, *, metric_timeout=180, on_metric=None):
    """Sequential, bounded scoring. Failures/NaN are missing measurements, never zero."""
    for sample in rows:
        row = {key: sample[key] for key in ("case_id", "kind", "mode")}
        row["metrics"] = {}
        inputs, skip = metric_inputs(sample)
        if skip:
            row["skipped"] = skip
        for name, kwargs in inputs:
            if on_metric:
                on_metric(row["case_id"], name)
            started = perf_counter()
            try:
                result = await asyncio.wait_for(metrics[name].ascore(**kwargs), metric_timeout)
                value = result.value
                if (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or not 0 <= value <= 1
                ):
                    raise ValueError("Invalid metric score")
                measured = {"status": "ok", "value": value, "reason": result.reason}
                if sample["kind"] == "calibration":
                    measured.update(
                        expected=sample["expected"],
                        absolute_error=abs(value - sample["expected"]),
                        agrees=abs(value - sample["expected"]) <= sample["tolerance"],
                    )
            except Exception as exc:
                measured = {"status": "error", "value": None, "error": type(exc).__name__}
            measured["seconds"] = round(perf_counter() - started, 4)
            row["metrics"][name] = measured
        report["rows"].append(row)
        report["summary"] = summarize(report["rows"])
        write_report(output, report)
        print(f"ragas {row['kind']} {row['case_id']} {'skipped' if skip else 'done'}", flush=True)
    return report


async def run(args):
    # Disable RAGAS telemetry before importing its package. No production imports depend on it.
    os.environ["RAGAS_DO_NOT_TRACK"] = "true"
    try:
        from openai import AsyncOpenAI
        from ragas.llms import llm_factory
        from ragas.metrics.collections import (
            ContextPrecisionWithReference,
            ContextRecall,
            Faithfulness,
        )
    except ImportError as exc:
        raise SystemExit(
            "Install the locked evaluation dependency group in the evaluation venv"
        ) from exc
    rows = read_samples(args.samples, args.calibration)
    if args.case_id:
        selected = set(args.case_id)
        if selected - {r["case_id"] for r in rows}:
            raise ValueError("Unknown case selection")
        rows = [r for r in rows if r["case_id"] in selected]
    if args.model == settings.llm_model and not args.allow_self_judge:
        raise ValueError("Same generation model: explicitly opt in with --allow-self-judge")
    api_key = os.getenv("RAGAS_JUDGE_API_KEY")
    if args.backend == "openai" and not api_key:
        raise ValueError(
            "Set RAGAS_JUDGE_API_KEY in the process environment; never put it in a report"
        )
    generation = {"temperature": 0, "max_tokens": 2048, "seed": 42}
    extra_body = {"seed": 42}
    if args.backend == "llama-cpp":
        extra_body["chat_template_kwargs"] = {"enable_thinking": False}
        generation["enable_thinking"] = False
    calls = []
    import httpx

    async def observe(response):
        if not response.request.url.path.endswith("/chat/completions"):
            return
        await response.aread()
        record = {
            "status_code": response.status_code,
            "metric": current["metric"],
            "case_id": current["case_id"],
        }
        try:
            request = json.loads(response.request.content)
            record["prompt_sha256"] = hashlib.sha256(
                json.dumps(request.get("messages"), ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            body = response.json()
            record.update(
                model=body.get("model"), usage=body.get("usage"), choices=body.get("choices")
            )
            if body.get("model") != args.model:
                raise ValueError("Judge returned an unexpected model alias")
        finally:
            calls.append(record)

    current = {"metric": None, "case_id": None}
    digest = hashlib.sha256(args.samples.read_bytes()).hexdigest()
    runner_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    lock_digest = hashlib.sha256((ROOT / "backend/uv.lock").read_bytes()).hexdigest()
    report = {
        "schema_version": 1,
        "completed": False,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "input_sha256": digest,
        "runner_sha256": runner_digest,
        "lock_sha256": lock_digest,
        "python": platform.python_version(),
        "judge": {
            "model": args.model,
            "backend": args.backend,
            "identity_note": args.judge_identity,
            "same_model_as_generator": args.model == settings.llm_model,
            "identity_verification": "API alias checked; weight identity supplied separately",
        },
        "ragas_version": importlib.metadata.version("ragas"),
        "openai_client_version": importlib.metadata.version("openai"),
        "generation": generation,
        "metric_timeout_seconds": args.metric_timeout,
        "http_timeout_seconds": args.http_timeout,
        "rows": [],
        "http_calls": calls,
        "scope": "Authored calibration controls"
        if args.calibration
        else "Saved public RAG samples",
    }
    runtime_path = ROOT / "data/judge-runtime.json"
    if args.backend == "llama-cpp" and runtime_path.exists():
        runtime = json.loads(runtime_path.read_text("utf-8"))
        if runtime["model"] == args.model:
            report["judge"]["runtime_manifest"] = runtime
    # Explicit URL/key only: never fall back to an account's default cloud endpoint.
    async with httpx.AsyncClient(
        timeout=args.http_timeout, event_hooks={"response": [observe]}
    ) as transport:
        async with AsyncOpenAI(
            base_url=args.base_url,
            api_key=api_key or "local",
            max_retries=0,
            http_client=transport,
        ) as client:
            models = await client.models.list()
            if args.model not in {m.id for m in models.data}:
                raise ValueError("Requested judge model not advertised by server")
            if args.backend == "llama-cpp":
                props_url = args.base_url.removesuffix("/v1").rstrip("/") + "/props"
                props = await transport.get(props_url)
                props.raise_for_status()
                body = props.json()
                report["judge"]["server_settings"] = {
                    key: body.get(key) for key in ("default_generation_settings", "total_slots")
                }
            llm = llm_factory(
                args.model,
                client=client,
                temperature=0,
                max_tokens=2048,
                max_retries=2,
                extra_body=extra_body,
            )
            metrics = {
                "faithfulness": Faithfulness(llm=llm),
                "context_precision": ContextPrecisionWithReference(llm=llm),
                "context_recall": ContextRecall(llm=llm),
            }

            def on_metric(case_id, name):
                current.update(case_id=case_id, metric=name)

            await score_rows(
                rows,
                metrics,
                args.output,
                report,
                metric_timeout=args.metric_timeout,
                on_metric=on_metric,
            )
    report.update(
        completed=True,
        finished_at=datetime.now(timezone.utc).isoformat(),
        inputs_unchanged=(
            digest == hashlib.sha256(args.samples.read_bytes()).hexdigest()
            and runner_digest == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            and lock_digest == hashlib.sha256((ROOT / "backend/uv.lock").read_bytes()).hexdigest()
        ),
    )
    write_report(args.output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--base-url", required=True, help="Explicit compatible endpoint, including /v1"
    )
    parser.add_argument("--model", required=True, help="Judge model alias; no default model")
    parser.add_argument("--backend", choices=("llama-cpp", "openai"), default="llama-cpp")
    parser.add_argument(
        "--judge-identity", required=True, help="Revision/checksum or an honest unknown label"
    )
    parser.add_argument("--calibration", action="store_true")
    parser.add_argument("--allow-self-judge", action="store_true")
    parser.add_argument("--case-id", action="append")
    parser.add_argument(
        "--http-timeout", type=int, choices=range(1, 601), default=360, metavar="SECONDS"
    )
    parser.add_argument(
        "--metric-timeout", type=int, choices=range(1, 901), default=720, metavar="SECONDS"
    )
    args = parser.parse_args()
    report = asyncio.run(run(args))
    if not report["inputs_unchanged"] or any(g["errors"] for g in report["summary"].values()):
        raise SystemExit(1)
    if args.calibration and any(
        not r.get("metrics", {}).get("faithfulness", {}).get("agrees", False)
        for r in report["rows"]
    ):
        raise SystemExit(2)
