from datetime import date

import pytest

from app.integrations.city import opening_window
from app.integrations.curated import enrich
from app.planning.models import Participant, Place, Request
from app.planning.service import checked_plan


def mapped_pois():
    return [
        {
            "id": f"osm:node:{pid}",
            "name": "Mapped POI",
            "category": "history",
            "latitude": 38.713,
            "longitude": -9.139,
            "source_url": "https://www.openstreetmap.org",
            "fee": "yes",
            "opening_hours": "Tu-Su 10:00-19:00",
            "osm_timestamp": "2026-10-01",
            "cost_per_person": None,
            "window": [600, 1140],
            "duration": 60,
            "eligible": False,
            "issues": ["unknown_price"],
        }
        for pid in (4419041593, 5068486965, 11047643391, 13174527080, 13096661171, 12926040967)
    ]


def test_enrichment_adds_paid_and_nature_pois_with_sources():
    pois, version = enrich(mapped_pois(), date(2026, 10, 27), opening_window)
    assert len(pois) >= 40
    assert any(p["eligible"] for p in pois)
    assert not next(p for p in pois if p["id"] == "osm:node:1301526823")["eligible"]
    assert {p["category"] for p in pois} == {"history", "art", "nature", "food"}
    assert len([p for p in pois if p["cost_per_person"] > 0]) >= 30
    assert version == "lisbon-curated-2026-10-25-meals-v1"
    assert all(p["provenance"]["price_source"].startswith("https://") for p in pois)


def test_official_schedule_overrides_osm_and_preserves_original():
    bicos = "osm:node:12926040967"
    sunday, _ = enrich(mapped_pois(), date(2026, 10, 25), opening_window)
    monday, _ = enrich(mapped_pois(), date(2026, 10, 26), opening_window)
    assert not next(p for p in sunday if p["id"] == bicos)["eligible"]
    record = next(p for p in monday if p["id"] == bicos)
    assert record["eligible"]
    assert record["window"] == [600, 1080]
    assert record["cost_per_person"] == 0
    assert record["provenance"]["original_osm"]["opening_hours"] == "Tu-Su 10:00-19:00"


def test_expired_facts_are_not_used_as_osm_fallback():
    pois, _ = enrich(mapped_pois(), date(2026, 11, 23), opening_window)
    assert all(not p["eligible"] for p in pois)
    assert all("curation_outside_validity" in p["issues"] for p in pois)


def test_missing_osm_node_does_not_get_invented_coordinates():
    pois, _ = enrich([], date(2026, 10, 23), opening_window)
    assert len(pois) >= 6
    # Missing original mapped records without coordinates remain absent. New reviewed
    # records may carry an OSM ID if their coordinates have an explicit source.
    assert not {p["id"] for p in mapped_pois()} & {p["id"] for p in pois}
    assert all(p["provenance"]["coordinates_source"] for p in pois)


def test_curated_data_does_not_unlock_restricted_access():
    mapped = mapped_pois()
    mapped[0]["issues"].append("restricted_access")
    pois, _ = enrich(mapped, date(2026, 10, 23), opening_window)
    assert not next(p for p in pois if p["id"] == mapped[0]["id"])["eligible"]


def test_group_budget_uses_standard_adult_price_for_each_participant():
    pois, _ = enrich(mapped_pois(), date(2026, 10, 23), opening_window)
    p = next(p for p in pois if p["id"] == "osm:node:11047643391")
    place = Place(p["id"], p["category"], p["cost_per_person"], p["duration"], *p["window"])
    participants = [Participant("a", {"art": 1}), Participant("b", {"art": 1})]
    for budget, feasible in ((1999, False), (2000, True)):
        result = checked_plan(
            Request(participants, budget, 540, 1080, mandatory=frozenset({p["id"]})),
            [place],
            {("start", p["id"]): 10},
            "beam",
        )
        assert result.is_feasible is feasible
        assert result.total_cost == (2000 if feasible else 0)


def test_new_reviews_do_not_redate_old_facts_or_apply_before_their_review():
    pois, _ = enrich(mapped_pois(), date(2026, 10, 23), opening_window)
    old = next(p for p in pois if p["id"] == "osm:node:4419041593")
    food = next(p for p in pois if p["id"] == "curated:lisbon:nicolau-baixa")
    assert old["provenance"]["checked_on"] == "2026-10-23" and old["eligible"]
    assert food["provenance"]["checked_on"] == "2026-10-24"
    assert food["provenance"]["valid_until"] == "2026-11-22"
    assert not food["eligible"] and "curation_outside_validity" in food["issues"]


@pytest.mark.parametrize("algorithm", ["greedy", "beam", "cp-sat"])
def test_food_orders_charge_every_participant_and_respect_exact_budget(algorithm):
    pois, _ = enrich([], date(2026, 10, 27), opening_window)
    selected = [p for p in pois if p["category"] == "food"]
    assert {p["cost_per_person"] for p in selected} == {280, 1800, 2000}
    assert all(p["eligible"] and p["provenance"]["price_policy"] for p in selected)
    places = [
        Place(p["id"], p["category"], p["cost_per_person"], p["duration"], *p["window"])
        for p in selected
    ]
    ids = ["start", *[p.id for p in places]]
    travel = {(a, b): 10 for a in ids for b in ids if a != b}
    participants = [Participant("a", {"food": 1}), Participant("b", {"food": 1})]
    mandatory = frozenset(p.id for p in places)
    for budget, feasible in [(8160, True), (8159, False)]:
        result = checked_plan(
            Request(participants, budget, 540, 1080, mandatory=mandatory), places, travel, algorithm
        )
        assert result.is_feasible is feasible
        if feasible:
            assert result.total_cost == 8160 and result.validation.is_valid
            assert {v.place_id for v in result.visits} == mandatory


def test_food_and_garden_schedule_boundaries_and_sources():
    pois, _ = enrich([], date(2026, 10, 30), opening_window)
    records = {p["id"]: p for p in pois}
    assert records["curated:lisbon:nicolau-baixa"]["window"] == [510, 1350]
    assert records["curated:lisbon:amelia-campo-de-ourique"]["window"] == [540, 1320]
    assert records["curated:lisbon:gulbenkian-garden"]["window"] == [600, 960]
    assert records["curated:lisbon:estufa-fria"]["window"] == [600, 1020]
    monday, _ = enrich([], date(2026, 10, 26), opening_window)
    assert not next(p for p in monday if p["id"] == "curated:lisbon:estufa-fria")["eligible"]
    assert all(p["provenance"]["coordinates_source"].startswith("https://") for p in pois)


def test_food_requires_priced_order_and_entry_review_bounds():
    import json

    from app.core.config import ROOT
    from app.integrations.curated import Catalog, Entry

    body = json.loads((ROOT / "data/lisbon-curated.json").read_text("utf-8"))
    food = next(p for p in body["pois"] if p["category"] == "food")
    with pytest.raises(ValueError):
        Entry.model_validate({**food, "price_policy": None})
    with pytest.raises(ValueError):
        Entry.model_validate({**food, "cost_per_person": True})
    food["valid_until"] = "2026-12-31"
    with pytest.raises(ValueError):
        Catalog.model_validate(body)
