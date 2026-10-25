from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.auth import (
    COOKIE,
    DB,
    DUMMY_HASH,
    auth_form_guard,
    csrf_token,
    current_account,
    passwords,
    start_session,
    throttle,
    token_hash,
)
from app.db.models import Account, AuthSession, SavedTraveller
from app.schemas.accounts import (
    AccountOutput,
    LoginInput,
    RegisterInput,
    SessionOutput,
    TravellerInput,
    TravellerOutput,
)

router = APIRouter(prefix="/api/v1", tags=["Accounts"])
Owner = Annotated[Account, Depends(current_account)]


@router.post(
    "/auth/register",
    response_model=SessionOutput,
    status_code=201,
    dependencies=[Depends(auth_form_guard)],
)
def register(payload: RegisterInput, request: Request, response: Response, session: DB):
    throttle(session, request, "register")
    account = Account(
        email=payload.email,
        name=payload.name.strip(),
        password_hash=passwords.hash(payload.password.get_secret_value()),
    )
    session.add(account)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "Не удалось зарегистрировать адрес. Попробуйте войти") from exc
    return start_session(session, account, request, response)


@router.post("/auth/login", response_model=SessionOutput, dependencies=[Depends(auth_form_guard)])
def login(payload: LoginInput, request: Request, response: Response, session: DB):
    throttle(session, request, "login", payload.email)
    throttle(session, request, "login_ip")
    account = session.scalar(select(Account).where(Account.email == payload.email))
    valid = passwords.verify(
        payload.password.get_secret_value(), account.password_hash if account else DUMMY_HASH
    )
    if not account or not valid:
        raise HTTPException(401, "Неверная почта или пароль")
    return start_session(session, account, request, response)


@router.get("/auth/me", response_model=SessionOutput)
def me(owner: Owner, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {
        "account": AccountOutput.model_validate(owner),
        "csrf_token": csrf_token(request.cookies[COOKIE]),
    }


@router.post("/auth/logout", status_code=204)
def logout(owner: Owner, request: Request, response: Response, session: DB):
    stored = session.get(AuthSession, token_hash(request.cookies[COOKIE]))
    session.delete(stored)
    session.commit()
    response.delete_cookie(COOKIE, path="/")
    response.headers["Cache-Control"] = "no-store"


@router.get("/travellers", response_model=list[TravellerOutput])
def travellers(owner: Owner, session: DB, q: str = Query(default="", max_length=100)):
    statement = select(SavedTraveller).where(SavedTraveller.owner_account_id == owner.id)
    if q.strip():
        statement = statement.where(SavedTraveller.name.icontains(q.strip(), autoescape=True))
    return list(
        session.scalars(statement.order_by(SavedTraveller.name, SavedTraveller.id).limit(500))
    )


def owned_traveller(session, identifier, owner):
    profile = session.get(SavedTraveller, identifier)
    if profile is None or profile.owner_account_id != owner.id:
        raise HTTPException(404, "Участник не найден")
    return profile


@router.post("/travellers", response_model=TravellerOutput, status_code=201)
def save_traveller(payload: TravellerInput, owner: Owner, session: DB):
    profile = SavedTraveller(owner_account_id=owner.id, **payload.model_dump(mode="json"))
    profile.name = profile.name.strip()
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


@router.put("/travellers/{identifier}", response_model=TravellerOutput)
def update_traveller(identifier: int, payload: TravellerInput, owner: Owner, session: DB):
    profile = owned_traveller(session, identifier, owner)
    for field, value in payload.model_dump(mode="json").items():
        setattr(profile, field, value.strip() if field == "name" else value)
    session.commit()
    session.refresh(profile)
    return profile


@router.delete("/travellers/{identifier}", status_code=204)
def delete_traveller(identifier: int, owner: Owner, session: DB):
    session.delete(owned_traveller(session, identifier, owner))
    session.commit()
