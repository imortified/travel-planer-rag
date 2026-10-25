from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.dates import trip_today
from app.core.destinations import Destination
from app.planning.alternatives import ComparisonMetrics
from app.planning.models import Result
from app.schemas.ages import AgeProfile
from app.schemas.assistant import ExplanationOutput
from app.schemas.flights import FlightChoice
from app.schemas.lodging import LodgingSelection
from app.schemas.trip_costs import BudgetSettings, MealSettings
from app.schemas.visa import TravelProfile
from app.schemas.xotelo import XoteloChoice

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
    saved_traveller_id: int | None = None
    interests: dict[str, float]
    travel_profile: TravelProfile = Field(default_factory=TravelProfile)
    age_profile: AgeProfile = Field(default_factory=AgeProfile)


class GroupOutput(NamedOutput):
    members: list[MemberOutput]


class TripParameters(BaseModel):
    flight_plan: FlightChoice | None = None
    lodging_quote: XoteloChoice | None = None
    lodging_plan: LodgingSelection | None = None
    budget_settings: BudgetSettings | None = None
    meal_settings: MealSettings | None = None
    pace: Literal["unrestricted", "relaxed", "balanced", "intensive"] = "unrestricted"
    destination: Destination = "Demo City"
    travel_date: date | None = None
    end_date: date | None = None
    currency: Literal["EUR"] = "EUR"
    purpose: Literal["tourism", "other", "unknown"] = "tourism"
    budget: int = Field(ge=0, le=1_000_000_000)
    start: int = Field(default=540, ge=0, lt=1440)
    end: int = Field(default=1080, gt=0, le=1440)
    mandatory: set[str] = Field(default_factory=set)
    excluded: set[str] = Field(default_factory=set)
    semantic_query: str | None = Field(default=None, min_length=1, max_length=300, pattern=r"\S")

    @model_validator(mode="after")
    def check_interval(self):
        if self.flight_plan and (
            self.destination == "Demo City"
            or self.travel_date is None
            or self.budget_settings is None
            or self.budget_settings.transport_scope != "ground_only"
        ):
            raise ValueError(
                "Для перелёта нужны реальный город, даты и отдельный бюджет наземного транспорта"
            )
        if self.lodging_quote:
            if self.lodging_plan:
                raise ValueError("Выберите один источник проживания: каталог или Xotelo")
            if (
                self.destination == "Demo City"
                or self.travel_date is None
                or self.budget_settings is None
            ):
                raise ValueError("Для Xotelo нужны реальный город, даты и бюджет всей поездки")
        if self.lodging_plan:
            if self.destination == "Demo City" or self.travel_date is None:
                raise ValueError("Выбор проживания требует реального города и дат поездки")
            if self.budget_settings is None:
                raise ValueError("Для выбранного проживания включите бюджет всей поездки")
            if self.lodging_plan.checkin > self.travel_date or self.lodging_plan.checkout < (
                self.end_date or self.travel_date
            ):
                raise ValueError("Даты проживания должны покрывать период поездки")
        if self.budget_settings and self.budget_settings.committed > self.budget:
            raise ValueError("Жильё, транспорт и резерв превышают общий бюджет поездки")
        if self.meal_settings:
            if self.destination == "Demo City":
                raise ValueError("Планирование питания требует каталога реального города")
            for name, slot in self.meal_settings.slots():
                if min(self.end, slot.end) - max(self.start, slot.start) < slot.duration:
                    raise ValueError(f"Окно {name} не помещается в выбранное время дня")
        if self.semantic_query and self.destination == "Demo City":
            raise ValueError("Semantic retrieval requires a real city")
        if self.end_date is not None:
            if self.travel_date is None or not 0 <= (self.end_date - self.travel_date).days <= 13:
                raise ValueError("Period must have a start date and contain 1-14 days")
        if self.destination != "Demo City" and self.travel_date is None:
            raise ValueError("travel_date is required for a real city")
        if self.start >= self.end:
            raise ValueError("start must precede end")
        return self


class TripInput(TripParameters):
    @model_validator(mode="after")
    def check_new_trip_date(self):
        if self.travel_date is not None and self.travel_date < trip_today(self.destination):
            raise ValueError(
                "Дата начала новой поездки не может быть в прошлом. "
                "Выберите сегодня или будущую дату по часовому поясу города назначения."
            )
        return self


class TripOutput(TripParameters):
    flight_snapshot: dict | None = None
    lodging_snapshot: dict | None = None
    pace_settings: dict | None = None
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
    visa_report: dict | None = None
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
    objective_version: str | None = None


class RouteSetOutput(BaseModel):
    id: int
    trip_request_id: int
    algorithm: str
    search_info: SearchInfo
    diagnostic: Result | None
    visa_report: dict | None = None
    created_at: datetime
    options: list[RouteOutput]
