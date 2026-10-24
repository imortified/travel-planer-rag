from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace

import httpx
import pytest
from cache_helpers import MemoryRedis
from catalog_helpers import NOW, snapshot

from app.catalog.reader import CatalogReader
from app.catalog.refresh import collect_snapshot
from app.catalog.repository import PublishedSnapshot
from app.catalog.schema import SnapshotData
from app.integrations.city import CityProvider
from app.integrations.http_cache import RedisHttpCache, SourceUnavailable, cached_get


def reader(data=None, now=NOW):
    data = data or snapshot()
    return CatalogReader(
        loader=lambda: PublishedSnapshot(1, NOW, data, data.digest()), clock=lambda: now
    )


def test_days_recompute_windows_preserve_facts_and_do_not_mutate_snapshot():
    data = snapshot()
    before = data.digest()
    source = reader(data)
    monday = source.catalog(date(2026, 10, 26))
    tuesday = source.catalog(date(2026, 10, 27))
    assert not monday["pois"][0]["eligible"]
    assert tuesday["pois"][0]["window"] == [600, 1080]
    assert data.digest() == before and "window" not in data.pois[0].model_dump()
    assert (
        monday["catalog_snapshot"]["version_id"] == tuesday["catalog_snapshot"]["version_id"] == 1
    )


def test_reader_pins_version_and_time_once_even_if_publication_changes():
    calls = []
    data = snapshot()

    def load():
        calls.append(1)
        return PublishedSnapshot(len(calls), NOW, data, data.digest())

    source = CatalogReader(load, lambda: NOW)
    first = source.catalog(date(2026, 10, 24))
    later = source.catalog(date(2026, 10, 25))
    assert len(calls) == 1 and first["catalog_snapshot"] == later["catalog_snapshot"]


def test_expired_snapshot_is_visible_but_cannot_be_used_for_routing():
    source = reader(now=NOW + timedelta(days=7))
    result = source.catalog(date(2026, 10, 27))
    assert result["catalog_snapshot"]["status"] == "expired"
    assert "catalog_expired" in result["pois"][0]["issues"]
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("Routing must not run"))
    ) as client:
        with pytest.raises(SourceUnavailable, match="устарел"):
            CityProvider(client, RedisHttpCache(MemoryRedis()), catalog_source=source).dataset(
                date(2026, 10, 27)
            )


def test_curated_validity_is_not_extended_by_refresh_or_historical_trip_date():
    data = snapshot(fetched=NOW + timedelta(days=35), curated=True)
    result = reader(data, now=NOW + timedelta(days=35)).catalog(date(2026, 11, 1))
    assert "curation_review_expired" in result["pois"][0]["issues"]
    assert not result["pois"][0]["eligible"]
    result = reader(snapshot(curated=True)).catalog(date(2026, 11, 23))
    assert "curation_outside_validity" in result["pois"][0]["issues"]


def test_unknown_prices_and_restricted_access_are_never_made_free_or_available():
    body = snapshot().model_dump(mode="json")
    body["pois"][0].update(cost_per_person=None, issues=["restricted_access"])
    result = reader(SnapshotData.model_validate(body)).catalog(date(2026, 10, 27))
    assert result["pois"][0]["cost_per_person"] is None
    assert {"unknown_price", "restricted_access"} <= set(result["pois"][0]["issues"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("latitude", float("nan")),
        ("longitude", 181),
        ("cost_per_person", True),
        ("cost_per_person", -1),
        ("duration", 0),
        ("name", " "),
    ],
)
def test_invalid_facts_are_rejected(field, value):
    body = snapshot().model_dump(mode="json")
    body["pois"][0][field] = value
    with pytest.raises(ValueError):
        SnapshotData.model_validate(body)


