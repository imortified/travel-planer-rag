from dataclasses import replace
from datetime import date

import pytest

from app.catalog.age_rules import AgeRules
from app.planning.age_pricing import prepare_age_pricing, quote
from app.planning.costs import age_at, group_cost
from app.planning.models import DayInput, Participant, Place, Request, Visit
from app.planning.period import plan_period
from app.planning.pricing import with_visit_prices
from app.planning.service import checked_plan
from app.planning.validator import validate_route
from app.schemas.ages import AgeProfile

DAY = date(2026, 10, 26)
RULES = {
    "checked_on": "2026-10-01",
    "valid_until": "2026-11-22",
    "source_url": "https://example.org/verified-age-tariffs",
    "notes": "Fixture only",
    "bands": [
        {"id": key, "label": key, "min_age": lo, "max_age": hi, "cost": cost}
        for key, lo, hi, cost in [
            ("infant", 0, 2, 0),
            ("child", 3, 12, 1900),
            ("adult", 13, 64, 3100),
            ("senior", 65, 120, 2200),
        ]
    ],
}


def fixture(ages=(30, 35, 8), budget=8100):
    participants = [Participant(str(i), {"nature": 1}, age) for i, age in enumerate(ages)]
    request = Request(participants, budget, 540, 1080, mandatory=frozenset({"zoo"}))
    data = DayInput(
        DAY,
        [Place("zoo", "nature", 3100, 60, 600, 1080)],
        {("start", "zoo"): 5},
        {"pois": [{"id": "zoo", "name": "Zoo", "cost_per_person": 3100, "age_rules": RULES}]},
    )
    return request, prepare_age_pricing(request, [data], assessed_on=DAY)


@pytest.mark.parametrize("algorithm", ["greedy", "beam", "cp-sat"])
def test_family_tariffs_change_actual_solver_budget_and_saved_individual_prices(algorithm):
    request, inputs = fixture()
    data = inputs[0]
    result = checked_plan(request, data.places, data.travel, algorithm, time_limit=3)
    assert result.is_feasible and result.validation.computed_cost == 8100
    assert result.total_cost == 8100 and result.objective.cost_ratio == 1
    result = with_visit_prices(result, inputs, 3, request.participants)
    visit = result.visits[0]
    assert visit.cost_per_person is None  # A heterogeneous group has no invented uniform fare.
    assert visit.group_cost == 8100
    assert [row["cost"] for row in visit.participant_prices] == [3100, 3100, 1900]
    assert validate_route(request, result.visits, data.places, data.travel, 8100).is_valid
    refused = checked_plan(
        replace(request, budget=8099), data.places, data.travel, algorithm, time_limit=3
    )
    assert not refused.is_feasible


@pytest.mark.parametrize(
    "age,expected",
    [
        (0, 0),
        (2, 0),
        (3, 1900),
        (12, 1900),
        (13, 3100),
        (64, 3100),
        (65, 2200),
        (120, 2200),
        (None, 3100),
    ],
)
def test_age_thresholds_and_unknown_age_are_explicit(age, expected):
    request, inputs = fixture((age,), 10000)
    row = inputs[0].data_context["pois"][0]["participant_prices"][0]
    assert row["cost"] == expected and row["age"] == age
    assert group_cost(inputs[0].places[0], request.participants) == expected
    if age is None:
        assert row["reason"] == "regular_fallback_unknown_age"


def test_birthday_changes_prices_on_visit_dates_without_mutating_reference_catalog():
    person = Participant("child", {"nature": 1}, birth_date=date(2013, 10, 27))
    assert age_at(person, DAY) == 12 and age_at(person, date(2026, 10, 27)) == 13
    place = Place("zoo", "nature", 3100, 60, 600, 1080)
    poi = {"age_rules": RULES}
    first, _ = quote(place, poi, [person], DAY, DAY)
    second, _ = quote(place, poi, [person], date(2026, 10, 27), DAY)
    assert first[0]["cost"] == 1900 and second[0]["cost"] == 3100
    assert place.cost_per_person == 3100 and place.participant_costs is None


