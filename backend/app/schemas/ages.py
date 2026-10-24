"""Age is supplied for the trip start, or calculated on each visit from an optional birthday."""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    age_on_start: int | None = Field(default=None, strict=True, ge=0, le=120)
    birth_date: date | None = None

    @model_validator(mode="after")
    def one_source(self):
        if self.age_on_start is not None and self.birth_date is not None:
            raise ValueError("Укажите возраст на начало поездки или дату рождения, не оба поля")
        return self
