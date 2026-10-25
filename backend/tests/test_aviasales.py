import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from cache_helpers import MemoryRedis

from app.integrations.aviasales import Aviasales, search_link
from app.integrations.http_cache import RedisHttpCache, SourceUnavailable
from app.schemas.flights import FlightSearch


def search(**patch):
    return FlightSearch(
        destination="Lisbon", origin="WAW", departure=date.today() + timedelta(days=7), **patch
    )


def body(request):
    q = request.url.params
    row = {
        "origin": q["origin"],
        "destination": q["destination"],
        "origin_airport": q["origin"],
        "destination_airport": q["destination"],
        "departure_at": q["departure_at"] + "T22:00:00+01:00",
        "price": 81.5,
        "airline": "W6",
        "flight_number": "1431",
        "transfers": 0,
        "link": "/search/WAW1511LIS1?expected_price_currency=eur",
        "duration_to": 305,
    }
    if "return_at" in q:
        row.update(
            return_at=q["return_at"] + "T12:00:00+00:00", return_transfers=0, duration_back=300
        )
    return {"success": True, "currency": "eur", "data": [row]}


def test_header_only_cache_keeps_retrieval_time_and_does_not_invent_source_age():
    requests, store = [], MemoryRedis()

    def handle(request):
        requests.append(request)
        assert request.headers["X-Access-Token"] == "unit-fake-token"
        assert "token" not in request.url.params and "adults" not in request.url.params
        return httpx.Response(200, json=body(request))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        provider = Aviasales(RedisHttpCache(store), client, "unit-fake-token")
        first = provider.search(search())
        again = provider.search(search())
        assert first["options"][0]["observed_price"] == 8150
        assert first["source_found_at"] is None and not first["group_total_confirmed"]
        assert again["cache"]["read"] == "hit"
        assert first["fetched_at"] == again["fetched_at"]
        assert first["expires_at"] == again["expires_at"]
        assert len(requests) == 1 and "unit-fake-token" not in json.dumps(first)
        for patch in (
            {"market": "us"},
            {"direct": True},
            {"return_date": search().departure + timedelta(days=2)},
        ):
            provider.search(search(**patch))
        changed = search().model_copy(update={"departure": search().departure + timedelta(days=1)})
        provider.search(changed)
    assert len(requests) == len(store.values) == 5
    assert all("unit-fake-token" not in key for key in store.values)


@pytest.mark.parametrize(
    "damage",
    [
        "price",
        "currency",
        "direction",
        "date",
        "timezone",
        "link",
        "expiry",
        "token",
        "error",
        "transfers",
        "duration",
    ],
)
def test_invalid_or_credential_containing_responses_are_never_cached(damage):
    store = MemoryRedis()

    def handle(request):
        response = body(request)
        row = response["data"][0]
        if damage == "price":
            row["price"] = True
        if damage == "currency":
            response["currency"] = "usd"
        if damage == "direction":
            row["destination"] = "OPO"
        if damage == "date":
            row["departure_at"] = "2026-01-01T12:00:00Z"
        if damage == "timezone":
            row["departure_at"] = request.url.params["departure_at"] + "T12:00:00"
        if damage == "link":
            row["link"] = "https://evil.example/search/steal"
        if damage == "expiry":
            row["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        if damage == "token":
            response["debug"] = "unit-fake-token"
        if damage == "error":
            response["success"] = False
        if damage == "transfers":
            row["transfers"] = -1
        if damage == "duration":
            row["duration_to"] = False
        return httpx.Response(200, json=response)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(SourceUnavailable):
            Aviasales(RedisHttpCache(store), client, "unit-fake-token").search(search())
    assert not store.values


def test_empty_is_cached_but_429_is_not_retried_and_no_stale_fallback():
    calls, store = [], MemoryRedis()

    def handle(request):
        calls.append(request)
        if len(calls) > 1:
            return httpx.Response(429, json={"error": "unit-fake-token"})
        return httpx.Response(200, json={"success": True, "currency": "eur", "data": []})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        provider = Aviasales(RedisHttpCache(store), client, "unit-fake-token")
        assert provider.search(search())["options"] == []
        assert provider.search(search())["cache"]["read"] == "hit"
        # Corrupt freshness rather than sleeping. The old body must not mask 429.
        key = next(iter(store.values))
        envelope = json.loads(store.values[key][0])
        envelope["fetched_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        store.values[key] = (json.dumps(envelope).encode(), store.values[key][1])
        with pytest.raises(SourceUnavailable):
            provider.search(search())
    assert len(calls) == 2


def test_redis_outage_uses_fresh_source_and_missing_token_never_calls_source():
    from redis.exceptions import ConnectionError

    class DownRedis:
        def get(self, *_):
            raise ConnectionError()

    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=body(request))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert (
            Aviasales(RedisHttpCache(DownRedis()), client, "unit-fake-token").search(search())[
                "cache"
            ]["read"]
            == "unavailable"
        )
        with pytest.raises(SourceUnavailable):
            Aviasales(RedisHttpCache(), client, "").search(search())
    assert len(calls) == 1


@pytest.mark.parametrize(
    "value",
    [
        "//evil.example/search/x",
        "javascript:alert(1)",
        "https://www.aviasales.com.evil/search/x",
        "https://user@aviasales.com/search/x",
        "https://aviasales.com:443/search/x",
        "/search/x\nheader",
        "/other/x",
    ],
)
def test_external_links_are_restricted_to_provider_search(value):
    with pytest.raises(ValueError):
        search_link(value)
