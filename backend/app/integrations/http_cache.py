"""Redis TTL cache for validated public API responses; outages use fresh upstream data."""

import base64
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache

import httpx
from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError, ResponseError
from redis.retry import Retry

from app.core.config import settings
from app.core.logging import emit, error_kind, request_id

logger = logging.getLogger(__name__)


class SourceUnavailable(Exception):
    pass


class CacheUnavailable(Exception):
    pass


class InvalidCacheEntry(Exception):
    pass


@lru_cache(maxsize=1)
def redis_client():
    return Redis.from_url(
        settings.redis_url,
        decode_responses=False,
        socket_connect_timeout=settings.redis_timeout_seconds,
        socket_timeout=settings.redis_timeout_seconds,
        retry=Retry(NoBackoff(), 0),
        health_check_interval=30,
    )


@dataclass(frozen=True)
class CacheEntry:
    body: bytes
    fetched_at: str


class RedisHttpCache:
    def __init__(self, client=None, prefix=None):
        self.client = client if client is not None else redis_client()
        self.prefix = prefix or settings.redis_cache_prefix
        if not re.fullmatch(r"[a-zA-Z0-9:_-]{1,100}", self.prefix):
            raise ValueError("Invalid Redis cache prefix")

    def key(self, provider, url, params, ttl):
        if provider not in ("osm", "osrm", "xotelo", "aviasales", "flight_places"):
            raise ValueError("Unknown cache provider")
        # The full URL includes OSRM profile and ordered coordinates. Canonical query
        # parameters include annotations/options. TTL changes get a separate key.
        digest = hashlib.sha256(
            json.dumps([url, params, ttl], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return f"{self.prefix}:{provider}:{digest}"

    def get(self, key, ttl):
        try:
            raw = self.client.get(key)
        except ResponseError as exc:
            if str(exc).startswith("WRONGTYPE"):
                raise InvalidCacheEntry("Cache value has an unexpected Redis type") from exc
            raise CacheUnavailable() from exc
        except RedisError as exc:
            raise CacheUnavailable() from exc
        if raw is None:
            return None
        try:
            value = json.loads(raw)
            if type(value["version"]) is not int or value["version"] != 1:
                raise ValueError("Unknown envelope version")
            fetched = datetime.fromisoformat(value["fetched_at"])
            if fetched.utcoffset() is None:
                raise ValueError("Cache timestamp must include timezone")
            age = (datetime.now(timezone.utc) - fetched).total_seconds()
            if not 0 <= age < ttl:
                raise ValueError("Stale or future cache timestamp")
            body = base64.b64decode(value["body"], validate=True)
            if hashlib.sha256(body).hexdigest() != value["sha256"]:
                raise ValueError("Cache checksum mismatch")
            return CacheEntry(body, value["fetched_at"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise InvalidCacheEntry("Invalid cache envelope") from exc

    def put(self, key, body, fetched_at, ttl):
        value = json.dumps(
            {
                "version": 1,
                "body": base64.b64encode(body).decode("ascii"),
                "fetched_at": fetched_at,
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        )
        try:
            if not self.client.set(key, value, ex=ttl):
                raise CacheUnavailable()
        except RedisError as exc:
            raise CacheUnavailable() from exc


def cached_get(
    client,
    url,
    params,
    ttl,
    cache,
    provider,
    validate,
    *,
    bypass_cache=False,
    attempts=2,
    headers=None,
):
    if type(attempts) is not int or not 1 <= attempts <= 2:
        raise ValueError("Invalid upstream attempt limit")
    key = cache.key(provider, url, params, ttl)
    info = {
        "backend": "redis",
        "key": key,
        "ttl_seconds": ttl,
        "read": "miss",
        "write": "not_attempted",
    }
    try:
        entry = None if bypass_cache else cache.get(key, ttl)
        if bypass_cache:
            info["read"] = "bypassed"
        if entry:
            try:
                parsed = validate(entry.body)
            except ValueError:
                raise InvalidCacheEntry("Cached response failed source validation") from None
            emit(
                logger,
                "cache.read",
                provider=provider,
                cache_state="hit",
                ttl_seconds=ttl,
                cache_age_seconds=(
                    datetime.now(timezone.utc) - datetime.fromisoformat(entry.fetched_at)
                ).total_seconds(),
            )
            return parsed, entry.fetched_at, {**info, "read": "hit"}
    except InvalidCacheEntry:
        info["read"] = "invalid"
        emit(logger, "cache.read", logging.WARNING, provider=provider, cache_state="invalid")
    except CacheUnavailable:
        info["read"] = "unavailable"
        emit(logger, "cache.read", logging.WARNING, provider=provider, cache_state="unavailable")

    if info["read"] in ("miss", "bypassed"):
        emit(logger, "cache.read", provider=provider, cache_state=info["read"], ttl_seconds=ttl)

    for attempt in range(attempts):
        started = time.perf_counter()
        try:
            correlation = {"X-Request-ID": request_id.get()} if request_id.get() else {}
            response = client.get(url, params=params, headers={**(headers or {}), **correlation})
            response.raise_for_status()
            emit(
                logger,
                "upstream.request.completed",
                provider=provider,
                method="GET",
                status_code=response.status_code,
                attempt=attempt + 1,
                duration_ms=(time.perf_counter() - started) * 1000,
            )
            break
        except httpx.HTTPError as exc:
            emit(
                logger,
                "upstream.request.failed",
                logging.ERROR if attempt == attempts - 1 else logging.WARNING,
                exception=exc,
                provider=provider,
                method="GET",
                status_code=exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError)
                else None,
                attempt=attempt + 1,
                will_retry=attempt < attempts - 1,
                reason=error_kind(exc),
                duration_ms=(time.perf_counter() - started) * 1000,
            )
            if attempt == attempts - 1:
                raise SourceUnavailable(
                    f"{provider.upper()} unavailable; no stale or synthetic fallback"
                ) from exc
            time.sleep(0.25)
    fetched = datetime.now(timezone.utc).isoformat()
    try:
        parsed = validate(response.content)
    except ValueError as exc:
        emit(
            logger,
            "upstream.response.rejected",
            logging.ERROR,
            exception=exc,
            provider=provider,
            reason="invalid_response",
        )
        raise SourceUnavailable(f"Invalid {provider.upper()} response; not cached") from exc
    if info["read"] == "unavailable":
        info["write"] = "skipped"
    else:
        try:
            cache.put(key, response.content, fetched, ttl)
            info["write"] = "stored"
        except CacheUnavailable:
            info["write"] = "unavailable"
            emit(
                logger, "cache.write", logging.WARNING, provider=provider, cache_state="unavailable"
            )
    if info["write"] != "unavailable":
        emit(logger, "cache.write", provider=provider, cache_state=info["write"], ttl_seconds=ttl)
    return parsed, fetched, info
