"""Versioned synthetic instances, generated independently of every optimizer."""

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from math import ceil, hypot
from random import Random

from app.planning.models import DayInput, Participant, Place, Request
from app.planning.scenarios import scenarios

GENERATOR_VERSION = "group-ttdp-benchmark-v1"


@dataclass(frozen=True)
class Instance:
    name: str
    family: str
    seed: int | None
    request: Request
    inputs: list[DayInput]
    nominal_size: int | None = None

    def payload(self):
        request = asdict(self.request)
        request["mandatory"] = sorted(request["mandatory"])
        request["excluded"] = sorted(request["excluded"])
        if request["meal_ids"]:
            request["meal_ids"] = sorted(request["meal_ids"])
        else:
            request.pop("meal_ids")  # Keep historical benchmark instance hashes stable.
        return {
            "generator": GENERATOR_VERSION,
            "name": self.name,
            "family": self.family,
            "seed": self.seed,
            "request": request,
            "inputs": [
                {
                    "date": str(d.travel_date),
                    "places": [asdict(p) for p in d.places],
                    "travel": [[a, b, t] for (a, b), t in sorted(d.travel.items())],
                }
                for d in self.inputs
            ],
        }

    def digest(self):
        body = json.dumps(self.payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode()).hexdigest()


def generated(size, days, conflict, seed):
    if not 2 <= size <= 100 or not 1 <= days <= 14:
        raise ValueError("Benchmark supports 2-100 POIs and 1-14 days")
    rng = Random(seed * 10000 + size * 100 + days * 10 + int(conflict))
    categories = ["history", "nature", "art", "food"]
    count = 2 if days == 1 else (6 if size == 60 else 4)
    participants = []
    for i in range(count):
        interests = {
            c: (1.0 if j == i % 4 else 0.1) if conflict else [0.9, 0.7, 0.6, 0.5][j]
            for j, c in enumerate(categories)
        }
        participants.append(Participant(f"u{i}", interests))
    coords = {f"p{i:03}": (rng.randrange(80), rng.randrange(80)) for i in range(size)}
    coords["start"], coords["p000"] = (40, 40), (42, 41)
    duration = 360 if seed % 4 == 1 else 540
    request = Request(
        participants,
        count * days * 1800,
        540,
        540 + duration,
        mandatory=frozenset({"p000"}),
        excluded=frozenset({f"p{size - 1:03}"}),
    )
    templates = [
        Place(
            f"p{i:03}",
            categories[i % 4],
            rng.choice([0, 300, 500, 900, 1400]),
            rng.choice([30, 45, 60, 90]),
            540,
            540 + duration,
        )
        for i in range(size)
    ]
    templates[0] = replace(templates[0], cost_per_person=0, duration=45)
    inputs = []
    for d in range(days):
        places = []
        for p in templates:
            if p.id != "p000" and rng.random() < 0.15:
                continue
            opens = 540 + rng.choice([0, 0, 60, 120])
            closes = min(request.end, opens + rng.choice([180, 240, 360]))
            places.append(replace(p, opens=opens, closes=closes) if p.id != "p000" else p)
        travel = {}
        for a in ["start"] + [p.id for p in places]:
            for b in [p.id for p in places]:
                if a == b or (b != "p000" and rng.random() < 0.03):
                    continue
                travel[a, b] = (
                    2
                    + ceil(hypot(coords[a][0] - coords[b][0], coords[a][1] - coords[b][1]) / 3)
                    + d % 2
                )
        inputs.append(DayInput(date(2026, 10, 24) + timedelta(days=d), places, travel))
    family = "conflict" if conflict else "aligned"
    return Instance(f"n{size}-d{days}-{family}-s{seed}", family, seed, request, inputs, size)


def suite(sizes=(12, 30, 60), seeds=(17, 43), day_counts=(1, 3)):
    for name, request, places, travel in scenarios():
        yield Instance(
            name, "control", None, request, [DayInput(date(2026, 10, 24), places, travel)]
        )
    for size in sizes:
        for days in day_counts:
            for conflict in (False, True):
                for seed in seeds:
                    yield generated(size, days, conflict, seed)
