"""Opt-in sequential localhost MVP verification; persists explicitly named demo trips."""

import argparse
import json
import math
import platform
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from time import perf_counter
from urllib.parse import urlsplit

import httpx

from app.core.config import ROOT


def latency(rows):
    values = sorted(row["elapsed_seconds"] for row in rows)
    return {
        "attempts": len(rows),
        "successful": sum(row["ok"] for row in rows),
        "errors": sum(not row["ok"] for row in rows),
        "p50_seconds": median(values) if values else None,
        "p95_seconds": values[math.ceil(len(values) * 0.95) - 1] if values else None,
        "max_seconds": max(values) if values else None,
    }


def measured(client, method, path, body=None, expected=200):
    started = perf_counter()
    row = {"method": method, "path": path, "ok": False}
    data = None
    try:
        response = client.request(method, path, json=body)
        row.update(http_status=response.status_code, response_bytes=len(response.content))
        data = response.json()
        row["ok"] = response.status_code == expected
    except (httpx.HTTPError, ValueError) as exc:
        row["error_kind"] = type(exc).__name__
    row["elapsed_seconds"] = perf_counter() - started
    return row, data


def require(client, method, path, body=None, expected=200):
    row, data = measured(client, method, path, body, expected)
    if not row["ok"]:
        raise RuntimeError(
            f"Setup request failed: {method} {path}; status={row.get('http_status')}"
        )
    return data


def make_group(client, count):
    group = require(
        client,
        "POST",
        "/api/v1/groups",
        {"name": f"MVP verification: {count} synthetic travellers"},
        201,
    )
    for i in range(count):
        user = require(client, "POST", "/api/v1/users", {"name": f"Demo member {i + 1}"}, 201)
        base = f"/api/v1/groups/{group['id']}/members"
        require(client, "POST", base, {"user_id": user["id"]}, 201)
        interests = (
            {"history": 1, "nature": 0.4, "art": 0.6, "food": 0.3}
            if i % 2 == 0
            else {"history": 0.3, "nature": 1, "art": 0.4, "food": 0.6}
        )
        require(client, "PUT", f"{base}/{user['id']}/preferences", {"interests": interests})
        require(client, "PUT", f"{base}/{user['id']}/age-profile", {"age_on_start": 30 + i})
        require(
            client,
            "PUT",
            f"{base}/{user['id']}/travel-profile",
            {
                "citizenship": "DE",
                "passport_kind": "ordinary",
                "special_entry_basis": False,
            },
        )
    return group["id"]


def trip_payload(start, days, budget=200000):
    return {
        "destination": "Lisbon",
        "travel_date": str(start),
        "end_date": str(start + timedelta(days=days - 1)),
        "budget": budget,
        "pace": "balanced",
        "budget_settings": {
            "lodging": 30000,
            "transport": 6000,
            "flights": 0,
            "transport_scope": "ground_only",
            "reserve": 20000,
        },
    }


def route_metadata(data):
    options = data.get("options", []) if isinstance(data, dict) else []
    if not options:
        return {"feasible": False, "options": 0, "osrm_cache_reads": [], "catalog_versions": []}
    result = options[0]["result"]
    days = result.get("days") or [result]
    contexts = [day.get("data_context") or {} for day in days]
    return {
        "feasible": all(
            o["result"]["is_feasible"] and o["result"]["validation"]["is_valid"] for o in options
        ),
        "options": len(options),
        "route_id": options[0]["id"],
        "active_days": (result.get("planning_summary") or {}).get("active_days"),
        "stops": len(result["visits"]),
        "stops_cost": result["total_cost"],
        "known_total": result["budget_breakdown"]["known_total"],
        "osrm_cache_reads": [(c.get("cache") or {}).get("osrm", {}).get("read") for c in contexts],
        "catalog_versions": sorted(
            {c["catalog_snapshot"]["version_id"] for c in contexts if c.get("catalog_snapshot")}
        ),
    }


