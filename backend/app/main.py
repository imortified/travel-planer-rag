import logging

from fastapi import FastAPI

from app.api.assistant import router as assistant_router
from app.api.destinations import router as destinations_router
from app.api.groups import router as groups_router
from app.api.routes import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = FastAPI(title="Group Travel Planner", version="0.1.0")
app.include_router(router)
app.include_router(groups_router)
app.include_router(destinations_router)
app.include_router(assistant_router)
