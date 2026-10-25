from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.catalog.lodging import search_lodgings
from app.core.destinations import RealDestination
from app.db.session import get_session
from app.schemas.lodging import Kind

router = APIRouter(prefix="/api/v1", tags=["lodging"])


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
