"""Offline replay of anonymized POI snapshots. No database, HTTP or LLM calls."""

import argparse
import hashlib
import json
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path
from time import perf_counter

from app.evaluation.runner import write_report
from app.planning.models import DayInput, Participant, Place, Request
from app.planning.pace import PROFILES, planning_summary
from app.planning.period import plan_period


def load_fixture(path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload["version"] != "pace-replay-v1":
        raise ValueError("Unsupported replay fixture")
    spec = payload["request"]
    request = Request(
        [Participant(**p) for p in spec["participants"]],
        spec["budget"],
        spec["start"],
        spec["end"],
        frozenset(spec["mandatory"]),
        frozenset(spec["excluded"]),
    )
    inputs = [
        DayInput(
            date.fromisoformat(day["travel_date"]),
            [Place(**p) for p in day["places"]],
            {(t["from"], t["to"]): t["minutes"] for t in day["travel"]},
            {"eligible_count": day["eligible_count"]},
        )
        for day in payload["days"]
    ]
    return request, inputs, payload["saved_baseline"]


def evaluate(path, time_limit=5.0):
    request, inputs, saved = load_fixture(path)
    rows = []
    for mode, policy in {"unrestricted": None, **PROFILES}.items():
        paced = replace(request, pace=policy)
        for algorithm in ("greedy", "beam", "cp-sat"):
            started = perf_counter()
            result = plan_period(paced, inputs, algorithm, time_limit=time_limit)
            summary = planning_summary(paced, result, inputs)
            rows.append(
                {
                    "pace": mode,
                    "settings": asdict(policy) if policy else None,
                    "algorithm": algorithm,
                    "elapsed_seconds": perf_counter() - started,
                    "is_feasible": result.is_feasible,
                    "total_cost": result.total_cost,
                    "active_days": summary["active_days"],
                    "visits": len(result.visits),
                    "daily_visits": [d["visit_count"] for d in summary["days"]],
                    "daily_active_minutes": [d["active_minutes"] for d in summary["days"]],
                    "candidate_count": summary["candidate_count"],
                    "coverage_upper_bound": summary["coverage_upper_bound"],
                    "warnings": summary["warnings"],
                    "objective": asdict(result.objective) if result.objective else None,
                    "solver": asdict(result.solver) if result.solver else None,
                    "validation": asdict(result.validation),
                }
            )
            print(
                f"{mode:12} {algorithm:6} feasible={result.is_feasible} "
                f"days={summary['active_days']} visits={len(result.visits)} "
                f"cost={result.total_cost}",
                flush=True,
            )
    return {
        "version": "pace-replay-v1",
        "fixture_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "cp_sat_time_limit_seconds": time_limit,
        "beam_width": 32,
        "saved_baseline": saved,
        "note": "Single fixed snapshot; no general quality conclusions. "
        "Compare scores within one pace only.",
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--time-limit", type=float, default=5.0)
    args = parser.parse_args()
    write_report(args.output, evaluate(args.input, args.time_limit))


if __name__ == "__main__":
    main()
