import asyncio
import json
from types import SimpleNamespace

import pytest

from app.core.config import ROOT
from app.evaluation.rag_retrieval import grade_search, load_dataset, rank_metrics, summarize_search
from app.evaluation.ragas_runner import metric_inputs, read_samples, score_rows, summarize


def test_hand_calculated_ranking_and_fixed_precision_denominator():
    # Gains 1, 3, 0; ideal gains 3, 1. This checks graded ordering, not just a hit count.
    import math

    result = rank_metrics(["partial", "direct", "noise"], {"direct": 2, "partial": 1}, 3)
    assert result["precision"] == pytest.approx(2 / 3)
    assert result["recall"] == 1
    assert result["ndcg"] == pytest.approx((1 + 3 / math.log2(3)) / (3 + 1 / math.log2(3)))
    assert rank_metrics([], {"direct": 2}, 3)["precision"] == 0
    assert rank_metrics(["direct"], {"direct": 2}, 3)["precision"] == pytest.approx(1 / 3)
    assert rank_metrics(["noise", "direct"], {"direct": 2}, 3)["mrr"] == 0.5


def test_no_reference_has_no_fabricated_perfect_recall():
    assert all(value is None for value in rank_metrics(["noise"], {}, 3).values())
    with pytest.raises(ValueError):
        rank_metrics(["same", "same"], {"same": 2}, 3)


def test_frozen_labels_are_resolvable_and_no_evidence_cases_are_explicit():
    corpus, snapshot, _ = load_dataset(ROOT / "data/evaluation/rag-queries.json")
    assert len(corpus["cases"]) == 40
    assert len(snapshot["pois"]) == 13
    assert sum(not c["relevance"] for c in corpus["cases"]) == 8
    assert all(p["eligible"] for p in snapshot["pois"])


def test_thresholded_noise_is_counted_separately_from_category_clarification():
    case = {"relevance": {}, "expected_category": None}
    output = {
        "matches": [{"place_id": "noise", "score": 0.7}],
        "document_threshold": 0.4,
        "category": None,
    }
    grade = grade_search(case, output)
    assert grade["category_correct"]
    assert grade["context_returned_without_evidence"]
    summary = summarize_search([{"grade": grade, "seconds": 1}, {"error": "Timeout", "seconds": 2}])
    assert summary["errors"] == 1 and summary["no_evidence_queries"] == 1
    assert summary["macro_ranking"]["3"]["recall"] is None


def sample(kind="explanation", mode="llm"):
    return {
        "case_id": "public",
        "kind": kind,
        "mode": mode,
        "sample": {"user_input": "Почему?", "response": "Ответ", "retrieved_contexts": ["Факт"]},
    }


def test_ragas_inputs_never_score_empty_evidence_as_perfect():
    row = sample("retrieval")
    row["sample"]["reference"] = ""
    assert metric_inputs(row) == ([], "no_reference_evidence")
    row["sample"]["reference"] = "Эталон"
    inputs, skip = metric_inputs(row)
    assert skip is None and {name for name, _ in inputs} == {"context_precision", "context_recall"}
    assert all("response" not in kwargs for _, kwargs in inputs)
    row["sample"]["retrieved_contexts"] = []
    assert metric_inputs(row) == ([], "empty_context")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1, True])
def test_bad_judge_measurements_are_errors_not_zero(tmp_path, value):
    class Metric:
        async def ascore(self, **kwargs):
            return SimpleNamespace(value=value, reason=None)

    report = {"rows": []}
    asyncio.run(
        score_rows([sample()], {"faithfulness": Metric()}, tmp_path / "report.json", report)
    )
    group = report["summary"]["explanation:llm:faithfulness"]
    assert group == {"scored": 0, "errors": 1, "mean": None}
    assert report["rows"][0]["metrics"]["faithfulness"]["value"] is None


def test_metric_timeout_is_bounded_and_does_not_abort_other_samples(tmp_path):
    class Slow:
        async def ascore(self, **kwargs):
            await asyncio.sleep(1)

    report = {"rows": []}
    asyncio.run(
        score_rows(
            [sample()], {"faithfulness": Slow()}, tmp_path / "r.json", report, metric_timeout=0.01
        )
    )
    assert report["rows"][0]["metrics"]["faithfulness"]["error"] == "TimeoutError"


def test_llm_and_template_averages_are_separate():
    rows = [
        {**sample(mode=mode), "metrics": {"faithfulness": {"status": "ok", "value": score}}}
        for mode, score in (("llm", 0), ("template", 1))
    ]
    assert summarize(rows)["explanation:llm:faithfulness"]["mean"] == 0
    assert summarize(rows)["explanation:template:faithfulness"]["mean"] == 1


def test_calibration_controls_are_read_without_a_model():
    rows = read_samples(ROOT / "data/evaluation/ragas-calibration.json", calibration=True)
    assert len(rows) == 8
    assert {r["expected"] for r in rows} == {0, 0.5, 1}


def test_duplicate_sample_ids_are_rejected(tmp_path):
    path = tmp_path / "samples.jsonl"
    path.write_text((json.dumps(sample()) + "\n") * 2, "utf-8")
    with pytest.raises(ValueError):
        read_samples(path)
