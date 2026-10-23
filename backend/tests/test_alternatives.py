from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.planning.alternatives import alternatives, distance
from app.planning.models import DayInput, Participant, Place, Request, Result, Visit
from app.planning.period import validate_period
from app.planning.validator import validate_route


def scenario():
    places = [
        Place("museum", "history", 500, 60, 540, 1080),
        Place("park", "nature", 0, 60, 540, 1080),
        Place("gallery", "art", 200, 60, 540, 1080),
        Place("garden", "nature", 100, 60, 540, 1080),
    ]
    travel = {(a, b.id): 5 for a in ["start"] + [p.id for p in places] for b in places if a != b.id}
    request = Request(
        [
            Participant("a", {"history": 1, "nature": 0.2}),
            Participant("b", {"nature": 1, "art": 0.5}),
        ],
        1500,
        540,
        750,
    )
    return request, [DayInput(date(2026, 10, 23), places, travel)]


@pytest.mark.parametrize("algorithm", ["beam", "greedy"])
def test_distinct_checked_options_metrics_and_determinism(algorithm):
    request, inputs = scenario()
    outcome = alternatives(request, inputs, algorithm)
    assert len(outcome.options) == 3
    assert outcome == alternatives(request, inputs, algorithm)
    for index, candidate in enumerate(outcome.options):
        result, comparison = candidate.result, candidate.comparison
        assert result.is_feasible and result.validation.is_valid
        assert validate_route(request, result.visits, inputs[0].places, inputs[0].travel).is_valid
        assert result.total_cost <= request.budget
        assert comparison.visit_count == len(result.visits)
        by_id = {p.id: p for p in inputs[0].places}
        expected = {
            p.id: sum(p.interests.get(by_id[v.place_id].category, 0) for v in result.visits)
            for p in request.participants
        }
        assert comparison.participant_utility == expected
        assert comparison.mean_utility == sum(expected.values()) / 2
        assert comparison.min_utility == min(expected.values())
        assert comparison.utility_gap == max(expected.values()) - min(expected.values())
        assert all(distance(result, c.result) >= 0.25 for c in outcome.options[:index])
    assert outcome.options[0].result.objective.value >= outcome.options[-1].result.objective.value
    assert outcome.diagnostic is None
    assert outcome.search_info["attempts"] <= outcome.search_info["attempt_limit"]


def test_original_mandatory_excluded_budget_and_missing_transitions_remain_hard_constraints():
    request, inputs = scenario()
    request = replace(request, mandatory=frozenset({"museum"}), excluded=frozenset({"gallery"}))
    inputs = [
        replace(inputs[0], travel={k: v for k, v in inputs[0].travel.items() if k[1] != "garden"})
    ]
    outcome = alternatives(request, inputs)
    assert outcome.options
    for candidate in outcome.options:
        ids = {v.place_id for v in candidate.result.visits}
        assert "museum" in ids and not ids & {"gallery", "garden"}
        assert candidate.result.validation.is_valid


def test_all_required_returns_one_without_order_or_time_duplicates():
    request, inputs = scenario()
    request = replace(
        request, end=1080, budget=2000, mandatory=frozenset(p.id for p in inputs[0].places)
    )
    outcome = alternatives(request, inputs)
    assert len(outcome.options) == 1
    assert outcome.search_info["note_codes"] == ["fewer_distinct_options_found"]
    assert outcome.search_info["attempts"] == 1


def test_unsatisfiable_returns_diagnostic_without_invalid_options():
    request, inputs = scenario()
    request = replace(request, mandatory=frozenset({"museum"}), budget=0)
    outcome = alternatives(request, inputs)
    assert not outcome.options
    assert not outcome.diagnostic.is_feasible
    assert "mandatory_not_scheduled" in outcome.diagnostic.reasons
    assert outcome.search_info["note_codes"] == ["no_feasible_options_found"]


def test_multiday_uses_daily_data_and_shared_budget_without_repeated_visits():
    request, inputs = scenario()
    first = inputs[0]
    inputs = [
        replace(first, places=[p for p in first.places if p.id != "museum"]),
        replace(first, travel_date=first.travel_date + timedelta(days=1)),
    ]
    request = replace(request, end=670, budget=1200, mandatory=frozenset({"museum"}))
    outcome = alternatives(request, inputs, multi_day=True)
    assert len(outcome.options) >= 2
    for candidate in outcome.options:
        result = candidate.result
        assert len(result.days) == 2
        assert any(v.place_id == "museum" for v in result.days[1].visits)
        ids = [v.place_id for v in result.visits]
        assert len(ids) == len(set(ids))
        report, _ = validate_period(
            request, [d.visits for d in result.days], inputs, result.total_cost
        )
        assert report.is_valid


def test_independent_check_rejects_forged_feasible_result(monkeypatch):
    request, inputs = scenario()
    monkeypatch.setattr(
        "app.planning.alternatives.checked_plan",
        lambda *_: Result(True, [Visit("museum", 540, 540, 541)], 0, {}, []),
    )
    outcome = alternatives(request, inputs)
    assert not outcome.options
    assert not outcome.diagnostic.is_feasible
    assert "cost_mismatch" in outcome.diagnostic.reasons


def test_large_set_alternatives_change_at_least_quarter_of_places():
    request, inputs = scenario()
    first = inputs[0]
    places = [Place(f"p{i}", "nature", 0, 10, 540, 1080) for i in range(9)]
    travel = {(a, b.id): 0 for a in ["start"] + [p.id for p in places] for b in places if a != b.id}
    outcome = alternatives(
        replace(request, end=1080), [replace(first, places=places, travel=travel)]
    )
    assert len(outcome.options) == 3
    assert len(outcome.options[0].result.visits) == 9
    assert all(distance(c.result, outcome.options[0].result) >= 0.25 for c in outcome.options[1:])
