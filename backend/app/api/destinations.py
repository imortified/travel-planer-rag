from datetime import date, timedelta

from fastapi import APIRouter, HTTPException

from app.integrations.city import ORIGIN, CityProvider, SourceUnavailable

router = APIRouter(prefix="/api/v1/destinations", tags=["Destinations"])


@router.get("")
def destinations():
    return [
        {"id": "Demo City", "synthetic": True, "currency": "EUR"},
        {
            "id": "Lisbon",
            "synthetic": False,
            "currency": "EUR",
            "timezone": "Europe/Lisbon",
            "transport": "driving",
            "origin": ORIGIN,
            "travel_date_required": True,
        },
    ]


@router.get("/Lisbon/pois")
def lisbon_pois(travel_date: date, end_date: date | None = None):
    last = end_date or travel_date
    if not 0 <= (last - travel_date).days <= 13:
        raise HTTPException(422, "Period must contain 1-14 days")
    try:
        catalogs = [
            CityProvider().catalog(travel_date + timedelta(days=i))
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
