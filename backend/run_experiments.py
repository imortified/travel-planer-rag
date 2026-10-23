"""Paired, auditable offline comparison. Run --help; no HTTP, DB or LLM in measurements."""

import argparse
import csv
import hashlib
import json
import os
import platform
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from statistics import mean, median
from time import perf_counter

import ortools

from app.planning.benchmark import GENERATOR_VERSION, generated, suite
from app.planning.cp_sat import cp_sat_plan
from app.planning.objective import VERSION, Objective, Weights
from app.planning.period import plan_period, validate_period
from app.planning.service import checked_plan


def run_one(instance, algorithm, weights, time_limit, beam_width):
    request, inputs = instance.request, instance.inputs
    before = perf_counter()
    objective = Objective(request, inputs, weights)
    if algorithm == "cp-sat":
        result = cp_sat_plan(request, inputs, objective, time_limit)
    elif len(inputs) > 1:
        result = plan_period(request, inputs, algorithm, beam_width, objective)
    else:
        result = checked_plan(
            request, inputs[0].places, inputs[0].travel, algorithm, beam_width, objective
        )
    elapsed = perf_counter() - before
    schedule = [d.visits for d in result.days] if result.days is not None else [result.visits]
    validation, _ = validate_period(request, schedule, inputs, result.total_cost)
    if result.is_feasible and not validation.is_valid:
        raise RuntimeError(f"Invalid purported solution: {instance.name}/{algorithm}")
    score = result.objective
    return {
        "algorithm": algorithm,
        "elapsed_seconds": elapsed,
        "feasible": result.is_feasible,
        "validation_valid": validation.is_valid,
        "objective": score.value if score else None,
        "reward": score.reward if score else None,
        "cost_ratio": score.cost_ratio if score else None,
        "fairness_gap": score.fairness_gap if score else None,
        "travel_minutes": score.travel_minutes if score else None,
        "total_cost": result.total_cost,
        "visit_count": len(result.visits),
        "solver": asdict(result.solver) if result.solver else None,
        "reasons": result.reasons,
        "schedule": [[asdict(v) for v in visits] for visits in schedule],
    }


def summarize(rows):
    summary = []
    for algorithm in ("greedy", "beam", "cp-sat"):
        selected = [r for r in rows if r["algorithm"] == algorithm]
        feasible = [r for r in selected if r["feasible"]]
        times = sorted(r["elapsed_seconds"] for r in selected)
        summary.append(
            {
                "algorithm": algorithm,
                "runs": len(selected),
                "feasible": len(feasible),
                "median_seconds": median(times),
                "p95_seconds": times[ceil(len(times) * 0.95) - 1],
                "integer_optimal": sum(
                    bool(r["solver"] and r["solver"]["integer_model_optimal"]) for r in selected
                ),
                "mean_absolute_gap_bound": mean(
                    r["absolute_gap_to_upper_bound"]
                    for r in feasible
                    if r["absolute_gap_to_upper_bound"] is not None
                )
                if any(r.get("absolute_gap_to_upper_bound") is not None for r in feasible)
                else None,
            }
        )
    return summary


def attach_bounds(rows):
    def group(row):
        return row["instance"], row["policy"], row.get("sensitivity", False)

    keys = {group(r) for r in rows}
    for key in keys:
        paired = [r for r in rows if group(r) == key]
        valid = [r for r in paired if r["feasible"]]
        upper = [
            r["solver"]["original_upper_bound"]
            for r in paired
            if r["solver"] and r["solver"]["original_upper_bound"] is not None
        ]
        best = max((r["objective"] for r in valid), default=None)
        bound = min(upper) if upper else None
        if valid and any(r["solver"] and r["solver"]["status"] == "INFEASIBLE" for r in paired):
            raise RuntimeError("A feasible route contradicts a CP-SAT infeasibility proof")
        if bound is not None and best > bound + 1e-8:
            raise RuntimeError("A feasible route exceeds the certified objective upper bound")
        for r in paired:
            r["best_found_objective"] = best
            r["original_upper_bound"] = bound
            r["regret_to_best_found"] = max(0.0, best - r["objective"]) if r["feasible"] else None
            r["absolute_gap_to_upper_bound"] = (
                max(0.0, bound - r["objective"]) if (r["feasible"] and bound is not None) else None
            )


