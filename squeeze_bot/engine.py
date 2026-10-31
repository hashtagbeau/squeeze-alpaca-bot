"""One-second loop body: exits keep running after 10:00; entries do not."""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from squeeze_bot.checklist import evaluate
from squeeze_bot.clockutil import in_scan_window, scan_due, to_et
from squeeze_bot.config import Settings
from squeeze_bot.exits import data_loss, data_loss_signal, evaluate_bar, evaluate_tick
from squeeze_bot.indicators import macd_series
from squeeze_bot.logbook import LogBook
from squeeze_bot.models import ManagedPosition, MinuteBar, Observation
from squeeze_bot.money import D
from squeeze_bot.orders import FillResult, OrderBroker, execute_entry
from squeeze_bot.risk import dollar_pnl, realized_rr, sell_limit
from squeeze_bot.state import DayState, save_state


class _PositionView:
    def __init__(self, symbol: str, qty: int, avg_entry: float, current: float) -> None:
        self.symbol = symbol
        self.qty = qty
        self.avg_entry = avg_entry
        self.current = current


class PaperSession:
    def __init__(
        self,
        broker: OrderBroker,
        log: LogBook,
        settings: Settings,
        state: DayState,
        *,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        self.broker = broker
        self.log = log
        self.settings = settings
        self.state = state
        self.clock = clock
        self.sleep = sleep
        self.position: ManagedPosition | None = None
        self.last_scan: datetime | None = None
        self.last_decision_meta: dict[str, object] = {}

    def startup(self, now: datetime) -> None:
        """Any open equity position is flattened. A lost state file still blocks a second trade."""
        positions = self.broker.list_positions()
        flattened = False
        for pos in positions:
            if pos.qty == 0:
                continue
            flattened = True
            limit = sell_limit(pos.current)
            order = self.broker.submit_limit_sell(pos.symbol, abs(pos.qty), limit, True)
            current = self.broker.get_order(order.id)
            fill = current.filled_avg_price if current.filled_avg_price is not None else pos.current
            pnl = dollar_pnl(pos.avg_entry, fill, abs(pos.qty))
            self.log.write_trade(
                when=now,
                symbol=pos.symbol,
                grade="",
                price=pos.current,
                pct_change=None,
                rel_vol=None,
                float_shares=None,
                news_url="",
                fill=pos.avg_entry,
                stop=None,
                target=None,
                rr=None,
                exit_reason="restart_flatten",
                pnl=pnl,
                reasons="restart_flatten",
            )
            self.state.mark_done(pos.symbol)
        if flattened:
            self.state.trades = max(self.state.trades, 1)
            save_state(self.settings.state_path, self.state)

    def run_once(
        self,
        now: datetime,
        observations: list[Observation],
        *,
        bars: list[MinuteBar] | None = None,
        last_price: float | None = None,
        fetch_ok: bool = True,
        trading_day: bool = True,
    ) -> None:
        if self.position is not None:
            self._manage(now, bars or [], last_price, fetch_ok)
        if self.position is not None:
            return
        if self.state.trades > 0:
            return
        if not trading_day or not in_scan_window(now):
            return
        if not scan_due(self.last_scan, now):
            return
        self.last_scan = now
        for obs in observations:
            if self.state.trades > 0:
                return
            if self.state.is_done(obs.symbol):
                obs.symbol_done = True
            obs.trades_today = self.state.trades
            decision = evaluate(obs)
            self.log.write_checklist(decision.items, symbol=obs.symbol, when=now)
            if not decision.trade:
                self.log.write_no_trade(decision, now)
                continue
            result = execute_entry(
                self.broker,
                symbol=obs.symbol,
                qty=decision.shares,
                limit_price=decision.entry if decision.entry is not None else D(0),
                extended_hours=decision.extended_hours,
                clock=self.clock,
                sleep=self.sleep,
            )
            self._apply_entry(obs, decision, result, now)
            if self.position is not None:
                return

    def _apply_entry(self, obs: Observation, decision, result: FillResult, now: datetime) -> None:
        self.state.mark_done(obs.symbol)
        if not result.counts_as_trade:
            self.log.write_no_trade(decision, now, extra_reasons=["unfilled_10s"])
            save_state(self.settings.state_path, self.state)
            return
        self.state.trades += 1
        fill = D(result.filled_avg_price if result.filled_avg_price is not None else decision.entry)
        stop = decision.stop if decision.stop is not None else fill
        target = decision.target if decision.target is not None else fill
        largest = decision.largest_green if decision.largest_green is not None else D(0)
        self.position = ManagedPosition(
            symbol=obs.symbol,
            qty=result.filled_qty,
            fill_price=fill,
            stop=stop,
            target=target,
            largest_green=largest,
            filled_at=now,
            planned_entry=decision.entry if decision.entry is not None else fill,
        )
        self.last_decision_meta = {
            "grade": decision.grade,
            "price": decision.price,
            "pct_change": decision.pct_change,
            "rel_vol": decision.rel_vol,
            "float_shares": decision.float_shares,
            "news_url": decision.news_url,
            "reasons": "source=entry",
        }
        save_state(self.settings.state_path, self.state)

    def _manage(
        self,
        now: datetime,
        bars: list[MinuteBar],
        last_price: float | None,
        fetch_ok: bool,
    ) -> None:
        pos = self.position
        if pos is None:
            return
        last_end = bars[-1].end if bars else None
        if data_loss(last_end, now, fetch_ok):
            ref = last_price if last_price is not None else pos.fill_price
            self._complete(data_loss_signal(ref).reason, data_loss_signal(ref).limit_price, now)
            return
        ordered = sorted(bars, key=lambda bar: bar.start)
        series = macd_series([bar.close for bar in ordered])
        for index, bar in enumerate(ordered):
            if bar.start <= pos.filled_at:
                continue
            if pos.last_bar_start is not None and bar.start <= pos.last_bar_start:
                continue
            prev = series[index - 1] if index else None
            tracker, signal = evaluate_bar(pos, pos.tracker, bar, prev, series[index])
            pos.tracker = tracker
            pos.last_bar_start = bar.start
            if signal is not None:
                self._complete(signal.reason, signal.limit_price, now)
                return
        if last_price is not None:
            signal = evaluate_tick(pos, last_price)
            if signal is not None:
                self._complete(signal.reason, signal.limit_price, now)

    def _complete(self, reason: str, limit: Decimal, now: datetime) -> None:
        pos = self.position
        if pos is None:
            return
        order = self.broker.submit_limit_sell(pos.symbol, pos.qty, limit, True)
        current = self.broker.get_order(order.id)
        fill_px = current.filled_avg_price if current.filled_avg_price is not None else limit
        rr = realized_rr(pos.fill_price, pos.stop, pos.target)
        pnl = dollar_pnl(pos.fill_price, fill_px, pos.qty)
        meta = self.last_decision_meta
        reasons = str(meta.get("reasons", ""))
        if reason == "data_loss":
            reasons = "data_loss"
        self.log.write_trade(
            when=now,
            symbol=pos.symbol,
            grade=str(meta.get("grade", "")),
            price=meta.get("price"),
            pct_change=meta.get("pct_change"),
            rel_vol=meta.get("rel_vol"),
            float_shares=meta.get("float_shares"),
            news_url=str(meta.get("news_url", "")),
            fill=pos.fill_price,
            stop=pos.stop,
            target=pos.target,
            rr=rr,
            exit_reason=reason,
            pnl=pnl,
            reasons=reasons,
        )
        self.position = None
        save_state(self.settings.state_path, self.state)


def session_date_of(now: datetime):
    return to_et(now).date()
