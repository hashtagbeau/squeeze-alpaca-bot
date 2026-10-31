"""Paper loop and replay CLI. There is no model call on the order path."""

import argparse
import os
import time
from datetime import date, datetime

from squeeze_bot.clockutil import in_scan_window, scan_due, to_et
from squeeze_bot.config import Settings
from squeeze_bot.engine import PaperSession
from squeeze_bot.guard import PaperOnlyError, assert_paper_environment
from squeeze_bot.logbook import LogBook
from squeeze_bot.models import CheckItem
from squeeze_bot.state import DayState, load_state


def run_paper(env=None) -> None:
    source = os.environ if env is None else env
    assert_paper_environment(source)
    settings = Settings.from_env(source)
    from squeeze_bot.broker import AlpacaBroker

    broker = AlpacaBroker.from_env(source, settings)
    log = LogBook(settings.checklist_path, settings.log_csv)
    now = broker.now_et()
    state = load_state(settings.state_path, to_et(now).date())
    session = PaperSession(
        broker,
        log,
        settings,
        state,
        clock=time.monotonic,
        sleep=time.sleep,
    )
    session.startup(now)
    while True:
        now = broker.now_et()
        today = to_et(now).date()
        if session.state.session != today:
            session.state = DayState(today)
        trading_day = broker.is_trading_day(today)
        halt = settings.halt_file.exists()
        bars = []
        last_price = None
        fetch_ok = True
        if session.position is not None:
            try:
                local = to_et(now)
                start = datetime.combine(today, datetime.min.time(), tzinfo=local.tzinfo).replace(
                    hour=4
                )
                bars = broker.minute_bars(session.position.symbol, start, local)
                last_price = float(broker.latest_trade(session.position.symbol).price)
            except (OSError, RuntimeError, ValueError):
                fetch_ok = False
        observations = []
        should_scan = (
            session.position is None
            and session.state.trades == 0
            and trading_day
            and in_scan_window(now)
            and scan_due(session.last_scan, now)
        )
        if should_scan and halt:
            log.write_checklist(
                [CheckItem("kill_switch", "FAIL", "halt_file", "HALT present")],
                symbol="*",
                when=now,
            )
            session.last_scan = now
        elif should_scan:
            for symbol in broker.gainers():
                try:
                    observations.append(
                        broker.observe(
                            symbol,
                            now,
                            halt=halt,
                            trading_day=trading_day,
                            state=session.state,
                        )
                    )
                except (OSError, RuntimeError, ValueError):
                    continue
        session.run_once(
            now,
            observations,
            bars=bars,
            last_price=last_price,
            fetch_ok=fetch_ok,
            trading_day=trading_day,
        )
        time.sleep(1)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="squeeze-bot")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="Run the paper trading loop")
    replay = sub.add_parser("replay", help="Dry-run historical 1-minute bars (no orders)")
    replay.add_argument("--symbol", required=True)
    replay.add_argument("--date", required=True, help="Session date YYYY-MM-DD (America/New_York)")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "run":
            run_paper()
            return
        from squeeze_bot.replay import run_replay

        events = run_replay(args.symbol.upper(), date.fromisoformat(args.date))
    except PaperOnlyError as exc:
        raise SystemExit(str(exc)) from exc
    if args.cmd == "replay":
        for event in events:
            if event["kind"] in {"fill", "exit", "cancel", "open"}:
                print(event)


if __name__ == "__main__":
    main()
