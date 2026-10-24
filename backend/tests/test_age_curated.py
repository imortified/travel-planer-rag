import json
from datetime import date

import pytest

from app.core.config import ROOT
from app.integrations.curated import Catalog
from app.planning.age_pricing import quote
from app.planning.models import Participant, Place

DAY = date(2026, 10, 26)


def catalog(city):
    return Catalog.model_validate_json(
        (ROOT / f"data/{city.lower()}-curated.json").read_text("utf-8")
    )


def priced(city, identifier, ages, free=False):
    entry = next(p for p in catalog(city).pois if p.id == identifier)
    place = Place(entry.id, entry.category, entry.cost_per_person, entry.duration, 540, 1080)
    people = [Participant(str(i), {}, age) for i, age in enumerate(ages)]
    return quote(
        place, {**entry.model_dump(mode="json"), "date_free_admission": free}, people, DAY, DAY
    )


def test_verified_zoo_tariffs_and_maat_companion_policy():
    rows, reason = priced("Lisbon", "osm:way:30104081", [0, 2, 3, 12, 13, 64, 65, None])
    assert reason is None
    assert [row["cost"] for row in rows] == [0, 0, 1900, 1900, 3100, 3100, 2200, 3100]
    rows, reason = priced("Lisbon", "osm:node:5150663698", [30, 8, 65])
    assert reason is None and [row["cost"] for row in rows] == [1600, 0, 1600]
    assert priced("Lisbon", "osm:node:5150663698", [8])[1] == "adult_companion_required"
    assert priced("Lisbon", "osm:node:5150663698", [None])[1] == "age_required_for_access_check"


def test_porto_exact_over65_boundary_and_free_sunday_override():
    rows, _ = priced("Porto", "osm:way:255778741", [17, 18, 65, 66])
    assert [row["cost"] for row in rows] == [0, 400, 400, 200]
    rows, _ = priced("Porto", "osm:way:255778741", [17, 18, 65, 66], free=True)
    assert all(row["cost"] == 0 and row["reason"] == "date_free_admission" for row in rows)


@pytest.mark.parametrize(
    "identifier,ordinary,discount",
    [("osm:way:146132549", 1200, 1000), ("osm:way:172868730", 1100, 900)],
)
def test_sintra_partial_verified_bands_have_explicit_fallback(identifier, ordinary, discount):
    rows, _ = priced("Sintra", identifier, [5, 6, 17, 18, 64, 65, 66])
    assert [row["cost"] for row in rows] == [
        ordinary,
        discount,
        discount,
        ordinary,
        ordinary,
        ordinary,
        discount,
    ]
    assert rows[0]["reason"] == rows[5]["reason"] == "regular_fallback_no_age_band"


def test_age_reviews_are_bounded_and_food_orders_have_no_invented_discount():
    counts = {
        city: sum(p.age_rules is not None for p in catalog(city).pois)
        for city in ("Lisbon", "Porto", "Sintra")
    }
    assert counts == {"Lisbon": 2, "Porto": 5, "Sintra": 2}
    body = json.loads((ROOT / "data/lisbon-curated.json").read_text("utf-8"))
    entry = next(p for p in body["pois"] if p.get("age_rules"))
    entry["age_rules"]["valid_until"] = "2026-11-23"
    with pytest.raises(ValueError, match="Age-rule review"):
        Catalog.model_validate(body)
