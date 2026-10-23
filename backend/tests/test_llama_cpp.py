import json

import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from app.integrations.llama_cpp import InvalidLlmOutput, LlamaCpp, LlmUnavailable


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
