"""Versioned reference facts; overrides never erase their OSM originals."""

import json
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StrictInt, model_validator

from app.core.config import ROOT


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str
    name: str
    category: Literal["history", "nature", "art", "food"]
    cost_per_person: StrictInt = Field(ge=0)
    duration: int = Field(ge=60, le=240)
    opening_hours: str
    price_source: HttpUrl
    hours_source: HttpUrl
    notes: str
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    coordinates_source: HttpUrl | None = None
    checked_on: date | None = None
    valid_until: date | None = None
    price_policy: str | None = Field(default=None, min_length=1, pattern=r"\S")
    free_weekdays: list[Literal[0, 1, 2, 3, 4, 5, 6]] | None = None
    free_dates: list[date] | None = None
    restricted_access: bool = False

    @model_validator(mode="after")
    def coordinates(self):
        if (self.checked_on is None) != (self.valid_until is None):
            raise ValueError("Entry review dates must be supplied together")
        if self.category == "food" and (not self.price_policy or not self.notes.strip()):
            raise ValueError("Food POI requires an explicit priced order and notes")
        if self.id.startswith("curated:") and (
            self.latitude is None or self.longitude is None or self.coordinates_source is None
        ):
            raise ValueError("Standalone POI requires coordinates and source")
        return self


class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    checked_on: date
    valid_until: date
    price_policy: str
    pois: list[Entry]

    @model_validator(mode="after")
    def consistency(self):
        if self.valid_until < self.checked_on or len({p.id for p in self.pois}) != len(self.pois):
            raise ValueError("Invalid curated validity range or duplicate ID")
        for entry in self.pois:
            if entry.checked_on is not None and not (
                self.checked_on <= entry.checked_on <= entry.valid_until <= self.valid_until
            ):
                raise ValueError("Entry review dates must be inside catalog review bounds")
        return self


def enrich(
    pois, travel_date, window_parser, path: Path | None = None, catalog: Catalog | None = None
):
    catalog = catalog or Catalog.model_validate(
        json.loads((path or ROOT / "data" / "lisbon-curated.json").read_text(encoding="utf-8"))
    )
    records = {p["id"]: dict(p) for p in pois}
    for entry in catalog.pois:
        checked_on = entry.checked_on or catalog.checked_on
        valid_until = entry.valid_until or catalog.valid_until
        original = records.get(entry.id)
        if original is None and not entry.id.startswith("curated:") and entry.latitude is None:
            continue  # Missing mapped POI must not acquire invented coordinates.
        poi = dict(original or {})
        provenance = {
            "version": catalog.version,
            "checked_on": str(checked_on),
            "valid_until": str(valid_until),
            "price_policy": entry.price_policy or catalog.price_policy,
            "price_source": str(entry.price_source),
            "hours_source": str(entry.hours_source),
            "notes": entry.notes,
        }
        if original:
            provenance["original_osm"] = {
                k: original.get(k) for k in ("name", "fee", "opening_hours", "osm_timestamp")
            }
        else:
            if (
                entry.latitude is None
                or entry.longitude is None
                or entry.coordinates_source is None
            ):
                raise ValueError("Standalone POI requires sourced coordinates")
            poi.update(
                id=entry.id,
                latitude=entry.latitude,
                longitude=entry.longitude,
                source_url=str(entry.coordinates_source),
            )
            provenance["coordinates_source"] = str(entry.coordinates_source)
        window = window_parser(entry.opening_hours, travel_date)
        issues = [
            i
            for i in poi.get("issues", [])
            if i
            not in (
                "unknown_price",
                "unknown_closed_or_unsupported_hours",
            )
        ]
        if not checked_on <= travel_date <= valid_until:
            issues.append("curation_outside_validity")
        if window is None:
            issues.append("unknown_closed_or_unsupported_hours")
        if entry.restricted_access and "restricted_access" not in issues:
            issues.append("restricted_access")
        poi.update(
            name=entry.name,
            category=entry.category,
            cost_per_person=entry.cost_per_person,
            duration=entry.duration,
            duration_source="curated_planning_policy",
            opening_hours=entry.opening_hours,
            window=window,
            issues=issues,
            eligible=not issues,
            provenance=provenance,
        )
        if entry.free_weekdays is not None:
            poi["free_weekdays"] = entry.free_weekdays
        if entry.free_dates is not None:
            poi["free_dates"] = [str(day) for day in entry.free_dates]
        records[entry.id] = poi
    return sorted(records.values(), key=lambda p: p["id"]), catalog.version