def test_date_free_admission_wins_over_age_prices_but_expired_discounts_do_not_apply():
    person = Participant("p", {}, 8)
    place = Place("museum", "art", 3100, 60, 600, 1080)
    rows, _ = quote(place, {"age_rules": RULES, "date_free_admission": True}, [person], DAY, DAY)
    assert rows[0]["cost"] == 0 and rows[0]["reason"] == "date_free_admission"
    expired = {**RULES, "valid_until": "2026-10-25"}
    rows, _ = quote(place, {"age_rules": expired}, [person], DAY, DAY)
    assert rows[0]["cost"] == 3100 and rows[0]["reason"] == "regular_fallback_expired_rule"
    rows, _ = quote(place, {}, [person], DAY, DAY)
    assert rows[0]["reason"] == "regular_fallback_no_age_rule"


def test_menu_orders_are_not_automatically_free_or_discounted_for_children():
    place = Place("cafe", "food", 2000, 60, 600, 1080)
    rows, reason = quote(
        place, {}, [Participant("adult", {}, 30), Participant("baby", {}, 1)], DAY, DAY
    )
    assert reason is None and [row["cost"] for row in rows] == [2000, 2000]
    assert all(row["reason"] == "explicit_menu_order" for row in rows)


def test_reviewed_age_restrictions_and_companion_requirements_filter_reference_candidates():
    request, inputs = fixture((8,), 10000)
    data = inputs[0]
    for patch, ages, reason in [
        ({"min_age": 18}, [8], "age_admission_restricted"),
        ({"min_age": 18}, [None], "age_required_for_access_check"),
        ({"accompanied_under": 13}, [8, 16], "adult_companion_required"),
    ]:
        people = [Participant(str(i), {}, age) for i, age in enumerate(ages)]
        _, actual = quote(data.places[0], {"age_rules": {**RULES, **patch}}, people, DAY, DAY)
        assert actual == reason
    rows, reason = quote(
        data.places[0],
        {"age_rules": {**RULES, "accompanied_under": 13}},
        [Participant("a", {}, 18), Participant("c", {}, 8)],
        DAY,
        DAY,
    )
    assert reason is None and rows
    with pytest.raises(ValueError, match="Обязательное место"):
        prepare_age_pricing(
            request,
            [
                replace(
                    data,
                    data_context={"pois": [{"id": "zoo", "age_rules": {**RULES, "min_age": 18}}]},
                )
            ],
            assessed_on=DAY,
        )


def test_age_profiles_and_bands_reject_ambiguous_or_invalid_values():
    for age in (-1, 121, True, 8.5):
        with pytest.raises(ValueError):
            AgeProfile(age_on_start=age)
    with pytest.raises(ValueError):
        AgeProfile(age_on_start=8, birth_date="2018-01-01")
    with pytest.raises(ValueError):
        AgeRules.model_validate(
            {**RULES, "bands": [RULES["bands"][0], {**RULES["bands"][1], "min_age": 2}]}
        )
    with pytest.raises(ValueError):
        age_at(Participant("p", {}, birth_date=date(2027, 1, 1)), DAY)


def test_validator_recomputes_vector_sum_and_detects_tampered_saved_prices():
    request, inputs = fixture()
    data = inputs[0]
    visit = Visit(
        "zoo",
        545,
        600,
        660,
        group_cost=9300,
        participant_prices=[{"participant_id": "0", "cost": 1}],
    )
    report = validate_route(request, [visit], data.places, data.travel, reported_cost=9300)
    assert report.computed_cost == 8100
    assert {"cost_mismatch", "visit_price_mismatch", "participant_price_mismatch"} <= {
        row.code for row in report.violations
    }
    for vector in ({"0": 3100}, {"0": 3100, "1": 3100, "2": -1}):
        with pytest.raises(ValueError):
            validate_route(
                request, [], [replace(data.places[0], participant_costs=vector)], data.travel
            )


@pytest.mark.parametrize("algorithm", ["greedy", "beam", "cp-sat"])
def test_multi_day_group_cost_uses_each_days_prices_and_period_validator(algorithm):
    request, inputs = fixture(budget=9000)
    request = replace(request, mandatory=frozenset({"zoo", "other"}))
    other = Place(
        "other",
        "nature",
        3100,
        60,
        600,
        1080,
        participant_costs={p.id: 0 for p in request.participants},
    )
    days = [inputs[0], DayInput(date(2026, 10, 27), [other], {("start", "other"): 5})]
    result = plan_period(request, days, algorithm, time_limit=3)
    assert result.is_feasible and result.total_cost == 8100
    assert result.validation.computed_cost == 8100
