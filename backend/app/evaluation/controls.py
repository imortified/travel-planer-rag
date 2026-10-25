"""Injected faults exercise real adapter/API guards without stopping the user's model."""

from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.evaluation.explanations import fixtures
from app.integrations.llama_cpp import LlamaCpp, LlmUnavailable
from app.main import app
from app.nlp.explanations import explain
from app.nlp.parser import parse_request
from app.schemas.assistant import ExplanationDraft


def run_controls():
    results = []
    for kind, expected in [("timeout", 503), ("invalid_json", 502)]:
        calls = []

        def handle(request):
            calls.append(1)
            if kind == "timeout":
                raise httpx.ReadTimeout("Synthetic timeout", request=request)
            return httpx.Response(
                200,
                json={
                    "choices": [{"finish_reason": "stop", "message": {"content": "{} unparseable"}}]
                },
            )

        with httpx.Client(transport=httpx.MockTransport(handle)) as mock_http:
            model = LlamaCpp(mock_http)
            with (
                patch(
                    "app.api.assistant.parse_request", side_effect=lambda p: parse_request(p, model)
                ),
                TestClient(app) as api,
            ):
                response = api.post("/api/v1/assistant/parse", json={"text": "Хочу в отпуск"})
                manual = api.post(
                    "/api/v1/plans/demo",
                    json={
                        "participants": [{"id": "test", "interests": {"history": 1}}],
                        "budget": 10000,
                        "start": 540,
                        "end": 1080,
                    },
                )
                passed = (
                    response.status_code == expected
                    and manual.status_code == 200
                    and len(calls) == (1 if kind == "timeout" else 2)
                )
                results.append(
                    {
                        "id": "parse_" + kind,
                        "passed": passed,
                        "status_code": response.status_code,
                        "manual_planner_status": manual.status_code,
                        "attempts": len(calls),
                    }
                )

    for kind in ("unavailable", "invented_fact"):

        class FaultModel:
            def structured(self, system, data, schema, verify=None):
                if kind == "unavailable":
                    raise LlmUnavailable("Synthetic unavailable model")
                return ExplanationDraft(
                    selected_ids=[*data["required_ids"], "Виза гарантирована."]
                ), "synthetic-fault"

        _, route, trip, primary = fixtures()[0]
        output = explain(route, trip, primary, 2, FaultModel())
        reason = "llm_unavailable" if kind == "unavailable" else "unverified_output"
        passed = (
            output.mode == "template"
            and output.fallback_reason == reason
            and all("Виза гарантирована" not in p for p in output.paragraphs)
        )
        results.append(
            {
                "id": "explain_" + kind,
                "passed": passed,
                "mode": output.mode,
                "fallback_reason": output.fallback_reason,
            }
        )
    return results
