from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Participant:
    id: str
    interests: dict[str, float]


@dataclass(frozen=True)
class Place:
    id: str
    category: str
    cost_per_person: int
    duration: int
    opens: int
    closes: int


@dataclass(frozen=True)
class Request:
    participants: list[Participant]
    budget: int
    start: int
    end: int
    mandatory: frozenset[str] = field(default_factory=frozenset)
    excluded: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Visit:
    place_id: str
    arrival: int
    start: int
    end: int


@dataclass(frozen=True)
class Violation:
    code: str
    message: str
    place_id: str | None = None
    visit_index: int | None = None


@dataclass(frozen=True)
class ValidationReport:
    is_valid: bool
    computed_cost: int
    violations: list[Violation]


@dataclass(frozen=True)
class DayInput:
    travel_date: date
    places: list[Place]
    travel: dict[tuple[str, str], int]
    data_context: dict | None = None


@dataclass(frozen=True)
class DayResult:
    travel_date: str
    visits: list[Visit]
    total_cost: int
    validation: ValidationReport
    data_context: dict | None = None


@dataclass(frozen=True)
class Result:
    is_feasible: bool
    visits: list[Visit]
    total_cost: int
    satisfaction: dict[str, float]
    reasons: list[str]
    validation: ValidationReport | None = None
    data_context: dict | None = None
    days: list[DayResult] | None = None