def test_duplicate_ids_and_inconsistent_provenance_are_rejected():
    body = snapshot(curated=True).model_dump(mode="json")
    body["pois"].append(deepcopy(body["pois"][0]))
    with pytest.raises(ValueError):
        SnapshotData.model_validate(body)
    body["pois"].pop()
    body["pois"][0]["provenance"]["version"] = "different"
    with pytest.raises(ValueError):
        SnapshotData.model_validate(body)


def test_individual_review_dates_stay_inside_manifest_bounds():
    body = snapshot(curated=True).model_dump(mode="json")
    body["pois"][0]["provenance"]["checked_on"] = "2026-10-24"
    assert SnapshotData.model_validate(body).pois[0].provenance.checked_on == date(2026, 10, 24)
    body["pois"][0]["provenance"]["valid_until"] = "2026-11-23"
    with pytest.raises(ValueError):
        SnapshotData.model_validate(body)


def test_dataset_uses_published_origin_and_no_osm_with_empty_redis():
    calls = []

    def handle(request):
        calls.append(request)
        assert "/table/" in request.url.path and "-9.1395,38.7135" in request.url.path
        return httpx.Response(200, json={"code": "Ok", "durations": [[0, 61], [60, 0]]})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        places, travel, context = CityProvider(
            client, RedisHttpCache(MemoryRedis()), catalog_source=reader()
        ).dataset(date(2026, 10, 27))
    assert len(calls) == 1 and len(places) == 1
    assert travel["start", "osm:node:1"] == 2
    assert context["cache"]["catalog"]["backend"] == "postgresql"
    assert context["catalog_snapshot"]["content_sha256"] == snapshot().digest()


def test_force_refresh_bypasses_old_redis_without_deleting_it_on_failure():
    cache = RedisHttpCache(MemoryRedis())
    import datetime
    import json

    old = json.dumps({"old": True}).encode()
    key = cache.key("osm", "https://example.org", {}, 60)
    cache.put(key, old, datetime.datetime.now(datetime.timezone.utc).isoformat(), 60)
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"new": True}))
    ) as client:
        parsed, _, info = cached_get(
            client, "https://example.org", {}, 60, cache, "osm", json.loads, bypass_cache=True
        )
    assert parsed == {"new": True} and info["read"] == "bypassed"
    assert json.loads(cache.get(key, 60).body) == parsed
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client:
        with pytest.raises(SourceUnavailable):
            cached_get(
                client, "https://example.org", {}, 60, cache, "osm", json.loads, bypass_cache=True
            )
    assert json.loads(cache.get(key, 60).body) == parsed


def test_refresh_failure_does_not_open_database(monkeypatch):
    from app.catalog.refresh import refresh

    def fail(*args):
        raise ValueError("Invalid source")

    monkeypatch.setattr("app.catalog.refresh.collect_snapshot", fail)
    with pytest.raises(ValueError, match="Invalid source"):
        refresh(session_factory=lambda: pytest.fail("Database opened before validation"))


def test_collect_snapshot_strips_daily_results_and_forces_fresh_source(tmp_path):
    path = tmp_path / "curated.json"
    path.write_text(
        '{"version":"fixture-v1","checked_on":"2026-10-01","valid_until":"2026-11-22","price_policy":"fixture","pois":[]}',
        encoding="utf-8",
    )
    data = snapshot()

    def fetch(day, *, curated_catalog, force):
        assert force and curated_catalog.version == "fixture-v1"
        return {
            "destination": "Lisbon",
            "origin": data.manifest.origin.model_dump(),
            "source": str(data.manifest.source),
            "fetched_at": NOW.isoformat(),
            "attribution": "fixture",
            "osm_poi_count": 1,
            "pois": [
                {**data.pois[0].model_dump(mode="json"), "window": [600, 1080], "eligible": True}
            ],
        }

    result = collect_snapshot(SimpleNamespace(fetch_catalog=fetch), path, NOW)
    assert "window" not in result.pois[0].model_dump() and result.manifest.fetched_at == NOW
