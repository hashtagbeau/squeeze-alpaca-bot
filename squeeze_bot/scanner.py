"""Scanner filters and exclusions. Pure functions; no network."""

from datetime import date, datetime, timedelta
from decimal import Decimal

from squeeze_bot.clockutil import session_date
from squeeze_bot.constants import (
    ADV_DAYS,
    IPO_MAX_AGE_DAYS,
    LULD_TOKENS,
    MAX_FLOAT,
    MAX_PRICE,
    MIN_DAY_CHANGE,
    MIN_RVOL,
    POP_LOOKBACK_DAYS,
)
from squeeze_bot.models import DailyBar, MinuteBar, NewsItem, Tri
from squeeze_bot.money import D


def price_ok(price: float) -> bool:
    return D(price) <= MAX_PRICE


def change_pct(price: float | None, prior_close: float | None) -> Decimal | None:
    if price is None or prior_close is None:
        return None
    base = D(prior_close)
    if base <= 0:
        return None
    return (D(price) - base) / base


def change_ok(price: float | None, prior_close: float | None) -> bool:
    pct = change_pct(price, prior_close)
    return pct is not None and pct >= MIN_DAY_CHANGE


def cumulative_volume(bars: list[MinuteBar], today: date, forming_volume: float = 0) -> Decimal:
    total = D(0)
    for bar in bars:
        if session_date(bar.start) == today:
            total += D(bar.volume)
    total += D(forming_volume)
    return total


def average_daily_volume(daily_bars: list[DailyBar], today: date) -> Decimal | None:
    """Mean of the prior 30 trading-day volumes, excluding today. Fewer than 30 is undefined."""
    prior = sorted((bar for bar in daily_bars if bar.day < today), key=lambda bar: bar.day)
    if len(prior) < ADV_DAYS:
        return None
    window = prior[-ADV_DAYS:]
    return sum((D(bar.volume) for bar in window), D(0)) / D(ADV_DAYS)


def relative_volume(cum: Decimal | None, average: Decimal | None) -> Decimal | None:
    if cum is None or average is None or average <= 0:
        return None
    return cum / average


def rvol_ok(cum: Decimal | None, average: Decimal | None) -> bool:
    value = relative_volume(cum, average)
    return value is not None and value >= MIN_RVOL


def float_under_max(shares: float | None, limit: int = MAX_FLOAT) -> bool:
    return shares is not None and D(shares) < D(limit)


def first_qualifying_news(articles: list[NewsItem], cutoff: datetime) -> NewsItem | None:
    qualified = [
        article
        for article in articles
        if article.url and article.published_at > cutoff
    ]
    if not qualified:
        return None
    return min(qualified, key=lambda article: article.published_at)


def ipo_exclusion(earliest: date | None, today: date, history_truncated: bool) -> Tri:
    """IPO < 90 calendar days fails. Age 90 does not. Truncated history older than 90 passes."""
    if earliest is None:
        return Tri.UNCERTAIN
    age = (today - earliest).days
    if history_truncated and age >= IPO_MAX_AGE_DAYS:
        return Tri.OK
    if age < IPO_MAX_AGE_DAYS:
        return Tri.FAIL
    return Tri.OK


def second_day_exclusion(
    prior_close: float | None,
    prior_prior_close: float | None,
    prior_high: float | None,
) -> Tri:
    """Prior day >= +50% close-to-close OR prior-day HOD >= +50% vs the close before that day."""
    if prior_close is None or prior_prior_close is None or prior_high is None:
        return Tri.UNCERTAIN
    base = D(prior_prior_close)
    if base <= 0:
        return Tri.UNCERTAIN
    close_to_close = (D(prior_close) - base) / base
    hod_move = (D(prior_high) - base) / base
    if close_to_close >= MIN_DAY_CHANGE or hod_move >= MIN_DAY_CHANGE:
        return Tri.FAIL
    return Tri.OK


def _pop_and_fade_day(high: float, close: float, prev_close: float) -> bool:
    base = D(prev_close)
    if base <= 0:
        return False
    gain = (D(high) - base) / base
    if gain < MIN_DAY_CHANGE:
        return False
    span = D(high) - base
    if span <= 0:
        return False
    giveback = (D(high) - D(close)) / span
    return giveback >= MIN_DAY_CHANGE


def pop_and_fade_exclusion(daily_bars: list[DailyBar], today: date) -> Tri:
    """Any day in the last 30 calendar days (not including today) that popped and gave back."""
    if not daily_bars:
        return Tri.UNCERTAIN
    start = today - timedelta(days=POP_LOOKBACK_DAYS)
    window = [bar for bar in daily_bars if start <= bar.day < today]
    ordered = sorted(daily_bars, key=lambda bar: bar.day)
    for bar in window:
        prev = [older for older in ordered if older.day < bar.day]
        if not prev:
            continue
        if _pop_and_fade_day(bar.high, bar.close, prev[-1].close):
            return Tri.FAIL
    return Tri.OK


def is_pop_and_fade_day(high: float, close: float, prev_close: float) -> bool:
    return _pop_and_fade_day(high, close, prev_close)


def red_to_green_exclusion(
    day_low: float | None,
    last: float | None,
    prior_close: float | None,
) -> Tri:
    """Traded strictly below prior close and last price is strictly back above it."""
    if day_low is None or last is None or prior_close is None:
        return Tri.UNCERTAIN
    base = D(prior_close)
    if base <= 0:
        return Tri.UNCERTAIN
    traded_red = D(day_low) < base
    now_green = D(last) > base
    if traded_red and now_green:
        return Tri.FAIL
    return Tri.OK


def session_day_low(
    bars: list[MinuteBar],
    today: date,
    last_price: float | None,
) -> float | None:
    lows = [bar.low for bar in bars if session_date(bar.start) == today]
    if last_price is not None:
        lows.append(last_price)
    if not lows:
        return None
    return min(lows)


def prior_day_stats(
    daily_bars: list[DailyBar], today: date
) -> tuple[float | None, float | None, float | None, float | None]:
    """prior close, prior high, close before that, prior volume."""
    prior = sorted((bar for bar in daily_bars if bar.day < today), key=lambda bar: bar.day)
    if not prior:
        return None, None, None, None
    last = prior[-1]
    earlier = prior[-2].close if len(prior) >= 2 else None
    return last.close, last.high, earlier, last.volume


def is_luld(conditions: object = None, reason_code: str = "", reason_message: str = "") -> bool:
    """True when a quote condition or halt reason is an LULD / volatility pause."""
    parts: list[str] = []
    if isinstance(conditions, str):
        parts.append(conditions)
    elif isinstance(conditions, list | tuple):
        parts.extend(str(part) for part in conditions)
    if reason_code:
        parts.append(reason_code)
    if reason_message:
        parts.append(reason_message)
    for part in parts:
        token = part.strip().upper()
        if token in LULD_TOKENS or "LULD" in token or "VOLATILITY TRADING PAUSE" in token:
            return True
    return False


def earliest_daily(daily_bars: list[DailyBar]) -> date | None:
    if not daily_bars:
        return None
    return min(bar.day for bar in daily_bars)
