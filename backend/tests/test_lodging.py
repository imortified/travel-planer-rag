from datetime import date, datetime, timedelta, timezone

import pytest

from app.schemas.groups import TripInput
from app.schemas.lodging import LodgingCatalog, LodgingProperty, LodgingSelection, public_website

NOW = datetime(2026, 10, 25, 10, tzinfo=timezone.utc)


def property_(identifier=1001, city="Lisbon", name="Hôtel Lisboa", kind="hotel"):
    return LodgingProperty(
        id=f"osm:node:{identifier}",
        destination=city,
        name=name,
        kind=kind,
        latitude=38.71,
        longitude=-9.14,
        location_kind="mapped_node",
        address="Rua do Teste, 1",
        website_url="https://example.org/hotel",
        source_url=f"https://www.openstreetmap.org/node/{identifier}",
    )


def catalog(city="Lisbon", name="Hôtel Lisboa", source=None):
    return LodgingCatalog(
        destination=city,
        source="https://download.geofabrik.de/europe/portugal.html",
        source_upstream_asof=source or NOW - timedelta(days=1),
        downloaded_at=NOW - timedelta(hours=1),
        input_sha256="a" * 64,
        properties=[property_(city=city, name=name), property_(1002, city, "Albergue", "hostel")],
    )


def selection(**overrides):
    return {
        "property_id": "osm:node:1001",
        "catalog_version_id": 1,
        "checkin": "2026-10-26",
        "checkout": "2026-10-28",
        "rooms": 1,
        **overrides,
    }


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "data:text/html,bad",
        "https://user:pass@example.org/",
        "http://127.0.0.1/",
        "http://localhost/",
        "https://private.local/",
        "https://example.org:8001/",
    ],
)
def test_directory_website_does_not_allow_unsafe_link_schemes_or_local_addresses(value):
    assert public_website(value) is None
    with pytest.raises(ValueError):
        LodgingProperty.model_validate({**property_().model_dump(), "website_url": value})


def test_catalog_identity_checksum_and_source_dates_are_validated():
    data = catalog()
    assert (
        data.digest()
        == data.model_copy(update={"properties": list(reversed(data.properties))}).digest()
    )
    assert data.digest() != catalog(name="New name").digest()
    with pytest.raises(ValueError, match="Duplicate"):
        LodgingCatalog.model_validate(
            {**data.model_dump(), "properties": [data.properties[0], data.properties[0]]}
        )
    with pytest.raises(ValueError, match="city mismatch"):
        LodgingCatalog.model_validate({**data.model_dump(), "destination": "Porto"})
    with pytest.raises(ValueError, match="source must match"):
        LodgingProperty.model_validate(
            {**property_().model_dump(), "source_url": "https://example.org"}
        )
    with pytest.raises(ValueError, match="timezones"):
        LodgingCatalog.model_validate(
            {**data.model_dump(), "downloaded_at": datetime(2026, 10, 25)}
        )


@pytest.mark.parametrize(
    "patch",
    [
        {"checkout": "2026-10-26"},
        {"checkout": "2026-11-26"},
        {"rooms": 0},
        {"rooms": True},
        {"catalog_version_id": 0},
        {"total_cost": 100},
    ],
)
def test_selection_rejects_invalid_dates_rooms_and_client_quotes(patch):
    with pytest.raises(ValueError):
        LodgingSelection.model_validate(selection(**patch))


def test_trip_requires_real_city_full_budget_and_covered_dates(monkeypatch):
    monkeypatch.setattr("app.schemas.groups.trip_today", lambda _: date(2026, 10, 23))
    payload = {
        "destination": "Lisbon",
        "travel_date": "2026-10-26",
        "end_date": "2026-10-28",
        "budget": 50000,
        "budget_settings": {"lodging": 25000},
        "lodging_plan": selection(),
    }
    assert TripInput.model_validate(payload).lodging_plan.checkout == date(2026, 10, 28)
    for patch in [
        {"destination": "Demo City"},
        {"budget_settings": None},
        {"end_date": "2026-10-29"},
        {"lodging_plan": selection(checkin="2026-10-27")},
    ]:
        with pytest.raises(ValueError):
            TripInput.model_validate({**payload, **patch})
    unknown = TripInput.model_validate({**payload, "budget_settings": {"lodging": None}})
    assert unknown.budget_settings.lodging is None  # An unknown quote must not become zero.
