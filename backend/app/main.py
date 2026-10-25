from fastapi import FastAPI
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.accounts import router as accounts_router
from app.api.assistant import router as assistant_router
from app.api.destinations import router as destinations_router
from app.api.flights import router as flights_router
from app.api.groups import router as groups_router
from app.api.lodging import router as lodging_router
from app.api.routes import router
from app.api.visa import router as visa_router
from app.core.logging import configure_logging
from app.core.request_logging import RequestLoggingMiddleware, unhandled_error

configure_logging()
app = FastAPI(title="Group Travel Planner", version="0.1.0")
app.add_middleware(RequestLoggingMiddleware)
app.add_exception_handler(Exception, unhandled_error)
app.include_router(router)
app.include_router(groups_router)
app.include_router(destinations_router)
app.include_router(assistant_router)
app.include_router(visa_router)
app.include_router(lodging_router)
app.include_router(flights_router)
app.include_router(accounts_router)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    if request.url.path.startswith("/api/v1/auth/"):
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()
                ]
            },
        )
    return await request_validation_exception_handler(request, exc)
