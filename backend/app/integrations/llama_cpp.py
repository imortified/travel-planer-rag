"""Small llama-server adapter; no model, runtime or SDK is embedded in FastAPI."""

import json

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import settings


class LlmUnavailable(Exception):
    pass


class InvalidLlmOutput(Exception):
    pass


class LlamaCpp:
    def __init__(self, client=None):
        self.client = client

    def structured(self, system: str, data: dict, schema: type[BaseModel], verify=None):
        if self.client is None:
            with httpx.Client(
                timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=3)
            ) as client:
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
        for attempt in range(2):
            try:
                response = self.client.post(
                    f"{settings.llm_base_url.rstrip('/')}/v1/chat/completions",
                    json={
                        "model": settings.llm_model,
                        "messages": messages,
                        "temperature": 0,
                        "seed": 42,
                        "max_tokens": 1400,
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
                raise LlmUnavailable("Local llama.cpp server is unavailable") from exc
            try:
                body = response.json()
                choice = body["choices"][0]
                if choice.get("finish_reason") != "stop":
                    raise ValueError("Incomplete generation")
                result = schema.model_validate_json(choice["message"]["content"])
                if verify is not None:
                    verify(result)
                return result, str(body.get("model") or settings.llm_model)
            except (ValueError, TypeError, KeyError, IndexError, ValidationError) as exc:
                if attempt:
                    raise InvalidLlmOutput("Model did not return valid structured data") from exc
                messages.append(
                    {
                        "role": "user",
                        "content": "Previous output was invalid. Return a complete JSON object "
                        "matching the schema. Copy supplied facts exactly when explaining. "
                        "No prose, markdown or reasoning.",
                    }
                )
        raise InvalidLlmOutput("Model output validation failed")
