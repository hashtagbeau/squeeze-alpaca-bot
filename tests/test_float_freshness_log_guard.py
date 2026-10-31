"""Float agreement, freshness, checklist log, CSV, and the paper-only guard."""

import csv
import json
from datetime import timedelta

from squeeze_bot.checklist import evaluate
from squeeze_bot.constants import CSV_HEADER, PAPER_BASE_URL
from squeeze_bot.floats import (
    fetch_finviz_float,
    load_float_csv,
    parse_finviz_float,
    parse_sec_shares,
    parse_suffixed_number,
    relative_gap,
    resolve_float,
    sources_disagree,
)
from squeeze_bot.guard import (
    MissingCredentials,
    PaperOnlyError,
    assert_paper_environment,
    is_live_trading_url,
    load_credentials,
    paper_client_kwargs,
)
from squeeze_bot.logbook import LogBook
from squeeze_bot.money import D, to_mt_iso
from squeeze_bot.replay import simulate_session
from tests.support import at, golden_observation


def test_float_sources_agree_within_20_percent_and_use_finviz():
    assert sources_disagree(100, 120) is False
    assert relative_gap(100, 120) == D("0.2")
    assert sources_disagree(100, 120.01) is True
    agreed = resolve_float(4_000_000, 4_200_000, None, False)
    assert agreed.status == "ok"
    assert agreed.value == 4_000_000
    assert agreed.source == "finviz"
    only_sec = resolve_float(None, 4_000_000, None, False)
    assert only_sec.value == 4_000_000
    assert only_sec.source == "sec_companyfacts"
    only_finviz = resolve_float(4_000_000, None, None, False)
    assert only_finviz.source == "finviz"


def test_float_disagreement_or_both_missing_fails_and_20m_is_exclusive():
    assert resolve_float(None, None, None, False).status == "missing"
    assert resolve_float(4_000_000, 8_000_000, None, False).status == "disagree"
    assert resolve_float(20_000_000, 20_000_000, None, False).status == "too_large"
    assert resolve_float(19_999_999, 19_999_999, None, False).status == "ok"
    missing = evaluate(golden_observation(finviz_float=None, sec_shares=None))
    assert _item(missing, "float_sources").kind == "uncertain"
    assert missing.grade == "C"
    assert missing.trade is False
    too_big = evaluate(golden_observation(finviz_float=20_000_000, sec_shares=20_000_000))
    assert _item(too_big, "float_under_20m").result == "FAIL"
    assert too_big.trade is False


def test_float_csv_disagreement_fails_and_a_missing_row_does_not(tmp_path):
    assert resolve_float(4_000_000, 4_000_000, None, False).status == "ok"
    # Exactly 20% off still agrees. A hair more fails.
    assert resolve_float(4_000_000, 4_000_000, 4_800_000, True).status == "ok"
    assert resolve_float(4_000_000, 4_000_000, 4_800_001, True).status == "csv_disagree"
    off = evaluate(
        golden_observation(csv_row_present=True, csv_float=4_000_000 * 1.25)
    )
    assert _item(off, "float_csv").result == "FAIL"
    assert off.trade is False
    absent = evaluate(golden_observation(csv_row_present=False, csv_float=None))
    assert _item(absent, "float_csv").result == "PASS"
    assert absent.trade is True
    path = tmp_path / "float.csv"
    assert load_float_csv(path) == {}
    path.write_text(
        "symbol,float,source,ts_mt\nSQZ,4000000,desk,2026-10-28T05:00:00-06:00\n",
        encoding="utf-8",
    )
    assert load_float_csv(path)["SQZ"] == 4_000_000
    # A file with no row for the symbol is the same as a missing row.
    path.write_text(
        "symbol,float,source,ts_mt\nOTHER,1,desk,2026-10-28T05:00:00-06:00\n",
        encoding="utf-8",
    )
    assert "SQZ" not in load_float_csv(path)


def test_finviz_and_sec_parsers_and_finviz_url(monkeypatch):
    html = '<td>Shs Float</div></td><td class="snapshot-td2"><b>4.50M</b></td>'
    assert parse_finviz_float(html) == 4_500_000
    assert parse_suffixed_number("800K") == 800_000
    assert parse_suffixed_number("1.25B") == 1_250_000_000
    assert parse_finviz_float("<td>Shs Float</td><td>-</td>") is None
    facts = {
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {"end": "2026-06-30", "filed": "2026-08-01", "val": 4_000_000},
                            {"end": "2026-09-30", "filed": "2026-10-15", "val": 4_200_000},
                        ]
                    }
                }
            }
        }
    }
    assert parse_sec_shares(facts) == 4_200_000
    captured = {}

    class Body:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'<td>Shs Float</td><td class="x"><b>1.00M</b></td>'

    def opener(req, timeout=0):
        captured["url"] = req.full_url
        return Body()

    monkeypatch.setattr("urllib.request.urlopen", opener)
    assert fetch_finviz_float("sqz", opener=opener) == 1_000_000
    assert captured["url"] == "https://finviz.com/quote.ashx?t=SQZ"


