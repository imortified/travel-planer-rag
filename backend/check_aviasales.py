"""One read-only Aviasales Data API probe. No booking or group-price calculation."""

import argparse
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"


def probe(client, token, origin, destination, departure, return_date=None, market="ru"):
    params = {
        "origin": origin,
        "destination": destination,
        "departure_at": str(departure),
        "currency": "eur",
        "market": market,
        "one_way": "false" if return_date else "true",
        "limit": 5,
        "page": 1,
        "sorting": "price",
    }
    if return_date:
        params["return_at"] = str(return_date)
    result = {
        "provider": "aviasales_data_api",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "query": params,
        "price_kind": "provider_cached_observation",
        "group_total_confirmed": False,
        "booking_status": "not_booked",
    }
    try:
        response = client.get(URL, params=params, headers={"X-Access-Token": token})
        result["http_status"] = response.status_code
        if response.status_code != 200:
            return {**result, "status": "http_error"}
        body = response.json()
        if not isinstance(body, dict) or body.get("success") is not True:
            return {**result, "status": "provider_error"}
        rows = body.get("data")
        if rows == {}:
            rows = []
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            return {**result, "status": "invalid_response"}
        # Whitelist observations. Never print raw response/errors, headers or token.
        fields = {
            "origin",
            "destination",
            "origin_airport",
            "destination_airport",
            "price",
            "departure_at",
            "return_at",
            "airline",
            "flight_number",
            "transfers",
            "return_transfers",
            "duration",
            "duration_to",
            "duration_back",
        }
        observations = [
            {
                key: value.replace(token, "[REDACTED]") if isinstance(value, str) else value
                for key, value in row.items()
                if key in fields and type(value) in (str, int, float, bool, type(None))
            }
            for row in rows
        ]
        currency = body.get("currency")
        if currency != "eur":
            return {**result, "status": "unexpected_currency"}
        return {
            **result,
            "status": "observations" if observations else "empty",
            "currency": currency,
            "count": len(observations),
            "observations": observations,
        }
    except httpx.HTTPError:
        return {**result, "status": "network_error"}
    except ValueError:
        return {**result, "status": "invalid_response"}


def airport(value):
    value = value.upper()
    if not re.fullmatch(r"[A-Z]{3}", value):
        raise argparse.ArgumentTypeError("Use a three-letter IATA city/airport code")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", type=airport, required=True)
    parser.add_argument("--destination", type=airport, required=True)
    parser.add_argument("--departure", type=date.fromisoformat, required=True)
    parser.add_argument("--return-date", type=date.fromisoformat)
    parser.add_argument("--market", choices=("ru", "us"), default="ru")
    args = parser.parse_args()
    if args.departure < date.today() or (args.return_date and args.return_date <= args.departure):
        parser.error("Use a current/future departure and a later return date")
    token = os.getenv("TRAVELPAYOUTS_API_TOKEN") or dotenv_values(ROOT / ".env").get(
        "TRAVELPAYOUTS_API_TOKEN"
    )
    if not token or not token.strip():
        parser.error("Set TRAVELPAYOUTS_API_TOKEN in the ignored root .env")
    with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
        result = probe(
            client,
            token.strip(),
            args.origin,
            args.destination,
            args.departure,
            args.return_date,
            args.market,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"observations", "empty"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
