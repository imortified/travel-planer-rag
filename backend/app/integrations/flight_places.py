"""Public city/airport suggestions; no affiliate token or passenger data is sent."""

import json
import unicodedata
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from app.integrations.http_cache import RedisHttpCache, cached_get
from app.schemas.flights import Iata

URL = "https://autocomplete.travelpayouts.com/places2"
TTL = 86400


class FlightPlace(BaseModel):
    type: Literal["city", "airport"]
    code: Iata
    name: str = Field(min_length=1, max_length=200)
    country_name: str = Field(min_length=1, max_length=200)
    city_code: Iata | None = None
    city_name: str | None = Field(default=None, max_length=200)


def search_term(value):
    term = unicodedata.normalize("NFC", value).strip()
    if not 2 <= len(term) <= 80 or any(unicodedata.category(c).startswith("C") for c in term):
        raise ValueError("Введите от 2 до 80 символов названия города или аэропорта")
    return term


def parse_places(raw):
    if len(raw) > 512000:
        raise ValueError("Oversized place response")
    rows = json.loads(raw)
    if not isinstance(rows, list) or len(rows) > 300:
        raise ValueError("Invalid place list")
    places = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid place")
        if row.get("type") not in ("city", "airport"):
            continue
        place = FlightPlace.model_validate(
            {key: row.get(key) for key in FlightPlace.model_fields}
        ).model_dump()
        places[(place["type"], place["code"])] = place
    # Keep provider relevance within each type, with city-wide searches first.
    return sorted(places.values(), key=lambda p: p["type"] != "city")[:20]


class FlightPlaces:
    def __init__(self, cache=None, client=None):
        self.cache = cache if cache is not None else RedisHttpCache()
        self.client = client

    def search(self, term):
        params = {"term": search_term(term), "locale": "ru", "types[]": ["city", "airport"]}

        def execute(client):
            places, fetched, cache = cached_get(
                client,
                URL,
                params,
                TTL,
                self.cache,
                "flight_places",
                parse_places,
                attempts=1,
            )
            return {
                "places": places,
                "fetched_at": fetched,
                "cache": {k: v for k, v in cache.items() if k != "key"},
            }

        if self.client is not None:
            return execute(self.client)
        with httpx.Client(timeout=httpx.Timeout(10, connect=3), follow_redirects=False) as client:
            return execute(client)
