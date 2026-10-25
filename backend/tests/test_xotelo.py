from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cache_helpers import MemoryRedis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.integrations.http_cache import RedisHttpCache, SourceUnavailable
from app.integrations.xotelo import Xotelo, amount
from app.schemas.xotelo import XoteloStay


def stay(rooms=1):
    day = (datetime.now(timezone.utc) + timedelta(days=7)).date()
    return XoteloStay(
        destination="Lisbon",
        hotel_key="g189158-d13402042",
        checkin=day,
        checkout=day + timedelta(days=2),
        rooms=rooms,
    )


def response(request, empty_children=False):
    params = request.url.params
    if request.url.path.endswith("/list"):
        result = {
            "offset": int(params["offset"]),
            "limit": 30,
            "total_count": 1,
            "list": [
                {
                    "key": "g189158-d13402042",
                    "name": "Hotel Da Baixa",
                    "url": "https://www.tripadvisor.com/Hotel_Review-g189158-d13402042-Reviews-Hotel_Da_Baixa.html",
                    "geo": {"latitude": 38.712166, "longitude": -9.137548},
                }
            ],
        }
    else:
        rates = (
            []
            if empty_children and "age_of_children" in params
            else [{"code": "BookingCom", "name": "Booking.com", "rate": 100.50, "tax": None}]
        )
        result = {
            "chk_in": params["chk_in"],
            "chk_out": params["chk_out"],
            "currency": "EUR",
            "rates": rates,
        }
    return {
        "error": None,
        "timestamp": datetime.now(timezone.utc).timestamp() * 1000,
        "result": result,
    }


def test_cache_hits_retain_observation_times_and_distinguish_entire_guest_query():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response(request))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = Xotelo(RedisHttpCache(MemoryRedis(), prefix="test:xotelo"), client)
        first = provider.rates(stay(), 2, [])
        second = provider.rates(stay(), 2, [])
        assert len(requests) == 1 and second["cache"]["read"] == "hit"
        assert (
            first["fetched_at"] == second["fetched_at"]
            and first["expires_at"] == second["expires_at"]
        )
        assert first["rates"][0] == {
            "code": "BookingCom",
            "name": "Booking.com",
            "nightly_base": 10050,
            "tax_reported": None,
            "estimated_base_total": 20100,
        }
        provider.rates(stay(), 2, [8])
        provider.rates(stay(2), 4, [])
        changed = stay().model_copy(update={"checkout": stay().checkout + timedelta(days=1)})
        provider.rates(changed, 2, [])
        assert len(requests) == 4
        assert requests[1].url.params["age_of_children"] == "8"
        multi = provider.rates(stay(2), 4, [])
        assert multi["rates"][0]["estimated_base_total"] is None
        assert requests[2].url.params["rooms"] == "2"


@pytest.mark.parametrize(
    "fault",
    ["currency", "dates", "stale", "future", "negative", "fraction", "error", "tax", "source"],
)
def test_invalid_or_stale_prices_are_never_cached(fault):
    cache = RedisHttpCache(MemoryRedis(), prefix="test:xotelo-invalid")

    def handler(request):
        body = response(request)
        if fault == "currency":
            body["result"]["currency"] = "USD"
        if fault == "dates":
            body["result"]["chk_out"] = "1999-01-01"
        if fault == "stale":
            body["timestamp"] -= 3_600_000
        if fault == "future":
            body["timestamp"] += 3_600_000
        if fault == "negative":
            body["result"]["rates"][0]["rate"] = -1
        if fault == "fraction":
            body["result"]["rates"][0]["rate"] = 1.234
        if fault == "error":
            body["error"] = {"message": "blocked"}
        if fault == "tax":
            body["result"]["rates"][0]["tax"] = "unknown"
        if fault == "source":
            body["result"]["rates"][0]["code"] = "<script>"
        return httpx.Response(200, json=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceUnavailable):
            Xotelo(cache, client).rates(stay(), 2, [])
    assert not cache.client.values


def test_upstream_failure_does_not_retry_or_use_an_expired_cache():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(429)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceUnavailable):
            Xotelo(RedisHttpCache(MemoryRedis()), client).rates(stay(), 2, [])
    assert len(requests) == 1


def test_redis_failure_still_allows_fresh_validated_upstream_data():
    class Unavailable(MemoryRedis):
        def get(self, key):
            raise RedisConnectionError("offline")

    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response(req)))
    ) as client:
        result = Xotelo(RedisHttpCache(Unavailable()), client).rates(stay(), 2, [])
    assert result["cache"]["read"] == "unavailable" and result["cache"]["write"] == "skipped"


def test_empty_family_response_is_distinct_and_never_replaced_with_adult_prices():
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response(req, True)))
    ) as client:
        result = Xotelo(RedisHttpCache(MemoryRedis()), client).rates(stay(), 1, [8])
    assert result["rates"] == [] and result["query"]["age_of_children"] == "8"


def test_listing_checks_source_identity_and_rejects_foreign_links():
    def handler(request):
        body = response(request)
        body["result"]["list"][0]["url"] = "https://example.org/g189158-d13402042"
        return httpx.Response(200, json=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceUnavailable):
            Xotelo(RedisHttpCache(MemoryRedis()), client).hotels("Lisbon")


@pytest.mark.parametrize("value", [None, True, -1, float("nan"), float("inf"), "€100"])
def test_money_does_not_invent_a_value(value):
    with pytest.raises(ValueError):
        amount(value)
