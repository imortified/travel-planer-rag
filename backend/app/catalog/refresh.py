"""Fetch and validate off-transaction, then atomically publish one immutable version."""

import json
import logging
from datetime import datetime, time, timedelta, timezone
from time import perf_counter
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.catalog.repository import publish
from app.catalog.schema import SnapshotData
from app.core.config import settings
from app.core.destinations import city_for
from app.core.logging import emit
from app.db.session import engine
from app.integrations.city import CityProvider, opening_window
from app.integrations.curated import Catalog, enrich

logger = logging.getLogger(__name__)
DYNAMIC_ISSUES = {
    "unknown_price",
    "unknown_closed_or_unsupported_hours",
    "curation_outside_validity",
}


def collect_snapshot(
    provider=None, curated_path=None, now=None, destination="Lisbon", curated_only=False
):
    city = city_for(destination)
    path = curated_path or city.curated_path
    curated = Catalog.model_validate(json.loads(path.read_text(encoding="utf-8")))
    assessed = now or datetime.now(timezone.utc)
    today = assessed.astimezone(ZoneInfo("Europe/Lisbon")).date()
    if max([curated.checked_on, *[p.checked_on for p in curated.pois if p.checked_on]]) > today:
        raise ValueError("Curated facts are dated in the future")
    # Refresh means actual upstream fetch, not a new timestamp on an old Redis response.
    if curated_only:
        if any(
            p.latitude is None or p.longitude is None or p.coordinates_source is None
            for p in curated.pois
        ):
            raise ValueError(
                "Reviewed-only publication requires standalone POI with sourced coordinates"
            )
        pois, _ = enrich([], today, opening_window, catalog=curated)
        # Re-publishing a local file must not reset its source freshness timestamp.
        checked = min([curated.checked_on, *[p.checked_on for p in curated.pois if p.checked_on]])
        fetched = datetime.combine(checked, time.min, tzinfo=timezone.utc)
        source = {
            "destination": city.id,
            "origin": city.origin,
            "pois": pois,
            "fetched_at": fetched.isoformat(),
            "source": str(curated.pois[0].price_source),
            "attribution": "Source-reviewed public venue facts; individual sources in provenance",
        }
    else:
        source = (provider or CityProvider(destination=destination)).fetch_catalog(
            assessed.date(), curated_catalog=curated, force=True
        )
        if source["osm_poi_count"] < 1:
            raise ValueError("Empty OSM source cannot replace the published catalog")
    fetched = datetime.fromisoformat(source["fetched_at"])
    facts = []
    for poi in source["pois"]:
        record = {k: v for k, v in poi.items() if k not in {"window", "eligible"}}
        record["issues"] = [issue for issue in record["issues"] if issue not in DYNAMIC_ISSUES]
        facts.append(record)
    return SnapshotData.model_validate(
        {
            "manifest": {
                "destination": source["destination"],
                "origin": source["origin"],
                "source": source["source"],
                "bbox": city.bbox,
                "fetched_at": source["fetched_at"],
                "fresh_until": fetched + timedelta(seconds=settings.catalog_max_age_seconds),
                "catalog_version": curated.version,
                "curated_checked_on": curated.checked_on,
                "curated_valid_until": curated.valid_until,
                "attribution": source["attribution"],
            },
            "pois": facts,
        }
    )


def refresh(
    provider=None,
    curated_path=None,
    session_factory=None,
    now=None,
    destination="Lisbon",
    curated_only=False,
):
    started = perf_counter()
    try:
        data = collect_snapshot(provider, curated_path, now, destination, curated_only)
        factory = session_factory or (lambda: Session(engine))
        with factory() as session, session.begin():
            published, reused = publish(session, data, now)
    except Exception as exc:
        emit(
            logger,
            "catalog.refresh.failed",
            logging.ERROR,
            exception=exc,
            duration_ms=(perf_counter() - started) * 1000,
        )
        raise
    emit(
        logger,
        "catalog.refresh.completed",
        version_id=published.version_id,
        poi_count=len(data.pois),
        reused=reused,
        duration_ms=(perf_counter() - started) * 1000,
    )
    return published, reused
