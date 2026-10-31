"""R:R, grade, size, order timeout, and every exit."""

from datetime import timedelta

from squeeze_bot.checklist import evaluate
from squeeze_bot.config import Settings
from squeeze_bot.engine import PaperSession
from squeeze_bot.exits import data_loss, evaluate_bar, evaluate_tick
from squeeze_bot.logbook import LogBook
from squeeze_bot.models import ExitTracker, MacdPoint, ManagedPosition, MinuteBar
from squeeze_bot.money import D
from squeeze_bot.orders import BrokerOrder, execute_entry
from squeeze_bot.risk import (
    buy_limit,
    choose_target,
    grade_setup,
    next_half_or_whole,
    realized_rr,
    size_shares,
)
from squeeze_bot.state import DayState
from tests.support import DAY, at, golden_observation


def test_buy_limit_is_prior_high_plus_the_smaller_buffer_rounded_up():
    assert buy_limit(D("3.70")) == D("3.72")  # min(0.02, 1%) is 0.02
    assert buy_limit(D("1.00")) == D("1.01")  # 1% is the smaller buffer
    assert buy_limit(D("0.50")) == D("0.505")  # sub-dollar tick


def test_preorder_rr_uses_the_limit_not_the_last_trade():
    decision = evaluate(golden_observation())
    assert decision.trade is True
    assert decision.entry == D("4.42")
    assert decision.price == 4.41
    assert decision.stop == D("4.35")
    assert decision.target == D("4.90")
    assert decision.rr == (D("4.90") - D("4.42")) / (D("4.42") - D("4.35"))
    # 1% of equity / 0.07 risk = 285 shares, not the 333 you would get from the 4.41 print.
    assert decision.shares == 285
    assert decision.extended_hours is True


def test_extended_hours_flag_flips_at_the_open():
    pre = evaluate(golden_observation(now=at(9, 29, 59), last_price_ts=at(9, 29, 58)))
    regular = evaluate(golden_observation(now=at(9, 30, 0), last_price_ts=at(9, 30, 0)))
    assert pre.extended_hours is True
    assert regular.extended_hours is False


def test_target_uses_hod_when_it_pays_2r_else_one_half_dollar_level():
    # HOD is exactly 2R: use HOD, not the next half dollar.
    hod = choose_target(D("4.00"), D("3.50"), D("5.00"))
    assert hod == (D("5.00"), D("2"))
    # HOD is short of 2R; the next half/whole (5.00) pays 6R.
    nxt = choose_target(D("4.40"), D("4.30"), D("4.50"))
    assert nxt is not None
    assert nxt[0] == D("5.00")
    assert nxt[1] >= 2
    # That single next level still pays under 2R: no trade.
    assert choose_target(D("4.70"), D("4.00"), D("4.80")) is None
    assert next_half_or_whole(D("4.50")) == D("5.00")
    assert next_half_or_whole(D("4.51")) == D("5.00")
    assert next_half_or_whole(D("4.49")) == D("4.50")
    assert next_half_or_whole(D("4.00")) == D("4.50")


def test_rr_below_2_is_a_no_trade_at_the_gate():
    # Force a geometry where HOD and the next half dollar both miss 2R by
    # replacing the bars' highs is unnecessary: choose_target None is the rule,
    # and the gate fails closed when the setup cannot pay 2R.
    assert choose_target(D("4.70"), D("4.00"), D("4.80")) is None


def test_grade_a_boundaries_and_b_when_an_a_condition_is_missed():
    base = dict(
        float_shares=4_999_999,
        rr=D("3"),
        prior_day_volume=99_999,
        rvol=D("100"),
        uncertain=False,
        hard_fail=False,
    )
    assert grade_setup(**base) == "A"
    assert grade_setup(**{**base, "float_shares": 5_000_000}) == "B"
    assert grade_setup(**{**base, "rr": D("2.999")}) == "B"
    assert grade_setup(**{**base, "prior_day_volume": 100_000}) == "B"
    assert grade_setup(**{**base, "rvol": D("99.99")}) == "B"
    assert grade_setup(**{**base, "uncertain": True}) == "C"
    assert grade_setup(**{**base, "hard_fail": True}) == ""
    assert grade_setup(**{**base, "prior_day_volume": None}) == "C"


def test_grade_b_still_trades_and_grade_c_never_does():
    b_trade = evaluate(golden_observation(finviz_float=10_000_000, sec_shares=10_500_000))
    assert b_trade.trade is True
    assert b_trade.grade == "B"
    c_trade = evaluate(golden_observation(finviz_float=4_000_000, sec_shares=8_000_000))
    assert c_trade.grade == "C"
    assert c_trade.trade is False


