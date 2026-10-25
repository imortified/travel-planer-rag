from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.core.dates import trip_today
from app.schemas.groups import TripInput, TripOutput


@pytest.mark.parametrize("city", ["Lisbon", "Porto", "Sintra"])
def test_calendar_uses_destination_zone_at_midnight_and_dst(city):
    # Lisbon is UTC+1 before the October DST change, and UTC+0 afterwards.
    assert trip_today(city, datetime(2026, 10, 23, 23, 30, tzinfo=timezone.utc)) == date(
        2026, 10, 24
    )
    assert trip_today(city, datetime(2026, 10, 25, 23, 30, tzinfo=timezone.utc)) == date(
        2026, 10, 25
    )
    assert trip_today("Demo City", datetime(2026, 10, 23, 23, 30, tzinfo=timezone.utc)) == date(
        2026, 10, 23
    )
    with pytest.raises(ValueError, match="timezone"):
        trip_today(city, datetime(2026, 10, 25))


@pytest.mark.parametrize("city", ["Demo City", "Lisbon", "Porto", "Sintra"])
def test_new_request_rejects_past_but_accepts_today_and_future(monkeypatch, city):
    today = date(2026, 11, 2)
    monkeypatch.setattr("app.schemas.groups.trip_today", lambda _destination: today)
    with pytest.raises(ValidationError, match="не может быть в прошлом"):
        TripInput(destination=city, travel_date=today - timedelta(days=1), budget=100)
    for day in (today, today + timedelta(days=1)):
        assert TripInput(destination=city, travel_date=day, budget=100).travel_date == day
    assert TripInput(budget=100).travel_date is None


def test_output_validation_never_uses_the_current_clock(monkeypatch):
    def unexpected(_destination):
        pytest.fail("Historical output must not check today's date")

    monkeypatch.setattr("app.schemas.groups.trip_today", unexpected)
    saved = TripOutput(
        id=1,
        group_id=1,
        destination="Lisbon",
        travel_date="2020-01-01",
        budget=10000,
        participants_snapshot=[],
        created_at="2020-01-01T12:00:00Z",
    )
    assert saved.travel_date == date(2020, 1, 1)
