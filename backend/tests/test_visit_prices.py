from datetime import date

from app.planning.models import DayInput, Participant, Place, Request
from app.planning.period import plan_period
from app.planning.pricing import with_visit_prices
from app.planning.service import checked_plan


def test_date_specific_prices_match_daily_and_period_totals_without_mutation():
    request = Request(
        [Participant("1", {"history": 1}), Participant("2", {"history": 1})],
        10000,
        540,
        1080,
        mandatory=frozenset({"a", "b"}),
    )
    inputs = [
        DayInput(
            date(2026, 10, 24), [Place("a", "history", 800, 60, 540, 1080)], {("start", "a"): 1}
        ),
        DayInput(
            date(2026, 10, 25), [Place("b", "history", 1250, 60, 540, 1080)], {("start", "b"): 1}
        ),
    ]
    original = plan_period(request, inputs, "greedy")
    priced = with_visit_prices(original, inputs, 2)
    assert [v.cost_per_person for v in priced.visits] == [800, 1250]
    assert all(v.group_cost is None for v in original.visits)
    assert priced.visits == [v for day in priced.days for v in day.visits]
    assert sum(v.group_cost for v in priced.visits) == priced.total_cost == 4100
    assert all(sum(v.group_cost for v in day.visits) == day.total_cost for day in priced.days)


def test_free_single_day_visit_has_explicit_zero_price():
    request = Request([Participant("1", {"nature": 1})], 0, 540, 1080)
    places = [Place("park", "nature", 0, 60, 540, 1080)]
    travel = {("start", "park"): 1}
    original = checked_plan(request, places, travel)
    priced = with_visit_prices(original, [DayInput(date.min, places, travel)], 1)
    assert priced.visits[0].cost_per_person == priced.visits[0].group_cost == 0
    assert priced.total_cost == 0 and priced.validation.is_valid
