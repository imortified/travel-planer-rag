"""Pure ASGI request correlation, including sync endpoints, streaming and error responses."""

import asyncio
import logging
from time import perf_counter
from uuid import uuid4

from starlette.responses import JSONResponse

from app.core.logging import emit, request_id, valid_request_id

logger = logging.getLogger(__name__)


def request_fields(scope, status, started):
    method = scope.get("method", "OTHER")
    if method not in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}:
        method = "OTHER"
    route = getattr(scope.get("route"), "path", "<unmatched>")
    if route == "<unmatched>" and scope.get("path") in {
        "/docs",
        "/redoc",
        "/openapi.json",
        "/docs/oauth2-redirect",
    }:
        route = scope["path"]
    return {
        "method": method,
        "route": route,
        "status_code": status,
        "duration_ms": (perf_counter() - started) * 1000,
    }


class RequestLoggingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = [v for k, v in scope.get("headers", []) if k.lower() == b"x-request-id"]
        supplied = headers[0].decode("ascii", errors="replace") if len(headers) == 1 else None
        rid = valid_request_id(supplied) or str(uuid4())
        scope["request_id"] = rid
        token = request_id.set(rid)
        started, status, finished = perf_counter(), None, False

        async def correlated_send(message):
            nonlocal status, finished
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {
                    **message,
                    "headers": [
                        (k, v)
                        for k, v in message.get("headers", [])
                        if k.lower() != b"x-request-id"
                    ]
                    + [(b"x-request-id", rid.encode("ascii"))],
                }
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                finished = True
            await send(message)

        try:
            await self.app(scope, receive, correlated_send)
        except Exception as exc:
            # ServerErrorMiddleware/Uvicorn run outside this context after reset.
            try:
                exc._travel_request_id = rid
            except (AttributeError, TypeError):
                pass
            emit(
                logger,
                "http.request.failed",
                logging.ERROR,
                exception=exc,
                **request_fields(scope, status or 500, started),
            )
            raise
        except asyncio.CancelledError:
            emit(
                logger,
                "http.request.cancelled",
                logging.WARNING,
                reason="cancelled",
                **request_fields(scope, status or 499, started),
            )
            raise
        else:
            status = status or 500
            emit(
                logger,
                "http.request.completed" if finished else "http.request.incomplete",
                logging.ERROR
                if status >= 500
                else logging.WARNING
                if status >= 400 or not finished
                else logging.INFO,
                **request_fields(scope, status, started),
            )
        finally:
            request_id.reset(token)


async def unhandled_error(request, exc):
    # The outer error handler still has scope when the middleware context is reset.
    return JSONResponse(
        {"detail": "Internal server error"},
        status_code=500,
        headers={"X-Request-ID": request.scope["request_id"]},
    )
