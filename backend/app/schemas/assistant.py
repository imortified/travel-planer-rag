from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.destinations import Destination, RealDestination

Category = Literal["history", "nature", "food", "art"]
Weight = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
ShortText = Annotated[str, Field(min_length=1, max_length=100, pattern=r"\S")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DraftMember(StrictModel):
    name: ShortText
    interests: dict[Category, Weight] = Field(default_factory=dict)


class Evidence(StrictModel):
    destination: str | None = None
    dates: str | None = None
    budget: str | None = None
    participants: str | None = None
    hours: str | None = None
    interests: str | None = None


class ExtractedTrip(StrictModel):
    destination: Destination | None = None
    travel_date: date | None = None
    end_date: date | None = None
    duration_days: int | None = Field(default=None, ge=1, le=365)
    budget_eur: float | None = Field(default=None, ge=0, le=10_000_000, allow_inf_nan=False)
    currency: Literal["EUR", "other"] | None = None
    budget_scope: Literal["group", "per_person"] | None = None
    participant_count: int | None = Field(default=None, ge=1, le=100)
    participants: list[DraftMember] = Field(default_factory=list, max_length=6)
    common_interests: dict[Category, Weight] = Field(default_factory=dict)
    start: int | None = Field(default=None, ge=0, lt=1440)
    end: int | None = Field(default=None, gt=0, le=1440)
    mandatory_names: list[ShortText] = Field(default_factory=list, max_length=12)
    excluded_names: list[ShortText] = Field(default_factory=list, max_length=12)
    unsupported: list[ShortText] = Field(default_factory=list, max_length=12)
    evidence: Evidence = Field(default_factory=Evidence)


class ParseInput(StrictModel):
    text: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    member_names: list[ShortText] = Field(default_factory=list, max_length=6)


class SemanticInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination: RealDestination = "Lisbon"
    text: str = Field(min_length=1, max_length=300, pattern=r"\S")
    travel_date: date
    end_date: date | None = None


class Question(StrictModel):
    field: str
    text: str


class ParseOutput(StrictModel):
    draft: ExtractedTrip
    group_budget: int | None
    questions: list[Question]
    warnings: list[str]
    model: str
    prompt_version: str
    reference_date: date


class ExplanationOutput(StrictModel):
    mode: Literal["llm", "template"]
    model: str | None
    prompt_version: str
    verified: bool
    facts: dict[str, str]
    selected_ids: list[str]
    paragraphs: list[str]
    fallback_reason: str | None = None
    sources: dict[str, dict] = Field(default_factory=dict)


class ExplanationClaim(StrictModel):
    fact_id: str
    sentence: str


class ExplanationDraft(StrictModel):
    claims: list[ExplanationClaim] = Field(min_length=3, max_length=8)
