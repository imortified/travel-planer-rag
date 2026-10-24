from datetime import date

import httpx
import pytest
from cache_helpers import MemoryRedis

from app.integrations.city import (
    CityProvider,
    IncompleteData,
    SourceUnavailable,
    opening_window,
    parse_catalog,
)
from app.integrations.http_cache import RedisHttpCache
from app.planning.models import Participant, Request
from app.planning.service import checked_plan

DAY = date(2026, 10, 23)
XML = b"""<osm version="0.6">
<node id="1" lat="38.713" lon="-9.139" timestamp="2026-10-01T00:00:00Z">
<tag k="name" v="Free museum"/><tag k="tourism" v="museum"/>
<tag k="fee" v="no"/><tag k="opening_hours" v="Tu-Su 10:00-18:00; Dec 25 off"/>
</node><node id="2" lat="38.714" lon="-9.138">
<tag k="name" v="Unknown price"/><tag k="tourism" v="museum"/>
<tag k="opening_hours" v="24/7"/></node>
<node id="3" lat="38.714" lon="-9.137">
<tag k="name" v="Unknown hours"/><tag k="tourism" v="museum"/><tag k="fee" v="no"/>
</node></osm>"""


def test_osm_normalization_and_unknowns():
    pois = parse_catalog(XML, DAY)
    assert len(pois) == 3
    assert pois[0]["eligible"]
    assert pois[0]["window"] == [600, 1080]
    assert pois[0]["cost_per_person"] == 0
    assert pois[1]["cost_per_person"] is None
    assert not pois[1]["eligible"]
    assert not pois[2]["eligible"]
    assert pois[2]["window"] is None


def test_opening_hours_use_trip_date_and_preserve_breaks():
    assert opening_window("Tu-Su 10:00-18:00; Dec 25 off", DAY) == [600, 1080]
    assert opening_window("Tu-Su 10:00-18:00; Dec 25 off", date(2026, 12, 25)) is None
    assert opening_window("Tu-Su 10:00-18:00", date(2026, 10, 26)) is None
    assert opening_window("10:00-12:00,14:00-18:00", DAY) is None
    assert opening_window("not a schedule", DAY) is None


def provider(tmp_path, matrix=None):
    import json

    curated_path = tmp_path / "curated.json"
    curated_path.write_text(
        json.dumps(
            {
                "version": "test-osm-only",
                "checked_on": "2026-01-01",
                "valid_until": "2026-12-31",
                "price_policy": "test",
                "pois": [],
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/map"):
            return httpx.Response(200, content=XML)
        return httpx.Response(200, json={"code": "Ok", "durations": matrix or [[0, 61], [None, 0]]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return CityProvider(
        client, RedisHttpCache(MemoryRedis()), curated_path, live_catalog=True
    ), calls


def test_real_dataset_cache_rounding_and_null_routes(tmp_path):
    source, calls = provider(tmp_path)
    places, travel, context = source.dataset(DAY)
    assert travel == {("start", "osm:node:1"): 2}
    assert context["transport"] == "driving"
    assert context["pois"][0]["name"] == "Free museum"
    assert context["places_snapshot"][0]["opens"] == 600
    assert len(calls) == 2
    source.dataset(DAY)
    assert len(calls) == 2
    result = checked_plan(
        Request([Participant("a", {"history": 1})], 0, 540, 1080), places, travel, "beam"
    )
    assert result.validation.is_valid


def test_missing_mandatory_metadata_is_not_silently_ignored(tmp_path):
    source, _ = provider(tmp_path)
    with pytest.raises(IncompleteData):
        source.dataset(DAY, frozenset({"osm:node:2"}))


@pytest.mark.parametrize(
    "matrix", [[[0]], [[0, -1], [0, 0]], [[0, "fast"], [0, 0]], [[0, True], [0, 0]]]
)
def test_malformed_matrix_is_rejected(tmp_path, matrix):
    source, _ = provider(tmp_path, matrix)
    with pytest.raises(SourceUnavailable, match="OSRM"):
        source.dataset(DAY)
    assert not any(":osrm:" in key for key in source.cache.client.values)


def test_source_failure_never_falls_back_to_synthetic_data(tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503)))
    with pytest.raises(SourceUnavailable):
        CityProvider(client, RedisHttpCache(MemoryRedis()), live_catalog=True).catalog(DAY)


def test_malformed_osm_is_not_cached_and_fresh_response_can_repair_it():
    bodies = iter([b"<invalid>", XML])
    cache = RedisHttpCache(MemoryRedis())
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=next(bodies)))
    ) as client:
        source = CityProvider(client, cache, live_catalog=True)
        with pytest.raises(SourceUnavailable, match="OSM"):
            source.catalog(DAY)
        assert not cache.client.values
        catalog = source.catalog(DAY)
    assert any(p["id"] == "osm:node:1" for p in catalog["pois"])
    assert catalog["cache"]["osm"]["write"] == "stored"


def test_closed_date_is_empty_in_period_mode(tmp_path):
    source, calls = provider(tmp_path)
    places, travel, context = source.dataset(date(2026, 10, 26), allow_empty=True)
    assert places == []
    assert travel == {}
    assert context["pois"] == []
    assert len(calls) == 1  # No routing request for an empty day.


def test_period_catalog_includes_poi_open_on_later_date(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    source, _ = provider(tmp_path)
    monkeypatch.setattr("app.api.destinations.CityProvider", lambda: source)
    client = TestClient(app)
    response = client.get(
        "/api/v1/destinations/Lisbon/pois?travel_date=2026-10-26&end_date=2026-10-27"
    )
    assert response.status_code == 200
    first = response.json()["pois"][0]
    assert first["eligible"]
    assert first["available_dates"] == ["2026-10-27"]
    assert (
        client.get(
            "/api/v1/destinations/Lisbon/pois?travel_date=2026-10-26&end_date=2026-10-25"
        ).status_code
        == 422
    )