def test_freshness_quote_5s_and_bars_60s():
    fresh = golden_observation()
    age = fresh.now - fresh.minute_bars[-1].end
    assert age.total_seconds() <= 60
    assert (fresh.now - fresh.last_price_ts).total_seconds() <= 5
    assert evaluate(fresh).trade is True

    quote_edge = golden_observation(last_price_ts=fresh.now - timedelta(seconds=5))
    # golden() rebuilt `now`, so pin both clocks to the same observation.
    base = golden_observation()
    base.last_price_ts = base.now - timedelta(seconds=5)
    assert _item(evaluate(base), "freshness_quote").result == "PASS"
    base.last_price_ts = base.now - timedelta(seconds=5, milliseconds=1)
    stale_quote = evaluate(base)
    assert _item(stale_quote, "freshness_quote").result == "FAIL"
    assert stale_quote.trade is False

    bars = golden_observation()
    bars.now = bars.minute_bars[-1].end + timedelta(seconds=60)
    bars.last_price_ts = bars.now
    assert _item(evaluate(bars), "freshness_bars").result == "PASS"
    bars.now = bars.minute_bars[-1].end + timedelta(seconds=60, milliseconds=1)
    bars.last_price_ts = bars.now
    stale_bars = evaluate(bars)
    assert _item(stale_bars, "freshness_bars").result == "FAIL"
    assert stale_bars.trade is False
    del quote_edge


def test_entry_cutoff_and_one_trade_and_symbol_done_are_hard_fails():
    late = evaluate(golden_observation(now=at(10, 0, 0), last_price_ts=at(10, 0, 0)))
    assert _item(late, "entry_cutoff").result == "FAIL"
    assert late.trade is False
    second = evaluate(golden_observation(trades_today=1))
    assert _item(second, "one_trade_per_day").result == "FAIL"
    assert second.trade is False
    done = evaluate(golden_observation(symbol_done=True))
    assert _item(done, "symbol_not_done").result == "FAIL"
    assert done.trade is False


def test_golden_checklist_is_all_pass_and_logged(tmp_path):
    decision = evaluate(golden_observation())
    assert decision.trade is True
    assert decision.grade == "A"
    assert decision.reasons == []
    names = [item.item for item in decision.items]
    assert names == [
        "trading_day",
        "scan_window",
        "kill_switch",
        "symbol_not_done",
        "price",
        "pct_change",
        "rvol",
        "float_sources",
        "float_under_20m",
        "float_csv",
        "news",
        "ipo",
        "second_day",
        "pop_and_fade",
        "luld_halt",
        "red_to_green",
        "front_side",
        "first_pullback",
        "pullback_volume",
        "pullback_vwap",
        "retrace_50",
        "heavy_selling",
        "macd_entry",
        "trigger",
        "entry_cutoff",
        "one_trade_per_day",
        "reward_risk",
        "freshness_quote",
        "freshness_bars",
        "sizing",
        "grade",
    ]
    assert all(item.result == "PASS" for item in decision.items)
    book = LogBook(tmp_path / "checklist.jsonl", tmp_path / "log.csv")
    book.write_checklist(decision.items, symbol="SQZ", when=decision_now())
    book.write_no_trade(decision, decision_now())
    lines = (tmp_path / "checklist.jsonl").read_text().strip().splitlines()
    assert len(lines) == len(decision.items)
    first = json.loads(lines[0])
    assert set(first) >= {"timestamp", "ticker", "item", "result", "source"}
    assert first["result"] == "PASS"
    assert first["timestamp"].endswith("-06:00")
    # Second write must not repeat the header.
    book.write_no_trade(decision, decision_now())
    rows = list(csv.reader((tmp_path / "log.csv").open()))
    assert rows[0] == CSV_HEADER.split(",")
    assert rows[1][0].endswith("-06:00")
    assert rows[1][15] == "paper"
    assert rows[1][2] == "no-trade"
    assert len(rows) == 3
    assert rows[2][0]  # timestamp present


def test_trade_row_uses_the_signed_off_header_and_edmonton_time(tmp_path):
    when = at(7, 58, 10)
    assert to_mt_iso(when).startswith("2026-10-28T05:58:10")
    assert to_mt_iso(when).endswith("-06:00")
    book = LogBook(tmp_path / "c.jsonl", tmp_path / "log.csv")
    book.write_trade(
        when=when,
        symbol="SQZ",
        grade="A",
        price=4.41,
        pct_change=D("1.205"),
        rel_vol=D("100"),
        float_shares=4_000_000,
        news_url="https://news.example/sqz",
        fill=D("4.50"),
        stop=D("4.35"),
        target=D("4.90"),
        rr=D("0.4") / D("0.15"),
        exit_reason="target",
        pnl=D("50"),
        reasons="source=replay",
    )
    row = list(csv.reader((tmp_path / "log.csv").open()))[1]
    assert row[1] == "SQZ"
    assert row[2] == "trade"
    assert row[3] == "A"
    assert row[9] == "4.5"  # actual fill, not the 4.42 limit
    assert row[13] == "target"
    assert row[15] == "paper"
    assert row[16] == "source=replay"


