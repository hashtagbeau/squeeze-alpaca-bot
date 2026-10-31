"""Scanner filter boundaries and every exclusion."""

from datetime import date, timedelta
from decimal import Decimal

from squeeze_bot.checklist import evaluate
from squeeze_bot.clockutil import (
    entries_allowed_clock,
    in_scan_window,
    is_trading_day,
    prior_session_close,
    scan_due,
)
from squeeze_bot.models import DailyBar, MinuteBar, NewsItem, Tri
from squeeze_bot.money import ET
from squeeze_bot.scanner import (
    average_daily_volume,
    change_ok,
    change_pct,
    first_qualifying_news,
    ipo_exclusion,
    is_luld,
    is_pop_and_fade_day,
    pop_and_fade_exclusion,
    price_ok,
    red_to_green_exclusion,
    relative_volume,
    rvol_ok,
    second_day_exclusion,
)
from tests.support import DAY, at, dailies, golden_observation, minute

# --- price <= 9.00 ---


def test_price_filter_at_9_passes():
    assert price_ok(9.00) is True
    decision = evaluate(golden_observation(last_price=9.00))
    item = _item(decision, "price")
    assert item.result == "PASS"


def test_price_filter_above_9_fails():
    assert price_ok(9.01) is False
    decision = evaluate(golden_observation(last_price=9.01))
    assert decision.trade is False
    assert _item(decision, "price").result == "FAIL"


def test_price_filter_below_9_passes():
    assert price_ok(8.99) is True


# --- day change >= +50% ---


def test_day_change_at_50_percent_passes():
    assert change_ok(3.0, 2.0) is True
    assert change_pct(3.0, 2.0) == Decimal("0.5")


def test_day_change_below_50_percent_fails():
    assert change_ok(2.99, 2.0) is False
    decision = evaluate(golden_observation(last_price=2.50, prior_close=2.0))
    assert _item(decision, "pct_change").result == "FAIL"
    assert decision.trade is False


def test_day_change_missing_prior_close_is_uncertain():
    decision = evaluate(golden_observation(prior_close=None))
    assert _item(decision, "pct_change").kind == "uncertain"
    assert decision.grade == "C"
    assert decision.trade is False


# --- rvol >= 100 ---


def test_rvol_at_100_passes_and_below_fails():
    assert rvol_ok(Decimal("10000"), Decimal("100")) is True
    assert relative_volume(Decimal("10000"), Decimal("100")) == Decimal("100")
    assert rvol_ok(Decimal("9999"), Decimal("100")) is False


def test_rvol_undefined_when_average_is_zero_or_short_history():
    assert relative_volume(Decimal("10"), Decimal("0")) is None
    assert rvol_ok(Decimal("10"), None) is False
    short = dailies()[:29]
    assert average_daily_volume(short, DAY) is None
    decision = evaluate(golden_observation(daily_bars=short))
    assert _item(decision, "rvol").kind == "uncertain"
    assert decision.trade is False


def test_rvol_uses_exactly_30_prior_sessions_excluding_today():
    rows = dailies(volume=1000, n=40)
    rows.append(DailyBar(DAY, 1, 1, 1, 1, 9_999_999))
    avg = average_daily_volume(rows, DAY)
    assert avg == Decimal("1000")


def test_rvol_gate_fails_when_average_dwarfs_today():
    fat = dailies(volume=10_000_000, n=30)
    decision = evaluate(golden_observation(daily_bars=fat))
    assert _item(decision, "rvol").result == "FAIL"
    assert decision.trade is False


# --- float < 20M (source rules live in test_float_and_freshness) ---


def test_scanner_window_boundaries():
    assert in_scan_window(at(7, 30, 0)) is True
    assert in_scan_window(at(7, 29, 59)) is False
    assert in_scan_window(at(9, 59, 59)) is True
    assert in_scan_window(at(10, 0, 0)) is False
    saturday = at(8, 0, day=date(2026, 10, 31))
    assert in_scan_window(saturday) is False


