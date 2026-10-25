import os
from datetime import date

import pytest
from test_groups_db import client as client
from test_groups_db import create_group

from app.planning.models import Place

pytestmark = pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="Requires PostgreSQL")


def test_api_rejects_past_before_storage_and_keeps_undated_demo(client, monkeypatch):
    monkeypatch.setattr("app.schemas.groups.trip_today", lambda _destination: date(2026, 11, 2))
    gid, _ = create_group(client)
    past = client.post(
        f"/api/v1/groups/{gid}/trips",
        json={"destination": "Lisbon", "travel_date": "2026-11-01", "budget": 10000},
    )
    assert past.status_code == 422 and "не может быть в прошлом" in past.text
    assert client.get(f"/api/v1/groups/{gid}/trips").json() == []
    assert client.post(f"/api/v1/groups/{gid}/trips", json={"budget": 10000}).status_code == 201


@pytest.mark.parametrize("algorithm", ["greedy", "beam", "cp-sat"])
@pytest.mark.parametrize("collection", ["routes", "route-sets"])
def test_date_rollover_preserves_history_and_recalculation(
    client, monkeypatch, algorithm, collection
):
    monkeypatch.setattr("app.schemas.groups.trip_today", lambda _destination: date(2026, 11, 2))
    monkeypatch.setattr(
        "app.api.groups.CityProvider.dataset",
        lambda *_args, **_kwargs: (
            [Place("p", "history", 100, 60, 540, 1080)],
            {("start", "p"): 1},
            {"pois": [{"id": "p", "name": "Date regression fixture"}]},
        ),
    )
    gid, _ = create_group(client, 1)
    payload = {
        "destination": "Lisbon",
        "travel_date": "2026-11-02",
        "budget": 10000,
        "mandatory": ["p"],
    }
    created = client.post(f"/api/v1/groups/{gid}/trips", json=payload)
    assert created.status_code == 201, created.text
    saved = created.json()
    monkeypatch.setattr("app.schemas.groups.trip_today", lambda _destination: date(2027, 1, 1))
    assert client.post(f"/api/v1/groups/{gid}/trips", json=payload).status_code == 422
    assert client.get(f"/api/v1/trips/{saved['id']}").json() == saved
    assert client.get(f"/api/v1/groups/{gid}/trips").json() == [saved]
    calculated = client.post(f"/api/v1/trips/{saved['id']}/{collection}?algorithm={algorithm}")
    assert calculated.status_code == 201, calculated.text
    route = calculated.json()["options"][0] if collection == "route-sets" else calculated.json()
    assert route["result"]["is_feasible"]
    assert client.get(f"/api/v1/routes/{route['id']}").json() == route
