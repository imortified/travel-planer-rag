from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    database_url: str = "postgresql+psycopg://travel:local_dev_only@127.0.0.1:55432/travel_planner"
    osm_map_url: str = "https://www.openstreetmap.org/api/0.6/map"
    osrm_url: str = "https://router.project-osrm.org"
    llm_base_url: str = "http://127.0.0.1:8081"
    llm_model: str = "Qwen3-4B-Q4_K_M"
    llm_timeout_seconds: float = 60
    embedding_base_url: str = "http://127.0.0.1:8082"
    embedding_model: str = "Qwen3-Embedding-0.6B-Q8_0"
    embedding_timeout_seconds: float = 60
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: str | None = None
    qdrant_timeout_seconds: float = 10
    qdrant_collection_prefix: str = "travel"
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_cache_prefix: str = "travel:http:v1"
    redis_timeout_seconds: float = Field(default=1, gt=0, le=10)
    osm_cache_ttl_seconds: int = Field(default=86400, ge=1, le=604800)
    osrm_cache_ttl_seconds: int = Field(default=3600, ge=1, le=86400)


settings = Settings()
