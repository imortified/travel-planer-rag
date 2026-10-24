"""One-city OSM catalog and OSRM driving matrix. No invented prices or opening hours."""

import json
import math
import xml.etree.ElementTree as ET
from dataclasses import asdict
from datetime import date, datetime, timedelta
from datetime import time as day_time
from functools import lru_cache

import httpx
from opening_hours import OpeningHours, ParserError

from app.core.config import settings
from app.core.destinations import city_for
from app.integrations.curated import enrich
from app.integrations.http_cache import RedisHttpCache, SourceUnavailable, cached_get
from app.planning.candidates import CANDIDATE_LIMIT, PERIOD_POOL_LIMIT, POLICY, select_candidates
from app.planning.models import Place

BBOX = "-9.145,38.708,-9.130,38.720"
ORIGIN = {"id": "start", "name": "Rossio", "longitude": -9.1395, "latitude": 38.7135}


class IncompleteData(Exception):
    pass


def opening_window(expression, travel_date):
    result = cached_opening_window(expression, travel_date)
    return list(result) if result else None


@lru_cache(maxsize=8192)
def cached_opening_window(expression, travel_date):
    if not expression:
        return None
    try:
        # Evaluate local wall-clock time for the chosen date. Never use the server's date.
        hours = OpeningHours(expression)
        midnight = datetime.combine(travel_date, day_time())
        opened = [m for m in range(1440) if hours.is_open(midnight + timedelta(minutes=m))]
        if not opened or opened != list(range(opened[0], opened[-1] + 1)):
            return None  # Split or unknown windows cannot be flattened safely.
        return (opened[0], opened[-1] + 1)
    except (ValueError, TypeError, ParserError):
        return None


def parse_catalog(body: bytes, travel_date: date, bbox=BBOX):
    root = ET.fromstring(body)
    west, south, east, north = map(float, bbox.split(","))
    if root.tag != "osm":
        raise ValueError("Expected OSM XML")
    pois = []
    # Node coordinates identify actual mapped points; way/relation centres are not entrances.
    for node in root.findall("node"):
        tags = {t.attrib["k"]: t.attrib["v"] for t in node.findall("tag")}
        lat, lon = float(node.attrib["lat"]), float(node.attrib["lon"])
        if not math.isfinite(lat) or not math.isfinite(lon):
            raise ValueError("Invalid OSM coordinates")
        if not south <= lat <= north or not west <= lon <= east:
            continue
        if not tags.get("name") or tags.get("tourism") not in ("museum", "attraction", "viewpoint"):
            continue
        window = opening_window(tags.get("opening_hours"), travel_date)
        cost = 0 if tags.get("fee") == "no" else None
        issues = []
        if cost is None:
            issues.append("unknown_price")
        if window is None:
            issues.append("unknown_closed_or_unsupported_hours")
        if tags.get("access") in ("private", "no", "customers"):
            issues.append("restricted_access")
        if tags.get("fee:conditional") or tags.get("access:conditional"):
            issues.append("unsupported_conditional_access_or_fee")
        identifier = f"osm:node:{node.attrib['id']}"
        pois.append(
            {
                "id": identifier,
                "name": tags["name"],
                "latitude": lat,
                "longitude": lon,
                "category": "nature" if tags.get("tourism") == "viewpoint" else "history",
                "cost_per_person": cost,
                "window": window,
                "duration": 60,
                "duration_source": "planning_policy_60_minutes",
                "opening_hours": tags.get("opening_hours"),
                "fee": tags.get("fee"),
                "source_url": f"https://www.openstreetmap.org/node/{node.attrib['id']}",
                "osm_timestamp": node.get("timestamp"),
                "issues": issues,
                "eligible": not issues,
            }
        )
    return sorted(pois, key=lambda p: p["id"])


