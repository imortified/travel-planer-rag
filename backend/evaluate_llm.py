"""Opt-in live acceptance checks; run only with the selected llama-server loaded."""

import json
from datetime import date
from pathlib import Path
from time import monotonic

from app.core.logging import configure_logging
from app.nlp.parser import parse_request
from app.schemas.assistant import ParseInput

CASES = [
    {
        "id": "complete-group",
        "text": (
            "Анна и Максим едут в Лиссабон с 23 по 25 октября 2026. "
            "Общий бюджет 100 евро. Любим историю и природу. Каждый день с 9 до 18."
        ),
        "members": [],
        "expected_budget": 10000,
        "expected_start": "2026-10-23",
        "expected_end": "2026-10-25",
        "expected_questions": [],
    },
    {
        "id": "per-person",
        "text": "Едем вдвоём в Лиссабон 24 октября 2026. Бюджет 50 евро на человека.",
        "members": ["Анна", "Максим"],
        "expected_budget": 10000,
        "expected_start": "2026-10-24",
        "expected_end": None,
        "expected_questions": [],
    },
    {
        "id": "missing-budget",
        "text": "Хочу в Лиссабон 24 октября 2026, интересует история.",
        "members": ["Анна"],
        "expected_budget": None,
        "expected_start": "2026-10-24",
        "expected_end": None,
        "expected_questions": ["budget"],
    },
    {
        "id": "ambiguous-scope",
        "text": "Едем в Лиссабон 24 октября 2026. Бюджет 100 евро.",
        "members": ["Анна", "Максим"],
        "expected_budget": None,
        "expected_start": "2026-10-24",
        "expected_end": None,
        "expected_questions": ["budget_scope"],
    },
    {
        "id": "unsupported-currency",
        "text": "Хочу в Лиссабон 24 октября 2026, общий бюджет 100 долларов.",
        "members": ["Анна"],
        "expected_budget": None,
        "expected_start": "2026-10-24",
        "expected_end": None,
        "expected_questions": ["currency"],
    },
    {
        "id": "required-demo-place",
        "text": (
            "Анна едет в демо-город 24 октября 2026. Общий бюджет 100 евро. "
            "Обязательно музей, исключить рынок. Любит историю."
        ),
        "members": [],
        "expected_budget": 10000,
        "expected_start": "2026-10-24",
        "expected_end": None,
        "expected_questions": [],
    },
    {
        "id": "different-group",
        "text": (
            "Ира, Олег и Юля едут в Лиссабон с 1 по 2 ноября 2026. "
            "На всех 80 евро. Всем нравится искусство. Каждый день с 10 до 17."
        ),
        "members": [],
        "expected_budget": 8000,
        "expected_start": "2026-11-01",
        "expected_end": "2026-11-02",
        "expected_questions": [],
    },
]


def main():
    configure_logging(file_tag="llm-evaluation")
    results = []
    for case in CASES:
        before = monotonic()
        try:
            parsed = parse_request(
                ParseInput(text=case["text"], member_names=case["members"]),
                today=date(2026, 10, 23),
            )
            questions = {q.field for q in parsed.questions}
            passed = (
                parsed.group_budget == case["expected_budget"]
                and (str(parsed.draft.travel_date) if parsed.draft.travel_date else None)
                == case["expected_start"]
                and (str(parsed.draft.end_date) if parsed.draft.end_date else None)
                == case["expected_end"]
                and (
                    set(case["expected_questions"]) <= questions
                    if case["expected_questions"]
                    else not questions
                )
            )
            result = {"id": case["id"], "passed": passed, "output": parsed.model_dump(mode="json")}
        except Exception as exc:
            result = {"id": case["id"], "passed": False, "error": type(exc).__name__}
        result["seconds"] = round(monotonic() - before, 2)
        results.append(result)
        print(
            json.dumps({k: v for k, v in result.items() if k != "output"}, ensure_ascii=True),
            flush=True,
        )
    destination = Path(__file__).resolve().parents[1] / "docs" / "llm-evaluation.json"
    destination.write_text(
        json.dumps(
            {
                "reference_date": "2026-10-23",
                "runtime": "llama.cpp b11840",
                "model": "Qwen3-4B-Q4_K_M",
                "cases": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if not all(r["passed"] for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
