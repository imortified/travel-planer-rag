from fastapi import FastAPI

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
