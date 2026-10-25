import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.destinations import city_pois, lisbon_pois
from app.api.groups import DB, require
from app.core.auth import current_account
from app.core.logging import emit, span
from app.db.models import RouteOption, TripRequest
from app.integrations.embeddings import EmbeddingUnavailable
from app.integrations.llama_cpp import InvalidLlmOutput, LlmUnavailable
from app.integrations.qdrant import VectorStoreUnavailable
from app.nlp.explanations import explain
from app.nlp.parser import parse_request
from app.nlp.retrieval import Retrieval
from app.schemas.assistant import ExplanationOutput, ParseInput, ParseOutput, SemanticInput

router = APIRouter(prefix="/api/v1", tags=["Local LLM assistant"])
logger = logging.getLogger(__name__)


@router.post("/assistant/search")
def semantic_search(payload: SemanticInput):
    catalog = (
        lisbon_pois(payload.travel_date, payload.end_date)
        if payload.destination == "Lisbon"
        else city_pois(payload.destination, payload.travel_date, payload.end_date)
    )
    try:
        with span(logger, "retrieval.search", operation="search") as details:
            retrieval = (
                Retrieval()
                if payload.destination == "Lisbon"
                else Retrieval(destination=payload.destination)
            )
            result = retrieval.search(payload.text, [p for p in catalog["pois"] if p["eligible"]])
            details.update(poi_count=len(result["matches"]))
        return {
            **result,
            "destination": payload.destination,
            "travel_date": str(payload.travel_date),
            "end_date": str(payload.end_date or payload.travel_date),
        }
    except EmbeddingUnavailable as exc:
        raise HTTPException(
            503,
            "Эмбеддинги недоступны. Запустите scripts/start-embeddings.ps1; "
            "предпочтения можно задать ползунками.",
        ) from exc
    except VectorStoreUnavailable as exc:
        raise HTTPException(
            503,
            "Qdrant недоступен или индекс несовместим. "
            "Запустите docker compose up -d qdrant; "
            "предпочтения можно задать ползунками.",
        ) from exc


@router.post("/assistant/parse", response_model=ParseOutput)
def parse_text(payload: ParseInput):
    try:
        with span(logger, "assistant.parse", operation="parse") as details:
            result = parse_request(payload)
            details.update(question_count=len(result.questions), warning_count=len(result.warnings))
        return result
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


@router.post(
    "/routes/{route_id}/explanation",
    response_model=ExplanationOutput,
    dependencies=[Depends(current_account)],
)
def route_explanation(route_id: int, session: DB, refresh: bool = False):
    route = require(session, RouteOption, route_id)
    if route.explanation and not refresh:
        emit(logger, "explanation.loaded", route_id=route.id, reused=True)
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
    emit(logger, "explanation.saved", route_id=route.id, mode=output.mode)
    return output
