import os
from datetime import date

import pytest
from test_age_pricing import RULES
from test_groups_db import client as client
from test_groups_db import create_group

from app.planning.models import Place

pytestmark = pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="Requires PostgreSQL")


@pytest.mark.parametrize("algorithm", ["greedy", "beam", "cp-sat"])
@pytest.mark.parametrize("collection", ["routes", "route-sets"])
def test_age_profiles_affect_real_api_costs_and_history_is_frozen(
    client, monkeypatch, algorithm, collection
):
    monkeypatch.setattr("app.planning.age_pricing.assessment_date", lambda: date(2026, 10, 26))
    monkeypatch.setattr(
        "app.api.groups.CityProvider.dataset",
        lambda *_args, **_kwargs: (
            [Place("zoo", "nature", 3100, 60, 600, 1080)],
            {("start", "zoo"): 5},
            {
                "pois": [
                    {"id": "zoo", "name": "Fixture zoo", "category": "nature", "age_rules": RULES}
                ]
            },
        ),
    )
    gid, ids = create_group(client, 3)
    for uid, age in zip(ids, [30, 35, 8], strict=True):
        response = client.put(
            f"/api/v1/groups/{gid}/members/{uid}/age-profile", json={"age_on_start": age}
        )
        assert response.status_code == 200
        # Changing another part of the member profile must preserve the age declaration.
        response = client.put(
            f"/api/v1/groups/{gid}/members/{uid}/preferences", json={"interests": {"nature": 1}}
        )
        assert response.json()["age_profile"]["age_on_start"] == age
    trip = client.post(
        f"/api/v1/groups/{gid}/trips",
        json={
            "destination": "Lisbon",
            "travel_date": "2026-10-26",
            "budget": 8100,
            "mandatory": ["zoo"],
        },
    ).json()
    assert trip["participants_snapshot"][2]["age_profile"]["age_on_start"] == 8
    response = client.post(f"/api/v1/trips/{trip['id']}/{collection}?algorithm={algorithm}")
    assert response.status_code == 201, response.text
    saved = response.json()
    route = saved["options"][0] if collection == "route-sets" else saved
    assert route["result"]["is_feasible"] and route["result"]["total_cost"] == 8100
    visit = route["result"]["visits"][0]
    assert visit["cost_per_person"] is None and visit["group_cost"] == 8100
    assert [row["cost"] for row in visit["participant_prices"]] == [3100, 3100, 1900]
    client.put(f"/api/v1/groups/{gid}/members/{ids[2]}/age-profile", json={"age_on_start": 16})
    assert client.get(f"/api/v1/trips/{trip['id']}").json() == trip
    assert client.get(f"/api/v1/{collection}/{saved['id']}").json() == saved
    group = client.get(f"/api/v1/groups/{gid}").json()
    assert group["members"][2]["age_profile"]["age_on_start"] == 16


def test_birthday_profile_survives_json_snapshot_and_invalid_ages_are_rejected(client):
    gid, ids = create_group(client, 1)
    path = f"/api/v1/groups/{gid}/members/{ids[0]}/age-profile"
    for bad in (
        {"age_on_start": -1},
        {"age_on_start": True},
        {"age_on_start": 8, "birth_date": "2018-01-01"},
    ):
        assert client.put(path, json=bad).status_code == 422
    assert client.put(path, json={"birth_date": "2013-10-27"}).status_code == 200
    trip = client.post(
        f"/api/v1/groups/{gid}/trips",
        json={
            "destination": "Porto",
            "travel_date": "2026-10-26",
            "end_date": "2026-10-28",
            "budget": 10000,
        },
    )
    assert trip.status_code == 201
    assert trip.json()["participants_snapshot"][0]["age_profile"]["birth_date"] == "2013-10-27"
    client.put(path, json={"birth_date": "2027-01-01"})
    assert (
        client.post(
            f"/api/v1/groups/{gid}/trips",
            json={"destination": "Porto", "travel_date": "2026-10-26", "budget": 10000},
        ).status_code
        == 422
    )
