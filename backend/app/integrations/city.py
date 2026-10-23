"""One-city OSM catalog and OSRM driving matrix. No invented prices or opening hours."""

import json
import math
import xml.etree.ElementTree as ET
from dataclasses import asdict
from datetime import date, datetime, timedelta
from datetime import time as day_time

import httpx
from opening_hours import OpeningHours, ParserError

from app.core.config import settings
from app.integrations.curated import enrich
from app.integrations.http_cache import RedisHttpCache, SourceUnavailable, cached_get
from app.planning.models import Place

BBOX = "-9.145,38.708,-9.130,38.720"
ORIGIN = {"id": "start", "name": "Rossio", "longitude": -9.1395, "latitude": 38.7135}


class IncompleteData(Exception):
    pass


def opening_window(expression, travel_date):
    if not expression:
        return None
    try:
        # Evaluate local wall-clock time for the chosen date. Never use the server's date.
        hours = OpeningHours(expression)
        midnight = datetime.combine(travel_date, day_time())
        opened = [m for m in range(1440) if hours.is_open(midnight + timedelta(minutes=m))]
        if not opened or opened != list(range(opened[0], opened[-1] + 1)):
            return None  # Split or unknown windows cannot be flattened safely.
        return [opened[0], opened[-1] + 1]
    except (ValueError, TypeError, ParserError):
        return None


def parse_catalog(body: bytes, travel_date: date):
    root = ET.fromstring(body)
    if root.tag != "osm":
        raise ValueError("Expected OSM XML")
    pois = []
    # Node coordinates identify actual mapped points; way/relation centres are not entrances.
    for node in root.findall("node"):
        tags = {t.attrib["k"]: t.attrib["v"] for t in node.findall("tag")}
        lat, lon = float(node.attrib["lat"]), float(node.attrib["lon"])
        if not 38.708 <= lat <= 38.720 or not -9.145 <= lon <= -9.130:
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
    def __init__(self, client=None, cache=None, curated_path=None):
        self.client = client
        self.cache = cache if cache is not None else RedisHttpCache()
        self.curated_path = curated_path

    def catalog(self, travel_date):
        if self.client is None:
            with httpx.Client(
                timeout=httpx.Timeout(40, connect=5), follow_redirects=True
            ) as client:
                return CityProvider(client, self.cache, self.curated_path).catalog(travel_date)
        try:

            def validate(body):
                try:
                    return parse_catalog(body, travel_date)
                except (ET.ParseError, KeyError, TypeError, ValueError) as exc:
                    raise ValueError("Invalid OSM response") from exc

            parsed, fetched, cache_info = cached_get(
                self.client,
                settings.osm_map_url,
                {"bbox": BBOX},
                settings.osm_cache_ttl_seconds,
                self.cache,
                "osm",
                validate,
            )
            pois, version = enrich(parsed, travel_date, opening_window, self.curated_path)
        except (ET.ParseError, KeyError, ValueError) as exc:
            raise SourceUnavailable("Invalid OSM response") from exc
        return {
            "destination": "Lisbon",
            "currency": "EUR",
            "travel_date": str(travel_date),
            "timezone": "Europe/Lisbon",
            "origin": ORIGIN,
            "pois": pois,
            "fetched_at": fetched,
            "source": settings.osm_map_url,
            "catalog_version": version,
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
                return CityProvider(client, self.cache, self.curated_path).dataset(
                    travel_date, mandatory, allow_empty, semantic_query, excluded
                )
        catalog = self.catalog(travel_date)
        usable = [p for p in catalog["pois"] if p["eligible"]]
        if not allow_empty and mandatory - {p["id"] for p in usable}:
            raise IncompleteData(
                "Mandatory POI absent or missing supported hours/price/access data"
            )
        retrieval = None
        if semantic_query:
            from app.nlp.retrieval import Retrieval

            usable = [p for p in usable if p["id"] not in excluded or p["id"] in mandatory]
            retrieval = Retrieval().search(
                semantic_query, [p for p in usable if p["id"] not in excluded]
            )
        relevance = {m["place_id"]: m["score"] for m in retrieval["matches"]} if retrieval else {}
        usable.sort(key=lambda p: (p["id"] not in mandatory, -relevance.get(p["id"], -1), p["id"]))
        selected = usable[:12]
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
        if len(mandatory) > 12 or not selected:
            raise IncompleteData("No eligible POI for this date, or more than 12 mandatory POI")
        points = [ORIGIN] + selected
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
                "OSM nodes in central Lisbon plus source-verified curated locations",
                "Visit durations are planning policy, not source facts",
                "Standard adult entry prices; discounts, bundled tickets and booking fees excluded",
                "Driving estimates exclude traffic, parking and transport costs",
                "Travel is between OSRM-snapped road points, not door-to-door",
                "No return trip, visas or weather checks",
            ],
        }
        if retrieval:
            context["retrieval"] = {
                **retrieval,
                "candidate_policy": "mandatory_then_similarity_cap_12",
            }
        return places, travel, context
