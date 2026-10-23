from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TravelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    citizenship: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    passport_kind: Literal["unknown", "ordinary", "special"] = "unknown"
    biometric: bool | None = Field(default=None, strict=True)
    special_entry_basis: bool | None = Field(default=None, strict=True)
    visa_status: Literal["unknown", "held", "not_held"] = "unknown"

    @field_validator("citizenship", mode="before")
    @classmethod
    def normalize_country(cls, value):
        return value.strip().upper() if isinstance(value, str) else value


class VisaTraveller(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100, pattern=r"\S")
    profile: TravelProfile = Field(default_factory=TravelProfile)


class VisaPreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination: Literal["Lisbon", "Demo City"]
    travel_date: date | None = None
    end_date: date | None = None
    purpose: Literal["tourism", "other", "unknown"] = "tourism"
    travellers: list[VisaTraveller] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def dates(self):
        if self.end_date and (
            not self.travel_date or not 0 <= (self.end_date - self.travel_date).days <= 13
        ):
            raise ValueError("Period must contain 1-14 days")
        return self
