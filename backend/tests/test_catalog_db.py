"""Publication, rollback, concurrent writers and frozen API results in real PostgreSQL."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from uuid import uuid4

import httpx
import pytest
from cache_helpers import MemoryRedis
from catalog_helpers import NOW, snapshot
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.catalog import repository
from app.catalog.reader import CatalogReader
from app.catalog.repository import load_active, load_version, publish
from app.db.models import CatalogHead, CatalogPoi, CatalogVersion
from app.db.session import engine, get_session
from app.integrations.city import CityProvider
from app.integrations.http_cache import RedisHttpCache, SourceUnavailable
from app.main import app

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1", reason="Requires migrated PostgreSQL and RUN_DB_TESTS=1"
)


@pytest.fixture
def db():
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
            yield session
        transaction.rollback()


def test_roundtrip_idempotence_and_preserved_old_versions(db):
    city = "fixture-" + uuid4().hex
    first, reused = publish(db, snapshot(city), NOW)
    assert not reused
    same, reused = publish(db, snapshot(city), NOW)
    assert reused and same == first
    second, reused = publish(db, snapshot(city, name="Updated museum"), NOW)
    assert not reused and first.version_id != second.version_id
    assert load_active(db, city) == second
    assert load_version(db, first.version_id).data.pois[0].name == "Free museum"
    pinned = CatalogReader(lambda: first, lambda: NOW)
    assert pinned.catalog(NOW.date())["catalog_snapshot"]["version_id"] == first.version_id


@pytest.mark.parametrize("delta", [timedelta(days=-8), timedelta(seconds=1)])
def test_reject_stale_or_future_data_without_switching_head(db, delta):
    city = "fixture-" + uuid4().hex
    first, _ = publish(db, snapshot(city), NOW)
    with pytest.raises(ValueError):
        publish(db, snapshot(city, fetched=NOW + delta), NOW)
    assert load_active(db, city) == first


def test_reject_older_source_and_rollback_failed_readback(db, monkeypatch):
    city = "fixture-" + uuid4().hex
    first, _ = publish(db, snapshot(city), NOW)
    with pytest.raises(ValueError, match="newer"):
        publish(db, snapshot(city, fetched=NOW - timedelta(hours=1)), NOW)
    original = repository.load_version

    def fail_new(session, version_id):
        if version_id != first.version_id:
            raise ValueError("Injected readback failure")
        return original(session, version_id)

    with monkeypatch.context() as patch:
        patch.setattr(repository, "load_version", fail_new)
        with pytest.raises(ValueError, match="Injected"), db.begin_nested():
            publish(db, snapshot(city, name="Should roll back"), NOW)
    assert load_active(db, city) == first
    assert (
        db.scalar(
            select(func.count())
            .select_from(CatalogVersion)
            .where(CatalogVersion.destination == city)
        )
        == 1
    )


def test_detect_tampered_facts_and_return_safe_error(db):
    city = "fixture-" + uuid4().hex
    saved, _ = publish(db, snapshot(city), NOW)
    db.execute(
        update(CatalogPoi).where(CatalogPoi.version_id == saved.version_id).values(name="tampered")
    )
    db.expire_all()
    reader = CatalogReader(lambda: load_active(db, city), lambda: NOW)
    with pytest.raises(SourceUnavailable, match="недоступен"):
        reader.catalog(NOW.date())


def test_concurrent_refresh_is_atomic_and_old_writer_cannot_replace_newer():
    city = "concurrency-" + uuid4().hex
    locked, release, older_started = Event(), Event(), Event()
    try:
        with Session(engine) as session, session.begin():
            initial, _ = publish(session, snapshot(city, fetched=NOW - timedelta(hours=2)), NOW)

        def newer_writer():
            with Session(engine) as session, session.begin():
                result, _ = publish(session, snapshot(city, name="Newest"), NOW)
                locked.set()
                assert release.wait(5)
                return result

        def older_writer():
            older_started.set()
            with Session(engine) as session, session.begin():
                return publish(session, snapshot(city, fetched=NOW - timedelta(hours=1)), NOW)

        with ThreadPoolExecutor(max_workers=2) as pool:
            newest = pool.submit(newer_writer)
            try:
                assert locked.wait(5)
                older = pool.submit(older_writer)
                assert older_started.wait(5)
                with Session(engine) as session:
                    # An uncommitted publication exposes the previous complete version.
                    assert load_active(session, city).version_id == initial.version_id
            finally:
                release.set()
            result = newest.result(timeout=5)
            with pytest.raises(ValueError, match="newer"):
                older.result(timeout=5)
        with Session(engine) as session:
            assert load_active(session, city) == result
    finally:
        release.set()
        # Only the randomly named city owned by this test is removed.
        with Session(engine) as session, session.begin():
            ids = select(CatalogVersion.id).where(CatalogVersion.destination == city)
            session.execute(delete(CatalogHead).where(CatalogHead.destination == city))
            session.execute(delete(CatalogPoi).where(CatalogPoi.version_id.in_(ids)))
            session.execute(delete(CatalogVersion).where(CatalogVersion.destination == city))


@pytest.mark.parametrize("city", ["Lisbon", "Porto", "Sintra"])
def test_api_reads_offline_catalog_and_freezes_version_in_saved_results(db, monkeypatch, city):
    # This fixture transaction is always rolled back; a live catalog may be newer than NOW.
    db.execute(update(CatalogHead).where(CatalogHead.destination == city).values(version_id=None))
    saved, _ = publish(db, snapshot(city), NOW)
    reader = CatalogReader(lambda: load_active(db, city), lambda: NOW)
    calls = []

    def matrix(request):
        calls.append(request)
        assert "/table/" in request.url.path
        return httpx.Response(200, json={"code": "Ok", "durations": [[0, 60], [60, 0]]})

    def get_test_session():
        yield db

    app.dependency_overrides[get_session] = get_test_session
    try:
        with httpx.Client(transport=httpx.MockTransport(matrix)) as upstream:
            provider = CityProvider(
                upstream, RedisHttpCache(MemoryRedis()), catalog_source=reader, destination=city
            )
            monkeypatch.setattr("app.api.destinations.CityProvider", lambda **kwargs: provider)
            monkeypatch.setattr("app.api.groups.CityProvider", lambda **kwargs: provider)
            with TestClient(app) as client:
                catalog = client.get(
                    f"/api/v1/destinations/{city}/pois?travel_date=2026-10-27&end_date=2026-10-28"
                )
                assert catalog.status_code == 200 and not calls
                assert catalog.json()["catalog_snapshot"]["version_id"] == saved.version_id
                group = client.post("/api/v1/groups", json={"name": "Catalog test"}).json()
                user = client.post("/api/v1/users", json={"name": "Tester"}).json()
                gid = group["id"]
                client.post(f"/api/v1/groups/{gid}/members", json={"user_id": user["id"]})
                client.put(
                    f"/api/v1/groups/{gid}/members/{user['id']}/preferences",
                    json={"interests": {"history": 1}},
                )
                trip = client.post(
                    f"/api/v1/groups/{gid}/trips",
                    json={
                        "destination": city,
                        "travel_date": "2026-10-27",
                        "end_date": "2026-10-28",
                        "budget": 10000,
                    },
                ).json()
                response = client.post(f"/api/v1/trips/{trip['id']}/route-sets")
                assert response.status_code == 201, response.text
                result = response.json()
                assert result["options"]
                for option in result["options"]:
                    assert option["result"]["validation"]["is_valid"]
                    for day in option["result"]["days"]:
                        assert (
                            day["data_context"]["catalog_snapshot"]["version_id"]
                            == saved.version_id
                        )
                publish(db, snapshot(city, name="Changed after planning"), NOW)
                db.commit()
                # Reading history neither recomputes facts nor calls upstream services.
                before = len(calls)
                assert client.get(f"/api/v1/route-sets/{result['id']}").json() == result
                assert len(calls) == before
    finally:
        app.dependency_overrides.pop(get_session, None)
