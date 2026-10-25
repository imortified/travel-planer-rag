"""Small, explicit live pilot. Missing credentials and unsupported cases are never successes."""

import argparse
import json
import math
import os
import re
import time
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx
from dotenv import dotenv_values

from app.core.config import ROOT


def cents(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or not 0 <= amount <= 10_000_000:
            return None
        return int((amount * 100).quantize(Decimal("1")))
    except (InvalidOperation, ValueError, TypeError):
        return None


def redacted(value, secret):
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if key.lower() in {"api_key", "apikey", "access_token"}
                else redacted(item, secret)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redacted(item, secret) for item in value]
    if isinstance(value, str) and secret:
        return value.replace(secret, "[REDACTED]")
    return value


def normalized(value):
    if not isinstance(value, str):
        return ""
    text = "".join(
        c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c)
    ).casefold()
    return " ".join(re.findall(r"[a-z0-9]+", text))


def same_property(hotel, property_):
    a, b = (
        set(normalized(hotel["name"]).split()),
        set(normalized(property_.get("name", "")).split()),
    )
    geo = property_.get("gps_coordinates") or {}
    try:
        lat, lon = float(geo["latitude"]), float(geo["longitude"])
        if not math.isfinite(lat) or not math.isfinite(lon):
            return False
        distance = math.hypot(
            (lat - hotel["latitude"]) * 111_320,
            (lon - hotel["longitude"]) * 111_320 * math.cos(math.radians(lat)),
        )
        return distance <= 150 and len(a & b) / max(len(a), len(b), 1) >= 0.6
    except (KeyError, ValueError, TypeError):
        return False


def parameters(provider, hotel, party, checkin, checkout):
    if provider == "xotelo":
        params = {
            "hotel_key": hotel["xotelo_key"],
            "chk_in": str(checkin),
            "chk_out": str(checkout),
            "currency": "EUR",
            "rooms": party["rooms"],
            "adults": party["adults"],
        }
        if party["child_ages"]:
            params["age_of_children"] = ",".join(
                str(age if age > 1 else 0) for age in party["child_ages"]
            )
        return params
    if party["rooms"] != 1:
        raise ValueError("unsupported_room_count")
    params = {
        "engine": "google_hotels",
        "q": hotel["name"] + " Lisbon Portugal",
        "check_in_date": str(checkin),
        "check_out_date": str(checkout),
        "currency": "EUR",
        "adults": party["adults"],
        "children": len(party["child_ages"]),
        "gl": "pt",
        "hl": "en",
    }
    if party["child_ages"]:
        params["children_ages"] = ",".join(str(max(1, age)) for age in party["child_ages"])
    return params


def xotelo_result(body, case):
    result = body.get("result") or {}
    if body.get("error"):
        return {"status": "provider_error"}
    if not isinstance(result, dict):
        return {"status": "invalid_response"}
    if any(result.get(key) != case[key] for key in ("chk_in", "chk_out", "currency")):
        return {"status": "query_mismatch"}
    rates = result.get("rates")
    if not isinstance(rates, list):
        return {"status": "invalid_response"}
    quotes = []
    for row in rates:
        if not isinstance(row, dict):
            continue
        base, tax = cents(row.get("rate")), cents(row.get("tax"))
        if base is None:
            continue
        quotes.append(
            {
                "source": row.get("name"),
                "code": row.get("code"),
                "reported_rate_cents": base,
                "reported_tax_cents": tax,
            }
        )
    return {
        "status": "quotes" if quotes else "empty",
        "quotes": quotes,
        "source_timestamp": body.get("timestamp"),
        "occupancy_echo_confirmed": False,
        "unit_in_documentation": "per_room_per_night",
        "verified_stay_total_cents": None,
        "limitations": [
            "Response does not echo adults, child ages or rooms",
            "Tax null means unknown; other charges and rate conditions unverified",
            "Nightly/room price semantics require verification before budgeting",
        ],
    }


