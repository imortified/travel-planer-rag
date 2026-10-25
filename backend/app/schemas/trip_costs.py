"""Explicit user estimates and daily meal policies, frozen with the trip request."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Money = Annotated[int, Field(strict=True, ge=0, le=1_000_000_000)]


class BudgetSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["trip-budget-v1"] = "trip-budget-v1"
    lodging: Money | None = None
    transport: Money | None = None
    flights: Money | None = None
    transport_scope: Literal["all_transport", "ground_only"] = "all_transport"
    reserve: Money = 0

    @model_validator(mode="after")
    def separate_transport(self):
        if self.flights is not None and self.transport_scope != "ground_only":
            raise ValueError(
                "При отдельной сумме перелётов транспорт должен включать только наземные расходы"
            )
        return self

    @property
    def committed(self):
        return (self.lodging or 0) + (self.transport or 0) + (self.flights or 0) + self.reserve


class MealSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: int = Field(strict=True, ge=0, lt=1440)
    end: int = Field(strict=True, gt=0, le=1440)
    duration: int = Field(default=60, strict=True, ge=15, le=180)
    max_cost_per_person: Money = 2500

    @model_validator(mode="after")
    def interval(self):
        if self.end - self.start < self.duration:
            raise ValueError("Окно приёма пищи должно вмещать его длительность")
        return self


class MealSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["scheduled-meals-v1"] = "scheduled-meals-v1"
    breakfast: MealSlot | None = None
    lunch: MealSlot | None = None
    dinner: MealSlot | None = None

    def slots(self):
        return [
            (key, getattr(self, key))
            for key in ("breakfast", "lunch", "dinner")
            if getattr(self, key) is not None
        ]

    @model_validator(mode="after")
    def at_least_one(self):
        if not self.slots():
            raise ValueError("Выберите хотя бы один приём пищи")
        windows = sorted((slot.start, slot.end) for _, slot in self.slots())
        if any(a[1] > b[0] for a, b in zip(windows, windows[1:])):
            raise ValueError("Окна приёмов пищи не должны пересекаться")
        return self
