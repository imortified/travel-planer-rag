"""Run from backend: python compare_algorithms.py. No DB or network required."""

import json
from pathlib import Path
from statistics import median
from time import perf_counter

from app.planning.beam import route_key
from app.planning.scenarios import scenarios
from app.planning.service import checked_plan


def main():
    rows = []
    for name, request, places, travel in scenarios():
        for algorithm in ("greedy", "beam"):
            times = []
            for _ in range(20):
                before = perf_counter()
                result = checked_plan(request, places, travel, algorithm)
                times.append((perf_counter() - before) * 1000)
            rows.append(
                {
                    "scenario": name,
                    "algorithm": algorithm,
                    "feasible": result.is_feasible,
                    "utility": -route_key(request, places, result)[2],
                    "cost": result.total_cost,
                    "route": [v.place_id for v in result.visits],
                    "median_ms": round(median(times), 4),
                }
            )
    path = Path(__file__).resolve().parents[1] / "docs" / "algorithm-comparison.json"
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print("scenario | algorithm | feasible | utility | cost | median ms")
    for row in rows:
        print(
            " | ".join(
                str(row[k])
                for k in ("scenario", "algorithm", "feasible", "utility", "cost", "median_ms")
            )
        )
    print(f"Saved: {path}")


if __name__ == "__main__":
    main()