def serp_result(body, hotel, case):
    if body.get("error"):
        return {"status": "provider_error"}
    echoed = body.get("search_parameters") or {}
    if not isinstance(echoed, dict):
        return {"status": "invalid_response"}
    if any(
        str(echoed.get(key)) != str(case[key])
        for key in ("check_in_date", "check_out_date", "currency", "adults", "children")
    ):
        return {"status": "query_mismatch"}
    if case.get("children_ages") and str(echoed.get("children_ages")) != case["children_ages"]:
        return {"status": "query_mismatch"}
    properties = body.get("properties")
    if not isinstance(properties, list):
        properties = [body] if body.get("name") else []
    matches = [p for p in properties if isinstance(p, dict) and same_property(hotel, p)]
    if len(matches) != 1:
        return {
            "status": "ambiguous_identity" if matches else "property_not_found",
            "match_count": len(matches),
        }
    chosen = matches[0]
    total, nightly = chosen.get("total_rate") or {}, chosen.get("rate_per_night") or {}
    if not isinstance(total, dict) or not isinstance(nightly, dict):
        return {"status": "invalid_response"}
    quote = {
        "property_name": chosen.get("name"),
        "total_before_taxes_cents": cents(total.get("extracted_before_taxes_fees")),
        "total_after_taxes_cents": cents(total.get("extracted_after_taxes_fees")),
        "displayed_total_cents": cents(total.get("extracted_lowest")),
        "nightly_before_taxes_cents": cents(nightly.get("extracted_before_taxes_fees")),
        "nightly_after_taxes_cents": cents(nightly.get("extracted_after_taxes_fees")),
    }
    has_price = any(value is not None for key, value in quote.items() if key.endswith("_cents"))
    return {
        "status": "quotes" if has_price else "empty",
        "quotes": [quote] if has_price else [],
        "occupancy_echo_confirmed": True,
        "verified_stay_total_cents": None,
        "limitations": [
            "Search prices are not a booking confirmation",
            "Room type, meal/cancellation policy and extra charges are not independently matched",
            "Displayed total is not assumed to include taxes when explicit fields are missing",
        ],
    }


def request_json(client, url, params):
    started = time.perf_counter()
    try:
        response = client.get(url, params=params)
        elapsed = round((time.perf_counter() - started) * 1000)
        if response.status_code != 200:
            return {
                "status": "http_error",
                "http_status": response.status_code,
                "elapsed_ms": elapsed,
            }, None
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("Not a JSON object")
        return {"http_status": 200, "elapsed_ms": elapsed}, body
    except (httpx.HTTPError, ValueError):
        # Exception messages may contain the complete URL and API key.
        return {
            "status": "transport_or_invalid_json",
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }, None


def free_account(client, secret, required):
    if not secret:
        return {"status": "missing_api_key"}
    meta, body = request_json(client, "https://serpapi.com/account.json", {"api_key": secret})
    if body is None or body.get("error"):
        return {"status": "account_check_failed", "account_http_status": meta.get("http_status")}
    free = (
        str(body.get("plan_id", "")).lower() == "free"
        or str(body.get("plan_name", "")).lower() == "free"
    )
    remaining = body.get("total_searches_left")
    if not free:
        return {"status": "free_plan_not_confirmed"}
    if type(remaining) is not int or remaining < required:
        return {"status": "insufficient_verified_free_quota"}
    return {"status": "ready", "free_plan_confirmed": True, "remaining_searches": remaining}


