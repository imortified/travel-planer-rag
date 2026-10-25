from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.destinations import RealDestination


class XoteloStay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination: RealDestination
    hotel_key: str = Field(pattern=r"^g[1-9][0-9]*-d[1-9][0-9]*$")
    checkin: date
    checkout: date
    rooms: int = Field(default=1, strict=True, ge=1, le=6)
    list_offset: int = Field(default=0, strict=True, ge=0, le=5000)

    @model_validator(mode="after")
    def interval(self):
        if not 1 <= (self.checkout - self.checkin).days <= 30:
            raise ValueError("Укажите от 1 до 30 ночей")
        return self


class XoteloChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quote_id: UUID
    rate_code: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    apply_base_estimate: bool = Field(default=False, strict=True)
