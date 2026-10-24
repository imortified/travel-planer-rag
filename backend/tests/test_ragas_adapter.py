"""Real RAGAS/Instructor calculations over a controlled HTTP API, never judge quality."""

import asyncio
import importlib.util
import json
from types import SimpleNamespace

import httpx
import pytest

from app.evaluation.ragas_runner import run

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("ragas") is None, reason="Optional evaluation environment required"
)


@pytest.mark.parametrize("kind", ["explanation", "retrieval"])
@pytest.mark.parametrize("backend", ["llama-cpp", "openai"])
def test_real_ragas_compatible_client_and_scoring(tmp_path, monkeypatch, kind, backend):
    monkeypatch.setenv("RAGAS_JUDGE_API_KEY", "controlled-test-not-a-secret")
    payloads = (
        [
            {"statements": ["В саду есть озеро."]},
            {"statements": [{"statement": "В саду есть озеро.", "reason": "Given", "verdict": 1}]},
        ]
        if kind == "explanation"
        else [
            {"reason": "Given", "verdict": 1},
            {
                "classifications": [
                    {"statement": "В саду есть озеро.", "reason": "Given", "attributed": 1}
                ]
            },
        ]
    )
    requests = []

    def handle(request):
        if request.url.path == "/props":
            return httpx.Response(200, json={"total_slots": 1})
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {"id": "test-judge", "object": "model", "created": 0, "owned_by": "test"}
                    ],
                },
            )
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "test-judge"
        if backend == "llama-cpp":
            assert body["chat_template_kwargs"] == {"enable_thinking": False}
        else:
            assert "chat_template_kwargs" not in body
        result = payloads[len(requests)]
        requests.append(body)
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "test-judge",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": json.dumps(result)},
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    original = httpx.AsyncClient

    class ControlledClient(original):
        def __init__(self, **kw):
            super().__init__(**kw, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(httpx, "AsyncClient", ControlledClient)
    path = tmp_path / "samples.jsonl"
    sample = {
        "user_input": "Что есть в саду?",
        "retrieved_contexts": ["В саду есть озеро."],
        "response" if kind == "explanation" else "reference": "В саду есть озеро.",
    }
    path.write_text(
        json.dumps(
            {"case_id": "controlled", "kind": kind, "mode": "controlled_test", "sample": sample}
        )
        + "\n",
        "utf-8",
    )
    report = asyncio.run(
        run(
            SimpleNamespace(
                samples=path,
                output=tmp_path / "score.json",
                calibration=False,
                case_id=None,
                model="test-judge",
                backend=backend,
                    http_timeout=180,
                    metric_timeout=360,
                allow_self_judge=False,
                judge_identity="controlled test API",
                base_url="http://test/v1",
            )
        )
    )
    assert report["completed"] and report["inputs_unchanged"]
    assert len(requests) == 2 and len(report["http_calls"]) == 2
    assert all(
        g["mean"] == pytest.approx(1) and g["errors"] == 0 for g in report["summary"].values()
    )
