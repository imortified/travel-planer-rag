import os
from datetime import timedelta

import pytest
from test_groups_db import client as client
from test_groups_db import create_group
from test_lodging import NOW, catalog, selection

from app.catalog.lodging import load_lodgings, publish_lodgings, search_lodgings
from app.db.models import LodgingCatalogVersion
from app.db.session import get_session
from app.main import app
from app.planning.models import Place

pytestmark = pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="Requires PostgreSQL")


@pytest.fixture
def directory(client):
    generator = app.dependency_overrides[get_session]()
    session = next(generator)
    version, _ = publish_lodgings(session, catalog(), NOW)
    session.commit()
    try:
        yield session, version
    finally:
        generator.close()


def test_publication_reuses_versions_and_preserves_old_sources(directory):
    session, first = directory
    same, reused = publish_lodgings(session, catalog(), NOW)
    assert reused and same.id == first.id
    updated, _ = publish_lodgings(session, catalog(name="Renamed hotel"), NOW)
    assert updated.id != first.id
    assert load_lodgings(session, "Lisbon")[1].properties[0].name == "Renamed hotel"
    assert load_lodgings(session, "Lisbon", first.id)[1].properties[0].name == "Hôtel Lisboa"
    with pytest.raises(ValueError, match="older upstream"):
        publish_lodgings(session, catalog(source=NOW - timedelta(days=2)), NOW)
    with pytest.raises(ValueError, match="future"):
        publish_lodgings(
            session, catalog().model_copy(update={"downloaded_at": NOW + timedelta(hours=1)}), NOW
        )
    assert load_lodgings(session, "Lisbon")[0].id == updated.id
    session.commit()


def test_search_filters_accents_kind_pagination_and_never_claims_live_offers(client, directory):
    session, version = directory
    result = client.get(
        "/api/v1/destinations/Lisbon/lodgings?q=hotel&kind=hotel&page_size=1"
    ).json()
    assert result["total"] == 1 and result["properties"][0]["name"] == "Hôtel Lisboa"
    assert result["catalog_version_id"] == version.id and result["live_offers"] is False
    assert result["price_status"] == "unknown" and result["availability_status"] == "not_checked"
    assert (
        search_lodgings(session, "Lisbon", page=2, page_size=1)["properties"][0]["id"]
        == "osm:node:1001"
    )
    assert client.get("/api/v1/destinations/Lisbon/lodgings?q=nothing").json()["total"] == 0
    assert client.get("/api/v1/destinations/Lisbon/lodgings?page=0").status_code == 422
    assert client.get("/api/v1/destinations/Demo%20City/lodgings").status_code == 422


@pytest.mark.parametrize("algorithm", ["greedy", "beam", "cp-sat"])
@pytest.mark.parametrize("collection", ["routes", "route-sets"])
def test_lodging_is_frozen_and_charged_once_with_mixed_ages(
    client, directory, monkeypatch, algorithm, collection
):
    session, version = directory
    monkeypatch.setattr(
        "app.api.groups.CityProvider.dataset",
        lambda *_args, **_kwargs: (
            [Place("p", "nature", 100, 60, 540, 1080)],
            {("start", "p"): 1},
            {"pois": [{"id": "p", "name": "Park"}]},
        ),
    )
    gid, ids = create_group(client, 3)
    for uid, age in zip(ids, [30, 8, None], strict=True):
        assert (
            client.put(
                f"/api/v1/groups/{gid}/members/{uid}/age-profile", json={"age_on_start": age}
            ).status_code
            == 200
        )
    payload = {
        "destination": "Lisbon",
        "travel_date": "2026-10-26",
        "budget": 10300,
        "mandatory": ["p"],
        "budget_settings": {"lodging": 10000, "transport": 0, "reserve": 0},
        "lodging_plan": selection(catalog_version_id=version.id),
    }
    created = client.post(f"/api/v1/groups/{gid}/trips", json=payload)
    assert created.status_code == 201, created.text
    trip = created.json()
    stay = trip["lodging_snapshot"]
    assert stay["property"]["name"] == "Hôtel Lisboa" and stay["nights"] == 2
    assert stay["adults"] == 1 and stay["children_ages"] == [8] and stay["unknown_ages"] == 1
    assert stay["group_total_estimate"] == 10000 and stay["booking_status"] == "not_booked"
    response = client.post(f"/api/v1/trips/{trip['id']}/{collection}?algorithm={algorithm}")
    assert response.status_code == 201, response.text
    saved = response.json()
    route = saved["options"][0] if collection == "route-sets" else saved
    assert route["result"]["is_feasible"] and route["result"]["validation"]["is_valid"]
    assert route["result"]["total_cost"] == 300
    report = route["result"]["budget_breakdown"]
    assert report["known_total"] == 10300 and report["available_for_stops"] == 300
    publish_lodgings(session, catalog(name="Updated hotel"), NOW)
    session.commit()
    client.put(f"/api/v1/groups/{gid}/members/{ids[1]}/age-profile", json={"age_on_start": 9})
    assert client.get(f"/api/v1/trips/{trip['id']}").json() == trip
    assert client.get(f"/api/v1/{collection}/{saved['id']}").json() == saved


def test_unknown_total_remains_unknown_and_tampered_selection_is_rejected(client, directory):
    _, version = directory
    gid, _ = create_group(client, 1)
    payload = {
        "destination": "Lisbon",
        "travel_date": "2026-10-26",
        "budget": 50000,
        "budget_settings": {"lodging": None},
        "lodging_plan": selection(catalog_version_id=version.id),
    }
    response = client.post(f"/api/v1/groups/{gid}/trips", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["lodging_snapshot"]["group_total_estimate"] is None
    for patch in [{"property_id": "osm:node:999999"}, {"catalog_version_id": 99999999}]:
        invalid = {**payload, "lodging_plan": {**payload["lodging_plan"], **patch}}
        assert client.post(f"/api/v1/groups/{gid}/trips", json=invalid).status_code == 422
    invalid = {**payload, "destination": "Porto"}
    assert client.post(f"/api/v1/groups/{gid}/trips", json=invalid).status_code == 422


def test_corrupt_directory_cannot_be_searched_or_selected(client, directory):
    session, version = directory
    row = session.get(LodgingCatalogVersion, version.id)
    row.data = {**row.data, "attribution": "corrupted"}
    session.commit()
    assert client.get("/api/v1/destinations/Lisbon/lodgings").status_code == 503