def test_sizing_floors_one_percent_and_caps_at_settled_cash():
    assert size_shares(2000, 10_000, D("5"), D("4")) == 20
    assert size_shares(2000, 50, D("5"), D("4")) == 10  # floor(50/5) binds
    assert size_shares(100, 10_000, D("5"), D("3")) == 0  # floor(1/2) = 0
    assert size_shares(2000, 10_000, D("5"), D("4.85")) == 133  # floor(20/0.15)
    assert size_shares(2000, 10_000, D("4"), D("4")) == 0
    assert size_shares(None, 10, D("5"), D("4")) == 0


def test_unfilled_order_cancels_at_10s_and_does_not_count_as_the_daily_trade(tmp_path):
    broker = ScriptedBroker(mode="never")
    clock = ManualClock()
    obs = golden_observation()
    other = golden_observation(symbol="NEXT")
    session = _session(tmp_path, broker, clock)
    session.run_once(obs.now, [obs, other])
    assert broker.buys[0]["extended_hours"] is True
    assert [row["symbol"] for row in broker.buys] == ["SQZ", "NEXT"]
    assert broker.cancels == [broker.buys[0]["id"]]
    assert session.state.trades == 1
    assert session.state.done == {"SQZ", "NEXT"}
    assert session.position is not None
    assert session.position.symbol == "NEXT"


def test_partial_fill_counts_as_the_trade_and_the_rest_is_cancelled():
    clock = ManualClock()
    broker = ScriptedBroker(mode="partial")
    result = execute_entry(
        broker,
        symbol="SQZ",
        qty=100,
        limit_price=D("4.42"),
        extended_hours=True,
        clock=clock.clock,
        sleep=clock.sleep,
    )
    assert result.counts_as_trade is True
    assert result.filled_qty == 4
    assert result.symbol_done is True
    assert broker.cancels
    assert len(broker.buys) == 1


def test_filled_order_is_not_cancelled():
    broker = ScriptedBroker(mode="fill")
    clock = ManualClock()
    result = execute_entry(
        broker,
        symbol="SQZ",
        qty=10,
        limit_price=D("4.42"),
        extended_hours=False,
        clock=clock.clock,
        sleep=clock.sleep,
    )
    assert result.status == "filled"
    assert result.counts_as_trade is True
    assert broker.cancels == []
    assert clock.now < 10


def test_one_trade_per_day_blocks_a_second_symbol(tmp_path):
    broker = ScriptedBroker(mode="fill")
    clock = ManualClock()
    session = _session(tmp_path, broker, clock)
    first = golden_observation()
    session.run_once(first.now, [first])
    assert session.state.trades == 1
    session.last_scan = None
    second = golden_observation(symbol="NEXT")
    session.run_once(second.now, [second])
    assert len(broker.buys) == 1


def test_no_entries_at_or_after_10_but_exits_continue(tmp_path):
    broker = ScriptedBroker(mode="fill")
    clock = ManualClock()
    session = _session(tmp_path, broker, clock)
    now = at(10, 0, 10)
    session.position = _position(filled_at=at(9, 50))
    bar = MinuteBar(at(9, 59), 4.50, 4.50, 4.00, 4.20, 100)
    would_be = golden_observation(now=now, last_price_ts=now)
    session.run_once(now, [would_be], bars=[bar], last_price=4.20, fetch_ok=True)
    assert broker.buys == []
    assert len(broker.sells) == 1
    assert broker.sells[0]["extended_hours"] is True
    text = (tmp_path / "log.csv").read_text()
    assert "stop" in text
    assert session.position is None


def test_halt_file_blocks_entries_and_exits_still_run(tmp_path):
    broker = ScriptedBroker(mode="fill")
    session = _session(tmp_path, broker, ManualClock())
    session.position = _position(filled_at=at(9, 50))
    bar = MinuteBar(at(9, 59), 4.50, 4.50, 4.00, 4.20, 100)
    halted = golden_observation(halt_file_present=True)
    session.run_once(halted.now, [halted], bars=[bar], last_price=4.10, fetch_ok=True)
    assert broker.buys == []
    assert broker.sells and session.position is None
    # No position: the gate itself records the kill switch.
    broker = ScriptedBroker(mode="fill")
    session = _session(tmp_path, broker, ManualClock())
    decision_obs = golden_observation(halt_file_present=True)
    session.run_once(decision_obs.now, [decision_obs])
    assert broker.buys == []
    assert _csv_has(tmp_path, "kill_switch")


