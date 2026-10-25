from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.catalog.flights import fetch_observation, passengers_for, save_observation
from app.core.auth import current_account
from app.db.session import get_session
from app.integrations.http_cache import SourceUnavailable
from app.schemas.flights import FlightSearch

router = APIRouter(prefix="/api/v1", tags=["flights"], dependencies=[Depends(current_account)])


@router.post("/groups/{group_id}/flight-searches", status_code=201)
def flight_searches(
    group_id: int, payload: FlightSearch, session: Annotated[Session, Depends(get_session)]
):
    from app.api.groups import lock_group, members, require
    from app.db.models import Group

    require(session, Group, group_id)
    try:
        data = fetch_observation(payload, members(session, group_id))
        session.expire_all()
        lock_group(session, group_id)
        if passengers_for(members(session, group_id), payload) != data["passengers"]:
            raise HTTPException(409, "Группа изменилась во время поиска. Повторите запрос")
        result = save_observation(session, group_id, data)
        session.commit()
        return result
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except SourceUnavailable as exc:
        raise HTTPException(
            503,
            "Aviasales Data API сейчас недоступен. Общую оценку перелётов можно указать вручную.",
        ) from exc
