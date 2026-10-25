from dataclasses import asdict, replace
from datetime import date, timedelta
from math import isfinite
from types import SimpleNamespace

import pytest

from app.nlp.explanations import explain
from app.planning.alternatives import alternatives
from app.planning.models import DayInput, Participant, Place, Request, Visit
from app.planning.objective import VERSION, Objective, Weights
from app.planning.period import plan_period
from app.planning.service import checked_plan
from app.schemas.assistant import ExplanationDraft


def matrix(places, duration=0):
    return {
        (a, p.id): duration for a in ["start"] + [p.id for p in places] for p in places if a != p.id
    }


def test_formula_has_independently_calculated_contributions():
    req = Request(
        [Participant("a", {"h": 1, "n": 0.25}), Participant("b", {"n": 1})], 1000, 540, 720
    )
    places = [Place("h", "h", 100, 30, 540, 720), Place("n", "n", 0, 30, 540, 720)]
    report = Objective.single(req, places).evaluate([[Visit("h", 550, 600, 630)]])
    assert report.utility_bounds == {"a": 1.25, "b": 1}
    assert report.reward == pytest.approx(4 / 9)
    assert report.participant_coverage == {"a": 0.8, "b": 0}
    assert report.cost_ratio == 0.2
    assert report.travel_minutes == 10  # Fifty minutes waiting are excluded.
    assert report.travel_ratio == pytest.approx(10 / 180)
    assert report.fairness_gap == 0.8
    assert report.value == pytest.approx(4 / 9 - 0.15 * 0.2 - 0.15 * 10 / 180 - 0.35 * 0.8)
    assert sum(report.contributions.values()) == report.value


@pytest.mark.parametrize("algorithm", ["greedy", "beam"])
def test_cost_can_outweigh_a_small_utility_advantage(algorithm):
    req = Request([Participant("a", {"art": 1, "history": 0.9})], 1000, 540, 600)
    places = [Place("art", "art", 1000, 60, 540, 600), Place("history", "history", 0, 60, 540, 600)]
    travel = matrix(places)
    result = checked_plan(req, places, travel, algorithm)
    assert [v.place_id for v in result.visits] == ["history"]
    assert result.objective.value == pytest.approx(0.9 / 1.9)
    no_cost_penalty = Objective.single(req, places, Weights(cost=0))
    result = checked_plan(req, places, travel, algorithm, objective=no_cost_penalty)
    assert [v.place_id for v in result.visits] == ["art"]


@pytest.mark.parametrize("algorithm", ["greedy", "beam"])
def test_equal_interest_prefers_shorter_driving(algorithm):
    req = Request([Participant("a", {"h": 1})], 0, 540, 630)
    places = [Place("a_far", "h", 0, 60, 540, 630), Place("z_near", "h", 0, 60, 540, 630)]
    travel = matrix(places, 30) | {("start", "z_near"): 0}
    result = checked_plan(req, places, travel, algorithm)
    assert [v.place_id for v in result.visits] == ["z_near"]
    assert result.objective.travel_minutes == 0
    assert result.objective.cost_ratio == 0


def test_fairness_uses_coverage_not_raw_utility_gap():
    req = Request([Participant("a", {"h": 1}), Participant("b", {"n": 1})], 0, 540, 660)
    places = [
        Place("h1", "h", 0, 60, 540, 660),
        Place("h2", "h", 0, 60, 540, 660),
        Place("n", "n", 0, 60, 540, 660),
    ]
    objective = Objective.single(req, places)
    unfair = [[Visit("h1", 540, 540, 600), Visit("h2", 600, 600, 660)]]
    balanced = [[Visit("h1", 540, 540, 600), Visit("n", 600, 600, 660)]]
    left, right = objective.evaluate(unfair), objective.evaluate(balanced)
    assert left.mean_utility == right.mean_utility == 1
    assert left.fairness_gap == 1 and right.fairness_gap == 0.5
    assert right.value - left.value == pytest.approx(0.175)
    for algorithm in ("greedy", "beam"):
        result = checked_plan(req, places, matrix(places), algorithm)
        assert "n" in {v.place_id for v in result.visits}


def test_no_available_interest_does_not_create_an_impossible_fairness_target():
    req = Request(
        [Participant("a", {"h": 1}), Participant("b", {"food": 1}), Participant("c", {})],
        0,
        540,
        600,
    )
    places = [Place("h", "h", 0, 60, 540, 600)]
    score = checked_plan(req, places, matrix(places)).objective
    assert score.excluded_from_fairness == ["b", "c"]
    assert score.fairness_gap == 0
    assert score.reward == 1
    assert score.participant_coverage == {"a": 1, "b": 0, "c": 0}
    empty = Objective.single(replace(req, participants=[Participant("a", {})]), places)
    score = empty.evaluate([[Visit("h", 540, 540, 600)]])
    assert score.reward == score.fairness_gap == score.cost_ratio == 0
    assert isfinite(score.value)


def test_alternatives_keep_original_scales_after_temporary_exclusions():
    req = Request([Participant("a", {"h": 1})], 1000, 540, 660)
    places = [Place(f"p{i}", "h", 100, 60, 540, 660) for i in range(4)]
    inputs = [DayInput(date(2026, 10, 24), places, matrix(places))]
    output = alternatives(req, inputs)
    assert len(output.options) == 3
    for candidate in output.options:
        report = candidate.result.objective
        assert report.candidate_count == 4
        assert report.utility_bounds == {"a": 4}
        assert report.budget == 1000
        assert report.value == pytest.approx(report.reward - 0.15 * report.cost_ratio)
    values = [c.result.objective.value for c in output.options]
    assert values == sorted(values, reverse=True)
    assert output.search_info["objective_version"] == VERSION
    original_ban = Objective(replace(req, excluded=frozenset({"p0"})), inputs)
    assert original_ban.bounds == {"a": 3}


