"""Offline PostgreSQL catalog reader. One instance holds one version/read time per operation."""

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.catalog.repository import load_active
from app.core.logging import emit
from app.db.session import engine
from app.integrations.http_cache import SourceUnavailable

logger = logging.getLogger(__name__)


def load_current(destination="Lisbon"):
    with Session(engine) as session:
        return load_active(session, destination)


class CatalogReader:
    def __init__(self, loader=None, clock=None, destination="Lisbon"):
        self.loader = loader or (
            load_current if destination == "Lisbon" else lambda: load_current(destination)
        )
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._snapshot = None
        self._read_at = None

    @property
    def snapshot(self):
        if self._snapshot is None:
            try:
                self._snapshot = self.loader()
                self._read_at = self.clock()
            except (SQLAlchemyError, LookupError, ValueError) as exc:
                emit(logger, "catalog.read.failed", logging.ERROR, exception=exc)
                raise SourceUnavailable(
                    "Опубликованный каталог недоступен. "
                    "Подготовьте или обновите данные перед расчётом."
                ) from exc
            emit(
                logger,
                "catalog.snapshot.loaded",
                version_id=self._snapshot.version_id,
                poi_count=len(self._snapshot.data.pois),
            )
        return self._snapshot

    def catalog(self, travel_date):
        from app.integrations.city import opening_window

        snapshot = self.snapshot
        manifest = snapshot.data.manifest
        fresh = manifest.fetched_at <= self._read_at < manifest.fresh_until
        assessed_date = self._read_at.astimezone(ZoneInfo(manifest.timezone)).date()
        records = []
        for fact in snapshot.data.pois:
            poi = fact.model_dump(mode="json", exclude_none=True)
            poi["cost_per_person"] = fact.cost_per_person
            if fact.provenance and (
                travel_date.weekday() in (fact.free_weekdays or [])
                or travel_date in (fact.free_dates or [])
            ):
                poi["cost_per_person"] = 0
                poi["date_free_admission"] = True
            poi["opening_hours"] = fact.opening_hours
            window = opening_window(fact.opening_hours, travel_date)
            issues = list(fact.issues)
            if fact.cost_per_person is None:
                issues.append("unknown_price")
            if window is None:
                issues.append("unknown_closed_or_unsupported_hours")
            if fact.provenance:
                if not fact.provenance.checked_on <= travel_date <= fact.provenance.valid_until:
                    issues.append("curation_outside_validity")
                if not fact.provenance.checked_on <= assessed_date <= fact.provenance.valid_until:
                    issues.append("curation_review_expired")
            if not fresh:
                issues.append("catalog_expired")
            poi.update(window=window, issues=issues, eligible=not issues)
            records.append(poi)
        metadata = {
            "version_id": snapshot.version_id,
            "content_sha256": snapshot.content_sha256,
            "published_at": snapshot.published_at.isoformat(),
            "fetched_at": manifest.fetched_at.isoformat(),
            "fresh_until": manifest.fresh_until.isoformat(),
            "curated_version": manifest.catalog_version,
            "curated_review_until": str(manifest.curated_valid_until),
            "status": "ready" if fresh else "expired",
        }
        if manifest.source_upstream_asof:
            metadata["source_upstream_asof"] = manifest.source_upstream_asof.isoformat()
        return {
            "destination": manifest.destination,
            "currency": manifest.currency,
            "timezone": manifest.timezone,
            "travel_date": str(travel_date),
            "origin": manifest.origin.model_dump(),
            "pois": records,
            "fetched_at": manifest.fetched_at.isoformat(),
            "source": str(manifest.source),
            "catalog_version": manifest.catalog_version,
            "catalog_snapshot": metadata,
            "attribution": manifest.attribution,
            "cache": {
                "catalog": {
                    "backend": "postgresql",
                    "read": "published",
                    "version_id": snapshot.version_id,
                }
            },
        }
