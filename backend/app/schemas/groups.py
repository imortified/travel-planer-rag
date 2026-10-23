from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.planning.alternatives import ComparisonMetrics
from app.planning.models import Result
from app.schemas.assistant import ExplanationOutput

Name = Annotated[str, Field(min_length=1, max_length=100, pattern=r"\S")]
Weight = Annotated[float, Field(ge=0, le=1)]


class NamedInput(BaseModel):
    name: Name


class NamedOutput(NamedInput):
    model_config = ConfigDict(from_attributes=True)
    id: int


class MemberInput(BaseModel):
    user_id: int = Field(gt=0)


class PreferencesInput(BaseModel):
    interests: dict[Name, Weight]


class MemberOutput(BaseModel):
    user_id: int
    name: str
    interests: dict[str, float]


class GroupOutput(NamedOutput):
    members: list[MemberOutput]


class TripInput(BaseModel):
    destination: Literal["Demo City", "Lisbon"] = "Demo City"
    travel_date: date | None = None
    end_date: date | None = None
    currency: Literal["EUR"] = "EUR"
    budget: int = Field(ge=0, le=1_000_000_000)
    start: int = Field(default=540, ge=0, lt=1440)
    end: int = Field(default=1080, gt=0, le=1440)
    mandatory: set[str] = Field(default_factory=set)
    excluded: set[str] = Field(default_factory=set)

    @model_validator(mode="after")
    def check_interval(self):
        if self.end_date is not None:
            if self.travel_date is None or not 0 <= (self.end_date - self.travel_date).days <= 13:
                raise ValueError("Period must have a start date and contain 1-14 days")
        if self.destination == "Lisbon" and self.travel_date is None:
            raise ValueError("travel_date is required for Lisbon")
        if self.start >= self.end:
            raise ValueError("start must precede end")
        return self


class TripOutput(TripInput):
    id: int
    group_id: int
    participants_snapshot: list[MemberOutput]
    created_at: datetime


class RouteOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    trip_request_id: int
    route_set_id: int | None = None
    comparison: ComparisonMetrics | None = None
    explanation: ExplanationOutput | None = None
    algorithm: str
    data_source: str
    result: Result
    created_at: datetime


class SearchInfo(BaseModel):
    method: str
    attempts: int
    attempt_limit: int
    min_jaccard_distance: float
    note_codes: list[str]


class RouteSetOutput(BaseModel):
    id: int
    trip_request_id: int
    algorithm: str
    search_info: SearchInfo
    diagnostic: Result | None
    created_at: datetime
    options: list[RouteOutput]