def test_entries_stop_at_10_but_the_clock_helper_is_strict():
    assert entries_allowed_clock(at(9, 59, 59)) is True
    assert entries_allowed_clock(at(10, 0, 0)) is False


def test_scan_due_every_60_seconds():
    start = at(8, 0)
    assert scan_due(None, start) is True
    assert scan_due(start, start + timedelta(seconds=59)) is False
    assert scan_due(start, start + timedelta(seconds=60)) is True


def test_holiday_is_not_a_trading_day_and_blocks_the_gate():
    listed = {date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 29)}
    assert is_trading_day(DAY, listed) is False
    assert is_trading_day(date(2026, 10, 27), listed) is True
    decision = evaluate(golden_observation(is_trading_day=False))
    assert _item(decision, "trading_day").result == "FAIL"
    assert decision.trade is False


def test_prior_close_cutoff_uses_previous_session_not_a_holiday():
    # Wednesday is a holiday; previous session is Tuesday 16:00 ET.
    days = [date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 29)]
    cutoff = prior_session_close(days, DAY)
    assert cutoff == at(16, 0, day=date(2026, 10, 27))
    assert cutoff.tzinfo == ET


def test_weekend_blocks_even_if_the_calendar_is_wrong():
    saturday = at(8, 0, day=date(2026, 10, 31))
    decision = evaluate(golden_observation(now=saturday, is_trading_day=True))
    assert _item(decision, "scan_window").result == "FAIL"
    assert decision.trade is False


def test_news_must_print_after_the_prior_1600_close_and_carry_a_url():
    cutoff = at(16, 0, day=DAY - timedelta(days=1))
    assert first_qualifying_news([], cutoff) is None
    assert first_qualifying_news([NewsItem(cutoff, "https://x/exact")], cutoff) is None
    later = NewsItem(cutoff + timedelta(seconds=1), "https://x/later")
    assert first_qualifying_news([later], cutoff).url == "https://x/later"
    assert first_qualifying_news([NewsItem(later.published_at, "")], cutoff) is None
    missing = evaluate(golden_observation(news=[]))
    assert _item(missing, "news").result == "FAIL"
    assert missing.trade is False


# --- IPO < 90 days ---


def test_ipo_under_90_days_fails_and_day_90_passes():
    assert ipo_exclusion(DAY - timedelta(days=89), DAY, False) is Tri.FAIL
    assert ipo_exclusion(DAY - timedelta(days=90), DAY, False) is Tri.OK
    assert ipo_exclusion(None, DAY, False) is Tri.UNCERTAIN
    decision = evaluate(golden_observation(first_trade_date=DAY - timedelta(days=89)))
    assert _item(decision, "ipo").result == "FAIL"
    assert decision.trade is False


def test_ipo_truncated_history_older_than_90_days_passes():
    old = DAY - timedelta(days=180)
    assert ipo_exclusion(old, DAY, True) is Tri.OK


# --- second day ---


def test_second_day_close_to_close_at_50_percent_fails():
    assert second_day_exclusion(3.0, 2.0, 2.2) is Tri.FAIL
    assert second_day_exclusion(2.99, 2.0, 2.2) is Tri.OK


def test_second_day_hod_at_50_percent_fails_even_if_close_does_not():
    # Close-to-close is +10%, but the prior high was +50% versus the prior close.
    assert second_day_exclusion(2.2, 2.0, 3.0) is Tri.FAIL
    decision = evaluate(
        golden_observation(prior_close=2.2, prior_prior_close=2.0, prior_high=3.0)
    )
    assert _item(decision, "second_day").result == "FAIL"
    assert decision.trade is False


def test_second_day_missing_reference_is_uncertain():
    assert second_day_exclusion(2.0, None, 2.1) is Tri.UNCERTAIN
    decision = evaluate(golden_observation(prior_prior_close=None))
    assert _item(decision, "second_day").kind == "uncertain"
    assert decision.grade == "C"


# --- pop and fade, 30 calendar days ---


