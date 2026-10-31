"""Software-managed single exit. First hit in spec order wins. No partials, no trail."""

from dataclasses import replace
from decimal import Decimal

from squeeze_bot.constants import DATA_LOSS_AGE_S, HALF_R, STALL_BARS, TIME_STOP_BARS
from squeeze_bot.indicators import bearish_cross
from squeeze_bot.models import ExitSignal, ExitTracker, MacdPoint, ManagedPosition, MinuteBar
from squeeze_bot.money import D
from squeeze_bot.risk import sell_limit
from squeeze_bot.setup import heavy_selling_bar


def half_r_price(fill: Decimal, stop: Decimal) -> Decimal | None:
    risk = fill - stop
    if risk <= 0:
        return None
    return fill + HALF_R * risk


def data_loss(last_bar_end, now, fetch_ok: bool) -> bool:
    """Missing feed, or the newest closed bar is older than two minutes."""
    if not fetch_ok or last_bar_end is None or now is None:
        return True
    return (now - last_bar_end).total_seconds() > DATA_LOSS_AGE_S


def evaluate_tick(position: ManagedPosition, price: float) -> ExitSignal | None:
    """Intrabar stop and target. One price cannot print both; stop is checked first."""
    px = D(price)
    if px <= position.stop:
        return ExitSignal("stop", sell_limit(px))
    if px >= position.target:
        return ExitSignal("target", position.target)
    return None


def evaluate_bar(
    position: ManagedPosition,
    tracker: ExitTracker,
    bar: MinuteBar,
    macd_prev: MacdPoint | None,
    macd_cur: MacdPoint | None,
) -> tuple[ExitTracker, ExitSignal | None]:
    """Closed-bar exits. Caller passes only bars that start after the fill timestamp.

    Priority: stop, target, +0.5R time stop, heavy red bar, 3-bar stall, MACD cross.
    Same-bar stop and target: stop wins (path inside the bar is unknowable).
    """
    low = D(bar.low)
    high = D(bar.high)
    if low <= position.stop:
        return tracker, ExitSignal("stop", sell_limit(min(low, position.stop)))
    if high >= position.target:
        return tracker, ExitSignal("target", position.target)

    level = half_r_price(position.fill_price, position.stop)
    reached = tracker.half_r_reached or (level is not None and high >= level)
    bars_seen = tracker.bars_seen + 1
    updated = replace(tracker, bars_seen=bars_seen, half_r_reached=reached, stall=tracker.stall)
    if bars_seen >= TIME_STOP_BARS and not reached:
        return updated, ExitSignal("not_half_r_3_bars", sell_limit(bar.close))

    if position.largest_green > 0 and heavy_selling_bar(bar, position.largest_green):
        return updated, ExitSignal("heavy_selling", sell_limit(bar.close))

    stall = tracker.stall + 1 if high < position.target else 0
    updated = replace(updated, stall=stall)
    if stall >= STALL_BARS:
        return updated, ExitSignal("stall_3_bars", sell_limit(bar.close))

    series = [macd_prev, macd_cur]
    if bearish_cross(series, 1):
        return updated, ExitSignal("macd_cross", sell_limit(bar.close))
    return updated, None


def flatten_limit(reference: float | Decimal) -> ExitSignal:
    return ExitSignal("restart_flatten", sell_limit(reference))


def data_loss_signal(reference: float | Decimal) -> ExitSignal:
    return ExitSignal("data_loss", sell_limit(reference))
