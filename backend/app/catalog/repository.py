"""Atomic publication under a per-destination row lock; readers pin immutable versions."""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.catalog.schema import SnapshotData
from app.db.models import CatalogHead, CatalogPoi, CatalogVersion

CORE_FIELDS = {
    "name",
    "category",
    "latitude",
    "longitude",
    "cost_per_person",
    "duration",
    "opening_hours",
    "source_url",
}


@dataclass(frozen=True)
class PublishedSnapshot:
    version_id: int
    published_at: datetime
    data: SnapshotData
    content_sha256: str


def load_active(session, destination="Lisbon"):
    # Scalar select re-reads the pointer even if the Session has an older identity-map entry.
    version_id = session.scalar(
        select(CatalogHead.version_id).where(CatalogHead.destination == destination)
    )
    if version_id is None:
        raise LookupError("Catalog not published")
    return load_version(session, version_id)


def load_version(session, version_id):
    version = session.get(CatalogVersion, version_id)
    if version is None:
        raise ValueError("Missing catalog version")
    rows = session.scalars(
        select(CatalogPoi).where(CatalogPoi.version_id == version_id).order_by(CatalogPoi.place_id)
    ).all()
    records = []
    for row in rows:
        if not isinstance(row.details, dict) or set(row.details) & (CORE_FIELDS | {"id"}):
            raise ValueError("Conflicting catalog fields")
        records.append(
            {"id": row.place_id, **{key: getattr(row, key) for key in CORE_FIELDS}, **row.details}
        )
    data = SnapshotData.model_validate({"manifest": version.manifest, "pois": records})
    if (
        data.digest() != version.content_sha256
        or len(rows) != version.poi_count
        or data.manifest.destination != version.destination
        or data.manifest.fetched_at != version.fetched_at
        or data.manifest.fresh_until != version.fresh_until
    ):
        raise ValueError("Catalog checksum or metadata mismatch")
    return PublishedSnapshot(version.id, version.published_at, data, version.content_sha256)


def publish(session, data: SnapshotData, now=None):
    """Caller owns commit/rollback. Network/validation must finish BEFORE this transaction."""
    now = now or datetime.now(timezone.utc)
    if not data.manifest.fetched_at <= now < data.manifest.fresh_until:
        raise ValueError("Cannot publish stale or future source data")
    city = data.manifest.destination
    session.execute(
        insert(CatalogHead)
        .values(destination=city, version_id=None)
        .on_conflict_do_nothing(index_elements=[CatalogHead.destination])
    )
    head = session.scalar(
        select(CatalogHead)
        .where(CatalogHead.destination == city)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    digest = data.digest()
    if head.version_id is not None:
        previous = load_version(session, head.version_id)
        if previous.data.manifest.fetched_at > data.manifest.fetched_at:
            raise ValueError("A newer source snapshot is already published")
        if previous.content_sha256 == digest:
            return previous, True
    version = CatalogVersion(
        destination=city,
        fetched_at=data.manifest.fetched_at,
        fresh_until=data.manifest.fresh_until,
        content_sha256=digest,
        poi_count=len(data.pois),
        manifest=data.manifest.model_dump(mode="json"),
    )
    session.add(version)
    session.flush()
    for poi in data.pois:
        values = poi.model_dump(mode="json")
        session.add(
            CatalogPoi(
                version_id=version.id,
                place_id=values.pop("id"),
                **{key: values.pop(key) for key in CORE_FIELDS},
                details=values,
            )
        )
    session.flush()
    # Read back and verify all persisted facts before switching the public pointer.
    saved = load_version(session, version.id)
    head.version_id = version.id
    session.flush()
    return saved, False
