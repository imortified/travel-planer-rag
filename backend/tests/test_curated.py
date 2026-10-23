from datetime import date

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
    pois, version = enrich(mapped_pois(), date(2026, 10, 23), opening_window)
    assert len(pois) == 7
    assert all(p["eligible"] for p in pois)
    assert {p["category"] for p in pois} == {"history", "art", "nature"}
    assert len([p for p in pois if p["cost_per_person"] > 0]) == 6
    assert version == "lisbon-curated-2026-10-23"
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
    assert [p["id"] for p in pois] == ["curated:lisbon:ajuda-botanical-garden"]
    assert pois[0]["provenance"]["coordinates_source"]


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
