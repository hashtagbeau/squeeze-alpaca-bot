"""Checklist gate. Every item is PASS or FAIL with a source. Grade C never trades."""

from decimal import Decimal

from squeeze_bot.clockutil import (
    entries_allowed_clock,
    extended_hours_for_entry,
    in_scan_window,
    to_et,
)
from squeeze_bot.constants import BAR_MAX_AGE_S, QUOTE_MAX_AGE_S
from squeeze_bot.floats import resolve_float
from squeeze_bot.models import CheckItem, Decision, Observation, Tri
from squeeze_bot.money import D
from squeeze_bot.risk import buy_limit, choose_target, grade_setup, size_shares
from squeeze_bot.scanner import (
    average_daily_volume,
    change_ok,
    change_pct,
    cumulative_volume,
    first_qualifying_news,
    ipo_exclusion,
    pop_and_fade_exclusion,
    price_ok,
    red_to_green_exclusion,
    relative_volume,
    rvol_ok,
    second_day_exclusion,
    session_day_low,
)
from squeeze_bot.setup import analyze_setup, triggered


def _add(
    items: list[CheckItem],
    reasons: list[str],
    name: str,
    status: str,
    source: str,
    detail: str,
) -> None:
    if status == "pass":
        items.append(CheckItem(name, "PASS", source, detail))
        return
    kind = "uncertain" if status == "uncertain" else "hard"
    items.append(CheckItem(name, "FAIL", source, detail, kind=kind))
    reasons.append(name)


def _tri(items, reasons, name: str, value: Tri, source: str, detail: str) -> None:
    if value is Tri.OK:
        _add(items, reasons, name, "pass", source, detail)
    elif value is Tri.UNCERTAIN:
        _add(items, reasons, name, "uncertain", source, detail)
    else:
        _add(items, reasons, name, "fail", source, detail)


