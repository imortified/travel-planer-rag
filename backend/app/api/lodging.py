from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.catalog.lodging import search_lodgings
from app.core.auth import current_account
from app.core.destinations import RealDestination
from app.db.session import get_session
from app.integrations.http_cache import SourceUnavailable
from app.integrations.xotelo import Xotelo
from app.schemas.lodging import Kind
from app.schemas.xotelo import XoteloStay

router = APIRouter(prefix="/api/v1", tags=["lodging"])


@router.get("/destinations/{destination}/xotelo-hotels")
def xotelo_hotels(destination: RealDestination, offset: int = Query(default=0, ge=0, le=5000)):
    try:
        return Xotelo().hotels(destination, offset)
    except SourceUnavailable as exc:
        raise HTTPException(
            503,
            "Xotelo недоступен или вернул неподходящие данные. Доступен ручной каталог проживания.",
        ) from exc


@router.post(
    "/groups/{group_id}/lodging-quotes", status_code=201, dependencies=[Depends(current_account)]
)
def lodging_quotes(
    group_id: int, payload: XoteloStay, session: Annotated[Session, Depends(get_session)]
):
    from app.api.groups import lock_group, members, require
    from app.catalog.lodging_quotes import fetch_observation, guests_for, save_observation
    from app.db.models import Group

    require(session, Group, group_id)
    participants = members(session, group_id)
    try:
        data = fetch_observation(payload, participants)
        session.expire_all()
        lock_group(session, group_id)
        if (
            guests_for(members(session, group_id), payload.checkin, payload.checkout)
            != data["guests"]
        ):
            raise HTTPException(
                409, "Состав группы изменился во время загрузки цен. Повторите запрос"
            )
        result = save_observation(session, group_id, data)
        session.commit()
        return result
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except SourceUnavailable as exc:
        raise HTTPException(
            503, "Цены Xotelo сейчас недоступны. Используйте ручную оценку жилья."
        ) from exc


@router.get("/destinations/{destination}/lodgings")
def lodgings(
    destination: RealDestination,
    session: Annotated[Session, Depends(get_session)],
    q: str = Query(default="", max_length=100),
    kind: Kind | None = None,
    page: int = Query(default=1, ge=1, le=500),
    page_size: int = Query(default=20, ge=1, le=50),
):
    try:
        return search_lodgings(session, destination, q, kind, page, page_size)
    except (LookupError, ValueError, SQLAlchemyError) as exc:
        raise HTTPException(
            503, "Каталог проживания недоступен. Требуется публикация или проверка данных."
        ) from exc
