import json
from datetime import date, timedelta

import pytest
from catalog_helpers import NOW, snapshot
from fastapi.testclient import TestClient

from app.catalog.reader import CatalogReader
from app.catalog.refresh import collect_snapshot
from app.catalog.repository import PublishedSnapshot
from app.core.destinations import EXTRACT_REGIONS
from app.main import app


def sources(tmp_path):
    curated = tmp_path / "reviewed.json"
    curated.write_text(
        json.dumps(
            {
                "version": "fixture-v1",
                "checked_on": "2026-10-01",
                "valid_until": "2026-11-22",
                "price_policy": "adult",
                "pois": [],
            }
        ),
        encoding="utf-8",
    )
    records = []
    for identifier, tags in enumerate(
        [
            {"fee": "no", "opening_hours": "24/7", "leisure": "park"},
            {"fee": "yes", "opening_hours": "24/7", "tourism": "museum"},
            {"fee": "no", "opening_hours": "24/7", "access": "private"},
            {"fee": "no", "opening_hours": "24/7", "fee:conditional": "yes @ summer"},
            {"fee": "no"},
        ],
        start=1,
    ):
        records.append(
            {
                "id": f"osm:node:{identifier}",
                "name": f"Place {identifier}",
                "latitude": 41.146,
                "longitude": -8.611,
                "source_url": f"https://www.openstreetmap.org/node/{identifier}",
                "location_kind": "mapped_node",
                "tags": tags,
            }
        )
    extract = tmp_path / "extract.json"
    raw = {
        "country": "PT",
        "source": "https://download.geofabrik.de/europe/portugal.html",
        "source_timestamp": (NOW - timedelta(days=1)).isoformat(),
        "downloaded_at": (NOW - timedelta(hours=1)).isoformat(),
        "pois": {"Porto": records},
    }
    extract.write_text(json.dumps(raw), encoding="utf-8")
    return curated, extract


def test_extract_keeps_unknown_facts_and_access_restrictions(tmp_path):
    curated, extract = sources(tmp_path)
    data = collect_snapshot(
        curated_path=curated,
        extract_path=extract,
        now=NOW,
        destination="Porto",
    )
    reader = CatalogReader(
        loader=lambda: PublishedSnapshot(1, NOW, data, data.digest()),
        clock=lambda: NOW,
    )
    rows = {row["id"]: row for row in reader.catalog(NOW.date())["pois"]}
    assert rows["osm:node:1"]["eligible"] and rows["osm:node:1"]["cost_per_person"] == 0
    assert rows["osm:node:2"]["cost_per_person"] is None
    assert "unknown_price" in rows["osm:node:2"]["issues"]
    assert "restricted_access" in rows["osm:node:3"]["issues"]
    assert "unsupported_conditional_access_or_fee" in rows["osm:node:4"]["issues"]
    assert "unknown_closed_or_unsupported_hours" in rows["osm:node:5"]["issues"]
    assert all(not row["eligible"] for key, row in rows.items() if key != "osm:node:1")
    assert data.manifest.bbox == ",".join(str(v) for v in EXTRACT_REGIONS["Porto"])


def test_republishing_extract_cannot_reset_source_age(tmp_path):
    curated, extract = sources(tmp_path)
    first = collect_snapshot(
        curated_path=curated,
        extract_path=extract,
        now=NOW,
        destination="Porto",
    )
    second = collect_snapshot(
        curated_path=curated,
        extract_path=extract,
        now=NOW + timedelta(days=2),
        destination="Porto",
    )
    assert first.digest() == second.digest()
    assert first.manifest.fresh_until == NOW + timedelta(days=6)
    with pytest.raises(ValueError, match="Choose either"):
        collect_snapshot(curated_path=curated, extract_path=extract, curated_only=True, now=NOW)


def test_reviewed_free_date_is_specific_and_historical_hash_shape_is_preserved():
    original = snapshot(curated=True)
    fact = original.pois[0].model_copy(
        update={
            "cost_per_person": 1500,
            "free_dates": [date(2026, 10, 24)],
        }
    )
    data = original.model_copy(update={"pois": (fact,)})
    reader = CatalogReader(
        loader=lambda: PublishedSnapshot(1, NOW, data, data.digest()),
        clock=lambda: NOW,
    )
    assert reader.catalog(date(2026, 10, 24))["pois"][0]["cost_per_person"] == 0
    assert reader.catalog(date(2026, 10, 25))["pois"][0]["cost_per_person"] == 1500
    assert "source_upstream_asof" not in original.manifest.model_dump()
    assert "free_dates" not in original.pois[0].model_dump()


def test_country_reference_is_searchable_but_does_not_claim_complete_planning():
    client = TestClient(app)
    response = client.get("/api/v1/destinations/reference/portugal?query=Evora&limit=2")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 700 and not data["complete_coverage"]
    assert data["supported_cities"] == ["Lisbon", "Porto", "Sintra"]
    assert any(row["name"] == "Évora" for row in data["settlements"])
    assert all(row["planning_destination"] is None for row in data["settlements"])
    assert client.get("/api/v1/destinations/Evora/pois?travel_date=2026-10-24").status_code == 422
