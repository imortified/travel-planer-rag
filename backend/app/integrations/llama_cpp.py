"""Small llama-server adapter; no model, runtime or SDK is embedded in FastAPI."""

import json
import logging
from time import perf_counter

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.logging import emit, error_kind, request_id

logger = logging.getLogger(__name__)
EXPLANATION_MAX_TOKENS = 256


class LlmUnavailable(Exception):
    pass


class InvalidLlmOutput(Exception):
    pass


class LlamaCpp:
    def __init__(self, client=None):
        self.client = client

    def structured(self, system: str, data: dict, schema: type[BaseModel], verify=None):
        if self.client is None:
            with httpx.Client() as client:
                return LlamaCpp(client).structured(system, data, schema, verify)
        messages = [
            {
                "role": "system",
                "content": system
                + "\nOutput JSON schema:\n"
                + json.dumps(schema.model_json_schema(), ensure_ascii=False, separators=(",", ":")),
            },
            {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
        ]
        # A single repair request is allowed. Do not log user text or raw completions.
        operation = {"ExtractedTrip": "parse", "ExplanationDraft": "explain"}.get(
            schema.__name__, "structured"
        )
        is_explanation = operation == "explain"
        timeout_seconds = (
            settings.llm_explanation_timeout_seconds
            if is_explanation
            else settings.llm_timeout_seconds
        )
        # Share the explanation wait budget with its one possible repair request.
        deadline = perf_counter() + timeout_seconds
        for attempt in range(2):
            started = perf_counter()
            try:
                remaining = deadline - started if is_explanation else timeout_seconds
                if remaining <= 0:
                    raise httpx.ReadTimeout("Explanation wait budget exhausted")
                response = self.client.post(
                    f"{settings.llm_base_url.rstrip('/')}/v1/chat/completions",
                    timeout=httpx.Timeout(remaining, connect=min(3, remaining)),
                    headers={"X-Request-ID": request_id.get()} if request_id.get() else {},
                    json={
                        "model": settings.llm_model,
                        "messages": messages,
                        "temperature": 0,
                        "seed": 42,
                        "max_tokens": EXPLANATION_MAX_TOKENS if is_explanation else 1400,
                        "stream": False,
                        "chat_template_kwargs": {"enable_thinking": False},
                        "response_format": {
                            "type": "json_object",
                            "schema": schema.model_json_schema(),
                        },
                    },
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                emit(
                    logger,
                    "llm.call.failed",
                    logging.ERROR,
                    exception=exc,
                    provider="llama_cpp",
                    operation=operation,
                    model=settings.llm_model,
                    attempt=attempt + 1,
                    reason=error_kind(exc),
                    status_code=exc.response.status_code
                    if isinstance(exc, httpx.HTTPStatusError)
                    else None,
                    duration_ms=(perf_counter() - started) * 1000,
                )
                raise LlmUnavailable("Local llama.cpp server is unavailable") from exc
            try:
                body = response.json()
                choice = body["choices"][0]
                if choice.get("finish_reason") != "stop":
                    raise ValueError("Incomplete generation")
                result = schema.model_validate_json(choice["message"]["content"])
                if verify is not None:
                    verify(result)
                usage = body.get("usage")
                usage = usage if isinstance(usage, dict) else {}
                emit(
                    logger,
                    "llm.call.completed",
                    provider="llama_cpp",
                    operation=operation,
                    model=settings.llm_model,
                    attempt=attempt + 1,
                    duration_ms=(perf_counter() - started) * 1000,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                )
                return result, str(body.get("model") or settings.llm_model)
            except (ValueError, TypeError, KeyError, IndexError, ValidationError) as exc:
                emit(
                    logger,
                    "llm.output.rejected",
                    logging.WARNING,
                    exception=exc,
                    provider="llama_cpp",
                    operation=operation,
                    model=settings.llm_model,
                    attempt=attempt + 1,
                    will_retry=attempt == 0,
                    reason="invalid_response",
                    duration_ms=(perf_counter() - started) * 1000,
                )
                if attempt:
                    raise InvalidLlmOutput("Model did not return valid structured data") from exc
                messages.append(
                    {
                        "role": "user",
                        "content": "Previous output was invalid. Return a complete JSON object "
                        "matching the schema. For explanations select only known fact IDs "
                        "and include every required_ids entry exactly once. "
                        "No prose, markdown or reasoning.",
                    }
                )
        raise InvalidLlmOutput("Model output validation failed")
