import json

from app.core.config import ROOT
from app.planning.models import Place


def load_demo():
    data = json.loads((ROOT / "data" / "demo.json").read_text(encoding="utf-8"))
    places = [Place(**p) for p in data["places"]]
    travel = {
        (source, target): minutes
        for source, destinations in data["travel_minutes"].items()
        for target, minutes in destinations.items()
    }
    return places, travel
