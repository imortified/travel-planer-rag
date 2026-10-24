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
class Pace:
    mode: str
    max_visits: int
    max_active_minutes: int
    break_minutes: int
    version: str = "comfortable-pace-v1"

    def __post_init__(self):
        if (
            self.mode not in ("relaxed", "balanced", "intensive")
            or self.version != "comfortable-pace-v1"
            or any(
                type(v) is not int
                for v in (self.max_visits, self.max_active_minutes, self.break_minutes)
            )
            or not 1 <= self.max_visits <= 100
            or not 1 <= self.max_active_minutes <= 1440
            or not 0 <= self.break_minutes <= 1440
        ):
            raise ValueError("Invalid or unsupported pace settings")


@dataclass(frozen=True)
class Request:
    participants: list[Participant]
    budget: int
    start: int
    end: int
    mandatory: frozenset[str] = field(default_factory=frozenset)
    excluded: frozenset[str] = field(default_factory=frozenset)
    pace: Pace | None = None


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
class ObjectiveReport:
    version: str
    weights: dict[str, float]
    value: float
    contributions: dict[str, float]
    participant_utility: dict[str, float]
    participant_coverage: dict[str, float]
    utility_bounds: dict[str, float]
    excluded_from_fairness: list[str]
    mean_utility: float
    reward_bound: float
    reward: float
    cost_ratio: float
    travel_minutes: int
    travel_ratio: float
    fairness_gap: float
    candidate_count: int
    budget: int
    available_minutes: int
    day_coverage: float = 0.0


@dataclass(frozen=True)
class SolverReport:
    backend: str
    version: str
    status: str
    time_limit_seconds: float
    search_time_limit_seconds: float
    elapsed_seconds: float
    solver_seconds: float
    seed: int
    workers: int
    scale: int
    objective_error_bound: float
    integer_model_optimal: bool
    model_objective: float | None
    model_best_bound: float | None
    original_upper_bound: float | None
    absolute_gap_bound: float | None
    warm_start: bool


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
    objective: ObjectiveReport | None = None
    solver: SolverReport | None = None
    planning_summary: dict | None = None