def evaluate(obs: Observation) -> Decision:
    """Run every gate. Trade only when every item passes and the grade is A or B."""
    items: list[CheckItem] = []
    reasons: list[str] = []
    now = obs.now
    today = to_et(now).date()

    _add(
        items,
        reasons,
        "trading_day",
        "pass" if obs.is_trading_day else "fail",
        "alpaca_calendar",
        "listed" if obs.is_trading_day else "not a trading day",
    )
    _add(
        items,
        reasons,
        "scan_window",
        "pass" if in_scan_window(now) else "fail",
        "alpaca_clock",
        now.isoformat(),
    )
    _add(
        items,
        reasons,
        "kill_switch",
        "fail" if obs.halt_file_present else "pass",
        "halt_file",
        "HALT present" if obs.halt_file_present else "absent",
    )
    _add(
        items,
        reasons,
        "symbol_not_done",
        "fail" if obs.symbol_done else "pass",
        "state",
        "done" if obs.symbol_done else "open",
    )

    if obs.last_price is None:
        _add(items, reasons, "price", "uncertain", "alpaca_last_trade", "missing")
    else:
        _add(
            items,
            reasons,
            "price",
            "pass" if price_ok(obs.last_price) else "fail",
            "alpaca_last_trade",
            str(obs.last_price),
        )

    pct = change_pct(obs.last_price, obs.prior_close)
    if obs.last_price is None or obs.prior_close is None:
        _add(items, reasons, "pct_change", "uncertain", "alpaca_prior_close", "missing")
    else:
        _add(
            items,
            reasons,
            "pct_change",
            "pass" if change_ok(obs.last_price, obs.prior_close) else "fail",
            "alpaca_prior_close",
            "" if pct is None else format(pct, "f"),
        )

    cum = cumulative_volume(obs.minute_bars, today, obs.forming_volume)
    avg = average_daily_volume(obs.daily_bars, today)
    rvol = relative_volume(cum, avg)
    if avg is None:
        _add(items, reasons, "rvol", "uncertain", "alpaca_daily_bars", "fewer than 30 sessions")
    else:
        _add(
            items,
            reasons,
            "rvol",
            "pass" if rvol_ok(cum, avg) else "fail",
            "alpaca_minute_bars",
            "" if rvol is None else format(rvol, "f"),
        )

    resolved = resolve_float(
        obs.finviz_float, obs.sec_shares, obs.csv_float, obs.csv_row_present
    )
    if resolved.status in {"missing", "disagree"}:
        _add(items, reasons, "float_sources", "uncertain", resolved.source, resolved.status)
        _add(items, reasons, "float_under_20m", "uncertain", resolved.source, resolved.status)
    else:
        _add(items, reasons, "float_sources", "pass", resolved.source, resolved.status)
        under = resolved.value is not None and D(resolved.value) < D(20_000_000)
        _add(
            items,
            reasons,
            "float_under_20m",
            "pass" if under else "fail",
            resolved.source,
            "" if resolved.value is None else str(resolved.value),
        )
    if not obs.csv_row_present:
        _add(items, reasons, "float_csv", "pass", "float.csv", "row missing")
    elif resolved.status == "csv_disagree" or resolved.value is None or obs.csv_float is None:
        _add(items, reasons, "float_csv", "fail", "float.csv", "disagree >20% or unusable")
    else:
        _add(items, reasons, "float_csv", "pass", "float.csv", "within 20%")

    article = None
    if obs.news_cutoff is None:
        _add(items, reasons, "news", "uncertain", "alpaca_news", "no cutoff")
    else:
        article = first_qualifying_news(obs.news, obs.news_cutoff)
        _add(
            items,
            reasons,
            "news",
            "pass" if article is not None else "fail",
            "alpaca_news",
            article.url if article else "none",
        )

    _tri(
        items,
        reasons,
        "ipo",
        ipo_exclusion(obs.first_trade_date, today, obs.ipo_history_truncated),
        "alpaca_daily_bars",
        "" if obs.first_trade_date is None else obs.first_trade_date.isoformat(),
    )
    _tri(
        items,
        reasons,
        "second_day",
        second_day_exclusion(obs.prior_close, obs.prior_prior_close, obs.prior_high),
        "alpaca_daily_bars",
        "prior session",
    )
    _tri(
        items,
        reasons,
        "pop_and_fade",
        pop_and_fade_exclusion(obs.daily_bars, today),
        "alpaca_daily_bars",
        "30 calendar days",
    )
    if not obs.luld_known:
        _add(items, reasons, "luld_halt", "uncertain", "alpaca_quotes", "unknown")
    else:
        _add(
            items,
            reasons,
            "luld_halt",
            "fail" if obs.luld_halted else "pass",
            "alpaca_quotes",
            "halted" if obs.luld_halted else "clear",
        )
    day_low = session_day_low(obs.minute_bars, today, obs.last_price)
    _tri(
        items,
        reasons,
        "red_to_green",
        red_to_green_exclusion(day_low, obs.last_price, obs.prior_close),
        "alpaca_minute_bars",
        "" if day_low is None else str(day_low),
    )

    news_time = article.published_at if article is not None else None
    setup = analyze_setup(obs.minute_bars, news_time)
    if setup.front_side_uncertain:
        _add(items, reasons, "front_side", "uncertain", "macd_12_26_9", "warmup")
    else:
        _add(
            items,
            reasons,
            "front_side",
            "pass" if setup.front_side_live else "fail",
            "macd_12_26_9",
            "live" if setup.front_side_live else "ended",
        )
    if setup.phase == "done" and setup.done_reason == "abcd":
        _add(items, reasons, "first_pullback", "fail", "bars", "abcd")
    elif setup.phase == "pullback":
        _add(items, reasons, "first_pullback", "pass", "bars", "pullback")
    elif news_time is None:
        _add(items, reasons, "first_pullback", "fail", "bars", "no news")
    else:
        _add(items, reasons, "first_pullback", "fail", "bars", setup.phase)

    _flag(items, reasons, "pullback_volume", setup.volume_ok, setup.volume_uncertain, "bars")
    _flag(items, reasons, "pullback_vwap", setup.vwap_ok, setup.vwap_uncertain, "vwap")
    _flag(items, reasons, "retrace_50", setup.retrace_ok, setup.retrace_uncertain, "bars")
    if setup.heavy is None:
        _add(items, reasons, "heavy_selling", "uncertain", "bars", "no green leg bar")
    elif setup.phase != "pullback":
        _add(items, reasons, "heavy_selling", "fail", "bars", "no pullback")
    else:
        _add(
            items,
            reasons,
            "heavy_selling",
            "fail" if setup.heavy else "pass",
            "bars",
            "red bar above largest green leg volume" if setup.heavy else "clear",
        )
    _flag(items, reasons, "macd_entry", setup.macd_ok, setup.macd_uncertain, "macd_12_26_9")

    _add(
        items,
        reasons,
        "trigger",
        "pass" if triggered(obs.last_price, setup.prior_bar_high) else "fail",
        "alpaca_last_trade",
        "" if setup.prior_bar_high is None else format(setup.prior_bar_high, "f"),
    )
    _add(
        items,
        reasons,
        "entry_cutoff",
        "pass" if entries_allowed_clock(now) else "fail",
        "alpaca_clock",
        "before 10:00 ET" if entries_allowed_clock(now) else ">= 10:00 ET",
    )
    _add(
        items,
        reasons,
        "one_trade_per_day",
        "pass" if obs.trades_today == 0 else "fail",
        "state",
        str(obs.trades_today),
    )

    entry: Decimal | None = None
    target: Decimal | None = None
    rr: Decimal | None = None
    if setup.prior_bar_high is not None and setup.stop is not None and setup.hod is not None:
        entry = buy_limit(setup.prior_bar_high)
        chosen = choose_target(entry, setup.stop, setup.hod)
        if chosen is not None:
            target, rr = chosen
    if entry is None or setup.stop is None:
        _add(items, reasons, "reward_risk", "fail", "computed", "no setup")
    elif rr is None:
        _add(items, reasons, "reward_risk", "fail", "computed", "no level pays >= 2R")
    else:
        _add(
            items,
            reasons,
            "reward_risk",
            "pass" if rr >= D(2) else "fail",
            "computed",
            format(rr, "f"),
        )

    quote_age = None
    if obs.last_price_ts is not None:
        quote_age = (now - obs.last_price_ts).total_seconds()
    if quote_age is None:
        _add(items, reasons, "freshness_quote", "uncertain", "alpaca_last_trade", "missing ts")
    else:
        _add(
            items,
            reasons,
            "freshness_quote",
            "pass" if quote_age <= QUOTE_MAX_AGE_S else "fail",
            "alpaca_last_trade",
            f"{quote_age:.3f}s",
        )
    bar_age = None
    if setup.last_bar_end is not None:
        bar_age = (now - setup.last_bar_end).total_seconds()
    if bar_age is None:
        _add(items, reasons, "freshness_bars", "uncertain", "alpaca_minute_bars", "missing")
    else:
        _add(
            items,
            reasons,
            "freshness_bars",
            "pass" if bar_age <= BAR_MAX_AGE_S else "fail",
            "alpaca_minute_bars",
            f"{bar_age:.3f}s",
        )

    shares = 0
    if entry is None or setup.stop is None:
        _add(items, reasons, "sizing", "fail", "alpaca_account", "no setup")
    elif obs.equity is None or obs.settled_cash is None:
        _add(items, reasons, "sizing", "uncertain", "alpaca_account", "missing equity or cash")
    else:
        shares = size_shares(obs.equity, obs.settled_cash, entry, setup.stop)
        _add(
            items,
            reasons,
            "sizing",
            "pass" if shares >= 1 else "fail",
            "alpaca_account",
            str(shares),
        )

    uncertain = any(item.result == "FAIL" and item.kind == "uncertain" for item in items)
    hard_fail = any(item.result == "FAIL" and item.kind == "hard" for item in items)
    grade = grade_setup(
        float_shares=resolved.value,
        rr=rr,
        prior_day_volume=obs.prior_day_volume,
        rvol=rvol,
        uncertain=uncertain,
        hard_fail=hard_fail,
    )
    _add(
        items,
        reasons,
        "grade",
        "pass" if grade in {"A", "B"} else ("uncertain" if grade == "C" else "fail"),
        "computed",
        grade or "ungraded",
    )

    trade = all(item.result == "PASS" for item in items) and grade in {"A", "B"} and shares >= 1
    return Decision(
        symbol=obs.symbol,
        trade=trade,
        grade=grade,
        reasons=reasons,
        items=items,
        price=obs.last_price,
        pct_change=pct,
        rel_vol=rvol,
        float_shares=resolved.value,
        news_url="" if article is None else article.url,
        entry=entry,
        stop=setup.stop,
        target=target,
        rr=rr,
        shares=shares,
        extended_hours=extended_hours_for_entry(now),
        row_type="trade" if trade else "no-trade",
        largest_green=setup.largest_green,
    )


def _flag(
    items: list[CheckItem],
    reasons: list[str],
    name: str,
    ok: bool,
    uncertain: bool,
    source: str,
) -> None:
    if uncertain:
        _add(items, reasons, name, "uncertain", source, "uncertain")
    else:
        _add(items, reasons, name, "pass" if ok else "fail", source, "ok" if ok else "fail")
