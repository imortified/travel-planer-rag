import base64
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cache_helpers import MemoryRedis
from redis.exceptions import ConnectionError, ResponseError, TimeoutError

from app.integrations.http_cache import RedisHttpCache, SourceUnavailable, cached_get

URL = "https://example.org/table/v1/driving/1,2;3,4"
PARAMS = {"annotations": "duration"}


def timestamp(delta=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=delta)).isoformat()


def fetch(client, cache, ttl=60):
    return cached_get(client, URL, PARAMS, ttl, cache, "osrm", json.loads)


def test_key_covers_provider_endpoint_coordinate_order_profile_parameters_and_ttl():
    cache = RedisHttpCache(MemoryRedis())
    key = cache.key("osrm", URL, PARAMS, 60)
    variants = [
        cache.key("osm", URL, PARAMS, 60),
        cache.key("osrm", URL.replace("driving", "walking"), PARAMS, 60),
        cache.key("osrm", URL.replace("1,2;3,4", "3,4;1,2"), PARAMS, 60),
        cache.key("osrm", URL, {"annotations": "distance"}, 60),
        cache.key("osrm", URL, PARAMS, 61),
    ]
    assert all(value != key for value in variants)
    assert cache.key("osm", URL, {"b": 2, "a": 1}, 60) == cache.key(
        "osm", URL, {"a": 1, "b": 2}, 60
    )


def test_hit_preserves_original_fetch_time_and_does_not_rewrite_ttl():
    cache = RedisHttpCache(MemoryRedis())
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"value": 42})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first = fetch(client, cache)
        expiration = next(iter(cache.client.values.values()))[1]
        second = fetch(client, cache)
    assert len(calls) == 1 and first[:2] == second[:2]
    assert first[2]["read"] == "miss" and first[2]["write"] == "stored"
    assert second[2]["read"] == "hit" and second[2]["write"] == "not_attempted"
    assert next(iter(cache.client.values.values()))[1] == expiration


@pytest.mark.parametrize("fault", ["expired", "future", "checksum", "json", "source", "version"])
def test_invalid_cache_is_refetched_and_replaced(fault):
    cache = RedisHttpCache(MemoryRedis())
    key = cache.key("osrm", URL, PARAMS, 60)
    cache.put(key, b'{"old":true}', timestamp(), 60)
    raw = cache.client.get(key)
    value = json.loads(raw)
    if fault in ("expired", "future"):
        value["fetched_at"] = timestamp(-100 if fault == "expired" else 100)
    elif fault == "checksum":
        value["body"] = base64.b64encode(b"different").decode()
    elif fault == "json":
        raw = "not-json"
    elif fault == "source":
        cache.put(key, b"invalid upstream JSON", timestamp(), 60)
        raw = cache.client.get(key)
    elif fault == "version":
        value["version"] = True
    if fault not in ("json", "source"):
        raw = json.dumps(value)
    cache.client.set(key, raw, ex=60)
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"fresh": True}))
    ) as client:
        result, _, info = fetch(client, cache)
    assert result == {"fresh": True} and info["read"] == "invalid" and info["write"] == "stored"
    assert json.loads(cache.get(key, 60).body) == {"fresh": True}


@pytest.mark.parametrize("failure", [ConnectionError, TimeoutError, ResponseError])
def test_cache_read_outage_uses_fresh_data_without_second_cache_attempt(failure):
    class BrokenRedis:
        def get(self, key):
            raise failure()

        def set(self, *args, **kwargs):
            raise AssertionError("Skip write after a failed cache read")

    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"fresh": True}))
    ) as client:
        result, _, info = fetch(client, RedisHttpCache(BrokenRedis()))
    assert (
        result == {"fresh": True} and info["read"] == "unavailable" and info["write"] == "skipped"
    )


def test_cache_write_outage_does_not_fail_valid_upstream_response():
    class BrokenRedis(MemoryRedis):
        def set(self, *args, **kwargs):
            raise ConnectionError()

    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"fresh": True}))
    ) as client:
        result, _, info = fetch(client, RedisHttpCache(BrokenRedis()))
    assert result == {"fresh": True} and info["read"] == "miss" and info["write"] == "unavailable"


def test_wrong_redis_type_is_repaired_by_fresh_response():
    class WrongType(MemoryRedis):
        def get(self, key):
            if not self.values:
                raise ResponseError("WRONGTYPE")
            return super().get(key)

    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"fresh": True}))
    ) as client:
        result, _, info = fetch(client, RedisHttpCache(WrongType()))
    assert result == {"fresh": True} and info["read"] == "invalid" and info["write"] == "stored"


def test_malformed_success_response_is_never_cached_and_next_request_can_recover():
    cache = RedisHttpCache(MemoryRedis())
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"bad"))
    ) as client:
        with pytest.raises(SourceUnavailable, match="not cached"):
            fetch(client, cache)
    assert not cache.client.values
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"fresh": True}))
    ) as client:
        assert fetch(client, cache)[0] == {"fresh": True}


def test_stale_cache_and_failed_upstream_never_produce_a_route_from_stale_data(monkeypatch):
    monkeypatch.setattr("app.integrations.http_cache.time.sleep", lambda _: None)
    cache = RedisHttpCache(MemoryRedis())
    key = cache.key("osrm", URL, PARAMS, 60)
    cache.put(key, b'{"old":true}', timestamp(-100), 60)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceUnavailable):
            fetch(client, cache)
    assert len(calls) == 2
