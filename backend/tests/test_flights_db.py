import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cache_helpers import MemoryRedis
from test_aviasales import body, search
from test_groups_db import client as client
from test_groups_db import create_group

from app.db.models import FlightSearchObservation
from app.db.session import get_session
from app.integrations.aviasales import Aviasales
from app.integrations.http_cache import RedisHttpCache
from app.main import app
from app.planning.models import Place

pytestmark = pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="Requires PostgreSQL")


@pytest.fixture
def source(monkeypatch):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=body(request))

    with httpx.Client(transport=httpx.MockTransport(handle)) as upstream:
        provider = Aviasales(RedisHttpCache(MemoryRedis()), upstream, "unit-fake-token")
        monkeypatch.setattr("app.catalog.flights.Aviasales", lambda: provider)
        yield calls


def observation(client, gid):
    r = client.post(f"/api/v1/groups/{gid}/flight-searches", json=search().model_dump(mode="json"))
    assert r.status_code == 201, r.text
    return r.json()


def payload(observed):
    return {
        "destination": "Lisbon",
        "travel_date": str(search().departure),
        "budget": 100000,
        "mandatory": ["p"],
        "budget_settings": {
            "lodging": 10000,
            "transport": 5000,
            "flights": 30000,
            "transport_scope": "ground_only",
            "reserve": 1000,
        },
        "flight_plan": {
            "observation_id": observed["observation_id"],
            "option_id": observed["options"][0]["option_id"],
        },
    }


@pytest.mark.parametrize("algorithm", ["greedy", "beam", "cp-sat"])
@pytest.mark.parametrize("collection", ["routes", "route-sets"])
def test_manual_group_flights_charged_once_and_frozen_history_survives_expiry(
    client, source, monkeypatch, algorithm, collection
):
    monkeypatch.setattr(
        "app.api.groups.CityProvider.dataset",
        lambda *_args, **_kwargs: (
            [Place("p", "history", 100, 60, 540, 1080)],
            {("start", "p"): 1},
            {"pois": [{"id": "p", "name": "Park"}]},
        ),
    )
    gid, ids = create_group(client, 2)
    observed = observation(client, gid)  # Unknown ages are retained; not needed by Data API.
    assert observed["passengers"][0]["age_at_departure"] is None
    duplicate = observation(client, gid)
    assert duplicate["cache"]["read"] == "hit" and duplicate["fetched_at"] == observed["fetched_at"]
    created = client.post(f"/api/v1/groups/{gid}/trips", json=payload(observed))
    assert created.status_code == 201, created.text
    trip = created.json()
    assert trip["flight_snapshot"]["option"]["observed_price"] == 8150
    assert trip["flight_snapshot"]["group_total_estimate"] == 30000
    assert not trip["flight_snapshot"]["schedule_adjusted"]
    gen = app.dependency_overrides[get_session]()
    session = next(gen)
    record = session.get(FlightSearchObservation, observed["observation_id"])
    record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    session.commit()
    gen.close()
    assert client.post(f"/api/v1/groups/{gid}/trips", json=payload(observed)).status_code == 422
    client.put(f"/api/v1/groups/{gid}/members/{ids[0]}/age-profile", json={"age_on_start": 8})
    assert client.get(f"/api/v1/trips/{trip['id']}").json() == trip
    generated = client.post(f"/api/v1/trips/{trip['id']}/{collection}?algorithm={algorithm}")
    assert generated.status_code == 201, generated.text
    route = generated.json()["options"][0] if collection == "route-sets" else generated.json()
    report = route["result"]["budget_breakdown"]
    assert route["result"]["is_feasible"]
    assert report["known_total"] == 45200 and report["available_for_stops"] == 54000
    assert report["flights_estimate"] == 30000 and report["transport_estimate"] == 5000
    assert report["unknown_categories"] == []
    assert len(source) == 1


def test_owner_age_city_dates_option_checksum_and_transport_scope_are_checked(client, source):
    gid, ids = create_group(client, 2)
    observed = observation(client, gid)
    other, _ = create_group(client, 1)
    assert client.post(f"/api/v1/groups/{other}/trips", json=payload(observed)).status_code == 422
    for patch in (
        {"destination": "Porto"},
        {"travel_date": str(search().departure - timedelta(days=1))},
        {"flight_plan": {"observation_id": observed["observation_id"], "option_id": "0" * 32}},
        {"budget_settings": {"flights": 30000, "transport": 5000}},
    ):
        assert (
            client.post(f"/api/v1/groups/{gid}/trips", json=payload(observed) | patch).status_code
            == 422
        )
    gen = app.dependency_overrides[get_session]()
    session = next(gen)
    row = session.get(FlightSearchObservation, observed["observation_id"])
    original = row.content_sha256
    row.content_sha256 = "0" * 64
    session.commit()
    assert client.post(f"/api/v1/groups/{gid}/trips", json=payload(observed)).status_code == 422
    row.content_sha256 = original
    session.commit()
    gen.close()
    client.put(f"/api/v1/groups/{gid}/members/{ids[0]}/age-profile", json={"age_on_start": 8})
    assert client.post(f"/api/v1/groups/{gid}/trips", json=payload(observed)).status_code == 422


def test_unknown_group_total_is_not_the_observed_price_and_manual_flights_need_no_search(
    client, source
):
    gid, _ = create_group(client, 1)
    observed = observation(client, gid)
    data = payload(observed)
    data["budget_settings"]["flights"] = None
    r = client.post(f"/api/v1/groups/{gid}/trips", json=data)
    assert r.status_code == 201, r.text
    assert r.json()["flight_snapshot"]["group_total_estimate"] is None
    data["flight_plan"] = None
    data["budget_settings"]["flights"] = 0
    assert client.post(f"/api/v1/groups/{gid}/trips", json=data).status_code == 201
