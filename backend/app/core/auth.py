"""Opaque revocable cookie sessions, CSRF and ownership; accounts are not travellers."""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response
from pwdlib import PasswordHash
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    Account,
    AuthRateLimit,
    AuthSession,
    Group,
    RouteOption,
    RouteSet,
    TripRequest,
    User,
)
from app.db.session import get_session

DB = Annotated[Session, Depends(get_session)]
COOKIE = "travel_session"
passwords = PasswordHash.recommended()
DUMMY_HASH = passwords.hash("dummy-password-never-used-for-login")


def now():
    return datetime.now(timezone.utc)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token(token):
    return hmac.new(token.encode(), b"travel-planner-csrf-v1", hashlib.sha256).hexdigest()


def check_origin(request):
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (
        origin is not None and origin not in settings.auth_allowed_origins
    ):
        raise HTTPException(403, "Недопустимый источник запроса")


def auth_form_guard(request: Request):
    check_origin(request)
    # Custom header prevents browser form/login CSRF, including missing Origin.
    if request.headers.get("x-auth-request") != "travel-planner":
        raise HTTPException(403, "Требуется заголовок X-Auth-Request: travel-planner")


def current_account(request: Request, response: Response, session: DB):
    response.headers["Cache-Control"] = "no-store"
    token = request.cookies.get(COOKIE, "")
    stored = session.get(AuthSession, token_hash(token)) if 20 <= len(token) <= 128 else None
    if stored is None or stored.expires_at <= now():
        raise HTTPException(401, "Войдите в аккаунт")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_origin(request)
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), csrf_token(token)):
            raise HTTPException(403, "Сессия изменилась. Обновите страницу и повторите действие")
    account = session.get(Account, stored.account_id)
    if account is None:
        raise HTTPException(401, "Войдите в аккаунт")
    session.info["account_id"] = account.id
    return account


def start_session(session, account, request, response: Response):
    previous = request.cookies.get(COOKIE)
    if previous:
        session.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(previous)))
    session.execute(delete(AuthSession).where(AuthSession.expires_at <= now()))
    token = secrets.token_urlsafe(32)
    session.add(
        AuthSession(
            token_hash=token_hash(token),
            account_id=account.id,
            expires_at=now() + timedelta(days=settings.auth_session_days),
        )
    )
    session.commit()
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        max_age=settings.auth_session_days * 86400,
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"account": account, "csrf_token": csrf_token(token)}


def throttle(session, request, action, identity=""):
    stamp = now()
    seconds, limit = (
        (3600, 10) if action == "register" else (900, 60 if action == "login_ip" else 15)
    )
    bucket = int(stamp.timestamp()) // seconds
    host = request.client.host if request.client else "unknown"
    key = token_hash(f"{action}:{host}:{identity}:{bucket}")
    session.execute(delete(AuthRateLimit).where(AuthRateLimit.expires_at <= stamp))
    stmt = insert(AuthRateLimit).values(
        key=key, attempts=1, expires_at=stamp + timedelta(seconds=seconds)
    )
    attempts = session.scalar(
        stmt.on_conflict_do_update(
            index_elements=[AuthRateLimit.key], set_={"attempts": AuthRateLimit.attempts + 1}
        ).returning(AuthRateLimit.attempts)
    )
    session.commit()  # Persist failed attempts, too; independent of login outcome.
    if attempts > limit:
        raise HTTPException(
            429, "Слишком много попыток. Повторите позже", headers={"Retry-After": str(seconds)}
        )


def require_owned(session, model, identifier):
    obj = session.get(model, identifier)
    account_id = session.info.get("account_id")
    if obj is None:
        raise HTTPException(404, "Объект не найден")
    if model in (Group, User):
        owned = account_id is not None and obj.owner_account_id == account_id
    elif model is TripRequest:
        require_owned(session, Group, obj.group_id)
        owned = True
    elif model in (RouteOption, RouteSet):
        require_owned(session, TripRequest, obj.trip_request_id)
        owned = True
    else:
        owned = False
    if not owned:
        raise HTTPException(404, "Объект не найден")
    return obj
