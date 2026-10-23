from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.groups import DB, require
from app.db.models import RouteOption, TripRequest
from app.integrations.llama_cpp import InvalidLlmOutput, LlmUnavailable
from app.nlp.explanations import explain
from app.nlp.parser import parse_request
from app.schemas.assistant import ExplanationOutput, ParseInput, ParseOutput

router = APIRouter(prefix="/api/v1", tags=["Local LLM assistant"])


@router.post("/assistant/parse", response_model=ParseOutput)
def parse_text(payload: ParseInput):
    try:
        return parse_request(payload)
    except LlmUnavailable as exc:
        raise HTTPException(
            503,
            "llama.cpp недоступен. Запустите scripts/start-llama.ps1 или заполните форму вручную.",
        ) from exc
    except InvalidLlmOutput as exc:
        raise HTTPException(
            502,
            "Модель не вернула корректные параметры после повторной попытки. "
            "Упростите текст или заполните форму вручную.",
        ) from exc


@router.post("/routes/{route_id}/explanation", response_model=ExplanationOutput)
def route_explanation(route_id: int, session: DB, refresh: bool = False):
    route = require(session, RouteOption, route_id)
    if route.explanation and not refresh:
        return route.explanation
    result = route.result
    if not result.get("is_feasible") or not (result.get("validation") or {}).get("is_valid"):
        raise HTTPException(409, "Объяснение выбора доступно для маршрута, прошедшего проверку.")
    trip = require(session, TripRequest, route.trip_request_id)
    options = (
        list(
            session.scalars(
                select(RouteOption)
                .where(RouteOption.route_set_id == route.route_set_id)
                .order_by(RouteOption.id)
            )
        )
        if route.route_set_id
        else [route]
    )
    output = explain(route, trip, options[0], len(options))
    route.explanation = output.model_dump()
    session.commit()
    return output