def test_pop_and_fade_boundaries():
    assert is_pop_and_fade_day(3.0, 2.5, 2.0) is True  # +50% and gave back exactly 50%
    assert is_pop_and_fade_day(3.0, 2.51, 2.0) is False  # gave back just under 50%
    assert is_pop_and_fade_day(2.99, 2.0, 2.0) is False  # gain under 50%


def test_pop_and_fade_includes_day_30_and_excludes_day_31():
    fade = DailyBar(DAY - timedelta(days=30), 2, 3, 2, 2.5, 1000)
    old = DailyBar(DAY - timedelta(days=31), 2, 4, 2, 2.2, 1000)
    base = [DailyBar(DAY - timedelta(days=40), 2, 2, 2, 2.0, 1000)]
    assert pop_and_fade_exclusion(base + [fade], DAY) is Tri.FAIL
    assert pop_and_fade_exclusion(base + [old], DAY) is Tri.OK
    assert pop_and_fade_exclusion([], DAY) is Tri.UNCERTAIN
    decision = evaluate(golden_observation(daily_bars=base + [fade] + dailies()[:5]))
    # history is short, so rvol may be uncertain; the exclusion itself must fail.
    assert _item(decision, "pop_and_fade").result == "FAIL"


# --- LULD ---


def test_luld_tokens_match_only_volatility_pauses():
    assert is_luld("M") is True
    assert is_luld(["@", "T5"]) is True
    assert is_luld(reason_code="LULD") is True
    assert is_luld(reason_message="Volatility Trading Pause") is True
    assert is_luld(["@"]) is False
    assert is_luld(reason_code="T2", reason_message="News Released") is False


def test_luld_halt_today_fails_the_gate_and_a_clear_day_passes():
    halted = evaluate(golden_observation(luld_halted=True))
    assert _item(halted, "luld_halt").result == "FAIL"
    assert halted.trade is False
    clear = evaluate(golden_observation(luld_halted=False))
    assert _item(clear, "luld_halt").result == "PASS"


def test_luld_unknown_is_uncertain_no_trade():
    decision = evaluate(golden_observation(luld_known=False))
    assert _item(decision, "luld_halt").kind == "uncertain"
    assert decision.grade == "C"
    assert decision.trade is False


# --- red to green ---


def test_red_to_green_boundaries():
    assert red_to_green_exclusion(1.99, 2.01, 2.0) is Tri.FAIL
    assert red_to_green_exclusion(2.0, 2.5, 2.0) is Tri.OK  # never traded strictly red
    assert red_to_green_exclusion(1.5, 2.0, 2.0) is Tri.OK  # back to unchanged, not green
    assert red_to_green_exclusion(1.5, 1.8, 2.0) is Tri.OK  # still red
    assert red_to_green_exclusion(None, 2.5, 2.0) is Tri.UNCERTAIN


def test_red_to_green_today_blocks_the_gate():
    decision = evaluate(golden_observation(prior_close=2.50))
    assert _item(decision, "red_to_green").result == "FAIL"
    assert decision.trade is False


# --- light volume pop then heavy selling, pre-entry ---


def test_heavy_selling_volume_equal_to_largest_green_does_not_exclude():
    from squeeze_bot.setup import heavy_selling_bar

    red = minute(8, 0, 4.5, 4.5, 4.2, 4.3, 100_000)
    assert heavy_selling_bar(red, Decimal("100000")) is False
    assert heavy_selling_bar(red, Decimal("99999")) is True
    green = minute(8, 1, 4.2, 4.6, 4.2, 4.6, 500_000)
    assert heavy_selling_bar(green, Decimal("1")) is False


def test_red_bar_after_hod_with_heavier_volume_excludes():
    obs = golden_observation()
    bars = list(obs.minute_bars)
    # Replace the pullback with a red bar larger than every green leg bar.
    bars[-1] = MinuteBar(bars[-1].start, 4.55, 4.40, 4.35, 4.30, 250_000)
    decision = evaluate(golden_observation(minute_bars=bars, last_price=4.41))
    assert _item(decision, "heavy_selling").result == "FAIL"
    assert decision.trade is False


def _item(decision, name):
    for item in decision.items:
        if item.item == name:
            return item
    raise AssertionError(name)