def parse_matrix(body, points):
    try:
        data = json.loads(body)
        matrix = data["durations"]
        if (
            data["code"] != "Ok"
            or not isinstance(matrix, list)
            or len(matrix) != len(points)
            or any(not isinstance(row, list) or len(row) != len(points) for row in matrix)
        ):
            raise ValueError("Invalid matrix dimensions")
        travel = {}
        for i, row in enumerate(matrix):
            for j, seconds in enumerate(row):
                if seconds is not None:
                    if (
                        type(seconds) not in (float, int)
                        or not math.isfinite(seconds)
                        or seconds < 0
                    ):
                        raise ValueError("Invalid duration")
                    if i != j:
                        travel[points[i]["id"], points[j]["id"]] = math.ceil(seconds / 60)
        return data, travel
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid OSRM matrix") from exc


class CityProvider:
    def __init__(
        self,
        client=None,
        cache=None,
        curated_path=None,
        *,
        live_catalog=False,
        catalog_source=None,
        destination="Lisbon",
        preferences=None,
    ):
        self.city = city_for(destination)
        self.preferences = preferences or {}
        self.selection_state = {}
        self.client = client
        self.cache = cache if cache is not None else RedisHttpCache()
        self.curated_path = curated_path or self.city.curated_path
        self.live_catalog = live_catalog
        if catalog_source is None and not live_catalog:
            from app.catalog.reader import CatalogReader

            catalog_source = CatalogReader(destination=destination)
        self.catalog_source = catalog_source

    def with_client(self, client):
        provider = CityProvider(
            client,
            self.cache,
            self.curated_path,
            live_catalog=self.live_catalog,
            catalog_source=self.catalog_source,
            destination=self.city.id,
            preferences=self.preferences,
        )
        provider.selection_state = self.selection_state
        return provider

    def catalog(self, travel_date):
        if not self.live_catalog:
            return self.catalog_source.catalog(travel_date)
        catalog = self.fetch_catalog(travel_date)
        for poi in catalog["pois"]:
            if poi.get("provenance") and (
                travel_date.weekday() in (poi.get("free_weekdays") or [])
                or str(travel_date) in (poi.get("free_dates") or [])
            ):
                poi["cost_per_person"] = 0
        return catalog

    def fetch_catalog(self, travel_date, *, curated_catalog=None, force=False):
        """Explicit upstream fetch for refresh tooling/tests; API reads published snapshots."""
        if self.client is None:
            with httpx.Client(
                timeout=httpx.Timeout(40, connect=5), follow_redirects=True
            ) as client:
                return self.with_client(client).fetch_catalog(
                    travel_date, curated_catalog=curated_catalog, force=force
                )
        try:

            def validate(body):
                try:
                    parsed = parse_catalog(body, travel_date, self.city.bbox)
                    if len({p["id"] for p in parsed}) != len(parsed):
                        raise ValueError("Duplicate OSM POI IDs")
                    return parsed
                except (ET.ParseError, KeyError, TypeError, ValueError) as exc:
                    raise ValueError("Invalid OSM response") from exc

            parsed, fetched, cache_info = cached_get(
                self.client,
                settings.osm_map_url,
                {"bbox": self.city.bbox},
                settings.osm_cache_ttl_seconds,
                self.cache,
                "osm",
                validate,
                bypass_cache=force,
            )
            pois, version = enrich(
                parsed, travel_date, opening_window, self.curated_path, catalog=curated_catalog
            )
        except (ET.ParseError, KeyError, ValueError) as exc:
            raise SourceUnavailable("Invalid OSM response") from exc
        return {
            "destination": self.city.id,
            "currency": "EUR",
            "travel_date": str(travel_date),
            "timezone": self.city.timezone,
            "origin": self.city.origin,
            "pois": pois,
            "fetched_at": fetched,
            "source": settings.osm_map_url,
            "catalog_version": version,
            "osm_poi_count": len(parsed),
            "cache": {"osm": cache_info},
            "attribution": "© OpenStreetMap contributors, ODbL 1.0",
        }

    def dataset(
        self,
        travel_date,
        mandatory=frozenset(),
        allow_empty=False,
        semantic_query=None,
        excluded=frozenset(),
    ):
        if self.client is None:
            with httpx.Client(
                timeout=httpx.Timeout(40, connect=5), follow_redirects=True
            ) as client:
                return self.with_client(client).dataset(
                    travel_date, mandatory, allow_empty, semantic_query, excluded
                )
        catalog = self.catalog(travel_date)
        if len(mandatory) > CANDIDATE_LIMIT:
            raise IncompleteData(f"At most {CANDIDATE_LIMIT} mandatory POI are supported")
        if (catalog.get("catalog_snapshot") or {}).get("status") == "expired":
            raise SourceUnavailable("Сохранённый каталог устарел. Обновите данные перед расчётом.")
        if "pool" not in self.selection_state:
            self.selection_state["pool"] = {
                p["id"]
                for p in select_candidates(
                    [p for p in catalog["pois"] if p["id"] not in excluded or p["id"] in mandatory],
                    mandatory,
                    self.preferences,
                    limit=PERIOD_POOL_LIMIT,
                )
            }
        usable = [
            p
            for p in catalog["pois"]
            if p["eligible"] and p["id"] not in excluded and p["id"] in self.selection_state["pool"]
        ]
        if not allow_empty and mandatory - {p["id"] for p in usable}:
            raise IncompleteData(
                "Mandatory POI absent or missing supported hours/price/access data"
            )
        retrieval = None
        if semantic_query:
            from app.nlp.retrieval import Retrieval

            usable = [p for p in usable if p["id"] not in excluded or p["id"] in mandatory]
            retriever = (
                Retrieval() if self.city.id == "Lisbon" else Retrieval(destination=self.city.id)
            )
            retrieval = retriever.search(
                semantic_query, [p for p in usable if p["id"] not in excluded]
            )
            if retrieval.get("can_apply") is False:
                raise IncompleteData(retrieval["message"])
        relevance = {m["place_id"]: m["score"] for m in retrieval["matches"]} if retrieval else {}
        selected = select_candidates(
            usable, mandatory, self.preferences, relevance, offset=travel_date.toordinal()
        )
        if allow_empty and not selected:
            return (
                [],
                {},
                {
                    **catalog,
                    "pois": [],
                    "transport": "driving",
                    "places_snapshot": [],
                    "travel_minutes": [],
                },
            )
        if not selected:
            raise IncompleteData("No eligible POI for this date")
        points = [catalog["origin"]] + selected
        coordinates = ";".join(f"{p['longitude']},{p['latitude']}" for p in points)
        url = f"{settings.osrm_url.rstrip('/')}/table/v1/driving/{coordinates}"
        (data, travel), fetched, cache_info = cached_get(
            self.client,
            url,
            {"annotations": "duration"},
            settings.osrm_cache_ttl_seconds,
            self.cache,
            "osrm",
            lambda body: parse_matrix(body, points),
        )
        places = [
            Place(
                p["id"],
                p["category"],
                p["cost_per_person"],
                p["duration"],
                p["window"][0],
                p["window"][1],
            )
            for p in selected
        ]
        context = {
            **catalog,
            "pois": selected,
            "catalog_count": len(catalog["pois"]),
            "eligible_count": len(usable),
            "candidate_limit": CANDIDATE_LIMIT,
            "candidate_policy": POLICY,
            "period_pool_limit": PERIOD_POOL_LIMIT,
            "routing_fetched_at": fetched,
            "routing_source": settings.osrm_url,
            "cache": {**catalog.get("cache", {}), "osrm": cache_info},
            "routing_waypoints": data.get("sources"),
            "transport": "driving",
            "places_snapshot": [asdict(p) for p in places],
            "travel_minutes": [
                {"from": a, "to": b, "minutes": t} for (a, b), t in sorted(travel.items())
            ],
            "limitations": [
                "OSM data is not an official guarantee of opening or price",
                "Mapped city POI plus source-verified curated locations",
                "Visit durations are planning policy, not source facts",
                "Regular adult admission and explicit food orders per participant; "
                "other meals, extras and discounts excluded",
                "Driving estimates exclude traffic, parking and transport costs",
                "Travel is between OSRM-snapped road points, not door-to-door",
                "No return trip or weather checks; visa requirements are in a separate report",
            ],
        }
        if retrieval:
            context["retrieval"] = {
                **retrieval,
                "candidate_policy": POLICY,
            }
        return places, travel, context
