import os

import pytest
from test_groups_db import client as client
from test_groups_db import create_group
from test_meals_budget import LUNCH, fixture

pytestmark = pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="Requires PostgreSQL")


@pytest.mark.parametrize("algorithm", ["greedy", "beam", "cp-sat"])
@pytest.mark.parametrize("collection", ["routes", "route-sets"])
def test_api_freezes_meals_and_budget_and_restores_priced_results(
    client, monkeypatch, algorithm, collection
):
    _, data = fixture(days=2)

    def dataset(self, day, *args, **kwargs):
        source = data[(day - data[0].travel_date).days]
        return source.places, source.travel, source.data_context

    monkeypatch.setattr("app.api.groups.CityProvider.dataset", dataset)
    gid, _ = create_group(client, 2)
    payload = {
        "destination": "Porto",
        "travel_date": "2026-10-26",
        "end_date": "2026-10-27",
        "budget": 50000,
        "pace": "balanced",
        "meal_settings": LUNCH,
        "budget_settings": {"lodging": 10000, "transport": 2000, "reserve": 3000},
    }
    response = client.post(f"/api/v1/groups/{gid}/trips", json=payload)
    assert response.status_code == 201, response.text
    trip = response.json()
    assert trip["budget_settings"]["lodging"] == 10000
    assert trip["meal_settings"]["lunch"]["max_cost_per_person"] == 1500
    response = client.post(f"/api/v1/trips/{trip['id']}/{collection}?algorithm={algorithm}")
    assert response.status_code == 201, response.text
    saved = response.json()
    route = saved["options"][0] if collection == "route-sets" else saved
    result = route["result"]
    assert result["is_feasible"] and result["validation"]["is_valid"]
    assert all(
        sum(v["place_id"].startswith("meal:") for v in day["visits"]) == 1 for day in result["days"]
    )
    assert all(v["group_cost"] == v["cost_per_person"] * 2 for v in result["visits"])
    report = result["budget_breakdown"]
    assert report["food"] == 4000
    assert report["stops_total"] == result["total_cost"]
    assert report["known_total"] == result["total_cost"] + 12000
    assert report["available_for_stops"] == 35000
    assert report["unallocated_after_reserve"] == 35000 - result["total_cost"]
    assert client.get(f"/api/v1/{collection}/{saved['id']}").json() == saved
    assert client.get(f"/api/v1/trips/{trip['id']}").json() == trip


def test_api_rejects_estimates_larger_than_total_before_saving_trip(client):
    gid, _ = create_group(client, 1)
    response = client.post(
        f"/api/v1/groups/{gid}/trips",
        json={
            "budget": 1000,
            "budget_settings": {"lodging": 900, "transport": 200},
        },
    )
    assert response.status_code == 422
    assert client.get(f"/api/v1/groups/{gid}/trips").json() == []