def run(client, start, samples, read_samples, report):
    gid = make_group(client, 6)
    report["benchmark_group_id"] = gid
    cases = report.setdefault("planning", [])
    for days in (5, 7):
        payload = trip_payload(start, days)
        trip = require(client, "POST", f"/api/v1/groups/{gid}/trips", payload, 201)
        path = f"/api/v1/trips/{trip['id']}/route-sets?algorithm=beam&beam_width=32"
        warm, data = measured(client, "POST", path, expected=201)
        warm.update(route_metadata(data))
        if not warm["ok"] or not warm["feasible"]:
            raise RuntimeError(f"Warmup failed for {days}-day case")
        print(f"Warmup {days} days: {warm['elapsed_seconds']:.3f}s", flush=True)
        rows = []
        for i in range(samples):
            row, data = measured(client, "POST", path, expected=201)
            meta = route_metadata(data)
            row.update(meta)
            row["ok"] = row["ok"] and meta["feasible"]
            rows.append(row)
            print(
                f"{days} days {i + 1}/{samples}: {row['elapsed_seconds']:.3f}s, ok={row['ok']}",
                flush=True,
            )
        summary = latency(rows)
        warm_only = all(
            row["osrm_cache_reads"] and all(s == "hit" for s in row["osrm_cache_reads"])
            for row in rows
        )
        same_catalog = all(row["catalog_versions"] == warm["catalog_versions"] for row in rows)
        cases.append(
            {
                "days": days,
                "participants": 6,
                "group_id": gid,
                "trip_id": trip["id"],
                "payload": payload,
                "warmup": warm,
                "samples": rows,
                "latency": summary,
                "all_measured_osrm_hits": warm_only,
                "same_catalog": same_catalog,
                "nfr1_local_sequential_pass": samples >= 20
                and summary["errors"] == 0
                and summary["p95_seconds"] <= 30
                and warm_only
                and same_catalog,
            }
        )
    route_id = cases[-1]["warmup"]["route_id"]
    for _ in range(5):
        require(client, "GET", f"/api/v1/routes/{route_id}")
    reads = [measured(client, "GET", f"/api/v1/routes/{route_id}")[0] for _ in range(read_samples)]
    read_summary = latency(reads)
    # Negative request must fail before persistence; the old date never becomes a new trip.
    rejected, _ = measured(
        client,
        "POST",
        f"/api/v1/groups/{gid}/trips",
        {"destination": "Lisbon", "travel_date": "2000-01-01", "budget": 10000},
        422,
    )
    solo_id = make_group(client, 1)
    solo_trip = require(
        client, "POST", f"/api/v1/groups/{solo_id}/trips", trip_payload(start, 3), 201
    )
    solo_time, solo = measured(
        client, "POST", f"/api/v1/trips/{solo_trip['id']}/route-sets?algorithm=beam", expected=201
    )
    solo_time.update(route_metadata(solo))
    solo_time.update(group_id=solo_id, trip_id=solo_trip["id"])
    # Choose a known paid candidate for an intentionally impossible zero-budget request.
    catalog = require(client, "GET", f"/api/v1/destinations/Lisbon/pois?travel_date={start}")
    paid = next(
        p["id"] for p in catalog["pois"] if p["eligible"] and (p.get("cost_per_person") or 0) > 0
    )
    impossible = require(
        client,
        "POST",
        f"/api/v1/groups/{gid}/trips",
        {"destination": "Lisbon", "travel_date": str(start), "budget": 0, "mandatory": [paid]},
        201,
    )
    negative_time, negative = measured(
        client, "POST", f"/api/v1/trips/{impossible['id']}/route-sets?algorithm=beam", expected=201
    )
    negative_time["reported_infeasible"] = bool(
        negative
        and not negative.get("options")
        and negative.get("diagnostic")
        and not negative["diagnostic"]["is_feasible"]
        and negative["diagnostic"]["reasons"]
    )
    return {
        "planning": cases,
        "saved_route_reads": {
            "route_id": route_id,
            "warmups": 5,
            "samples": reads,
            "latency": read_summary,
            "nfr2_local_sequential_pass": read_samples >= 100
            and read_summary["errors"] == 0
            and read_summary["p95_seconds"] <= 0.3,
        },
        "past_date_rejected": rejected,
        "solo_demo": solo_time,
        "impossible_mandatory_demo": negative_time,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--read-samples", type=int, default=100)
    parser.add_argument("--output", type=Path, default=ROOT / ".local/evaluations/mvp-http.json")
    args = parser.parse_args()
    url = urlsplit(args.base_url)
    if (
        url.scheme != "http"
        or url.hostname not in {"127.0.0.1", "localhost", "::1"}
        or url.username
        or url.password
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
    ):
        parser.error("This verification runner accepts only a local HTTP API origin")
    if not 2 <= args.samples <= 50 or not 10 <= args.read_samples <= 500:
        parser.error("Use 2–50 planning samples and 10–500 read samples")
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
        "configuration": {
            "base_url": args.base_url,
            "start": str(args.start),
            "samples": args.samples,
            "read_samples": args.read_samples,
            "concurrency": 1,
            "algorithm": "beam",
            "beam_width": 32,
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "git_revision": subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
            ).stdout.strip(),
        },
        "latency_scope": (
            "Local HTTP through JSON decoding: POI/routing, alternatives, validation, DB commit. "
            "Excludes trip setup, LLM parsing and explanation."
        ),
        "limitations": [
            "Sequential local measurement, not a concurrent load test or universal SLA",
            "Warmups separate; failed requests remain in all measured latency statistics",
            "Persists synthetic demos; never changes or deletes existing user data",
        ],
    }
    try:
        with httpx.Client(
            base_url=args.base_url.rstrip("/"), timeout=120, trust_env=False
        ) as client:
            require(client, "GET", "/health")
            report.update(run(client, args.start, args.samples, args.read_samples, report))
        report["completed"] = True
    except Exception as exc:
        report["error_kind"] = type(exc).__name__
        print(f"Verification stopped: {type(exc).__name__}", flush=True)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Report saved: {args.output}", flush=True)
    return 0 if report["completed"] else 1
