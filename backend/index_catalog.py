"""Idempotently index public descriptions for a selected catalog period in Qdrant."""

import argparse
import json
from datetime import date, timedelta

from app.core.destinations import CITIES
from app.core.logging import configure_logging
from app.integrations.city import CityProvider
from app.nlp.retrieval import Retrieval


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", choices=list(CITIES), default="Lisbon")
    parser.add_argument("--travel-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat)
    args = parser.parse_args()
    configure_logging(file_tag="catalog-index")
    last = args.end_date or args.travel_date
    if not 0 <= (last - args.travel_date).days <= 13:
        parser.error("Period must contain 1-14 days")
    pois = {}
    provider = CityProvider(destination=args.city)
    for offset in range((last - args.travel_date).days + 1):
        catalog = provider.catalog(args.travel_date + timedelta(days=offset))
        pois.update({p["id"]: p for p in catalog["pois"] if p["eligible"]})
    result = Retrieval(destination=args.city).index([pois[key] for key in sorted(pois)])
    print(json.dumps({"ensured_documents": result}))


if __name__ == "__main__":
    main()
