"""Paths default to /home/box/squeeze-trader and can be overridden from the environment."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ROOT = Path("/home/box/squeeze-trader")


@dataclass(frozen=True)
class Settings:
    float_csv: Path
    halt_file: Path
    log_csv: Path
    checklist_path: Path
    state_path: Path
    data_feed: str = "iex"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        source = os.environ if env is None else env
        root = DEFAULT_ROOT

        def pick(name: str, default: Path) -> Path:
            raw = source.get(name, "")
            if isinstance(raw, str) and raw.strip():
                return Path(raw.strip())
            return default

        feed = source.get("SQUEEZE_DATA_FEED", "iex")
        if not isinstance(feed, str) or not feed.strip():
            feed = "iex"
        return cls(
            float_csv=pick("SQUEEZE_FLOAT_CSV", root / "float.csv"),
            halt_file=pick("SQUEEZE_HALT_FILE", root / "HALT"),
            log_csv=pick("SQUEEZE_LOG_CSV", root / "log.csv"),
            checklist_path=pick("SQUEEZE_CHECKLIST_PATH", root / "logs" / "checklist.jsonl"),
            state_path=pick("SQUEEZE_STATE_PATH", root / "state.json"),
            data_feed=feed.strip().lower(),
        )
