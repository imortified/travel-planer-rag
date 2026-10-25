"""Cached price observations, never passenger-specific quotes or complete flight segments."""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx

from app.core.config import settings
from app.integrations.http_cache import RedisHttpCache, SourceUnavailable, cached_get
from app.integrations.xotelo import amount
from app.schemas.flights import AIRPORTS

URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def aware(value):
    if not isinstance(value, str):
        raise ValueError("Missing flight timestamp")
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise ValueError("Flight timestamp needs timezone")
    return stamp


def search_link(value):
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) > 16000
        or any(c in value for c in ("\\", "\r", "\n", "\t"))
    ):
        raise ValueError("Invalid flight search link")
    url = urlsplit(value)
    if not url.path.startswith("/search/") or url.fragment:
        raise ValueError("Not a flight search link")
    if url.netloc:
        if (
            url.scheme != "https"
            or url.hostname not in {"www.aviasales.com", "aviasales.com"}
            or url.username
            or url.password
            or url.port
        ):
            raise ValueError("Unexpected flight search host")
    elif url.scheme or value.startswith("//"):
        raise ValueError("Invalid relative flight search link")
    return "https://www.aviasales.com" + url.path + ("?" + url.query if url.query else "")


def code(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{3}", value):
        raise ValueError("Invalid airport code")
    return value


def normalize(row, query):
    if not isinstance(row, dict):
        raise ValueError("Invalid flight observation")
    if row.get("origin") != query["origin"] or row.get("destination") != query["destination"]:
        raise ValueError("Flight direction mismatch")
    departure = aware(row.get("departure_at"))
    returning = aware(row["return_at"]) if row.get("return_at") else None
    if str(departure.date()) != query["departure_at"] or (
        str(returning.date()) if returning else None
    ) != query.get("return_at"):
        raise ValueError("Flight dates mismatch")
    if returning and returning <= departure:
        raise ValueError("Invalid return chronology")
    expiry = aware(row["expires_at"]) if row.get("expires_at") else None
    if expiry and expiry <= datetime.now(timezone.utc):
        raise ValueError("Provider observation expired")
    transfers = row.get("transfers")
    back_transfers = row.get("return_transfers") if returning else None
    if (
        type(transfers) is not int
        or not 0 <= transfers <= 10
        or (returning and (type(back_transfers) is not int or not 0 <= back_transfers <= 10))
    ):
        raise ValueError("Invalid transfers")
    if query["direct"] == "true" and (transfers or back_transfers):
        raise ValueError("Nonstop filter mismatch")
    airline, flight = row.get("airline"), row.get("flight_number")
    if (
        not isinstance(airline, str)
        or not re.fullmatch(r"[A-Z0-9]{2,3}", airline)
        or type(flight) not in (str, int)
        or not re.fullmatch(r"[A-Za-z0-9 -]{1,20}", str(flight))
    ):
        raise ValueError("Invalid carrier identity")
    item = {
        "origin": row["origin"],
        "destination": row["destination"],
        "origin_airport": code(row.get("origin_airport")),
        "destination_airport": code(row.get("destination_airport")),
        "departure_at": departure.isoformat(),
        "return_at": returning.isoformat() if returning else None,
        "observed_price": amount(row.get("price")),
        "currency": "EUR",
        "airline": airline,
        "flight_number": str(flight),
        "transfers": transfers,
        "return_transfers": back_transfers,
        "search_url": search_link(row.get("link")),
        "provider_expires_at": expiry.isoformat() if expiry else None,
        "source_found_at": None,
        "segments_complete": False,
    }
    # Durations in cached results need not describe all layovers. Never derive arrival.
    for key in ("duration_to", "duration_back"):
        value = row.get(key)
        if value is not None and (type(value) is not int or not 0 <= value <= 43200):
            raise ValueError("Invalid reported flight duration")
        item[key] = value
    item["option_id"] = digest(item)[:32]
    return item


class Aviasales:
    def __init__(self, cache=None, client=None, token=None):
        self.cache = cache if cache is not None else RedisHttpCache()
        self.client = client
        self.token = (
            token
            if token is not None
            else (
                settings.travelpayouts_api_token.get_secret_value()
                if settings.travelpayouts_api_token
                else ""
            )
        )

    def search(self, search):
        if not self.token.strip():
            raise SourceUnavailable("Aviasales token is not configured")
        query = {
            "origin": search.origin,
            "destination": AIRPORTS[search.destination],
            "departure_at": str(search.departure),
            "currency": "eur",
            "market": search.market,
            "one_way": "false" if search.return_date else "true",
            "direct": str(search.direct).lower(),
            "limit": 10,
            "page": 1,
            "sorting": "price",
        }
        if search.return_date:
            query["return_at"] = str(search.return_date)

        def validate(raw):
            if self.token.encode() in raw:
                raise ValueError("Unexpected credential in provider response")
            body = json.loads(raw)
            if (
                not isinstance(body, dict)
                or body.get("success") is not True
                or body.get("currency") != "eur"
            ):
                raise ValueError("Invalid provider envelope or currency")
            rows = body.get("data")
            if rows == {}:
                rows = []
            if not isinstance(rows, list) or len(rows) > 10:
                raise ValueError("Invalid flight list")
            options = [normalize(row, query) for row in rows]
            return {"query": query, "options": list({o["option_id"]: o for o in options}.values())}

        def execute(client):
            ttl = settings.aviasales_cache_ttl_seconds
            data, fetched, cache = cached_get(
                client,
                URL,
                query,
                ttl,
                self.cache,
                "aviasales",
                validate,
                attempts=1,
                headers={"X-Access-Token": self.token},
            )
            expires = datetime.fromisoformat(fetched) + timedelta(seconds=ttl)
            for option in data["options"]:
                if option["provider_expires_at"]:
                    expires = min(expires, aware(option["provider_expires_at"]))
            return {
                **data,
                "fetched_at": fetched,
                "expires_at": expires.isoformat(),
                "source_found_at": None,
                "price_kind": "provider_cached_observation",
                "group_total_confirmed": False,
                "booking_status": "not_booked",
                "cache": {k: v for k, v in cache.items() if k != "key"},
            }

        if self.client is not None:
            return execute(self.client)
        with httpx.Client(
            timeout=settings.aviasales_timeout_seconds, follow_redirects=False
        ) as client:
            return execute(client)
