"""Per-symbol squeeze state machine on closed 1-minute bars."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from squeeze_bot.constants import MAX_RETRACE, PULLBACK_VOL_RATIO
from squeeze_bot.indicators import bearish_cross, macd_series, session_vwap
from squeeze_bot.models import MinuteBar
from squeeze_bot.money import D


@dataclass
class SetupView:
    phase: str = "none"
    done_reason: str = ""
    front_side_live: bool = False
    front_side_uncertain: bool = False
    hod: Decimal | None = None
    leg_low: Decimal | None = None
    pullback_low: Decimal | None = None
    stop: Decimal | None = None
    prior_bar_high: Decimal | None = None
    last_bar_end: datetime | None = None
    vwap_ref: Decimal | None = None
    retrace: Decimal | None = None
    avg_leg_vol: Decimal | None = None
    avg_pb_vol: Decimal | None = None
    largest_green: Decimal | None = None
    volume_ok: bool = False
    volume_uncertain: bool = False
    vwap_ok: bool = False
    vwap_uncertain: bool = False
    retrace_ok: bool = False
    retrace_uncertain: bool = False
    heavy: bool | None = False
    macd_ok: bool = False
    macd_uncertain: bool = False
    macd_line: Decimal | None = None
    macd_signal: Decimal | None = None


def _last_bar_fields(view: SetupView, bars: list[MinuteBar]) -> SetupView:
    if bars:
        view.prior_bar_high = D(bars[-1].high)
        view.last_bar_end = bars[-1].end
    return view


def analyze_setup(bars: list[MinuteBar], news_time: datetime | None) -> SetupView:
    """Leg is the low after news through the HOD. First pullback only.

    A new HOD after that pullback is ABCD and the symbol is done for the day.
    Front side ends at the first MACD line-below-signal cross on a bar that
    starts at or after news_time, and stays ended.
    """
    ordered = sorted(bars, key=lambda bar: bar.start)
    view = SetupView()
    if not ordered:
        view.front_side_uncertain = news_time is not None
        return view
    view = _last_bar_fields(view, ordered)
    if news_time is None:
        view.phase = "none"
        view.front_side_live = False
        return view

    macd = macd_series([bar.close for bar in ordered])
    vwaps = session_vwap(ordered)
    view.front_side_live = True
    seen_macd = False
    for i, bar in enumerate(ordered):
        if bar.start < news_time:
            continue
        if macd[i] is not None:
            seen_macd = True
        if bearish_cross(macd, i):
            view.front_side_live = False
            break
    if not seen_macd:
        view.front_side_uncertain = True
        view.front_side_live = False

    post = [i for i, bar in enumerate(ordered) if bar.start >= news_time]
    if not post:
        view.phase = "building"
        return view

    hod: Decimal | None = None
    hod_i: int | None = None
    phase = "building"
    pullback_i: int | None = None
    for i in post:
        bar = ordered[i]
        high = D(bar.high)
        if hod is None or high > hod:
            if phase == "pullback":
                view.phase = "done"
                view.done_reason = "abcd"
                view.hod = high
                return view
            hod = high
            hod_i = i
            phase = "building"
            pullback_i = None
        elif hod_i is not None and i > hod_i and phase == "building":
            phase = "pullback"
            pullback_i = i

    view.phase = phase
    view.hod = hod
    if phase != "pullback" or hod_i is None or pullback_i is None or hod is None:
        return view

    window = [i for i in post if i <= hod_i]
    leg_low_i = min(window, key=lambda i: (D(ordered[i].low), i))
    leg_idx = [i for i in window if i >= leg_low_i]
    pb_idx = [i for i in post if i >= pullback_i]
    leg_bars = [ordered[i] for i in leg_idx]
    pb_bars = [ordered[i] for i in pb_idx]
    view.leg_low = D(ordered[leg_low_i].low)
    view.pullback_low = min(D(bar.low) for bar in pb_bars)
    view.stop = view.pullback_low
    low_i = min(pb_idx, key=lambda i: (D(ordered[i].low), i))
    view.vwap_ref = vwaps[low_i]
    view.prior_bar_high = D(ordered[pb_idx[-1]].high)

    avg_leg = sum((D(bar.volume) for bar in leg_bars), D(0)) / D(len(leg_bars))
    avg_pb = sum((D(bar.volume) for bar in pb_bars), D(0)) / D(len(pb_bars))
    view.avg_leg_vol = avg_leg
    view.avg_pb_vol = avg_pb
    if avg_leg <= 0:
        view.volume_uncertain = True
        view.volume_ok = False
    else:
        view.volume_ok = pullback_volume_ok(avg_pb, avg_leg)

    if view.vwap_ref is None:
        view.vwap_uncertain = True
        view.vwap_ok = False
    else:
        view.vwap_ok = pullback_above_vwap(view.pullback_low, view.vwap_ref)

    verdict = retrace_within_half(hod, view.leg_low, view.pullback_low)
    if verdict is None:
        view.retrace_uncertain = True
        view.retrace_ok = False
    else:
        span = hod - view.leg_low
        view.retrace = (hod - view.pullback_low) / span
        view.retrace_ok = verdict

    greens = [D(bar.volume) for bar in leg_bars if D(bar.close) > D(bar.open)]
    if not greens:
        view.heavy = None
        view.largest_green = None
    else:
        view.largest_green = max(greens)
        view.heavy = any(
            D(bar.close) < D(bar.open) and D(bar.volume) > view.largest_green for bar in pb_bars
        )

    last = macd[pb_idx[-1]]
    if last is None:
        view.macd_uncertain = True
        view.macd_ok = False
    else:
        view.macd_line = last.line
        view.macd_signal = last.signal
        view.macd_ok = macd_entry_ok(last.line, last.signal)
    return view


def macd_entry_ok(line: Decimal, signal: Decimal) -> bool:
    """MACD line strictly above the signal and strictly above zero."""
    return line > signal and line > 0


def pullback_volume_ok(avg_pullback: Decimal, avg_leg: Decimal) -> bool:
    return avg_leg > 0 and avg_pullback <= PULLBACK_VOL_RATIO * avg_leg


def pullback_above_vwap(pullback_low: Decimal, vwap: Decimal) -> bool:
    return pullback_low > vwap


def retrace_within_half(hod: Decimal, leg_low: Decimal, pullback_low: Decimal) -> bool | None:
    span = hod - leg_low
    if span <= 0:
        return None
    return (hod - pullback_low) / span <= MAX_RETRACE


def heavy_selling_bar(bar: MinuteBar, largest_green: Decimal | None) -> bool:
    if largest_green is None:
        return False
    return D(bar.close) < D(bar.open) and D(bar.volume) > largest_green


def triggered(last_trade: float | None, prior_high: Decimal | None) -> bool:
    """Strict break of the prior completed bar's high. Equality does not trigger."""
    if last_trade is None or prior_high is None:
        return False
    return D(last_trade) > prior_high
