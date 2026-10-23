"""Fixed synthetic evaluation cases, independent of the algorithm implementations."""

from dataclasses import replace

from app.planning.models import Participant, Place, Request


def scenarios():
    req = Request(
        [
            Participant("a", {"history": 1, "nature": 0.8}),
            Participant("b", {"history": 1, "nature": 0.8}),
        ],
        100,
        540,
        720,
    )
    places = [
        Place("a_late", "history", 0, 60, 660, 720),
        Place("b_early", "nature", 0, 60, 540, 600),
    ]
    travel = {("start", p.id): 0 for p in places}
    travel.update({("a_late", "b_early"): 0, ("b_early", "a_late"): 0})
    yield (
        "time_window_trap",
        replace(req, mandatory=frozenset(p.id for p in places)),
        places,
        travel,
    )
    yield "optional_window_trap", req, places, travel
    budget_places = [
        Place("expensive", "history", 50, 30, 540, 720),
        Place("cheap1", "nature", 25, 30, 540, 720),
        Place("cheap2", "nature", 25, 30, 540, 720),
    ]
    matrix = {
        (s, p.id): 0
        for s in ["start"] + [p.id for p in budget_places]
        for p in budget_places
        if s != p.id
    }
    yield "budget_trap", req, budget_places, matrix
    yield (
        "constraint_conflict",
        replace(req, mandatory=frozenset({"a_late"}), excluded=frozenset({"a_late"})),
        places,
        travel,
    )
    yield "missing_routing", req, places, {}
    yield "empty_catalog", req, [], {}