def run(cases, output, checkin=None, secret="", pause=1.1):
    checkin = checkin or date.fromisoformat(cases["checkin"])
    if checkin < datetime.now(timezone.utc).date():
        raise ValueError("Choose a future check-in date")
    planned = (
        len(cases["hotels"]) * len(cases["nights"]) * sum(p["rooms"] == 1 for p in cases["parties"])
    )
    if planned > 8:
        raise ValueError("Pilot permits at most 8 SerpApi search requests")
    if len(cases["hotels"]) * len(cases["nights"]) * len(cases["parties"]) > 12:
        raise ValueError("Pilot permits at most 12 Xotelo requests")
    if cases["city"] != "Lisbon" or cases["currency"] != "EUR":
        raise ValueError("This pilot matches Lisbon properties and EUR only")
    report = {
        "version": cases["version"],
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "currency": "EUR",
        "requests": [],
        "application_changed": False,
        "booking_performed": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        output.write_text(
            json.dumps(redacted(report, secret), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    with httpx.Client(timeout=35, follow_redirects=False) as client:
        account = free_account(client, secret, planned)
        report["serpapi_access"] = account
        print("SerpApi:", account["status"], flush=True)
        serp_block = None
        for hotel in cases["hotels"]:
            for nights in cases["nights"]:
                checkout = checkin + timedelta(days=nights)
                for party in cases["parties"]:
                    for provider in ["xotelo", "serpapi"]:
                        row = {
                            "provider": provider,
                            "hotel": hotel["name"],
                            "party_id": party["id"],
                            "adults": party["adults"],
                            "child_ages": party["child_ages"],
                            "rooms": party["rooms"],
                            "checkin": str(checkin),
                            "checkout": str(checkout),
                            "nights": nights,
                        }
                        if provider == "serpapi" and party["rooms"] != 1:
                            row.update(status="unsupported_room_count", request_sent=False)
                        elif provider == "serpapi" and (account["status"] != "ready" or serp_block):
                            row.update(status=serp_block or account["status"], request_sent=False)
                        else:
                            params = parameters(provider, hotel, party, checkin, checkout)
                            row["parameters"] = params.copy()
                            if provider == "serpapi":
                                params["api_key"] = secret
                            url = (
                                "https://data.xotelo.com/api/rates"
                                if provider == "xotelo"
                                else "https://serpapi.com/search.json"
                            )
                            meta, body = request_json(client, url, params)
                            row.update(meta, request_sent=True)
                            if body is not None:
                                row.update(
                                    xotelo_result(body, params)
                                    if provider == "xotelo"
                                    else serp_result(body, hotel, params)
                                )
                            if provider == "serpapi" and row["status"] in {
                                "http_error",
                                "provider_error",
                            }:
                                serp_block = "stopped_after_provider_error"
                            time.sleep(max(1.0, pause))
                        report["requests"].append(row)
                        save()
                        print(
                            provider, hotel["name"], party["id"], nights, row["status"], flush=True
                        )
    report["summary"] = {
        provider: dict(
            Counter(row["status"] for row in report["requests"] if row["provider"] == provider)
        )
        for provider in ["xotelo", "serpapi"]
    }
    report["serpapi_searches_sent"] = sum(
        r["provider"] == "serpapi" and r["request_sent"] for r in report["requests"]
    )
    report["limitations"] = [
        "Two hotels in Lisbon are a pilot, not representative quality evidence",
        "Lowest rates may represent different rooms/conditions; ranking by price is not justified",
        "Missing credentials or unsupported cases are skips, not failures or empty results",
        "No verified complete stay total; do not automatically replace the manual lodging estimate",
    ]
    save()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "data/hotel-provider-cases.json")
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".local/evaluations/hotel-provider-pilot.json"
    )
    parser.add_argument("--checkin", type=date.fromisoformat)
    args = parser.parse_args()
    local = dotenv_values(ROOT / ".env")
    secret = (
        os.getenv("SERPAPI_API_KEY")
        or local.get("SERPAPI_API_KEY")
        or os.getenv("SERPAPI_KEY")
        or local.get("SERPAPI_KEY")
        or ""
    )
    cases = json.loads(args.cases.read_text("utf-8"))
    result = run(cases, args.output, args.checkin, secret)
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
