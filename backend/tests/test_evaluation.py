"""Regression checks for measurement errors: missing outputs, guard fallback, repeats and traces."""

import json

import httpx
import pytest

from app.evaluation.explanations import evaluate_fixture, fixtures
from app.evaluation.observer import ObservedLlama
from app.evaluation.runner import read_corpus, run
from app.evaluation.scoring import grade, latency, summarize
from app.schemas.assistant import ExtractedTrip, ParseOutput, Question


def case():
    return {
        "id": "unit-case",
        "text": "Анна едет в Лиссабон 27 октября 2026, общий бюджет 100 евро.",
        "members": ["Анна"],
        "expected": {"group_budget": 10000, "draft.destination": "Lisbon"},
        "questions_required": [],
        "questions_allowed": [],
    }


def output():
    return ParseOutput.model_validate_json(
        json.dumps(
            {
                "draft": {},
                "group_budget": 10000,
                "questions": [],
                "warnings": [],
                "model": "fake",
                "prompt_version": "unit",
                "reference_date": "2026-10-24",
            }
        )
    )


def test_missing_output_never_earns_credit_for_expected_nulls():
    c = case()
    c["expected"] = {"group_budget": None, "draft.destination": None}
    assert not any(check["passed"] for check in grade(c))
    row = {"passed": False, "error": "LlmUnavailable", "checks": grade(c), "seconds": 60}
    result = summarize([row])
    assert result["errors"] == result["runs"] == 1
    assert result["per_field"]["group_budget"]["accuracy"] == 0
    assert result["latency_all"]["p95_seconds"] == 60
    assert result["latency_successful"]["n"] == 0


def test_extra_questions_fail_a_complete_case_and_constraints_ignore_case_and_order():
    c, result = case(), output()
    result.questions = [Question(field="budget", text="Unexpected clarification")]
    checks = grade(c, result.model_dump(mode="json"))
    assert not next(check for check in checks if check["field"] == "questions")["passed"]
    c["expected"] = {"draft.mandatory_names": ["Carmo", "Nicolau"]}
    result.draft.mandatory_names = ["nicolau", "CARMO"]
    assert grade(c, result.model_dump(mode="json"))[0]["passed"]


def test_latency_median_and_nearest_rank_p95():
    assert latency([{"seconds": 1}, {"seconds": 3}])["median_seconds"] == 2
    assert latency([{"seconds": i} for i in range(1, 21)])["p95_seconds"] == 19


def test_corpus_rejects_unknown_checks_and_duplicate_ids(tmp_path):
    path = tmp_path / "cases.json"
    body = {"version": "unit", "reference_date": "2026-10-24", "cases": [case()]}
    body["cases"][0]["expected"] = {"draft.nonexistent": None}
    path.write_text(json.dumps(body), "utf-8")
    with pytest.raises(ValueError, match="Unknown expected field"):
        read_corpus(path)
    body["cases"] = [case(), case()]
    path.write_text(json.dumps(body), "utf-8")
    with pytest.raises(ValueError, match="unique IDs"):
        read_corpus(path)


def test_observer_separates_schema_repair_from_success(monkeypatch):
    contents = iter(["not json", "{}"])
    original_client = httpx.Client

    def handle(request):
        return httpx.Response(
            200,
            json={
                "model": "fake",
                "choices": [{"finish_reason": "stop", "message": {"content": next(contents)}}],
            },
        )

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    observer = ObservedLlama()
    observer.structured("unit", {}, ExtractedTrip)
    assert [a["schema_valid"] for a in observer.attempts] == [False, True]


def test_fallback_is_grounded_but_not_counted_as_llm_composition():
    class InvalidModel:
        def structured(self, *args):
            raise ValueError("Synthetic invalid generation")

    result = evaluate_fixture(fixtures()[1], InvalidModel())
    assert result["passed"] and result["output"]["mode"] == "template"
    assert "unvisited" not in {s["place_id"] for s in result["output"]["sources"].values()}
    assert all(
        p in result["ragas_sample"]["retrieved_contexts"] for p in result["output"]["paragraphs"]
    )


def test_runner_keeps_failure_denominators_and_writes_completed_report(tmp_path, monkeypatch):
    class Unavailable:
        attempts = []

        def structured(self, *args):
            raise ValueError("Synthetic failure")

    monkeypatch.setattr("app.evaluation.runner.ObservedLlama", Unavailable)
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps({"version": "unit", "reference_date": "2026-10-24", "cases": [case()]}), "utf-8"
    )
    report_path, export_path = tmp_path / "report.json", tmp_path / "export.jsonl"
    report = run(path, report_path, export_path, repeat=2, skip_explanations=True)
    assert report["completed"] and report["inputs_unchanged"]
    assert report["parser_summary"]["runs"] == report["parser_summary"]["errors"] == 2
    assert report["parser_summary"]["passed"] == 0
    assert json.loads(report_path.read_text("utf-8")) == report
    assert report["ragas"]["scores_computed"] is False
    assert all(r["passed"] for r in report["fault_controls"])
