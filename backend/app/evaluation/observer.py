"""Record only public benchmark completions, never alter production adapter behavior."""

import httpx

from app.core.config import settings
from app.integrations.llama_cpp import LlamaCpp


class ObservedLlama:
    def __init__(self):
        self.attempts = []

    def structured(self, system, data, schema, verify=None):
        def observe(response):
            response.read()
            record = {"status_code": response.status_code, "schema_valid": False}
            try:
                body = response.json()
                choice = body["choices"][0]
                record["finish_reason"] = choice.get("finish_reason")
                record["usage"] = body.get("usage")
                draft = schema.model_validate_json(choice["message"]["content"])
                record.update(schema_valid=True, output=draft.model_dump(mode="json"))
            except (ValueError, KeyError, TypeError, IndexError):
                pass
            self.attempts.append(record)

        with httpx.Client(
            timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=3),
            event_hooks={"response": [observe]},
        ) as client:
            return LlamaCpp(client).structured(system, data, schema, verify)