def markdown(report):
    lines = [
        "# CP-SAT и расширенное сравнение",
        "",
        "Автоматический отчёт `run_experiments.py`. Методика: [cp-sat.md](cp-sat.md).",
        "",
        "## Основной набор",
        "",
        "Время включает подготовку, поиск и валидацию; без БД, сети и LLM.",
        "Медианы ниже объединяют разные задачи; подробные парные результаты — в JSON/CSV.",
        "",
        "| Метод | Запусков | Допустимых | Медиана, с | P95, с | OPTIMAL целочисленной модели |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in report["summary"]:
        lines.append(
            f"| {r['algorithm']} | {r['runs']} | {r['feasible']} | "
            f"{r['median_seconds']:.4f} | {r['p95_seconds']:.4f} | {r['integer_optimal']} |"
        )
    lines += [
        "",
        "## Парное качество на сгенерированных задачах",
        "",
        "F сравнивается внутри одной задачи и политики, затем агрегируется абсолютный разрыв.",
        "Разрыв до лучшего найденного не является разрывом до доказанного оптимума.",
        "Для таблицы ниже взяты общие задачи с решением у всех трёх методов;",
        "качество каждого метода — его лучший допустимый результат среди повторов.",
        "",
        "| Метод | Общих задач | Средний разрыв до лучшего F | Граница разрыва до оптимума F |",
        "| --- | ---: | ---: | ---: |",
    ]
    base = [
        r
        for r in report["runs"]
        if r["policy"] == "default" and r["family"] != "control" and not r["sensitivity"]
    ]
    common = [
        name
        for name in sorted({r["instance"] for r in base})
        if all(
            any(r["instance"] == name and r["algorithm"] == a and r["feasible"] for r in base)
            for a in ("greedy", "beam", "cp-sat")
        )
    ]
    for algorithm in ("greedy", "beam", "cp-sat"):
        selected = [
            max(
                (
                    r
                    for r in base
                    if r["instance"] == name and r["algorithm"] == algorithm and r["feasible"]
                ),
                key=lambda r: r["objective"],
            )
            for name in common
        ]
        gaps = [
            r["absolute_gap_to_upper_bound"]
            for r in selected
            if r["absolute_gap_to_upper_bound"] is not None
        ]
        lines.append(
            f"| {algorithm} | {len(common)} | "
            + (f"{mean(r['regret_to_best_found'] for r in selected):.6f}" if selected else "—")
            + " | "
            + (f"{mean(gaps):.6f}" if gaps else "—")
            + " |"
        )
    lines += [
        "",
        "## Разрез по размеру, дням и интересам",
        "",
        "Средние разрывы только успешных запусков; n указано в каждой ячейке.",
        "При разных n состав сравниваемых задач различается.",
        "| Мест | Дней | Интересы | Greedy: средний разрыв до лучшего | Beam | CP-SAT |",
        "| ---: | ---: | --- | ---: | ---: | ---: |",
    ]
    groups = sorted(
        {
            (r["size"], r["days"], r["family"])
            for r in report["runs"]
            if r["policy"] == "default" and r["family"] != "control"
        }
    )
    for size, days, family in groups:
        values = []
        for algorithm in ("greedy", "beam", "cp-sat"):
            selected = [
                r
                for r in report["runs"]
                if (r["size"], r["days"], r["family"], r["policy"], r["algorithm"])
                == (size, days, family, "default", algorithm)
                and r["feasible"]
                and not r["sensitivity"]
            ]
            values.append(
                f"{mean(r['regret_to_best_found'] for r in selected):.6f} (n={len(selected)})"
                if selected
                else "—"
            )
        lines.append(f"| {size} | {days} | {family} | " + " | ".join(values) + " |")
    lines += [
        "",
        "## Чувствительность коэффициентов",
        "",
        "Парные данные по тем же исходным задачам. Сырые F между политиками не сравниваются.",
        "",
        "| Политика | Метод | Средний C | Переезды, мин | Средний P | Допустимых запусков |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for policy in report["policies"]:
        for algorithm in ("greedy", "beam", "cp-sat"):
            selected = [
                r
                for r in report["runs"]
                if r["sensitivity"]
                and r["policy"] == policy
                and r["algorithm"] == algorithm
                and r["feasible"]
            ]
            if selected:
                lines.append(
                    f"| {policy} | {algorithm} | {mean(r['cost_ratio'] for r in selected):.4f} | "
                    f"{mean(r['travel_minutes'] for r in selected):.2f} | "
                    f"{mean(r['fairness_gap'] for r in selected):.4f} | {len(selected)} |"
                )
    lines += [
        "",
        "Все допустимые маршруты независимо проверены. UNKNOWN не означает невозможность.",
        "OPTIMAL относится к масштабированной целочисленной цели; граница ошибки и верхняя",
        "граница исходной F сохранены для каждого запуска. Равенства разрешаются методами",
        "по-разному; CP-SAT не доказывает оптимальность дополнительных правил ничьей.",
        "",
        "Набор синтетический, с двумя seed на конфигурацию. Результаты не являются",
        "оценкой качества на всём городе или доказательством превосходства одного метода.",
        "",
    ]
    return "\n".join(lines)


def write_report(report, path):
    """Recompute paired summaries and export without rerunning the measured searches."""
    runs = report["runs"]
    attach_bounds(runs)
    report["summary"] = summarize([r for r in runs if not r["sensitivity"]])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    path.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    fields = [
        "instance",
        "policy",
        "repetition",
        "algorithm",
        "sensitivity",
        "size",
        "candidate_count",
        "days",
        "family",
        "elapsed_seconds",
        "feasible",
        "objective",
        "reward",
        "cost_ratio",
        "travel_minutes",
        "fairness_gap",
        "visit_count",
        "total_cost",
        "regret_to_best_found",
        "original_upper_bound",
        "absolute_gap_to_upper_bound",
    ]
    with path.with_suffix(".csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(runs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, default=[12, 30, 60])
    parser.add_argument("--seeds", nargs="+", type=int, default=[17, 43])
    parser.add_argument("--days", nargs="+", type=int, default=[1, 3])
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--time-limit", type=float, default=2.0)
    parser.add_argument("--beam-width", type=int, default=32)
    parser.add_argument("--skip-sensitivity", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("../docs/cp-sat-experiments.json"))
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 10 or not 0.05 <= args.time_limit <= 60:
        parser.error("Use 1-10 repetitions and 0.05-60 seconds")
    instances = list(suite(args.sizes, args.seeds, args.days))
    policies = {
        "default": Weights(),
        "no_cost": Weights(cost=0),
        "no_travel": Weights(travel=0),
        "no_fairness": Weights(fairness=0),
        "double_fairness": Weights(fairness=0.7),
    }
    jobs = [(i, "default", False) for i in instances]
    if not args.skip_sensitivity:
        # Fixed paired subset, not selected after seeing optimizer results.
        sensitivity = [
            generated(n, d, True, args.seeds[0]) for n in args.sizes[:2] for d in args.days
        ]
        jobs += [(i, policy, True) for i in sensitivity for policy in policies]
    runs, manifest = [], {}
    for index, (instance, policy, sensitivity) in enumerate(jobs, 1):
        manifest[instance.name] = {
            "sha256": instance.digest(),
            "family": instance.family,
            "seed": instance.seed,
            "candidates": len({p.id for d in instance.inputs for p in d.places}),
            "nominal_size": instance.nominal_size,
            "days": len(instance.inputs),
            "participants": len(instance.request.participants),
        }
        for repetition in range(args.repetitions):
            for algorithm in ("greedy", "beam", "cp-sat"):
                row = run_one(
                    instance, algorithm, policies[policy], args.time_limit, args.beam_width
                )
                row.update(
                    instance=instance.name,
                    policy=policy,
                    repetition=repetition,
                    size=instance.nominal_size or manifest[instance.name]["candidates"],
                    candidate_count=manifest[instance.name]["candidates"],
                    days=len(instance.inputs),
                    family=instance.family,
                    sensitivity=sensitivity,
                )
                runs.append(row)
                print(
                    f"{index}/{len(jobs)} {instance.name} {policy} r{repetition} {algorithm} "
                    f"F={row['objective']} {row['elapsed_seconds']:.3f}s "
                    f"{row['solver']['status'] if row['solver'] else ''}",
                    flush=True,
                )
    attach_bounds(runs)
    source = b"".join(p.read_bytes() for p in sorted(Path("app/planning").glob("*.py")))
    report = {
        "generator_version": GENERATOR_VERSION,
        "objective_version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
            "ortools": ortools.__version__,
            "planning_code_sha256": hashlib.sha256(source).hexdigest(),
        },
        "configuration": {k: v for k, v in vars(args).items() if k != "output"},
        "policies": {name: asdict(weights) for name, weights in policies.items()},
        "instances": manifest,
        "runs": runs,
        "summary": summarize([r for r in runs if not r["sensitivity"]]),
    }
    write_report(report, args.output)
    print(f"Saved {len(runs)} validated runs: {args.output}", flush=True)


if __name__ == "__main__":
    main()
