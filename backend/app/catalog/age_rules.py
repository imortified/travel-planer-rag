"""Source-reviewed ordinary age tariffs. No inferred residency, membership or family offers."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class AgeBand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1, max_length=60)
    label: str = Field(min_length=1, max_length=200)
    min_age: int = Field(strict=True, ge=0, le=120)
    max_age: int = Field(strict=True, ge=0, le=120)
    cost: int = Field(strict=True, ge=0, le=1_000_000_000)

    @model_validator(mode="after")
    def interval(self):
        if self.min_age > self.max_age:
            raise ValueError("Invalid age band")
        return self


class AgeRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal["age-tariffs-v1"] = "age-tariffs-v1"
    checked_on: date
    valid_until: date
    source_url: HttpUrl
    bands: list[AgeBand] = Field(default_factory=list, max_length=20)
    min_age: int | None = Field(default=None, strict=True, ge=0, le=120)
    max_age: int | None = Field(default=None, strict=True, ge=0, le=120)
    accompanied_under: int | None = Field(default=None, strict=True, ge=1, le=120)
    companion_min_age: int = Field(default=18, strict=True, ge=1, le=120)
    notes: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def consistency(self):
        if self.valid_until < self.checked_on:
            raise ValueError("Invalid age-rule review interval")
        if self.min_age is not None and self.max_age is not None and self.min_age > self.max_age:
            raise ValueError("Invalid admission age range")
        bands = sorted(self.bands, key=lambda band: band.min_age)
        if len({band.id for band in bands}) != len(bands) or any(
            a.max_age >= b.min_age for a, b in zip(bands, bands[1:])
        ):
            raise ValueError("Duplicate or overlapping age bands")
        return self
