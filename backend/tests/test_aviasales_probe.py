import json
from datetime import date

import httpx
import pytest

from check_aviasales import probe


def test_token_is_header_only_and_output_is_not_a_group_price():
    def handle(request):
        assert request.headers["X-Access-Token"] == "fake-secret"
        assert "token" not in request.url.params
        assert request.url.params["one_way"] == "false"
        assert request.url.params["return_at"] == "2026-11-22"
        return httpx.Response(
            200,
            json={
                "success": True,
                "currency": "eur",
                "data": [
                    {
                        "price": 81,
                        "airline": "fake-secret",
                        "private": "unrelated",
                        "link": "secret-url",
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = probe(client, "fake-secret", "WAW", "LIS", date(2026, 11, 15), date(2026, 11, 22))
    assert result["status"] == "observations" and not result["group_total_confirmed"]
    assert result["observations"] == [{"price": 81, "airline": "[REDACTED]"}]
    assert "fake-secret" not in json.dumps(result)


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (401, {"error": "fake-secret"}, "http_error"),
        (429, {}, "http_error"),
        (200, {"success": False, "error": "fake-secret"}, "provider_error"),
        (200, {"success": True, "currency": "eur", "data": {}}, "empty"),
        (200, {"success": True, "currency": "eur", "data": [1]}, "invalid_response"),
        (200, {"success": True, "currency": "usd", "data": []}, "unexpected_currency"),
    ],
)
def test_errors_and_empty_are_distinct_without_leaking_response(status, body, expected):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, json=body)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = probe(client, "fake-secret", "WAW", "LIS", date(2026, 11, 15))
    assert result["status"] == expected and len(calls) == 1
    assert "fake-secret" not in json.dumps(result)
