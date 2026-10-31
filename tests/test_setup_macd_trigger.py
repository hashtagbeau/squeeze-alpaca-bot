"""Pullback rules, intrabar trigger, MACD, front side, and ABCD."""

from datetime import timedelta

from squeeze_bot.checklist import evaluate
from squeeze_bot.indicators import bearish_cross, ema_series, macd_series, session_vwap
from squeeze_bot.models import MinuteBar
from squeeze_bot.money import D
from squeeze_bot.setup import (
    analyze_setup,
    macd_entry_ok,
    pullback_above_vwap,
    pullback_volume_ok,
    retrace_within_half,
    triggered,
)
from tests.support import at, golden_observation


def test_pullback_volume_ratio_boundary():
    assert pullback_volume_ok(D("50"), D("100")) is True
    assert pullback_volume_ok(D("50.01"), D("100")) is False
    assert pullback_volume_ok(D("1"), D("0")) is False


def test_pullback_must_hold_above_vwap():
    assert pullback_above_vwap(D("3.01"), D("3.00")) is True
    assert pullback_above_vwap(D("3.00"), D("3.00")) is False
    assert pullback_above_vwap(D("2.99"), D("3.00")) is False


def test_retrace_boundary_is_half_the_leg():
    assert retrace_within_half(D("4"), D("2"), D("3")) is True  # exactly 50%
    assert retrace_within_half(D("4"), D("2"), D("2.99")) is False
    assert retrace_within_half(D("4"), D("4"), D("4")) is None


def test_vwap_resets_at_the_session_and_matches_typical_price():
    first = MinuteBar(at(8, 0), 2, 3, 1, 2, 10)
    second = MinuteBar(at(8, 1), 3, 4, 2, 3, 10)
    later = MinuteBar(at(8, 0, day=at(8, 0).date() + timedelta(days=1)), 10, 10, 10, 10, 5)
    values = session_vwap([first, second, later])
    assert values[0] == D("2")
    assert values[1] == D("2.5")
    assert values[2] == D("10")


def test_golden_pullback_passes_volume_vwap_and_retrace():
    decision = evaluate(golden_observation())
    for name in ("pullback_volume", "pullback_vwap", "retrace_50"):
        assert _item(decision, name).result == "PASS", name


def test_trigger_is_strictly_above_the_prior_completed_bar_high():
    assert triggered(4.41, D("4.40")) is True
    assert triggered(4.40, D("4.40")) is False
    assert triggered(4.39, D("4.40")) is False
    assert triggered(None, D("4.40")) is False
    blocked = evaluate(golden_observation(last_price=4.40))
    assert _item(blocked, "trigger").result == "FAIL"
    assert blocked.trade is False
    armed = evaluate(golden_observation(last_price=4.41))
    assert _item(armed, "trigger").result == "PASS"
    assert armed.entry == D("4.42")


def test_ema_is_sma_seeded():
    values = [D(i) for i in range(1, 15)]
    series = ema_series(values, 12)
    assert series[10] is None
    assert series[11] == D("6.5")
    # Next value is 13. k = 2/13. ema = 13*(2/13) + 6.5*(11/13) = 7.5
    assert series[12] == D("7.5")


def test_macd_warmup_and_bearish_cross_definition():
    closes = [float(i) for i in range(1, 60)]
    series = macd_series(closes)
    assert all(point is None for point in series[:33])
    assert series[33] is not None
    assert bearish_cross(series, 0) is False
    # Equal then below is a cross. Staying below is not a second cross.
    from squeeze_bot.models import MacdPoint

    hand = [
        MacdPoint(D("0.2"), D("0.1")),
        MacdPoint(D("0.1"), D("0.1")),
        MacdPoint(D("0.0"), D("0.1")),
        MacdPoint(D("-0.1"), D("0.05")),
    ]
    assert bearish_cross(hand, 1) is False  # line still equal to signal
    assert bearish_cross(hand, 2) is True
    assert bearish_cross(hand, 3) is False


def test_macd_entry_requires_line_above_signal_and_above_zero():
    assert macd_entry_ok(D("0.1"), D("0.1")) is False
    assert macd_entry_ok(D("0"), D("-0.1")) is False
    assert macd_entry_ok(D("-0.01"), D("-0.02")) is False
    assert macd_entry_ok(D("0.2"), D("0.1")) is True
    decision = evaluate(golden_observation())
    assert _item(decision, "macd_entry").result == "PASS"
    assert _item(decision, "front_side").result == "PASS"


def test_front_side_ends_on_the_first_cross_after_news_and_stays_ended():
    bars = _flat_then_drop(drop_at=50, n=70)
    news = at(7, 0) + timedelta(minutes=45)
    ended = analyze_setup(bars, news)
    assert ended.front_side_live is False
    # A later rally does not revive it.
    rally = list(bars)
    t0 = rally[-1].start
    price = 3.0
    for i in range(1, 15):
        price += 0.2
        rally.append(MinuteBar(t0 + timedelta(minutes=i), price, price + 0.05, price, price, 1000))
    still = analyze_setup(rally, news)
    assert still.front_side_live is False


def test_macd_cross_before_news_does_not_end_front_side():
    bars = _flat_then_drop(drop_at=40, n=70)
    news = at(7, 0) + timedelta(minutes=55)
    view = analyze_setup(bars, news)
    assert view.front_side_live is True


def test_abcd_new_hod_after_the_first_pullback_is_done_for_the_day():
    obs = golden_observation()
    bars = list(obs.minute_bars)
    nxt = bars[-1].start + timedelta(minutes=1)
    bars.append(MinuteBar(nxt, 4.6, 5.10, 4.55, 5.00, 50_000))
    decision = evaluate(
        golden_observation(
            minute_bars=bars,
            last_price=5.11,
            now=nxt + timedelta(seconds=30),
            last_price_ts=nxt + timedelta(seconds=20),
        )
    )
    assert _item(decision, "first_pullback").result == "FAIL"
    assert _item(decision, "first_pullback").detail == "abcd"
    assert decision.trade is False
    # And it does not re-arm on a later pullback.
    later = nxt + timedelta(minutes=2)
    bars.append(MinuteBar(later, 4.8, 4.85, 4.70, 4.75, 1_000))
    again = evaluate(
        golden_observation(
            minute_bars=bars,
            last_price=4.86,
            now=later + timedelta(seconds=40),
            last_price_ts=later + timedelta(seconds=30),
        )
    )
    assert _item(again, "first_pullback").detail == "abcd"
    assert again.trade is False


def _flat_then_drop(drop_at: int, n: int) -> list[MinuteBar]:
    bars = []
    start = at(7, 0)
    for i in range(n):
        px = 5.0 if i < drop_at else 5.0 - 0.15 * (i - drop_at + 1)
        stamp = start + timedelta(minutes=i)
        bars.append(MinuteBar(stamp, px + 0.05, px + 0.05, px - 0.05, px, 1000))
    return bars


def _item(decision, name):
    for item in decision.items:
        if item.item == name:
            return item
    raise AssertionError(name)
