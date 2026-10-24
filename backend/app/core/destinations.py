"""Supported single-city trips. Registry keys preserve existing Lisbon history."""

from dataclasses import dataclass
from typing import Literal

from app.core.config import ROOT

Destination = Literal["Demo City", "Lisbon", "Porto", "Sintra"]
RealDestination = Literal["Lisbon", "Porto", "Sintra"]

# Research rectangles include nearby areas, not administrative city boundaries.
EXTRACT_REGIONS = {
    "Lisbon": (-9.26, 38.68, -9.08, 38.81),
    "Porto": (-8.70, 41.13, -8.55, 41.20),
    "Sintra": (-9.50, 38.74, -9.20, 38.87),
}


@dataclass(frozen=True)
class City:
    id: str
    name: str
    origin_name: str
    latitude: float
    longitude: float
    bbox: str
    country: str = "PT"
    country_name: str = "Португалия"
    timezone: str = "Europe/Lisbon"
    currency: str = "EUR"

    @property
    def origin(self):
        return {
            "id": "start",
            "name": self.origin_name,
            "latitude": self.latitude,
            "longitude": self.longitude,
        }

    @property
    def curated_path(self):
        return ROOT / "data" / f"{self.id.lower()}-curated.json"


CITIES = {
    "Lisbon": City("Lisbon", "Лиссабон", "Rossio", 38.7135, -9.1395, "-9.145,38.708,-9.130,38.720"),
    "Porto": City(
        "Porto", "Порту", "Praça da Liberdade", 41.1462, -8.6110, "-8.620,41.140,-8.600,41.155"
    ),
    "Sintra": City(
        "Sintra", "Синтра", "Estação de Sintra", 38.7984, -9.3866, "-9.395,38.790,-9.378,38.805"
    ),
}


def city_for(destination):
    try:
        return CITIES[destination]
    except KeyError as exc:
        raise ValueError("Unsupported city") from exc
