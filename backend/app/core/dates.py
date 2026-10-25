"""Business calendar for new requests, independent of the server's local timezone."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.core.destinations import CITIES


def trip_today(destination, now=None):
    instant = now if now is not None else datetime.now(timezone.utc)
    if instant.utcoffset() is None:
        raise ValueError("Calendar reference must include timezone")
    # Real-city schedules use the destination clock; undated Demo City uses UTC.
    zone = ZoneInfo(CITIES[destination].timezone) if destination in CITIES else timezone.utc
    return instant.astimezone(zone).date()
