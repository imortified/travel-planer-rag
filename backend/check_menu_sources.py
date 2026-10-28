"""Fetch official sources already configured in the curated catalog; stage observed menu prices."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.catalog.source_prices import menu_prices
from app.core.destinations import CITIES, city_for
from app.integrations.curated import Catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", choices=list(CITIES), default="Lisbon")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    catalog = Catalog.model_validate_json(city_for(args.city).curated_path.read_text("utf-8"))
    checked = datetime.now(timezone.utc)
    results = []
    with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
        for url in sorted({str(p.price_source) for p in catalog.pois if p.category == "food"}):
            row = {"source_url": url, "checked_at": checked.isoformat(), "candidates": []}
            try:
                with client.stream("GET", url) as response:
                    row["http_status"] = response.status_code
                    response.raise_for_status()
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 2_000_000:
                            raise ValueError("Source exceeds size limit")
                        chunks.append(chunk)
                body = b"".join(chunks)
                row["sha256"] = hashlib.sha256(body).hexdigest()
                row["candidates"] = menu_prices(body.decode("utf-8", errors="replace"), url)
                row["status"] = "observed" if row["candidates"] else "no_structured_menu_price"
            except (httpx.HTTPError, ValueError):
                row["status"] = "source_unavailable"
            results.append(row)
            print(json.dumps({"source": url, "status": row["status"],
                              "observations": len(row["candidates"])}), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"city": args.city, "sources": results,
        "publication": "staged_only; match the exact order and hours before publication"},
        ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
