from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.nlp.explanations import explain
from app.schemas.assistant import ExplanationClaim, ExplanationDraft
from app.visa import assess_visas, rules_catalog

TODAY = date(2026, 10, 23)


def assessment(country="BY", offset=20, **profile):
    return assess_visas(
        "Lisbon",
        TODAY + timedelta(days=offset),
        None,
        [
            {
                "name": "Анна",
                "travel_profile": {
                    "citizenship": country,
                    "passport_kind": "ordinary",
                    "special_entry_basis": False,
                    "biometric": True,
                    **profile,
                },
            }
        ],
        today=TODAY,
    )


@pytest.mark.parametrize(
    "country,requirement",
    [
        ("BY", "required"),
        ("RU", "required"),
        ("UA", "not_required"),
        ("GE", "not_required"),
        ("DE", "not_required"),
        ("GB", "not_required"),
        ("US", "not_required"),
    ],
)
def test_seven_citizenships(country, requirement):
    report = assessment(country)
    row = report["participants"][0]
    assert row["requirement"] == requirement
    assert row["sources"] and all(s["url"].startswith("https://") for s in row["sources"])
    assert report["checked_on"] == "2026-10-23"


@pytest.mark.parametrize("country", ["UA", "GE"])
def test_biometric_condition(country):
    assert assessment(country, biometric=False)["participants"][0]["requirement"] == "required"
    row = assessment(country, biometric=None)["participants"][0]
    assert row["requirement"] == "unknown"
    assert "biometric_passport_not_confirmed" in row["reasons"]


@pytest.mark.parametrize(
    "profile",
    [
        {"passport_kind": "unknown"},
        {"passport_kind": "special"},
        {"special_entry_basis": None},
        {"special_entry_basis": True},
    ],
)
def test_special_cases_need_review(profile):
    assert assessment("US", **profile)["status"] == "needs_review"


def test_eu_citizenship_uses_own_rule():
    assert assessment("DE", passport_kind="unknown", special_entry_basis=None)["status"] == (
        "requirements_listed"
    )


@pytest.mark.parametrize("country", [None, "ZZ"])
def test_missing_or_unsupported_citizenship(country):
    assert assessment(country)["participants"][0]["requirement"] == "unknown"


def test_scope_dates_and_review_window_fail_closed():
    for kwargs, reason in [
        ({"travel_date": None}, "missing_dates"),
        ({"travel_date": TODAY - timedelta(days=1)}, "past_or_invalid_dates"),
        ({"end_date": TODAY + timedelta(days=40)}, "past_or_invalid_dates"),
        ({"travel_date": date(2026, 11, 23)}, "rules_outside_review_window"),
        ({"today": date(2026, 11, 23)}, "rules_outside_review_window"),
        ({"today": date(2026, 10, 22)}, "rules_outside_review_window"),
        ({"purpose": "other"}, "unsupported_purpose"),
        ({"destination": "Paris"}, "unsupported_destination"),
    ]:
        data = dict(
            destination="Lisbon",
            travel_date=TODAY + timedelta(days=2),
            end_date=None,
            today=TODAY,
            participants=[{"name": "Анна", "travel_profile": {"citizenship": "DE"}}],
        )
        row = assess_visas(**(data | kwargs))["participants"][0]
        assert row["requirement"] == "unknown"
        assert reason in row["reasons"]
    assert assess_visas("Demo City", None, None, [], today=TODAY)["status"] == "not_applicable"


def test_timing_is_warning_and_held_visa_is_not_verified():
    by = assessment("BY", offset=9)["participants"][0]
    ru = assessment("RU", offset=14)["participants"][0]
    assert by["timing"]["processing_days"] == 10
    assert by["timing"]["extended_processing_days"] == 30
    assert ru["timing"]["processing_days"] == 15
    assert ru["timing"]["extended_processing_days"] == 45
    assert "short_processing_time" in by["reasons"]
    assert "short_application_notice" in ru["reasons"]
    assert (
        "short_application_notice" not in assessment("RU", offset=15)["participants"][0]["reasons"]
    )
    assert "extended_processing_risk" in assessment("BY")["participants"][0]["reasons"]
    held = assessment("BY", visa_status="held")["participants"][0]
    assert held["requirement"] == "required" and held["timing"] is None
    assert held["reasons"] == ["held_visa_not_verified"]


def test_preview_has_no_database_dependency_and_rejects_client_clock(monkeypatch):
    monkeypatch.setattr("app.visa.reference_date", lambda: TODAY)
    with TestClient(app) as client:
        catalog = client.get("/api/v1/visa-rules").json()
        assert len(catalog["rules"]) == 7
        payload = {
            "destination": "Lisbon",
            "travel_date": "2026-11-01",
            "travellers": [{"name": "Анна", "profile": {"citizenship": " de "}}],
        }
        result = client.post("/api/v1/visa-check", json=payload)
        assert result.status_code == 200
        assert result.json()["participants"][0]["requirement"] == "not_required"
        for patch in [
            {"as_of": "2026-10-23"},
            {"end_date": "2026-12-01"},
            {"travellers": []},
            {"travellers": [{"name": "  "}]},
            {"travellers": [{"name": "A", "profile": {"citizenship": "Germany"}}]},
            {"travellers": [{"name": "A", "profile": {"biometric": "true"}}]},
            {"travellers": [{"name": "A", "profile": {"passport_number": "123"}}]},
        ]:
            assert client.post("/api/v1/visa-check", json=payload | patch).status_code == 422


@pytest.mark.parametrize("fault", [None, "omitted", "invented"])
def test_explanation_requires_saved_visa_fact(fault):
    route = SimpleNamespace(
        id=1,
        route_set_id=None,
        comparison=None,
        visa_report=assessment(),
        result={"visits": [], "total_cost": 0},
    )
    trip = SimpleNamespace(
        participants_snapshot=[{"name": "Анна", "user_id": 1}],
        budget=100,
        travel_date=TODAY,
        end_date=None,
        mandatory=[],
        excluded=[],
    )

    class Model:
        def structured(self, system, data, schema, verify=None):
            keys = ["summary", "budget", "limitations", "visa"]
            if fault == "omitted":
                keys.remove("visa")
            claims = [ExplanationClaim(fact_id=k, sentence=data["facts"][k]) for k in keys]
            if fault == "invented":
                claims[-1].sentence = "Всем разрешён въезд."
            return ExplanationDraft(claims=claims), "test-model"

    output = explain(route, trip, llm=Model())
    assert output.mode == ("llm" if fault is None else "template")
    assert "visa" in output.selected_ids
    assert "право въезда не подтверждены" in output.facts["visa"]
    assert "Всем разрешён въезд." not in output.paragraphs
    assert rules_catalog()["version"] == route.visa_report["version"]
