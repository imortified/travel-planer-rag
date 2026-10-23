"""Versioned static visa requirements and timing warnings, never an entry clearance."""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.core.config import ROOT
from app.schemas.visa import TravelProfile

RULES_PATH = ROOT / "data" / "visa-rules.json"


def reference_date():
    return datetime.now(ZoneInfo("Europe/Minsk")).date()


def rules_catalog(path=None):
    return json.loads(Path(path or RULES_PATH).read_text("utf-8"))


def assess_visas(
    destination, travel_date, end_date, participants, purpose="tourism", today=None, path=None
):
    today = today or reference_date()
    catalog = rules_catalog(path)
    first, last = travel_date, end_date or travel_date
    report = {
        "version": catalog["version"],
        "assessed_on": str(today),
        "checked_on": catalog["checked_on"],
        "review_by": catalog["review_by"],
        "destination_country": "PT" if destination == "Lisbon" else None,
        "purpose": purpose,
        "travel_date": str(first) if first else None,
        "end_date": str(last) if last else None,
        "status": "not_applicable",
        "participants": [],
        "limitations": catalog["limitations"],
    }
    if destination == "Demo City":
        return report
    checked, review = (
        date.fromisoformat(catalog["checked_on"]),
        date.fromisoformat(catalog["review_by"]),
    )
    rules = {rule["citizenship"]: rule for rule in catalog["rules"]}
    for person in participants:
        profile = TravelProfile.model_validate(person.get("travel_profile") or {})
        rule = rules.get(profile.citizenship)
        row = {
            "user_id": person.get("user_id"),
            "name": person["name"],
            "profile": profile.model_dump(),
            "citizenship_name": rule["name"] if rule else profile.citizenship,
            "requirement": "unknown",
            "status": "needs_review",
            "reasons": [],
            "summary": rule["summary"]
            if rule
            else "Для этого гражданства нет правила в ограниченном справочнике.",
            "sources": rule["sources"] if rule else [],
            "timing": None,
        }
        reasons = row["reasons"]
        if destination != catalog["destination"]:
            reasons.append("unsupported_destination")
        if purpose != catalog["purpose"]:
            reasons.append("unsupported_purpose")
        if not first or not last:
            reasons.append("missing_dates")
        elif first < today or not 0 <= (last - first).days <= 13:
            reasons.append("past_or_invalid_dates")
        if not checked <= today <= review or (first and (first < checked or last > review)):
            reasons.append("rules_outside_review_window")
        if not rule:
            reasons.append(
                "missing_citizenship" if not profile.citizenship else "unsupported_citizenship"
            )
        elif not rule["eu_citizen"]:
            if profile.passport_kind != "ordinary":
                reasons.append("ordinary_passport_not_confirmed")
            if profile.special_entry_basis is not False:
                reasons.append("special_basis_needs_review")
            if rule["biometric_exemption"] and profile.biometric is None:
                reasons.append("biometric_passport_not_confirmed")
        if not reasons:
            requirement = rule["requirement"]
            if rule["biometric_exemption"] and profile.biometric is False:
                requirement = "required"
                row["summary"] = (
                    "Биометрическое условие освобождения не выполнено; "
                    "требуется виза при отсутствии специальных оснований."
                )
            row["requirement"] = requirement
            row["status"] = "requirements_listed"
            if requirement == "required":
                if profile.visa_status == "held":
                    reasons.append("held_visa_not_verified")
                else:
                    row["status"] = "action_required"
                    lead = (first - today).days
                    row["timing"] = {
                        "days_until_trip": lead,
                        "application_notice_days": catalog["application_notice_days"],
                        "recommended_apply_by": str(
                            first - timedelta(days=catalog["application_notice_days"])
                        ),
                        "processing_days": rule["processing_days"],
                        "extended_processing_days": rule["extended_processing_days"],
                        "source_url": catalog["application_source"],
                    }
                    reasons.append(
                        "visa_not_held"
                        if profile.visa_status == "not_held"
                        else "visa_status_unknown"
                    )
                    if lead < catalog["application_notice_days"]:
                        reasons.append("short_application_notice")
                    if rule["processing_days"] and lead < rule["processing_days"]:
                        reasons.append("short_processing_time")
                    elif (
                        rule["extended_processing_days"] and lead < rule["extended_processing_days"]
                    ):
                        reasons.append("extended_processing_risk")
        report["participants"].append(row)
    statuses = {p["status"] for p in report["participants"]}
    report["status"] = (
        "needs_review"
        if "needs_review" in statuses
        else "action_required"
        if "action_required" in statuses
        else "requirements_listed"
    )
    return report
