"""Dry-run the full checklist on historical 1-minute bars. Does not submit orders."""

from dataclasses import replace
from datetime import date, datetime, timedelta

from squeeze_bot.checklist import evaluate
from squeeze_bot.clockutil import in_scan_window, to_et
from squeeze_bot.config import Settings
from squeeze_bot.exits import evaluate_bar
from squeeze_bot.indicators import macd_series
from squeeze_bot.logbook import LogBook
from squeeze_bot.models import ExitTracker, ManagedPosition, MinuteBar, Observation
from squeeze_bot.money import D
from squeeze_bot.risk import dollar_pnl, realized_rr


def simulate_session(
    template: Observation,
    minute_bars: list[MinuteBar],
    log: LogBook | None = None,
    luld_times: list[datetime] | None = None,
) -> list[dict]:
    """Walk a session. The forming bar's high is the intrabar last trade.

    A buy is filled at the limit when that bar trades at or through the limit.
    Otherwise the order is the 10-second cancel and the symbol is done.
    No broker object is touched.
    """
    ordered = sorted(minute_bars, key=lambda bar: bar.start)
    series = macd_series([bar.close for bar in ordered])
    events: list[dict] = []
    trades = 0
    done = False
    last_signature: tuple | None = None
    for index, bar in enumerate(ordered):
        now = bar.start + timedelta(seconds=30)
        if not template.is_trading_day or not in_scan_window(now):
            continue
        if trades or done:
            break
        closed = ordered[:index]
        halted = any(stamp <= now for stamp in (luld_times or []))
        if luld_times is None and template.luld_halted:
            halted = True
        visible_news = [item for item in template.news if item.published_at <= now]
        obs = replace(
            template,
            now=now,
            minute_bars=closed,
            last_price=bar.high,
            last_price_ts=now,
            forming_volume=bar.volume,
            trades_today=trades,
            symbol_done=done,
            news=visible_news,
            luld_halted=halted,
        )
        decision = evaluate(obs)
        signature = tuple((item.item, item.result) for item in decision.items)
        if signature != last_signature:
            last_signature = signature
            events.append(
                {
                    "kind": "decision",
                    "time": now,
                    "trade": decision.trade,
                    "grade": decision.grade,
                    "reasons": list(decision.reasons),
                }
            )
            if log is not None:
                log.write_checklist(decision.items, symbol=obs.symbol, when=now)
                if not decision.trade:
                    log.write_no_trade(decision, now, extra_reasons=["source=replay"])
        if not decision.trade or decision.entry is None or decision.stop is None:
            continue
        if D(bar.high) < decision.entry:
            done = True
            events.append({"kind": "cancel", "time": now, "reason": "unfilled_10s"})
            if log is not None:
                log.write_no_trade(decision, now, extra_reasons=["unfilled_10s", "source=replay"])
            break
        fill = decision.entry
        target = decision.target if decision.target is not None else fill
        position = ManagedPosition(
            symbol=obs.symbol,
            qty=decision.shares,
            fill_price=fill,
            stop=decision.stop,
            target=target,
            largest_green=decision.largest_green or D(0),
            filled_at=now,
            planned_entry=fill,
        )
        trades = 1
        events.append(
            {
                "kind": "fill",
                "time": now,
                "entry": fill,
                "stop": decision.stop,
                "target": target,
                "shares": decision.shares,
                "grade": decision.grade,
            }
        )
        tracker = ExitTracker()
        exit_event = None
        for offset in range(index + 1, len(ordered)):
            closed_bar = ordered[offset]
            if closed_bar.start <= position.filled_at:
                continue
            tracker, signal = evaluate_bar(
                position,
                tracker,
                closed_bar,
                series[offset - 1],
                series[offset],
            )
            if signal is None:
                continue
            exit_px = signal.limit_price
            rr = realized_rr(fill, decision.stop, target)
            pnl = dollar_pnl(fill, exit_px, decision.shares)
            exit_event = {
                "kind": "exit",
                "time": closed_bar.end,
                "reason": signal.reason,
                "exit": exit_px,
                "rr": rr,
                "pnl": pnl,
            }
            events.append(exit_event)
            if log is not None:
                log.write_trade(
                    when=closed_bar.end,
                    symbol=obs.symbol,
                    grade=decision.grade,
                    price=bar.high,
                    pct_change=decision.pct_change,
                    rel_vol=decision.rel_vol,
                    float_shares=decision.float_shares,
                    news_url=decision.news_url,
                    fill=fill,
                    stop=decision.stop,
                    target=target,
                    rr=rr,
                    exit_reason=signal.reason,
                    pnl=pnl,
                    reasons="source=replay",
                )
            break
        if exit_event is None:
            events.append({"kind": "open", "time": ordered[-1].end, "reason": "still_open"})
            if log is not None:
                log.write_trade(
                    when=ordered[-1].end,
                    symbol=obs.symbol,
                    grade=decision.grade,
                    price=bar.high,
                    pct_change=decision.pct_change,
                    rel_vol=decision.rel_vol,
                    float_shares=decision.float_shares,
                    news_url=decision.news_url,
                    fill=fill,
                    stop=decision.stop,
                    target=target,
                    rr=realized_rr(fill, decision.stop, target),
                    exit_reason="",
                    pnl=None,
                    reasons="source=replay;still_open",
                )
        break
    return events


def run_replay(symbol: str, session: date, env=None) -> list[dict]:
    """Fetch historical bars from Alpaca and simulate. Requires paper keys. No orders."""
    import os

    from squeeze_bot.broker import AlpacaBroker
    from squeeze_bot.constants import MANAGE_UNTIL
    from squeeze_bot.guard import assert_paper_environment
    from squeeze_bot.money import ET
    from squeeze_bot.state import DayState

    assert_paper_environment(env)
    settings = Settings.from_env(env)
    broker = AlpacaBroker.from_env(os.environ if env is None else env, settings)
    # Replay reads data only. It does not call submit_limit_buy or submit_limit_sell.
    # 20:00 ET is the fetch window, not an end-of-day flatten. The spec has no flatten time.
    end = datetime.combine(session, MANAGE_UNTIL, tzinfo=ET)
    template = broker.observe(
        symbol,
        end,
        halt=settings.halt_file.exists(),
        trading_day=broker.is_trading_day(session),
        state=DayState(session),
    )
    minutes = [bar for bar in template.minute_bars if to_et(bar.start).date() == session]
    if not minutes:
        raise RuntimeError(f"no 1-minute bars for {symbol} on {session.isoformat()}")
    luld_times, luld_known = broker.luld_times(symbol, minutes[0].start, end)
    template = replace(template, luld_known=luld_known, luld_halted=False)
    template = replace(
        template,
        now=minutes[0].start,
        minute_bars=[],
        trades_today=0,
        symbol_done=False,
    )
    log = LogBook(settings.checklist_path, settings.log_csv)
    return simulate_session(template, minutes, log, luld_times=luld_times)
