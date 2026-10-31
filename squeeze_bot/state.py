"""Daily trade count and done symbols survive a restart."""

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


@dataclass
class DayState:
    session: date
    trades: int = 0
    done: set[str] = field(default_factory=set)

    def mark_done(self, symbol: str) -> None:
        self.done.add(symbol.upper())

    def is_done(self, symbol: str) -> bool:
        return symbol.upper() in self.done


def load_state(path: Path, today: date) -> DayState:
    if not path.exists():
        return DayState(today)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DayState(today)
    if payload.get("date") != today.isoformat():
        return DayState(today)
    done = payload.get("done") or []
    trades = int(payload.get("trades") or 0)
    return DayState(today, trades, {str(symbol).upper() for symbol in done})


def save_state(path: Path, state: DayState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": state.session.isoformat(),
        "trades": state.trades,
        "done": sorted(state.done),
    }
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
