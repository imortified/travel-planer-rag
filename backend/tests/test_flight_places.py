import json

import httpx
import pytest
from cache_helpers import MemoryRedis
from fastapi.testclient import TestClient

from app.core.auth import current_account
from app.integrations.flight_places import FlightPlaces
from app.integrations.http_cache import RedisHttpCache, SourceUnavailable
from app.main import app

ROWS = [
    {
        "type": "airport",
        "code": "SVO",
        "name": "Шереметьево",
        "country_name": "Россия",
        "city_code": "MOW",
        "city_name": "Москва",
    },
    {"type": "city", "code": "MOW", "name": "Москва", "country_name": "Россия"},
    {"type": "city", "code": "MOW", "name": "Москва", "country_name": "Россия"},
    {"type": "country", "code": "RU", "name": "Россия"},
]


def test_public_suggestions_keep_city_and_airport_distinct_and_cache_without_a_token():
    calls, store = [], MemoryRedis()

    def handle(request):
        calls.append(request)
        assert request.url.params.get_list("types[]") == ["city", "airport"]
        assert request.url.params["locale"] == "ru"
        assert "token" not in request.url.params and "X-Access-Token" not in request.headers
        return httpx.Response(200, json=ROWS)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        provider = FlightPlaces(RedisHttpCache(store), client)
        first = provider.search("  Москва  ")
        again = provider.search("Москва")
        assert [(p["type"], p["code"]) for p in first["places"]] == [
            ("city", "MOW"),
            ("airport", "SVO"),
        ]
        assert again["cache"]["read"] == "hit" and first["fetched_at"] == again["fetched_at"]
        provider.search("Минск")
    assert len(calls) == len(store.values) == 2


@pytest.mark.parametrize("response", [{"error": "bad"}, ["bad"], [dict(ROWS[0], code="https://x")]])
def test_malformed_places_are_not_cached(response):
    store = MemoryRedis()
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    ) as client:
        with pytest.raises(SourceUnavailable):
            FlightPlaces(RedisHttpCache(store), client).search("Москва")
    assert not store.values


@pytest.mark.parametrize("term", ["", " ", "A", "x" * 81, "Mo\x00scow"])
def test_invalid_terms_do_not_call_provider(term):
    def unexpected(_):
        pytest.fail("Invalid term must not cause an HTTP request")

    with httpx.Client(transport=httpx.MockTransport(unexpected)) as client:
        with pytest.raises(ValueError):
            FlightPlaces(RedisHttpCache(MemoryRedis()), client).search(term)


def test_http_failure_is_not_an_empty_success_or_cached():
    store, calls = MemoryRedis(), []

    def handle(request):
        calls.append(request)
        return httpx.Response(429, json={"error": "private provider diagnostic"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(SourceUnavailable):
            FlightPlaces(RedisHttpCache(store), client).search("Москва")
    assert len(calls) == 1 and not store.values


def test_suggestions_endpoint_requires_login_and_maps_provider_failure(monkeypatch):
    with TestClient(app) as client:
        assert client.get("/api/v1/flight-places?term=Moscow").status_code == 401
        previous = dict(app.dependency_overrides)
        app.dependency_overrides[current_account] = lambda: object()
        try:
            monkeypatch.setattr(FlightPlaces, "search", lambda _self, _term: {"places": ROWS[:1]})
            assert (
                client.get("/api/v1/flight-places?term=Moscow").json()["places"][0]["code"] == "SVO"
            )
            assert client.get("/api/v1/flight-places?term=A").status_code == 422

            def failed(*_):
                raise SourceUnavailable("private provider diagnostic")

            monkeypatch.setattr(FlightPlaces, "search", failed)
            response = client.get("/api/v1/flight-places?term=Moscow")
            assert response.status_code == 503
            assert "private provider diagnostic" not in json.dumps(response.json())
            assert "вручную" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)
