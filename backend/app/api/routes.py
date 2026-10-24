import logging
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.core.logging import span
from app.integrations.demo import load_demo
from app.planning.models import DayInput, Participant, Request, Result
from app.planning.pricing import with_visit_prices
from app.planning.service import checked_plan
from app.schemas.planning import PlanInput

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health")
def health():
    """Liveness only; database readiness is a separate check."""
    return {"status": "ok"}


@router.post("/api/v1/plans/demo", response_model=Result)
def demo_plan(
    payload: PlanInput,
    algorithm: Literal["greedy", "beam", "cp-sat"] = "greedy",
    beam_width: Annotated[int, Query(ge=1, le=128)] = 32,
    time_limit_seconds: Annotated[float, Query(ge=0.05, le=30)] = 5.0,
):
    places, travel = load_demo()
    request = Request(
        participants=[Participant(p.id, p.interests) for p in payload.participants],
        budget=payload.budget,
        start=payload.start,
        end=payload.end,
        mandatory=frozenset(payload.mandatory),
        excluded=frozenset(payload.excluded),
    )
    with span(
        logger,
        "planning.solve",
        operation="demo",
        algorithm=algorithm,
        days=1,
        participant_count=len(request.participants),
        candidate_count=len(places),
    ) as details:
        result = checked_plan(
            request, places, travel, algorithm, beam_width, time_limit=time_limit_seconds
        )
        details.update(
            feasible=result.is_feasible,
            visit_count=len(result.visits),
            validation_valid=result.validation.is_valid,
            solver_status=result.solver.status if result.solver else None,
        )
    return with_visit_prices(
        result, [DayInput(date.min, places, travel)], len(request.participants)
    )
