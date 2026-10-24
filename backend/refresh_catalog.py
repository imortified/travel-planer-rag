"""Publish city POI facts; does not update official facts' checked_on or extend their validity."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.refresh import refresh
from app.catalog.repository import load_active
from app.core.destinations import CITIES
from app.core.logging import configure_logging, request_id
from app.db.models import CatalogVersion
from app.db.session import engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", choices=list(CITIES), default="Lisbon")
    parser.add_argument("--extract", type=Path, help="Normalized local Portugal PBF extract")
    parser.add_argument(
        "--curated-only",
        action="store_true",
        help="Publish only explicitly reviewed standalone facts; no OSM refresh",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Read active version/history without contacting external sources",
    )
    args = parser.parse_args()
    configure_logging(file_tag="catalog-refresh")
    token = request_id.set(str(uuid4()))
    try:
        if args.status:
            with Session(engine) as session:
                snapshot = load_active(session, args.city)
                versions = session.scalars(
                    select(CatalogVersion)
                    .where(CatalogVersion.destination == args.city)
                    .order_by(CatalogVersion.id.desc())
                    .limit(10)
                ).all()
                result = {
                    "active_version_id": snapshot.version_id,
                    "status": "ready"
                    if snapshot.data.manifest.fetched_at
                    <= datetime.now(timezone.utc)
                    < snapshot.data.manifest.fresh_until
                    else "expired",
                    "versions": [
                        {
                            "id": v.id,
                            "poi_count": v.poi_count,
                            "fetched_at": v.fetched_at.isoformat(),
                            "fresh_until": v.fresh_until.isoformat(),
                            "content_sha256": v.content_sha256,
                        }
                        for v in versions
                    ],
                }
        else:
            snapshot, reused = refresh(
                destination=args.city, curated_only=args.curated_only, extract_path=args.extract
            )
            result = {
                "published": True,
                "version_id": snapshot.version_id,
                "poi_count": len(snapshot.data.pois),
                "content_sha256": snapshot.content_sha256,
                "reused": reused,
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "published": False,
                    "error_type": type(exc).__name__,
                    "request_id": request_id.get(),
                    "detail": "Данные не опубликованы; предыдущая версия не изменена. "
                    "Проверьте миграцию 0009, источники и JSON-логи.",
                },
                ensure_ascii=False,
            )
        )
        return 1
    finally:
        request_id.reset(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