def test_paper_guard_refuses_live_urls_and_flags_and_reads_only_named_keys():
    assert is_live_trading_url("https://api.alpaca.markets") is True
    assert is_live_trading_url("https://api.alpaca.markets/v2") is True
    assert is_live_trading_url("wss://api.alpaca.markets/stream") is True
    assert is_live_trading_url(PAPER_BASE_URL) is False
    assert is_live_trading_url("https://data.alpaca.markets") is False
    assert_paper_environment({})
    assert_paper_environment({"APCA_API_BASE_URL": PAPER_BASE_URL})
    assert_paper_environment({"APCA_API_DATA_URL": "https://data.alpaca.markets"})
    for env in (
        {"APCA_API_BASE_URL": "https://api.alpaca.markets"},
        {"ALPACA_BASE_URL": "https://api.alpaca.markets"},
        {"ALPACA_PAPER": "false"},
        {"ALPACA_LIVE": "true"},
        {"NOTES": "use https://api.alpaca.markets today"},
    ):
        try:
            assert_paper_environment(env)
        except PaperOnlyError:
            continue
        raise AssertionError(env)
    try:
        load_credentials({"APCA_API_KEY_ID": "x", "APCA_API_SECRET_KEY": "y"})
    except MissingCredentials:
        pass
    else:
        raise AssertionError("APCA_* keys must not be accepted")
    try:
        load_credentials({})
    except MissingCredentials:
        pass
    else:
        raise AssertionError("missing keys")
    kwargs = paper_client_kwargs({"ALPACA_API_KEY_ID": "k", "ALPACA_API_SECRET_KEY": "s"})
    assert kwargs["paper"] is True
    assert kwargs["url_override"] == PAPER_BASE_URL
    assert kwargs["api_key"] == "k"


def test_broker_constructor_rejects_a_live_base_url(tmp_path):
    from squeeze_bot.broker import AlpacaBroker
    from squeeze_bot.config import Settings

    class Live:
        _base_url = "https://api.alpaca.markets"

    settings = Settings(
        float_csv=tmp_path / "float.csv",
        halt_file=tmp_path / "HALT",
        log_csv=tmp_path / "log.csv",
        checklist_path=tmp_path / "c.jsonl",
        state_path=tmp_path / "state.json",
    )
    try:
        AlpacaBroker(Live(), None, None, None, settings)
    except PaperOnlyError:
        return
    raise AssertionError("live client must not construct")


def test_replay_simulation_submits_no_orders_and_can_fill():
    obs = golden_observation()
    bars = list(obs.minute_bars)
    start = bars[-1].start + timedelta(minutes=1)
    bars.append(_bar(start, 4.40, 4.50, 4.36, 4.48, 5_000))
    bars.append(_bar(start + timedelta(minutes=1), 4.48, 4.95, 4.47, 4.92, 8_000))
    events = simulate_session(obs, bars)
    kinds = [event["kind"] for event in events]
    assert "fill" in kinds
    assert "exit" in kinds
    exit_row = next(event for event in events if event["kind"] == "exit")
    assert exit_row["reason"] == "target"
    # The simulator has no broker argument and did not need one.
    assert not hasattr(simulate_session, "broker")


def test_client_factory_passes_paper_true(monkeypatch, tmp_path):
    from squeeze_bot.broker import AlpacaBroker
    from squeeze_bot.config import Settings

    captured = {}

    class Trading:
        def __init__(self, key, secret, paper=False, url_override=None):
            captured["paper"] = paper
            captured["url"] = url_override
            captured["key"] = key
            self._base_url = url_override

    class Data:
        def __init__(self, *args, **kwargs):
            self._base_url = kwargs.get("url_override")

    monkeypatch.setattr("alpaca.trading.client.TradingClient", Trading)
    monkeypatch.setattr("alpaca.data.historical.stock.StockHistoricalDataClient", Data)
    monkeypatch.setattr("alpaca.data.historical.news.NewsClient", Data)
    monkeypatch.setattr("alpaca.data.historical.screener.ScreenerClient", Data)
    settings = Settings(
        float_csv=tmp_path / "float.csv",
        halt_file=tmp_path / "HALT",
        log_csv=tmp_path / "log.csv",
        checklist_path=tmp_path / "c.jsonl",
        state_path=tmp_path / "state.json",
    )
    AlpacaBroker.from_env(
        {"ALPACA_API_KEY_ID": "paper-key", "ALPACA_API_SECRET_KEY": "paper-secret"},
        settings,
    )
    assert captured["paper"] is True
    assert captured["url"] == PAPER_BASE_URL
    assert captured["key"] == "paper-key"


def decision_now():
    return golden_observation().now


def _item(decision, name):
    for item in decision.items:
        if item.item == name:
            return item
    raise AssertionError(name)


def _bar(start, opn, high, low, close, volume):
    from squeeze_bot.models import MinuteBar

    return MinuteBar(start, opn, high, low, close, volume)