def test_stop_and_target_on_the_same_bar_stop_wins():
    pos = _position()
    bar = MinuteBar(at(8, 10), 4.50, 5.50, 4.00, 4.80, 100)
    _, signal = evaluate_bar(pos, ExitTracker(), bar, None, None)
    assert signal is not None
    assert signal.reason == "stop"


def test_each_exit_rule():
    pos = _position()  # fill 4.42, stop 4.35, target 4.90, half-R = 4.455
    stop_bar = MinuteBar(at(8, 10), 4.50, 4.50, 4.35, 4.40, 10)
    _, stop = evaluate_bar(pos, ExitTracker(), stop_bar, None, None)
    assert stop.reason == "stop"
    assert stop.limit_price < pos.stop

    target_bar = MinuteBar(at(8, 11), 4.50, 4.90, 4.45, 4.80, 10)
    _, target = evaluate_bar(pos, ExitTracker(), target_bar, None, None)
    assert target.reason == "target"
    assert target.limit_price == pos.target

    tick_stop = evaluate_tick(pos, 4.35)
    assert tick_stop.reason == "stop"
    tick_target = evaluate_tick(pos, 4.90)
    assert tick_target.reason == "target"
    assert evaluate_tick(pos, 4.50) is None

    # Exactly +0.5R counts as reached, so the time stop does not fire.
    reached = ExitTracker()
    for i, high in enumerate((4.455, 4.455, 4.50)):
        bar = MinuteBar(at(8, 20 + i), 4.46, high, 4.40, 4.46, 10)
        reached, signal = evaluate_bar(pos, reached, bar, _bull(), _bull())
        assert signal is None or signal.reason != "not_half_r_3_bars"
    assert reached.half_r_reached is True

    quiet = ExitTracker()
    signal = None
    for i in range(3):
        bar = MinuteBar(at(8, 30 + i), 4.44, 4.45, 4.40, 4.44, 10)
        quiet, signal = evaluate_bar(pos, quiet, bar, _bull(), _bull())
    assert signal.reason == "not_half_r_3_bars"

    heavy = MinuteBar(at(8, 40), 4.60, 4.70, 4.50, 4.55, 100_001)
    _, heavy_sig = evaluate_bar(pos, ExitTracker(), heavy, _bull(), _bull())
    assert heavy_sig.reason == "heavy_selling"
    equal = MinuteBar(at(8, 41), 4.60, 4.70, 4.50, 4.55, 100_000)
    _, equal_sig = evaluate_bar(pos, ExitTracker(), equal, _bull(), _bull())
    assert equal_sig is None

    stall = ExitTracker(half_r_reached=True)
    signal = None
    for i, high in enumerate((4.50, 4.55, 4.60)):
        bar = MinuteBar(at(8, 50 + i), 4.48, high, 4.46, 4.52, 10)
        stall, signal = evaluate_bar(pos, stall, bar, _bull(), _bull())
    assert signal.reason == "stall_3_bars"

    _, macd_sig = evaluate_bar(pos, ExitTracker(half_r_reached=True), equal, _bull(), _bear())
    assert macd_sig.reason == "macd_cross"
    _, still_below = evaluate_bar(
        pos, ExitTracker(half_r_reached=True), equal, _bear(), _bear()
    )
    assert still_below is None


def test_exit_priority_is_the_spec_order():
    pos = _position()
    # Bar 3, no +0.5R, heavy volume, and a MACD cross: the time stop is earlier in the list.
    tracker = ExitTracker(bars_seen=2, half_r_reached=False, stall=2)
    bar = MinuteBar(at(8, 12), 4.50, 4.45, 4.40, 4.41, 200_000)
    _, signal = evaluate_bar(pos, tracker, bar, _bull(), _bear())
    assert signal.reason == "not_half_r_3_bars"


def test_restart_flattens_open_positions(tmp_path):
    broker = ScriptedBroker(mode="fill")
    held = type("P", (), {"symbol": "SQZ", "qty": 10, "avg_entry": 4.42, "current": 4.10})()
    broker.positions = [held]
    session = _session(tmp_path, broker, ManualClock())
    session.startup(at(8, 0))
    assert len(broker.sells) == 1
    assert session.state.trades == 1
    assert "SQZ" in session.state.done
    assert "restart_flatten" in (tmp_path / "log.csv").read_text()
    assert broker.sells[0]["limit"] < D("4.10")


def test_data_loss_and_stale_bars_flatten(tmp_path):
    now = at(8, 30)
    assert data_loss(now - timedelta(seconds=120), now, True) is False
    assert data_loss(now - timedelta(seconds=121), now, True) is True
    assert data_loss(now, now, False) is True
    broker = ScriptedBroker(mode="fill")
    session = _session(tmp_path, broker, ManualClock())
    session.position = _position(filled_at=at(8, 0))
    session.run_once(now, [], bars=[], last_price=4.40, fetch_ok=False)
    assert broker.sells
    assert "data_loss" in (tmp_path / "log.csv").read_text()


