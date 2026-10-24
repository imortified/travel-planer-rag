"""Explicit field checks: errors remain in denominators, latency uses nearest-rank p95."""

import math
from collections import defaultdict
from statistics import median


def projection(output, members):
    data = output.model_dump(mode="json") if hasattr(output, "model_dump") else output
    draft = data["draft"]
    return {
        **data,
        "names": sorted(p["name"] for p in draft["participants"]),
        "effective_count": draft["participant_count"] or len(draft["participants"]) or len(members),
        "individual_interests": {p["name"]: p["interests"] for p in draft["participants"]},
    }


def at_path(data, path):
    for key in path.split("."):
        if not isinstance(data, dict) or key not in data:
            return None
        data = data[key]
    return data


def grade(case, output=None):
    data = projection(output, case["members"]) if output is not None else None
    checks = []
    for path, expected in case["expected"].items():
        actual = at_path(data, path)
        if path in {"draft.mandatory_names", "draft.excluded_names"} and actual is not None:
            actual = sorted(v.casefold().strip() for v in actual)
            expected = sorted(v.casefold().strip() for v in expected)
        checks.append(
            {
                "field": path,
                "passed": data is not None and actual == expected,
                "expected": expected,
                "actual": actual,
            }
        )
    for path in case.get("nonempty", []):
        actual = at_path(data, path)
        checks.append(
            {"field": path, "passed": bool(actual), "expected": "nonempty", "actual": actual}
        )
    questions = {q["field"] for q in data["questions"]} if data else set()
    required = set(case.get("questions_required", []))
    allowed = set(case.get("questions_allowed", required))
    checks.append(
        {
            "field": "questions",
            "passed": data is not None and required <= questions <= allowed,
            "expected": {"required": sorted(required), "allowed": sorted(allowed)},
            "actual": sorted(questions),
        }
    )
    return checks


def latency(rows):
    values = sorted(row["seconds"] for row in rows)
    return {
        "n": len(values),
        "median_seconds": median(values) if values else None,
        "p95_seconds": values[math.ceil(len(values) * 0.95) - 1] if values else None,
        "max_seconds": values[-1] if values else None,
    }


def summarize(rows):
    fields = defaultdict(lambda: {"passed": 0, "total": 0})
    for row in rows:
        for check in row["checks"]:
            fields[check["field"]]["total"] += 1
            fields[check["field"]]["passed"] += int(check["passed"])
    return {
        "runs": len(rows),
        "passed": sum(row["passed"] for row in rows),
        "errors": sum("error" in row for row in rows),
        "per_field": {
            key: {**counts, "accuracy": counts["passed"] / counts["total"]}
            for key, counts in sorted(fields.items())
        },
        "latency_all": latency(rows),
        "latency_successful": latency([r for r in rows if "error" not in r]),
        "repairs": sum(len(row.get("attempts", [])) > 1 for row in rows),
        "first_response_schema_valid": sum(
            bool(r.get("attempts")) and r["attempts"][0]["schema_valid"] for r in rows
        ),
        "any_response_schema_valid": sum(
            any(a["schema_valid"] for a in r.get("attempts", [])) for r in rows
        ),
    }
