import pytest

from app.planning.benchmark import generated, suite
from app.planning.objective import Weights
from run_experiments import attach_bounds, run_one, summarize


def test_generated_instances_are_reproducible_and_profiles_and_hashes_differ():
    first = generated(12, 3, True, 17)
    assert first.payload() == generated(12, 3, True, 17).payload()
    assert first.digest() == generated(12, 3, True, 17).digest()
    assert first.digest() != generated(12, 3, True, 43).digest()
    assert first.digest() != generated(12, 3, False, 17).digest()
    assert len(list(suite([12], [17], [1]))) == 8


def test_benchmark_revalidates_routes_and_reports_paired_bounds():
    instance = generated(12, 1, True, 17)
    rows = []
    for algorithm in ("greedy", "beam", "cp-sat"):
        row = run_one(instance, algorithm, Weights(), 1, 32)
        row.update(instance=instance.name, policy="default")
        rows.append(row)
    attach_bounds(rows)
    assert all(r["feasible"] and r["validation_valid"] for r in rows)
    assert all(r["original_upper_bound"] >= r["objective"] - 1e-9 for r in rows)
    assert all(r["regret_to_best_found"] >= 0 for r in rows)
    assert len(summarize(rows)) == 3


def test_sensitivity_runs_do_not_supply_extra_attempts_to_primary_quality():
    rows = [
        {
            "instance": "same",
            "policy": "default",
            "sensitivity": sensitivity,
            "algorithm": "greedy",
            "feasible": True,
            "objective": score,
            "solver": None,
        }
        for sensitivity, score in [(False, 0.2), (True, 0.9)]
    ]
    attach_bounds(rows)
    assert rows[0]["best_found_objective"] == 0.2
    assert rows[0]["regret_to_best_found"] == 0


def test_experiment_refuses_a_bound_contradicting_a_valid_route():
    rows = [
        {
            "instance": "same",
            "policy": "default",
            "feasible": True,
            "objective": 0.8,
            "solver": {"status": "FEASIBLE", "original_upper_bound": 0.5},
        }
    ]
    with pytest.raises(RuntimeError, match="exceeds"):
        attach_bounds(rows)
