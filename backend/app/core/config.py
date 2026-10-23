from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    database_url: str = "postgresql+psycopg://travel:local_dev_only@127.0.0.1:55432/travel_planner"
    osm_map_url: str = "https://www.openstreetmap.org/api/0.6/map"
    osrm_url: str = "https://router.project-osrm.org"


settings = Settings()
