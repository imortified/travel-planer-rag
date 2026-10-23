"""Opt-in Redis tests use isolated keys and never flush the shared database."""

import os
import time
from uuid import uuid4

import httpx
import pytest

from app.integrations.http_cache import RedisHttpCache, cached_get, redis_client

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REDIS_TESTS") != "1", reason="Set RUN_REDIS_TESTS=1 with Redis running"
)


@pytest.fixture
def cache():
    prefix = f"test:travel:{uuid4().hex}"
    result = RedisHttpCache(redis_client(), prefix)
    try:
        yield result
    finally:
        keys = list(result.client.scan_iter(match=f"{prefix}:*"))
        if keys:
            assert all(key.decode().startswith(prefix + ":") for key in keys)
            result.client.delete(*keys)


def test_real_ttl_expiry_refetch_and_original_fetch_timestamp(cache):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=str(len(calls)).encode())

    def fetch(client):
        return cached_get(client, "https://example.org/osm", {}, 1, cache, "osm", int)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first = fetch(client)
        key = first[2]["key"]
        assert 0 < cache.client.pttl(key) <= 1000
        assert fetch(client)[:2] == first[:2] and len(calls) == 1
        deadline = time.monotonic() + 3
        while cache.client.exists(key) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not cache.client.exists(key)
        second = fetch(client)
    assert second[0] == 2 and second[1] != first[1]
    assert second[2]["read"] == "miss" and len(calls) == 2


def test_real_get_does_not_extend_expiration_and_process_instances_share_cache(cache):
    first = cached_get
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"42"))
    ) as client:
        result = first(client, "https://example.org/osm", {}, 60, cache, "osm", int)
    key = result[2]["key"]
    before = cache.client.pttl(key)
    other = RedisHttpCache(cache.client, cache.prefix)

    def fail(request):
        raise AssertionError("Another backend instance must reuse Redis")

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        hit = cached_get(client, "https://example.org/osm", {}, 60, other, "osm", int)
    assert hit[:2] == result[:2] and hit[2]["read"] == "hit"
    assert 0 < cache.client.pttl(key) <= before


def test_real_corrupt_type_is_replaced_without_flushing_other_keys(cache):
    key = cache.key("osm", "https://example.org/osm", {}, 60)
    cache.client.hset(key, "bad", "type")
    sentinel = f"{cache.prefix}:sentinel"
    cache.client.set(sentinel, "keep", ex=60)
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"42"))
    ) as client:
        result = cached_get(client, "https://example.org/osm", {}, 60, cache, "osm", int)
    assert result[0] == 42 and result[2]["read"] == "invalid"
    assert cache.client.get(sentinel) == b"keep" and cache.client.ttl(key) > 0
