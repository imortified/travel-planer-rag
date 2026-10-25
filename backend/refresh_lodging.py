"""Publish a validated public lodging extract atomically; never contacts booking services."""

import argparse
import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.catalog.lodging import publish_lodgings
from app.core.logging import configure_logging, emit
from app.db.session import engine
from app.schemas.lodging import LodgingCatalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract", type=Path, required=True)
    args = parser.parse_args()
    configure_logging(file_tag="catalog-refresh")
    data = json.loads(args.extract.read_text("utf-8"))
    catalogs = [LodgingCatalog.model_validate(row) for row in data["catalogs"]]
    if not catalogs or len({c.destination for c in catalogs}) != len(catalogs):
        raise ValueError("Extract must contain distinct, nonempty city catalogs")
    report = []
    with Session(engine) as session, session.begin():
        for catalog in sorted(catalogs, key=lambda c: c.destination):
            version, reused = publish_lodgings(session, catalog)
            report.append(
                {
                    "destination": catalog.destination,
                    "version_id": version.id,
                    "properties": len(catalog.properties),
                    "sha256": version.content_sha256,
                    "reused": reused,
                }
            )
    emit(
        logging.getLogger("app.catalog.lodging"),
        "lodging.catalog.published",
        poi_count=sum(len(c.properties) for c in catalogs),
    )
    print(json.dumps({"mode": "directory", "live_offers": False, "published": report}, indent=2))


if __name__ == "__main__":
    main()
