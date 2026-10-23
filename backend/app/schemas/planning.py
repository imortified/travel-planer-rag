from typing import Annotated

from pydantic import BaseModel, Field, model_validator


class ParticipantInput(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    interests: dict[str, Annotated[float, Field(ge=0, le=1)]]


class PlanInput(BaseModel):
    participants: list[ParticipantInput] = Field(min_length=1, max_length=6)
    budget: int = Field(ge=0, description="Total group budget in EUR cents")
    start: int = Field(default=540, ge=0, lt=1440)
    end: int = Field(default=1080, gt=0, le=1440)
    mandatory: set[str] = Field(default_factory=set)
    excluded: set[str] = Field(default_factory=set)

    @model_validator(mode="after")
    def validate_request(self):
        if self.start >= self.end:
            raise ValueError("start must precede end")
        if len({p.id for p in self.participants}) != len(self.participants):
            raise ValueError("Participant IDs must be unique")
        return self
