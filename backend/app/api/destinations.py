import logging
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException

from app.core.destinations import CITIES, RealDestination
from app.core.logging import emit
from app.integrations.city import CityProvider, SourceUnavailable

router = APIRouter(prefix="/api/v1/destinations", tags=["Destinations"])
logger = logging.getLogger(__name__)


@router.get("")
def destinations():
    return [
        {"id": "Demo City", "name": "Демо-город", "synthetic": True, "currency": "EUR"},
        *[
            {
                "id": city.id,
                "name": city.name,
                "country": city.country,
                "country_name": city.country_name,
                "synthetic": False,
                "currency": city.currency,
                "timezone": city.timezone,
                "transport": "driving",
                "origin": city.origin,
                "travel_date_required": True,
            }
            for city in CITIES.values()
        ],
    ]


@router.get("/Lisbon/pois")
def lisbon_pois(travel_date: date, end_date: date | None = None):
    return city_pois("Lisbon", travel_date, end_date)


@router.get("/{destination}/pois")
def city_pois(destination: RealDestination, travel_date: date, end_date: date | None = None):
    last = end_date or travel_date
    if not 0 <= (last - travel_date).days <= 13:
        raise HTTPException(422, "Period must contain 1-14 days")
    try:
        provider = (
            CityProvider() if destination == "Lisbon" else CityProvider(destination=destination)
        )
        catalogs = [
            provider.catalog(travel_date + timedelta(days=i))
            for i in range((last - travel_date).days + 1)
        ]
        pois = {}
        available = {}
        for catalog in catalogs:
            for poi in catalog["pois"]:
                pid = poi["id"]
                if pid not in pois or poi["eligible"]:
                    pois[pid] = poi
                if poi["eligible"]:
                    available.setdefault(pid, []).append(catalog["travel_date"])
        emit(
            logger,
            "catalog.loaded",
            days=len(catalogs),
            poi_count=len(pois),
            eligible_count=len(available),
            data_source="osm-osrm-lisbon-v2"
            if destination == "Lisbon"
            else "curated-osrm-portugal-v1",
        )
        return {
            **catalogs[0],
            "end_date": str(last),
            "pois": [
                {**poi, "available_dates": available.get(pid, [])}
                for pid, poi in sorted(pois.items())
            ],
        }
    except SourceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
