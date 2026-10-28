from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.destinations import RealDestination

AIRPORTS = {"Lisbon": "LIS", "Porto": "OPO", "Sintra": "LIS"}
Iata = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class FlightSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination: RealDestination
    origin: Iata
    departure: date
    return_date: date | None = None
    market: Literal["ru", "us"] = "ru"
    direct: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def check_dates(self):
        if self.origin == AIRPORTS[self.destination]:
            raise ValueError("Города или аэропорты вылета и назначения должны различаться")
        if self.return_date and not 0 <= (self.return_date - self.departure).days <= 365:
            raise ValueError("Обратный вылет должен быть не раньше первого, в пределах года")
        return self


class FlightChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation_id: UUID
    option_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
