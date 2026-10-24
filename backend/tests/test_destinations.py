import json
from datetime import date, timedelta

import pytest
from catalog_helpers import NOW, snapshot
from fastapi.testclient import TestClient

from app.catalog.reader import CatalogReader
from app.catalog.refresh import collect_snapshot
from app.catalog.repository import PublishedSnapshot
from app.core.destinations import CITIES
from app.integrations.city import CityProvider, parse_catalog
from app.main import app
from app.nlp.grounding import destination_supported
from app.nlp.retrieval import Retrieval
from app.planning.candidates import select_candidates
from app.schemas.groups import TripInput
from app.visa import assess_visas


def test_city_registry_and_api_preserve_legacy_keys():
    data = TestClient(app).get("/api/v1/destinations").json()
    assert {row["id"] for row in data} == {"Demo City", "Lisbon", "Porto", "Sintra"}
    for row in data:
        if not row["synthetic"]:
            assert row["country"] == "PT"
            assert row["origin"] == CITIES[row["id"]].origin


@pytest.mark.parametrize("city", ["Lisbon", "Porto", "Sintra"])
def test_real_cities_support_dates_search_and_portugal_visa_rules(city):
    trip = TripInput(
        destination=city, travel_date="2026-11-01", budget=1000, semantic_query="history"
    )
    assert trip.destination == city
    assert (
        assess_visas(city, date(2026, 11, 1), None, [], today=NOW.date())["destination_country"]
        == "PT"
    )
    with pytest.raises(ValueError):
        TripInput(destination=city, budget=1000)


def test_pinned_catalog_reader_loads_the_requested_city(monkeypatch):
    calls = []

    def load(city):
        calls.append(city)
        data = snapshot(destination=city)
        return PublishedSnapshot(1, NOW, data, data.digest())

    monkeypatch.setattr("app.catalog.reader.load_current", load)
    reader = CatalogReader(destination="Porto", clock=lambda: NOW)
    assert reader.catalog(NOW.date())["destination"] == "Porto"
    assert reader.catalog(NOW.date() + timedelta(days=1))["destination"] == "Porto"
    assert calls == ["Porto"]


def test_osm_bounds_and_provider_copies_are_city_specific():
    body = (
        b'<osm><node id="1" lat="41.146" lon="-8.61"><tag k="name" v="Museum"/>'
        b'<tag k="tourism" v="museum"/></node></osm>'
    )
    assert parse_catalog(body, NOW.date()) == []
    assert len(parse_catalog(body, NOW.date(), CITIES["Porto"].bbox)) == 1
    source = CityProvider(destination="Porto", preferences={"art": 1})
    clone = source.with_client(object())
    assert clone.city.id == "Porto" and clone.preferences == {"art": 1}
    assert clone.selection_state is source.selection_state


def test_qdrant_document_identity_does_not_cross_city_boundaries():
    poi = {"id": "same", "name": "Museum", "category": "art", "source_url": "https://example.org"}
    porto = Retrieval(destination="Porto").records([poi])[1][0]
    lisbon = Retrieval().records([poi])[1][0]
    assert porto["key"] != lisbon["key"]
    assert porto["metadata"]["city"] == "Porto"


def test_candidate_limit_category_coverage_and_rotation_keep_mandatory():
    pois = [
        {"id": str(i), "category": category}
        for i, category in enumerate(["art", "nature", "history", "food"] * 20)
    ]
    first = select_candidates(pois, {"79"}, {"art": 1}, offset=0)
    second = select_candidates(pois, {"79"}, {"art": 1}, offset=1)
    assert len(first) == len(second) == 24
    assert first[0]["id"] == second[0]["id"] == "79"
    assert {p["category"] for p in first} == {"art", "nature", "history", "food"}
    assert {p["id"] for p in first} != {p["id"] for p in second}


def test_reviewed_only_publication_does_not_reset_source_freshness(tmp_path):
    path = tmp_path / "city.json"
    path.write_text(
        json.dumps(
            {
                "version": "reviewed-v1",
                "checked_on": "2026-10-24",
                "valid_until": "2026-11-22",
                "price_policy": "adult",
                "pois": [
                    {
                        "id": "curated:porto:test",
                        "name": "Museum",
                        "category": "art",
                        "cost_per_person": 400,
                        "duration": 60,
                        "opening_hours": "Tu-Su 10:00-18:00",
                        "latitude": 41.15,
                        "longitude": -8.61,
                        "coordinates_source": "https://example.org/map",
                        "price_source": "https://example.org/price",
                        "hours_source": "https://example.org/hours",
                        "notes": "fixture",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    first = collect_snapshot(curated_path=path, now=NOW, destination="Porto", curated_only=True)
    second = collect_snapshot(
        curated_path=path, now=NOW + timedelta(days=1), destination="Porto", curated_only=True
    )
    assert first.digest() == second.digest()
    assert (
        first.manifest.destination == "Porto" and first.manifest.origin.name == "Praça da Liberdade"
    )
    assert first.manifest.fetched_at.hour == 0


def test_nlp_accepts_new_cities_but_requires_review_for_multiple_cities():
    assert destination_supported("Porto", "Порту", "Едем в Порту")
    assert destination_supported("Sintra", "Синтру", "Едем в Синтру")
    assert not destination_supported("Porto", "Порту", "Не в Порту, а в Синтру")
    assert not destination_supported("Porto", "Порту", "Посетим Порту и Лиссабон")


def test_free_weekday_prices_are_derived_without_changing_saved_regular_price():
    data = snapshot(curated=True)
    fact = data.pois[0].model_copy(update={"cost_per_person": 400, "free_weekdays": [6]})
    reviewed = data.model_copy(update={"pois": (fact,)})
    published = PublishedSnapshot(1, NOW, reviewed, reviewed.digest())
    reader = CatalogReader(loader=lambda: published, clock=lambda: NOW)
    assert reader.catalog(date(2026, 10, 24))["pois"][0]["cost_per_person"] == 400
    assert reader.catalog(date(2026, 10, 25))["pois"][0]["cost_per_person"] == 0
    assert reviewed.pois[0].cost_per_person == 400
    assert "free_weekdays" not in data.pois[0].model_dump()
