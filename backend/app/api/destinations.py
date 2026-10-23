from datetime import date

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
def lisbon_pois(travel_date: date):
    try:
        return CityProvider().catalog(travel_date)
    except SourceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
