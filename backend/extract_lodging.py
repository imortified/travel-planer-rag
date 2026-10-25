"""Extract mapped accommodation from a local Portugal PBF, without prices or availability.

Requires the optional catalog dependency group (osmium), as extract_portugal.py does.
The input file's original modification time is retained as the download time.
"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import osmium

from app.core.destinations import EXTRACT_REGIONS
from app.schemas.lodging import LodgingCatalog, LodgingProperty, public_website

KINDS = {"hotel", "hostel", "guest_house", "apartment", "motel"}


def lodging_tags(tags):
    return bool(
        tags.get("name")
        and tags.get("tourism") in KINDS
        and not any(tags.get(key) == "yes" for key in ("disused", "abandoned", "demolished"))
    )


class LodgingExtract(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.properties = {city: [] for city in EXTRACT_REGIONS}

    def add(self, kind, identifier, tags, lat, lon):
        for city, (west, south, east, north) in EXTRACT_REGIONS.items():
            if not (south <= lat <= north and west <= lon <= east):
                continue
            address = ", ".join(
                value
                for value in [
                    " ".join(filter(None, [tags.get("addr:street"), tags.get("addr:housenumber")])),
                    tags.get("addr:postcode"),
                    tags.get("addr:city"),
                ]
                if value
            )
            raw_url = tags.get("website") or tags.get("contact:website")
            website = public_website(raw_url)
            try:
                item = LodgingProperty(
                    id=f"osm:{kind}:{identifier}",
                    destination=city,
                    name=tags["name"],
                    kind=tags["tourism"],
                    latitude=lat,
                    longitude=lon,
                    location_kind="mapped_node" if kind == "node" else "mapped_way_bbox_centre",
                    address=address or None,
                    website_url=website,
                    source_url=f"https://www.openstreetmap.org/{kind}/{identifier}",
                )
            except ValueError:
                # A malformed website does not erase an otherwise useful mapped property.
                item = LodgingProperty(
                    id=f"osm:{kind}:{identifier}",
                    destination=city,
                    name=tags["name"],
                    kind=tags["tourism"],
                    latitude=lat,
                    longitude=lon,
                    location_kind="mapped_node" if kind == "node" else "mapped_way_bbox_centre",
                    address=address or None,
                    website_url=None,
                    source_url=f"https://www.openstreetmap.org/{kind}/{identifier}",
                )
            self.properties[city].append(item)

    def node(self, node):
        if node.location.valid() and lodging_tags(node.tags):
            self.add("node", node.id, dict(node.tags), node.location.lat, node.location.lon)

    def way(self, way):
        if not lodging_tags(way.tags):
            return
        points = [(n.location.lat, n.location.lon) for n in way.nodes if n.location.valid()]
        if points:
            latitudes, longitudes = zip(*points, strict=True)
            self.add(
                "way",
                way.id,
                dict(way.tags),
                (min(latitudes) + max(latitudes)) / 2,
                (min(longitudes) + max(longitudes)) / 2,
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with osmium.io.Reader(str(args.input), osmium.osm.NOTHING) as reader:
        timestamp = reader.header().get("osmosis_replication_timestamp")
    if not timestamp:
        raise ValueError("PBF lacks its source timestamp")
    handler = LodgingExtract()
    handler.apply_file(str(args.input), locations=True, idx="flex_mem")
    with args.input.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    catalogs = [
        LodgingCatalog(
            destination=city,
            source="https://download.geofabrik.de/europe/portugal.html",
            source_upstream_asof=timestamp,
            downloaded_at=datetime.fromtimestamp(args.input.stat().st_mtime, timezone.utc),
            input_sha256=digest,
            properties=sorted(items, key=lambda p: p.id),
        )
        for city, items in handler.properties.items()
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {"catalogs": [c.model_dump(mode="json") for c in catalogs]},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({c.destination: len(c.properties) for c in catalogs}))


if __name__ == "__main__":
    main()
