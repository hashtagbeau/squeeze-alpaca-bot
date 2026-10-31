"""Session clock. Alpaca calendar decides holidays; is_open is not the scan gate."""

from datetime import date, datetime, time, timedelta

from squeeze_bot.constants import (
    ENTRY_CUTOFF,
    PRIOR_CLOSE_TIME,
    RTH_OPEN,
    SCAN_START,
    VWAP_ANCHOR,
)
from squeeze_bot.money import ET, to_et


def clock_time(moment: datetime) -> time:
    return to_et(moment).time().replace(tzinfo=None)


def is_weekday(moment: datetime) -> bool:
    return to_et(moment).weekday() < 5


def in_scan_window(moment: datetime) -> bool:
    """[07:30:00, 10:00:00) ET on a weekday. 10:00:00 itself is outside."""
    if not is_weekday(moment):
        return False
    current = clock_time(moment)
    return SCAN_START <= current < ENTRY_CUTOFF


def entries_allowed_clock(moment: datetime) -> bool:
    """No new entries at or after 10:00:00 ET. Exits are not gated here."""
    if not is_weekday(moment):
        return False
    return clock_time(moment) < ENTRY_CUTOFF


def is_premarket(moment: datetime) -> bool:
    return clock_time(moment) < RTH_OPEN


def extended_hours_for_entry(moment: datetime) -> bool:
    return is_premarket(moment)


def session_date(moment: datetime) -> date:
    """Session anchored at 04:00 ET. A print before 04:00 belongs to the prior date."""
    local = to_et(moment)
    if local.time().replace(tzinfo=None) < VWAP_ANCHOR:
        return local.date() - timedelta(days=1)
    return local.date()


def trading_dates(days: list[date]) -> set[date]:
    return set(days)


def is_trading_day(day: date, days: set[date]) -> bool:
    return day in days


def prior_session_close(trading_days: list[date], today: date) -> datetime | None:
    """Most recent session date strictly before today, at 16:00 ET."""
    earlier = [day for day in trading_days if day < today]
    if not earlier:
        return None
    last = max(earlier)
    return datetime.combine(last, PRIOR_CLOSE_TIME, tzinfo=ET)


def scan_due(last_scan: datetime | None, now: datetime, interval_s: int = 60) -> bool:
    if last_scan is None:
        return True
    return (now - last_scan).total_seconds() >= interval_s
