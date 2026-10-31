"""Synthetic bars and a golden observation that satisfies every gate."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from squeeze_bot.models import DailyBar, MinuteBar, NewsItem, Observation

ET = ZoneInfo("America/New_York")
DAY = date(2026, 10, 28)  # Wednesday


def at(hour: int, minute: int, second: int = 0, day: date = DAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=ET)


def minute(
    hour: int,
    minute_: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    day: date = DAY,
) -> MinuteBar:
    return MinuteBar(at(hour, minute_, day=day), open_, high, low, close, volume)


def dailies(today: date = DAY, volume: float = 1000, n: int = 40) -> list[DailyBar]:
    """Quiet prior sessions so rvol, IPO, second-day, and pop-and-fade all pass."""
    rows = []
    for offset in range(n, 0, -1):
        day = today - timedelta(days=offset)
        rows.append(DailyBar(day, 1.9, 2.05, 1.85, 2.0, volume))
    return rows


def warmup_and_leg() -> tuple[list[MinuteBar], datetime]:
    """50 rising warmup bars, then a squeeze leg and one shallow pullback.

    The pullback close stays near the highs so MACD does not cross, while the
    bar's high sits far enough under the HOD for a >= 3R limit entry.
    """
    bars: list[MinuteBar] = []
    start = at(7, 0)
    price = 2.10
    for i in range(50):
        stamp = start + timedelta(minutes=i)
        price = round(price + 0.01, 2)
        bars.append(
            MinuteBar(stamp, price, round(price + 0.02, 2), price, round(price + 0.01, 2), 100)
        )
    # Leg begins at 07:50. News is 07:50.
    leg_start = at(7, 50)
    leg = [
        (3.00, 3.20, 2.90, 3.10, 100_000),
        (3.10, 3.40, 3.05, 3.30, 100_000),
        (3.30, 3.70, 3.25, 3.60, 100_000),
        (3.60, 4.00, 3.55, 3.90, 100_000),
        (3.90, 4.30, 3.85, 4.20, 100_000),
        (4.20, 4.60, 4.15, 4.50, 100_000),
        (4.50, 4.90, 4.45, 4.80, 100_000),
    ]
    for i, (opn, high, low, cls, vol) in enumerate(leg):
        bars.append(MinuteBar(leg_start + timedelta(minutes=i), opn, high, low, cls, vol))
    # Pullback at 07:57. High 4.40 is the trigger level; low 4.35 is the stop.
    # HOD is 4.90. Limit will be 4.42. Risk 0.07. Reward to HOD 0.48. R:R ~ 6.8.
    pull_at = leg_start + timedelta(minutes=len(leg))
    bars.append(MinuteBar(pull_at, 4.55, 4.40, 4.35, 4.38, 1_000))
    news_at = leg_start
    return bars, news_at


def golden_observation(**overrides) -> Observation:
    bars, news_at = warmup_and_leg()
    now = bars[-1].end + timedelta(seconds=10)
    prior_close = 2.0
    obs = Observation(
        symbol="SQZ",
        now=now,
        is_trading_day=True,
        halt_file_present=False,
        trades_today=0,
        symbol_done=False,
        last_price=4.41,
        last_price_ts=now - timedelta(seconds=1),
        prior_close=prior_close,
        prior_high=2.05,
        prior_prior_close=1.95,
        prior_day_volume=50_000,
        daily_bars=dailies(),
        minute_bars=bars,
        forming_volume=0,
        finviz_float=4_000_000,
        sec_shares=4_200_000,
        csv_float=None,
        csv_row_present=False,
        news=[NewsItem(news_at, "https://news.example/sqz", "contract")],
        news_cutoff=at(16, 0, day=DAY - timedelta(days=1)),
        luld_halted=False,
        luld_known=True,
        first_trade_date=DAY - timedelta(days=200),
        ipo_history_truncated=False,
        equity=2_000,
        settled_cash=2_000,
    )
    for key, value in overrides.items():
        setattr(obs, key, value)
    return obs
