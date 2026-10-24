"""Frozen synthetic routes: evaluate composition against complete saved facts, not live prices."""

from copy import deepcopy
from datetime import date
from types import SimpleNamespace

from app.core.config import ROOT
from app.nlp.explanations import REQUIRED, explain, facts_for, retrieved_sources


def fixtures():
    import json

    knowledge = json.loads((ROOT / "data/semantic-knowledge.json").read_text("utf-8"))
    ids = ["curated:lisbon:nicolau-baixa", "curated:lisbon:pasteis-de-belem"]
    names = ["Nicolau Lisboa — Brunch Nicolau", "Pastéis de Belém — pastel и эспрессо"]
    docs = [
        {
            "place_id": pid,
            "name": name,
            "score": 0.8,
            "description_kind": "curated",
            **knowledge["places"][pid],
        }
        for pid, name in zip(ids, names, strict=True)
    ]
    context = {
        "pois": [{"id": pid, "name": name} for pid, name in zip(ids, names, strict=True)],
        "places_snapshot": [{"id": pid, "category": "food"} for pid in ids],
        "retrieval": {
            "document_threshold": 0.4,
            "matches": [
                *docs,
                {
                    "place_id": "unvisited",
                    "name": "Не посещаемое место",
                    "score": 0.99,
                    "description_kind": "curated",
                    "description": "Этот текст не должен попасть в ответ.",
                    "source_url": "https://example.org/unvisited",
                },
            ],
        },
    }
    trip = SimpleNamespace(
        budget=10000,
        participants_snapshot=[{"user_id": 1, "name": "Анна"}, {"user_id": 2, "name": "Максим"}],
        travel_date=date(2026, 10, 27),
        end_date=None,
        mandatory=ids,
        excluded=[],
    )
    route = SimpleNamespace(
        id=1,
        route_set_id=1,
        visa_report=None,
        comparison={"participant_utility": {"1": 2, "2": 2}, "utility_gap": 0},
        result={
            "visits": [{"place_id": pid} for pid in ids],
            "total_cost": 4560,
            "data_context": context,
        },
    )
    single = deepcopy(route)
    single.result["data_context"] = None
    single.result["visits"] = [{"place_id": "museum"}]
    single.result["total_cost"] = 1500
    single.comparison = {"participant_utility": {"1": 1, "2": 1}, "utility_gap": 0}
    single_trip = deepcopy(trip)
    single_trip.mandatory = ["museum"]
    enriched = deepcopy(route)
    enriched.result["objective"] = {
        "value": 0.5,
        "contributions": {"reward": 0.7, "cost": -0.1, "travel": -0.05, "fairness": -0.05},
    }
    enriched.visa_report = {
        "status": "needs_review",
        "participants": [{"requirement": "required"}, {"requirement": "unknown"}],
    }
    alternative = deepcopy(route)
    alternative.id = 2
    alternative.result["total_cost"] = 560
    alternative.result["visits"] = [{"place_id": ids[1]}]
    alternative.result["data_context"]["retrieval"]["matches"] = [docs[1]]
    alternative.comparison = {"participant_utility": {"1": 1, "2": 1}, "utility_gap": 0}
    alternative_trip = deepcopy(trip)
    alternative_trip.mandatory = [ids[1]]
    return [
        ("single", single, single_trip, single),
        ("food-rag", route, trip, route),
        ("objective-visa-rag", enriched, trip, enriched),
        ("alternative", alternative, alternative_trip, route),
    ]


def evaluate_fixture(fixture, llm):
    name, route, trip, primary = fixture
    facts = facts_for(route, trip, primary, 2)
    output = explain(route, trip, primary, 2, llm)
    required = REQUIRED | {"choice"} | set(retrieved_sources(route))
    required |= {key for key in ("visa", "objective") if key in facts}
    checks = {
        "verified": output.verified,
        "required_facts": required <= set(output.selected_ids),
        "exact_fact_copy": output.paragraphs == [facts[key] for key in output.selected_ids],
        "visited_sources_only": all(
            d["place_id"] in {v["place_id"] for v in route.result["visits"]}
            for d in output.sources.values()
        ),
    }
    # All supplied facts are actual grounding context, including budget/objective/visa.
    sample = {
        "user_input": "Объясни выбор маршрута, его расходы, ограничения и источники.",
        "response": "\n".join(output.paragraphs),
        "retrieved_contexts": list(facts.values()),
    }
    return {
        "id": name,
        "passed": all(checks.values()),
        "checks": checks,
        "output": output.model_dump(mode="json"),
        "ragas_sample": sample,
    }
