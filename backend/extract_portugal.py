"""Extract public settlements and research POI from a local Portugal OSM PBF.

Requires `uv sync --group catalog`. Does not publish or invent prices/hours.
Mapped way centres are labelled representative points, never entrances.
"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import osmium

from app.core.destinations import EXTRACT_REGIONS as REGIONS


def interesting(tags):
    return tags.get("name") and (
        tags.get("amenity") in ("restaurant", "cafe", "fast_food", "food_court")
        or
        tags.get("tourism") in ("museum", "aquarium", "zoo", "attraction", "gallery")
        or tags.get("leisure") in ("garden", "park")
        or tags.get("historic") in ("castle", "monastery", "palace")
    )


class Extract(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.settlements = []
        self.pois = {city: [] for city in REGIONS}

    def add(self, kind, identifier, tags, lat, lon, location_kind):
        for city, (west, south, east, north) in REGIONS.items():
            if south <= lat <= north and west <= lon <= east:
                self.pois[city].append(
                    {
                        "id": f"osm:{kind}:{identifier}",
                        "name": tags["name"],
                        "latitude": lat,
                        "longitude": lon,
                        "tags": tags,
                        "location_kind": location_kind,
                        "source_url": f"https://www.openstreetmap.org/{kind}/{identifier}",
                    }
                )

    def node(self, node):
        if not node.location.valid():
            return
        if node.tags.get("place") in ("city", "town") and node.tags.get("name"):
            self.settlements.append(
                {
                    "name": node.tags["name"],
                    "osm_id": node.id,
                    "latitude": node.location.lat,
                    "longitude": node.location.lon,
                }
            )
        if interesting(node.tags):
            self.add(
                "node",
                node.id,
                dict(node.tags),
                node.location.lat,
                node.location.lon,
                "mapped_node",
            )

    def way(self, way):
        if not interesting(way.tags):
            return
        points = [(n.location.lat, n.location.lon) for n in way.nodes if n.location.valid()]
        if points:
            latitudes, longitudes = zip(*points, strict=True)
            lat = (min(latitudes) + max(latitudes)) / 2
            lon = (min(longitudes) + max(longitudes)) / 2
            self.add("way", way.id, dict(way.tags), lat, lon, "mapped_way_bbox_centre")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--settlements-output", type=Path, help="Optional country reference JSON")
    args = parser.parse_args()
    handler = Extract()
    with osmium.io.Reader(str(args.input), osmium.osm.NOTHING) as reader:
        source_timestamp = reader.header().get("osmosis_replication_timestamp")
    if not source_timestamp:
        raise ValueError("PBF header must contain osmosis_replication_timestamp")
    handler.apply_file(str(args.input), locations=True, idx="flex_mem")
    with args.input.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    data = {
        "source": "https://download.geofabrik.de/europe/portugal.html",
        "input_sha256": digest,
        "source_timestamp": source_timestamp,
        "downloaded_at": datetime.fromtimestamp(
            args.input.stat().st_mtime, timezone.utc
        ).isoformat(),
        "country": "PT",
        "regions": REGIONS,
        "limitations": [
            "Named mapped nodes/ways; not a complete list of cities or POI",
            "Way centres are representative map points, not visitor entrances",
            "No independent review of opening hours, fees or routing access",
        ],
        "settlements": sorted(handler.settlements, key=lambda p: (p["name"], p["osm_id"])),
        "pois": {city: sorted(pois, key=lambda p: p["id"]) for city, pois in handler.pois.items()},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.settlements_output:
        reference = {
            key: data[key]
            for key in (
                "source",
                "input_sha256",
                "source_timestamp",
                "downloaded_at",
                "country",
                "settlements",
            )
        }
        reference.update(
            version=f"portugal-settlements-{source_timestamp[:10]}",
            note="Mapped OSM city/town nodes; reference list, not complete planning coverage.",
        )
        args.settlements_output.parent.mkdir(parents=True, exist_ok=True)
        args.settlements_output.write_text(
            json.dumps(reference, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "settlements": len(data["settlements"]),
                "research_pois": {city: len(pois) for city, pois in data["pois"].items()},
            }
        )
    )


if __name__ == "__main__":
    main()
