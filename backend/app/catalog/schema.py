"""Validated catalog facts. Daily opening windows are derived, never stored here."""

import hashlib
import json
from datetime import date, timedelta
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    HttpUrl,
    StrictInt,
    model_validator,
)

from app.catalog.age_rules import AgeRules


class FactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Origin(FactModel):
    id: Literal["start"] = "start"
    name: str = Field(min_length=1, max_length=500, pattern=r"\S")
    latitude: Annotated[FiniteFloat, Field(ge=-90, le=90)]
    longitude: Annotated[FiniteFloat, Field(ge=-180, le=180)]


class Provenance(FactModel):
    version: str
    checked_on: date
    valid_until: date
    price_policy: str
    price_source: HttpUrl
    hours_source: HttpUrl
    notes: str
    original_osm: dict | None = None
    coordinates_source: HttpUrl | None = None
    validity_kind: Literal["source_period", "review_window"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def validity(self):
        if self.valid_until < self.checked_on:
            raise ValueError("Invalid fact validity interval")
        return self


class PoiFact(FactModel):
    id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=500, pattern=r"\S")
    category: Literal["history", "nature", "art", "food"]
    latitude: Annotated[FiniteFloat, Field(ge=-90, le=90)]
    longitude: Annotated[FiniteFloat, Field(ge=-180, le=180)]
    cost_per_person: Annotated[StrictInt, Field(ge=0, le=1_000_000_000)] | None
    duration: Annotated[StrictInt, Field(ge=1, le=1440)]
    duration_source: str
    opening_hours: str | None
    source_url: HttpUrl
    fee: str | None = None
    osm_timestamp: str | None = None
    issues: list[Literal["restricted_access", "unsupported_conditional_access_or_fee"]] = Field(
        default_factory=list
    )
    provenance: Provenance | None = None
    # Absent on historical snapshots: omitting null preserves their canonical hashes.
    free_weekdays: list[Literal[0, 1, 2, 3, 4, 5, 6]] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    free_dates: list[date] | None = Field(default=None, exclude_if=lambda value: value is None)
    meal_types: list[Literal["breakfast", "lunch", "dinner"]] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    age_rules: AgeRules | None = Field(default=None, exclude_if=lambda value: value is None)
    location_kind: Literal["mapped_node", "mapped_way_bbox_centre"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    amenity: str | None = Field(default=None, exclude_if=lambda value: value is None)
    website_url: HttpUrl | None = Field(default=None, exclude_if=lambda value: value is None)
    menu_url: HttpUrl | None = Field(default=None, exclude_if=lambda value: value is None)


class Manifest(FactModel):
    schema_version: Literal[1] = 1
    destination: str = Field(min_length=1, max_length=100)
    currency: Literal["EUR"] = "EUR"
    timezone: Literal["Europe/Lisbon"] = "Europe/Lisbon"
    origin: Origin
    source: HttpUrl
    bbox: str
    fetched_at: AwareDatetime
    fresh_until: AwareDatetime
    catalog_version: str
    curated_checked_on: date
    curated_valid_until: date
    curated_validity_kind: Literal["source_period", "review_window"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    attribution: str
    source_upstream_asof: AwareDatetime | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def validity(self):
        if not self.fetched_at < self.fresh_until <= self.fetched_at + timedelta(days=7):
            raise ValueError("Invalid catalog freshness interval")
        if self.source_upstream_asof and (
            self.source_upstream_asof > self.fetched_at
            or self.fresh_until > self.source_upstream_asof + timedelta(days=7)
        ):
            raise ValueError("Catalog freshness cannot outlive upstream snapshot freshness")
        if (
            self.curated_valid_until < self.curated_checked_on
            or self.source.username
            or self.source.password
        ):
            raise ValueError("Invalid catalog provenance")
        return self


class SnapshotData(FactModel):
    manifest: Manifest
    pois: tuple[PoiFact, ...] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({p.id for p in self.pois}) != len(self.pois):
            raise ValueError("Duplicate catalog POI IDs")
        if [p.id for p in self.pois] != sorted(p.id for p in self.pois):
            raise ValueError("Catalog records must be sorted")
        for p in self.pois:
            if (
                p.age_rules
                and not self.manifest.curated_checked_on
                <= p.age_rules.checked_on
                <= p.age_rules.valid_until
                <= self.manifest.curated_valid_until
            ):
                raise ValueError("Age tariff review is outside catalog review bounds")
            if p.provenance and (
                p.provenance.version != self.manifest.catalog_version
                or not self.manifest.curated_checked_on
                <= p.provenance.checked_on
                <= p.provenance.valid_until
                <= self.manifest.curated_valid_until
            ):
                raise ValueError("Catalog fact version differs from manifest")
        return self

    def digest(self):
        body = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(body.encode()).hexdigest()
