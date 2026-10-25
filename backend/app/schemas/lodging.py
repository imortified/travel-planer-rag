"""Public directory facts are independent of availability, guest eligibility and prices."""

import hashlib
import ipaddress
import json
from datetime import date, datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from app.core.destinations import RealDestination

Kind = Literal["hotel", "hostel", "guest_house", "apartment", "motel"]


def public_website(value):
    if not value:
        return None
    try:
        url = urlsplit(value.strip())
        if url.scheme not in {"http", "https"} or url.username or url.password or url.port:
            return None
        host = url.hostname or ""
        if "." not in host or host.endswith((".local", ".localhost", ".internal", ".test")):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        return value.strip() if len(value) <= 2000 else None
    except (ValueError, AttributeError):
        return None


class LodgingProperty(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^osm:(node|way):[1-9][0-9]*$")
    destination: RealDestination
    name: str = Field(min_length=1, max_length=500, pattern=r"\S")
    kind: Kind
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    location_kind: Literal["mapped_node", "mapped_way_bbox_centre"]
    address: str | None = Field(default=None, max_length=1000)
    website_url: HttpUrl | None = None
    source_url: HttpUrl

    @field_validator("website_url", mode="before")
    @classmethod
    def safe_website(cls, value):
        if value is not None and public_website(str(value)) is None:
            raise ValueError("Website must be a public HTTP(S) URL")
        return value

    @model_validator(mode="after")
    def osm_source(self):
        kind, identifier = self.id.split(":")[1:]
        if str(self.source_url) != f"https://www.openstreetmap.org/{kind}/{identifier}":
            raise ValueError("Lodging source must match its OSM identity")
        return self


class LodgingCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal["osm-lodging-directory-v1"] = "osm-lodging-directory-v1"
    destination: RealDestination
    source: HttpUrl
    source_upstream_asof: datetime
    downloaded_at: datetime
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    attribution: str = "© OpenStreetMap contributors, ODbL 1.0; Geofabrik Portugal extract"
    properties: list[LodgingProperty] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def consistent(self):
        if any(d.tzinfo is None for d in [self.source_upstream_asof, self.downloaded_at]):
            raise ValueError("Source timestamps require timezones")
        if self.source_upstream_asof > self.downloaded_at:
            raise ValueError("Upstream timestamp cannot follow download")
        if any(p.destination != self.destination for p in self.properties):
            raise ValueError("Lodging city mismatch")
        if len({p.id for p in self.properties}) != len(self.properties):
            raise ValueError("Duplicate property identity")
        return self

    def digest(self):
        body = self.model_dump(mode="json")
        body["properties"] = sorted(body["properties"], key=lambda p: p["id"])
        return hashlib.sha256(
            json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()


class LodgingSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    property_id: str = Field(pattern=r"^osm:(node|way):[1-9][0-9]*$")
    catalog_version_id: int = Field(strict=True, gt=0)
    checkin: date
    checkout: date
    rooms: int = Field(default=1, strict=True, ge=1, le=6)

    @model_validator(mode="after")
    def interval(self):
        if not 1 <= (self.checkout - self.checkin).days <= 30:
            raise ValueError("Проживание должно содержать от 1 до 30 ночей")
        return self
