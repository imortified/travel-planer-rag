import json

import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from app.core.config import settings
from app.integrations.llama_cpp import InvalidLlmOutput, LlamaCpp, LlmUnavailable
from app.schemas.assistant import ExplanationDraft, ExtractedTrip


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: int


def completion(content, finish="stop"):
    return {
        "model": "test-qwen",
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
    }


def test_llama_server_schema_non_thinking_and_structured_response():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=completion('{"value":3}'))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result, model = LlamaCpp(client).structured("Extract", {"text": "data"}, Answer)
    body = json.loads(requests[0].content)
    assert requests[0].url.path == "/v1/chat/completions"
    assert body["response_format"]["schema"]["additionalProperties"] is False
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["temperature"] == 0 and body["seed"] == 42
    assert result.value == 3 and model == "test-qwen"


@pytest.mark.parametrize(
    "invalid,finish",
    [
        ("bad json", "stop"),
        ('{"value":true}', "stop"),
        ('{"value":1,"other":1}', "stop"),
        ('{"value":1}', "length"),
    ],
)
def test_invalid_output_gets_one_repair_request(invalid, finish):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, json=completion(invalid, finish) if len(calls) == 1 else completion('{"value":2}')
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result, _ = LlamaCpp(client).structured("Extract", {}, Answer)
    assert result.value == 2 and len(calls) == 2


def test_semantic_verifier_participates_in_bounded_repair():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=completion(json.dumps({"value": len(calls)})))

    def verify(result):
        if result.value != 2:
            raise ValueError("Wrong fact")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result, _ = LlamaCpp(client).structured("Extract", {}, Answer, verify)
    assert result.value == 2 and len(calls) == 2


def test_repeated_invalid_output_is_not_accepted():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=completion("not json"))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(InvalidLlmOutput):
            LlamaCpp(client).structured("Extract", {}, Answer)
    assert len(calls) == 2


@pytest.mark.parametrize("status", [400, 503])
def test_http_failures_are_reported_without_schema_retries(status):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status))) as client:
        with pytest.raises(LlmUnavailable):
            LlamaCpp(client).structured("Extract", {}, Answer)


def test_timeout_is_unavailable():
    def fail(request):
        raise httpx.ReadTimeout("Local model timeout", request=request)

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(LlmUnavailable):
            LlamaCpp(client).structured("Extract", {}, Answer)


@pytest.mark.parametrize(
    "schema,seconds,tokens", [(ExtractedTrip, 60, 1400), (ExplanationDraft, 120, 256)]
)
def test_operation_limits_apply_even_to_injected_clients(monkeypatch, schema, seconds, tokens):
    monkeypatch.setattr(settings, "llm_timeout_seconds", 60)
    monkeypatch.setattr(settings, "llm_explanation_timeout_seconds", 120)
    calls = []

    def handler(request):
        calls.append(request)
        content = (
            "{}"
            if schema is ExtractedTrip
            else '{"selected_ids":["summary","budget","limitations"]}'
        )
        return httpx.Response(200, json=completion(content))

    with httpx.Client(transport=httpx.MockTransport(handler), timeout=1) as client:
        LlamaCpp(client).structured("Select", {}, schema)
    assert json.loads(calls[0].content)["max_tokens"] == tokens
    assert calls[0].extensions["timeout"]["read"] == pytest.approx(seconds, abs=0.1)
    assert calls[0].extensions["timeout"]["connect"] == 3


@pytest.mark.parametrize("first_seconds", [90, 121])
def test_explanation_repair_shares_remaining_wait_budget(monkeypatch, first_seconds):
    clock = [0.0]
    calls = []
    monkeypatch.setattr(settings, "llm_explanation_timeout_seconds", 120)
    monkeypatch.setattr("app.integrations.llama_cpp.perf_counter", lambda: clock[0])

    def handler(request):
        calls.append(request)
        clock[0] += first_seconds
        content = (
            "bad json" if len(calls) == 1 else '{"selected_ids":["summary","budget","limitations"]}'
        )
        return httpx.Response(200, json=completion(content))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        if first_seconds > 120:
            with pytest.raises(LlmUnavailable):
                LlamaCpp(client).structured("Select", {}, ExplanationDraft)
            assert len(calls) == 1
        else:
            LlamaCpp(client).structured("Select", {}, ExplanationDraft)
            assert len(calls) == 2
            assert calls[1].extensions["timeout"]["read"] == 30