@pytest.mark.parametrize("algorithm", ["greedy", "beam"])
def test_multiday_has_shared_budget_and_unique_poi_bounds(algorithm):
    req = Request([Participant("a", {"h": 1})], 1000, 540, 610, mandatory=frozenset({"a", "b"}))
    places = [Place("a", "h", 100, 60, 540, 610), Place("b", "h", 100, 60, 540, 610)]
    day = DayInput(date(2026, 10, 24), places, matrix(places, 10))
    inputs = [day, replace(day, travel_date=day.travel_date + timedelta(days=1))]
    result = plan_period(req, inputs, algorithm)
    score = result.objective
    assert result.validation.is_valid
    assert score.utility_bounds == {"a": 2}  # Each POI appears on both dates, counts once.
    assert score.budget == 1000 and score.cost_ratio == 0.2
    assert score.available_minutes == 140
    assert score.travel_minutes == 20
    assert score.travel_ratio == pytest.approx(20 / 140)


def test_date_specific_interests_and_prices_are_scored_on_the_actual_day():
    req = Request(
        [Participant("a", {"h": 1, "n": 0.1}), Participant("b", {"n": 1})], 1000, 540, 600
    )
    day = DayInput(date(2026, 10, 24), [Place("p", "h", 100, 60, 540, 600)], {})
    next_day = replace(
        day,
        travel_date=day.travel_date + timedelta(days=1),
        places=[Place("p", "n", 200, 60, 540, 600)],
    )
    score = Objective(req, [day, next_day]).evaluate([[], [Visit("p", 540, 540, 600)]])
    assert score.utility_bounds == {"a": 1, "b": 1}
    assert score.participant_utility == {"a": 0.1, "b": 1}
    assert score.cost_ratio == 0.4
    assert score.reward == 0.55


def test_invalid_objective_inputs_are_explicit():
    for weights in [{"cost": -1}, {"travel": float("nan")}, {"reward": 0}]:
        with pytest.raises(ValueError):
            Weights(**weights)
    req = Request([Participant("a", {"h": 1})], 0, 540, 600)
    objective = Objective.single(req, [Place("p", "h", 100, 60, 540, 600)])
    with pytest.raises(ValueError, match="zero budget"):
        objective.evaluate([[Visit("p", 540, 540, 600)]])
    with pytest.raises(ValueError, match="day count"):
        objective.evaluate([])


def test_explanation_fits_objective_visa_choice_and_two_retrieved_sources():
    req = Request([Participant("1", {"h": 1})], 1000, 540, 660)
    places = [Place("p1", "h", 100, 60, 540, 660), Place("p2", "h", 100, 60, 540, 660)]
    result = asdict(checked_plan(req, places, matrix(places)))
    result["data_context"] = {
        "retrieval": {
            "matches": [
                {
                    "place_id": p.id,
                    "name": p.id,
                    "description": "Проверенное описание.",
                    "description_kind": "curated",
                    "score": 0.9,
                    "source_url": "https://example.org/",
                }
                for p in places
            ]
        }
    }
    route = SimpleNamespace(
        id=1,
        route_set_id=1,
        comparison=None,
        result=result,
        visa_report={"status": "needs_review", "participants": [{"requirement": "unknown"}]},
    )
    trip = SimpleNamespace(
        budget=1000,
        participants_snapshot=[{"user_id": 1, "name": "A"}],
        travel_date=None,
        mandatory=[],
        excluded=[],
    )

    class Model:
        def structured(self, system, data, schema, verify=None):
            keys = [
                "summary",
                "budget",
                "limitations",
                "choice",
                "visa",
                "objective",
                "source_1",
                "source_2",
            ]
            return ExplanationDraft(selected_ids=keys), "test-model"

    output = explain(route, trip, llm=Model())
    assert output.mode == "llm"
    assert len(output.selected_ids) == 8
    assert len(output.sources) == 2


@pytest.mark.parametrize("fault", [None, "missing", "modified"])
def test_explanation_requires_the_exact_saved_objective(fault):
    req = Request([Participant("1", {"h": 1})], 1000, 540, 600)
    places = [Place("p", "h", 100, 60, 540, 600)]
    result = checked_plan(req, places, matrix(places))
    route = SimpleNamespace(id=1, route_set_id=None, comparison=None, result=asdict(result))
    trip = SimpleNamespace(
        budget=1000,
        participants_snapshot=[{"user_id": 1, "name": "A"}],
        travel_date=None,
        mandatory=[],
        excluded=[],
    )

    class Model:
        def structured(self, system, data, schema, verify=None):
            keys = ["summary", "budget", "limitations", "objective"]
            if fault == "missing":
                keys.remove("objective")
            if fault == "modified":
                keys[-1] = "Найден глобальный оптимум."
            return ExplanationDraft(selected_ids=keys), "test-model"

    output = explain(route, trip, llm=Model())
    assert output.mode == ("llm" if fault is None else "template")
    assert "objective" in output.selected_ids
    assert all("глобальный оптимум" not in p for p in output.paragraphs)
