import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cache_helpers import MemoryRedis
from test_groups_db import client as client
from test_groups_db import create_group
from test_xotelo import response, stay

from app.db.models import LodgingProviderQuote
from app.db.session import get_session
from app.integrations.http_cache import RedisHttpCache
from app.integrations.xotelo import Xotelo
from app.main import app
from app.planning.models import Place

pytestmark = pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="Requires PostgreSQL")


@pytest.fixture
def source(client, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response(request, True))

    with httpx.Client(transport=httpx.MockTransport(handler)) as upstream:
        provider = Xotelo(RedisHttpCache(MemoryRedis()), upstream)
        monkeypatch.setattr("app.api.lodging.Xotelo", lambda: provider)
        monkeypatch.setattr("app.catalog.lodging_quotes.Xotelo", lambda: provider)
        yield requests


def group(client, ages=(30, 35)):
    gid, ids = create_group(client, len(ages))
    for uid, age in zip(ids, ages, strict=True):
        client.put(f"/api/v1/groups/{gid}/members/{uid}/age-profile", json={"age_on_start": age})
    return gid, ids


def quote(client, gid, rooms=1):
    result = client.post(
        f"/api/v1/groups/{gid}/lodging-quotes", json=stay(rooms).model_dump(mode="json")
    )
    assert result.status_code == 201, result.text
    return result.json()


def payload(observation, estimate=20100, apply=True):
    start = stay().checkin
    return {
        "destination": "Lisbon",
        "travel_date": str(start),
        "end_date": str(start + timedelta(days=1)),
        "budget": 20300,
        "mandatory": ["p"],
        "budget_settings": {"lodging": estimate, "transport": 0, "reserve": 0},
        "lodging_quote": {
            "quote_id": observation["quote_id"],
            "rate_code": "BookingCom",
            "apply_base_estimate": apply,
        },
    }


@pytest.mark.parametrize("algorithm", ["greedy", "beam", "cp-sat"])
@pytest.mark.parametrize("collection", ["routes", "route-sets"])
def test_frozen_base_estimate_is_charged_once_and_history_survives_expiry(
    client, source, monkeypatch, algorithm, collection
):
    monkeypatch.setattr(
        "app.api.groups.CityProvider.dataset",
        lambda *_args, **_kwargs: (
            [Place("p", "nature", 100, 60, 540, 1080)],
            {("start", "p"): 1},
            {"pois": [{"id": "p", "name": "Park"}]},
        ),
    )
    gid, ids = group(client)
    observed = quote(client, gid)
    assert observed["rates"][0]["estimated_base_total"] == 20100
    duplicate = quote(client, gid)
    assert duplicate["fetched_at"] == observed["fetched_at"] and duplicate["cache"]["read"] == "hit"
    created = client.post(f"/api/v1/groups/{gid}/trips", json=payload(observed))
    assert created.status_code == 201, created.text
    trip = created.json()
    assert trip["lodging_plan"] is None and trip["lodging_snapshot"]["mode"] == "xotelo"
    assert trip["lodging_snapshot"]["price_status"] == "provider_base_estimate"
    assert trip["lodging_snapshot"]["booking_status"] == "not_booked"
    gen = app.dependency_overrides[get_session]()
    session = next(gen)
    record = session.get(LodgingProviderQuote, observed["quote_id"])
    record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    session.commit()
    gen.close()
    client.put(f"/api/v1/groups/{gid}/members/{ids[1]}/age-profile", json={"age_on_start": 36})
    assert client.get(f"/api/v1/trips/{trip['id']}").json() == trip
    result = client.post(f"/api/v1/trips/{trip['id']}/{collection}?algorithm={algorithm}")
    assert result.status_code == 201, result.text
    saved = result.json()
    route = saved["options"][0] if collection == "route-sets" else saved
    report = route["result"]["budget_breakdown"]
    assert route["result"]["is_feasible"] and report["known_total"] == 20300
    assert (
        report["available_for_stops"] == 200
        and report["lodging_price_basis"] == "xotelo_base_estimate"
    )
    assert "lodging_taxes_and_fees" in report["unknown_categories"]


def test_changed_group_wrong_owner_tampered_amount_and_unknown_rate_are_rejected(client, source):
    gid, ids = group(client)
    observed = quote(client, gid)
    other, _ = group(client)
    assert client.post(f"/api/v1/groups/{other}/trips", json=payload(observed)).status_code == 422
    assert (
        client.post(f"/api/v1/groups/{gid}/trips", json=payload(observed, estimate=1)).status_code
        == 422
    )
    invalid = payload(observed)
    invalid["lodging_quote"]["rate_code"] = "invented"
    assert client.post(f"/api/v1/groups/{gid}/trips", json=invalid).status_code == 422
    client.put(f"/api/v1/groups/{gid}/members/{ids[1]}/age-profile", json={"age_on_start": 8})
    assert client.post(f"/api/v1/groups/{gid}/trips", json=payload(observed)).status_code == 422


def test_multiple_rooms_are_manual_and_do_not_get_an_automatic_multiplier(client, source):
    gid, _ = group(client)
    observed = quote(client, gid, 2)
    assert observed["rates"][0]["estimated_base_total"] is None
    assert client.post(f"/api/v1/groups/{gid}/trips", json=payload(observed)).status_code == 422
    created = client.post(
        f"/api/v1/groups/{gid}/trips", json=payload(observed, estimate=19000, apply=False)
    )
    assert created.status_code == 201, created.text
    assert created.json()["lodging_snapshot"]["group_total_estimate"] == 19000


@pytest.mark.parametrize("damage", ["expired", "checksum"])
def test_expired_or_corrupted_observation_cannot_create_new_trip(client, source, damage):
    gid, _ = group(client)
    observed = quote(client, gid)
    gen = app.dependency_overrides[get_session]()
    session = next(gen)
    record = session.get(LodgingProviderQuote, observed["quote_id"])
    if damage == "expired":
        record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    else:
        record.content_sha256 = "0" * 64
    session.commit()
    gen.close()
    rejected = client.post(f"/api/v1/groups/{gid}/trips", json=payload(observed))
    assert rejected.status_code == 422
    assert ("истёк" if damage == "expired" else "повреждён") in rejected.json()["detail"]


def test_unknown_ages_make_no_upstream_call_and_empty_family_is_not_a_fake_offer(client, source):
    gid, _ = group(client, (30, None))
    count = len(source)
    assert (
        client.post(
            f"/api/v1/groups/{gid}/lodging-quotes", json=stay().model_dump(mode="json")
        ).status_code
        == 422
    )
    assert len(source) == count
    gid, _ = group(client, (30, 8))
    observed = quote(client, gid)
    assert observed["status"] == "empty" and observed["rates"] == []
    assert client.post(f"/api/v1/groups/{gid}/trips", json=payload(observed)).status_code == 422
    assert (
        source[-1].url.params["adults"] == "1" and source[-1].url.params["age_of_children"] == "8"
    )
