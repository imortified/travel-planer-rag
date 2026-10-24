from datetime import datetime, timedelta, timezone

from app.catalog.schema import SnapshotData

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


def snapshot(destination="Lisbon", fetched=NOW, name="Free museum", curated=False):
    provenance = (
        {
            "version": "fixture-v1",
            "checked_on": "2026-10-01",
            "valid_until": "2026-11-22",
            "price_policy": "fixture",
            "price_source": "https://example.org/price",
            "hours_source": "https://example.org/hours",
            "notes": "fixture",
        }
        if curated
        else None
    )
    return SnapshotData.model_validate(
        {
            "manifest": {
                "destination": destination,
                "origin": {
                    "id": "start",
                    "name": "Rossio",
                    "latitude": 38.7135,
                    "longitude": -9.1395,
                },
                "source": "https://www.openstreetmap.org/api/0.6/map",
                "bbox": "fixture",
                "fetched_at": fetched,
                "fresh_until": fetched + timedelta(days=7),
                "catalog_version": "fixture-v1",
                "curated_checked_on": "2026-10-01",
                "curated_valid_until": "2026-11-22",
                "attribution": "OSM fixture",
            },
            "pois": [
                {
                    "id": "osm:node:1",
                    "name": name,
                    "category": "history",
                    "latitude": 38.713,
                    "longitude": -9.139,
                    "cost_per_person": 0,
                    "duration": 60,
                    "duration_source": "planning_policy_60_minutes",
                    "opening_hours": "Tu-Su 10:00-18:00",
                    "source_url": "https://www.openstreetmap.org/node/1",
                    "issues": [],
                    "provenance": provenance,
                }
            ],
        }
    )