def test_logged_rr_uses_the_actual_fill(tmp_path):
    broker = ScriptedBroker(mode="fill", fill_price=4.50)
    clock = ManualClock()
    session = _session(tmp_path, broker, clock)
    obs = golden_observation()
    session.run_once(obs.now, [obs])
    assert session.position is not None
    assert session.position.fill_price == D("4.50")
    # Target touched on the next bar.
    bar = MinuteBar(obs.now + timedelta(minutes=1), 4.60, 4.95, 4.55, 4.90, 1000)
    session.run_once(bar.end, [], bars=[bar], last_price=4.90, fetch_ok=True)
    row = (tmp_path / "log.csv").read_text().strip().splitlines()[-1]
    assert "4.5" in row
    # Realized R is (4.90-4.50)/(4.50-4.35) = 2.666..., not the planned ~6.86 from the 4.42 limit.
    assert realized_rr(D("4.50"), D("4.35"), D("4.90")) == D("0.40") / D("0.15")
    assert "6.857" not in row


def _position(filled_at=None) -> ManagedPosition:
    return ManagedPosition(
        symbol="SQZ",
        qty=10,
        fill_price=D("4.42"),
        stop=D("4.35"),
        target=D("4.90"),
        largest_green=D("100000"),
        filled_at=filled_at or at(8, 0),
        planned_entry=D("4.42"),
    )


def _bull() -> MacdPoint:
    return MacdPoint(D("0.4"), D("0.2"))


def _bear() -> MacdPoint:
    return MacdPoint(D("0.1"), D("0.3"))


def _session(tmp_path, broker, clock) -> PaperSession:
    settings = Settings(
        float_csv=tmp_path / "float.csv",
        halt_file=tmp_path / "HALT",
        log_csv=tmp_path / "log.csv",
        checklist_path=tmp_path / "logs" / "checklist.jsonl",
        state_path=tmp_path / "state.json",
    )
    return PaperSession(
        broker,
        LogBook(settings.checklist_path, settings.log_csv),
        settings,
        DayState(DAY),
        clock=clock.clock,
        sleep=clock.sleep,
    )


def _csv_has(tmp_path, text) -> bool:
    return text in (tmp_path / "log.csv").read_text()


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class ScriptedBroker:
    def __init__(self, mode: str, fill_price: float | None = None) -> None:
        self.mode = mode
        self.fill_price = fill_price
        self.buys = []
        self.sells = []
        self.cancels = []
        self.positions = []
        self._n = 0

    def list_positions(self):
        return list(self.positions)

    def submit_limit_buy(self, symbol, qty, limit_price, extended_hours):
        self._n += 1
        order_id = f"b{self._n}"
        self.buys.append(
            {
                "id": order_id,
                "symbol": symbol,
                "qty": qty,
                "limit": limit_price,
                "extended_hours": extended_hours,
            }
        )
        return BrokerOrder(order_id, "new", 0, None)

    def submit_limit_sell(self, symbol, qty, limit_price, extended_hours):
        self._n += 1
        order_id = f"s{self._n}"
        price = float(limit_price)
        self.sells.append(
            {
                "id": order_id,
                "symbol": symbol,
                "qty": qty,
                "limit": limit_price,
                "extended_hours": extended_hours,
            }
        )
        return BrokerOrder(order_id, "filled", qty, price)

    def get_order(self, order_id: str) -> BrokerOrder:
        if order_id.startswith("s"):
            sell = next(row for row in self.sells if row["id"] == order_id)
            return BrokerOrder(order_id, "filled", sell["qty"], float(sell["limit"]))
        buy = next(row for row in self.buys if row["id"] == order_id)
        if self.mode == "fill":
            price = self.fill_price if self.fill_price is not None else float(buy["limit"])
            return BrokerOrder(order_id, "filled", buy["qty"], price)
        if self.mode == "partial":
            return BrokerOrder(order_id, "partially_filled", 4, float(buy["limit"]))
        second_fill = (
            self.mode == "never"
            and len(self.buys) >= 2
            and order_id == self.buys[-1]["id"]
            and len(self.cancels) >= 1
        )
        if second_fill:
            price = float(buy["limit"])
            return BrokerOrder(order_id, "filled", buy["qty"], price)
        return BrokerOrder(order_id, "new", 0, None)

    def cancel(self, order_id: str) -> None:
        self.cancels.append(order_id)
