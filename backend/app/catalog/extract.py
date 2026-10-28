"""Normalize a local Geofabrik extract; unknown fees and hours remain unknown."""

import json
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import HttpUrl, TypeAdapter

from app.core.destinations import EXTRACT_REGIONS
from app.integrations.curated import enrich


def public_url(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme in ("https", "http") and parsed.hostname and not (
            parsed.username or parsed.password
        ):
            return str(TypeAdapter(HttpUrl).validate_python(value))
    except ValueError:
        pass
    return None


def extracted_source(path, city, curated, travel_date, window_parser):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        raw.get("country") != "PT"
        or not raw.get("source_timestamp")
        or not raw.get("downloaded_at")
    ):
        raise ValueError("Portugal extract requires a country and upstream timestamp")
    pois = []
    for record in raw["pois"][city.id]:
        tags = record["tags"]
        food = tags.get("amenity") in ("restaurant", "cafe", "fast_food", "food_court")
        category = (
            "food" if food else "nature"
            if tags.get("leisure") in ("garden", "park")
            or tags.get("tourism") in ("zoo", "aquarium", "viewpoint")
            else "art"
            if tags.get("tourism") == "gallery" or tags.get("museum") == "art"
            else "history"
        )
        issues = []
        if tags.get("access") in ("private", "no", "customers"):
            issues.append("restricted_access")
        if tags.get("fee:conditional") or tags.get("access:conditional"):
            issues.append("unsupported_conditional_access_or_fee")
        pois.append(
            {
                "id": record["id"],
                "name": record["name"],
                "latitude": record["latitude"],
                "longitude": record["longitude"],
                "category": category,
                "cost_per_person": 0 if not food and tags.get("fee") == "no" else None,
                "duration": 60,
                "duration_source": "planning_policy_60_minutes",
                "opening_hours": tags.get("opening_hours"),
                "fee": tags.get("fee"),
                "source_url": record["source_url"],
                "location_kind": record["location_kind"],
                "issues": issues,
                **({"amenity": tags["amenity"]} if food else {}),
                **({"website_url": url} if (
                    url := public_url(tags.get("website") or tags.get("contact:website"))
                ) else {}),
                **({"menu_url": url} if (
                    url := public_url(tags.get("website:menu") or tags.get("contact:menu"))
                ) else {}),
            }
        )
    if not pois:
        raise ValueError("Empty extracted city cannot replace a published catalog")
    pois, _ = enrich(pois, travel_date, window_parser, catalog=curated)
    return {
        "destination": city.id,
        "origin": city.origin,
        "pois": pois,
        "fetched_at": raw["downloaded_at"],
        "source_upstream_asof": raw["source_timestamp"],
        "source": raw["source"],
        "bbox": ",".join(str(value) for value in EXTRACT_REGIONS[city.id]),
        "attribution": "© OpenStreetMap contributors, ODbL 1.0; Geofabrik Portugal extract; "
        "individual official sources for reviewed overrides",
    }
