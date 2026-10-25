"""Public prices with explicit units and freshness; no booking or stale fallback."""

import json
import math
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import httpx

from app.core.config import settings
from app.integrations.http_cache import RedisHttpCache, cached_get

LOCATIONS = {"Lisbon": "g189158", "Porto": "g189180", "Sintra": "g189164"}
BASE = "https://data.xotelo.com/api/"
LIST_SIZE = 30


def now():
    return datetime.now(timezone.utc)


def timestamp(value, ttl):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Missing provider response timestamp")
    stamp = datetime.fromtimestamp(value / 1000 if value > 100_000_000_000 else value, timezone.utc)
    if not -30 <= (now() - stamp).total_seconds() < ttl:
        raise ValueError("Stale or future provider observation")
    return stamp


def amount(value):
    if type(value) not in (int, float, str, Decimal):
        raise ValueError("Invalid provider amount")
    try:
        number = Decimal(str(value))
        if (
            not number.is_finite()
            or number < 0
            or number > 10_000_000
            or number * 100 != (number * 100).to_integral_value()
        ):
            raise ValueError("Invalid provider amount")
        return int(number * 100)
    except InvalidOperation as exc:
        raise ValueError("Invalid provider amount") from exc


def envelope(raw, ttl):
    try:
        body = json.loads(raw)
        if (
            not isinstance(body, dict)
            or body.get("error") is not None
            or not isinstance(body.get("result"), dict)
        ):
            raise ValueError("Provider returned an error")
        return body["result"], timestamp(body.get("timestamp"), ttl)
    except (TypeError, OverflowError, OSError) as exc:
        raise ValueError("Invalid provider envelope") from exc


def tripadvisor_url(value, hotel_key):
    if not isinstance(value, str):
        raise ValueError("Missing property source URL")
    url = urlsplit(value)
    if (
        url.scheme != "https"
        or url.hostname not in {"www.tripadvisor.com", "tripadvisor.com"}
        or url.username
        or url.password
        or url.port
        or hotel_key not in url.path
    ):
        raise ValueError("Property URL does not match provider identity")
    return value


class Xotelo:
    def __init__(self, cache=None, client=None):
        self.cache = cache if cache is not None else RedisHttpCache()
        self.client = client

    def fetch(self, endpoint, params, ttl, validate):
        def execute(client):
            parsed, fetched, cache = cached_get(
                client, BASE + endpoint, params, ttl, self.cache, "xotelo", validate, attempts=1
            )
            observed = datetime.fromisoformat(fetched)
            expires = min(
                observed + timedelta(seconds=ttl),
                datetime.fromisoformat(parsed["source_timestamp"]) + timedelta(seconds=ttl),
            )
            return {
                **parsed,
                "fetched_at": fetched,
                "expires_at": expires.isoformat(),
                "cache": {k: v for k, v in cache.items() if k != "key"},
            }

        if self.client is not None:
            return execute(self.client)
        with httpx.Client(
            timeout=settings.xotelo_timeout_seconds, follow_redirects=False
        ) as client:
            return execute(client)

    def hotels(self, destination, offset=0):
        params = {"location_key": LOCATIONS[destination], "limit": LIST_SIZE, "offset": offset}

        def validate(raw):
            result, stamp = envelope(raw, settings.xotelo_list_ttl_seconds)
            if (
                result.get("offset") != offset
                or result.get("limit") != LIST_SIZE
                or type(result.get("total_count")) is not int
                or result["total_count"] < 0
                or not isinstance(result.get("list"), list)
                or len(result["list"]) > LIST_SIZE
            ):
                raise ValueError("Invalid property list")
            hotels = []
            for item in result["list"]:
                if not isinstance(item, dict):
                    raise ValueError("Invalid property")
                key, name = item.get("key"), item.get("name")
                if (
                    not isinstance(key, str)
                    or not re.fullmatch(r"g[1-9][0-9]*-d[1-9][0-9]*", key)
                    or not isinstance(name, str)
                    or not 1 <= len(name.strip()) <= 500
                ):
                    raise ValueError("Invalid property identity")
                geo = item.get("geo") or {}
                if not isinstance(geo, dict):
                    raise ValueError("Invalid property coordinates")
                latitude, longitude = geo.get("latitude"), geo.get("longitude")
                if (
                    type(latitude) not in (int, float)
                    or type(longitude) not in (int, float)
                    or not -90 <= latitude <= 90
                    or not -180 <= longitude <= 180
                ):
                    raise ValueError("Invalid property coordinates")
                hotels.append(
                    {
                        "hotel_key": key,
                        "name": name,
                        "latitude": latitude,
                        "longitude": longitude,
                        "source_url": tripadvisor_url(item.get("url"), key),
                    }
                )
            if len({h["hotel_key"] for h in hotels}) != len(hotels):
                raise ValueError("Duplicate property identity")
            return {
                "destination": destination,
                "hotels": hotels,
                "total": result["total_count"],
                "offset": offset,
                "limit": LIST_SIZE,
                "source_timestamp": stamp.isoformat(),
            }

        return self.fetch("list", params, settings.xotelo_list_ttl_seconds, validate)

    def rates(self, stay, adults, child_ages):
        params = {
            "hotel_key": stay.hotel_key,
            "chk_in": str(stay.checkin),
            "chk_out": str(stay.checkout),
            "currency": "EUR",
            "rooms": stay.rooms,
            "adults": adults,
        }
        if child_ages:
            params["age_of_children"] = ",".join(
                str(age if age > 1 else 0) for age in sorted(child_ages)
            )

        def validate(raw):
            result, stamp = envelope(raw, settings.xotelo_rates_ttl_seconds)
            if any(
                result.get(k) != params[k] for k in ("chk_in", "chk_out", "currency")
            ) or not isinstance(result.get("rates"), list):
                raise ValueError("Provider quote query mismatch")
            rates = []
            for item in result["rates"]:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("name"), str)
                    or not 1 <= len(item["name"]) <= 200
                    or not isinstance(item.get("code"), str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", item["code"])
                ):
                    raise ValueError("Invalid rate source")
                base = amount(item.get("rate"))
                rates.append(
                    {
                        "code": item["code"],
                        "name": item["name"],
                        "nightly_base": base,
                        "tax_reported": None if item.get("tax") is None else amount(item["tax"]),
                        "estimated_base_total": base * (stay.checkout - stay.checkin).days
                        if stay.rooms == 1
                        else None,
                    }
                )
            if len({r["code"] for r in rates}) != len(rates):
                raise ValueError("Duplicate rate source")
            return {"rates": rates, "source_timestamp": stamp.isoformat(), "query": params}

        return self.fetch("rates", params, settings.xotelo_rates_ttl_seconds, validate)
