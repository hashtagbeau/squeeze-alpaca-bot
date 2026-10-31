"""Paper-only gate. Live trading URLs and flags refuse startup."""

import os
import re
from collections.abc import Mapping

from squeeze_bot.constants import PAPER_BASE_URL

_LIVE_HOST = re.compile(r"(?<!paper-)api\.alpaca\.markets", re.IGNORECASE)
_TRADING_URL_ENV = (
    "APCA_API_BASE_URL",
    "ALPACA_BASE_URL",
    "ALPACA_API_BASE_URL",
    "ALPACA_TRADE_URL",
    "APCA_API_URL",
)
_FALSEY = {"0", "false", "no", "live", "off"}
_TRUTHY = {"1", "true", "yes", "on"}


class PaperOnlyError(RuntimeError):
    """Raised when the process is configured for anything other than Alpaca paper."""


class MissingCredentials(RuntimeError):
    """ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY are required and are the only keys read."""


def is_live_trading_url(value: str) -> bool:
    return _LIVE_HOST.search(value) is not None


def assert_paper_environment(env: Mapping[str, str] | None = None) -> None:
    """Refuse to start if any live trading URL or live flag is configured."""
    source = os.environ if env is None else env
    problems: list[str] = []
    for key, raw in source.items():
        if not isinstance(raw, str):
            continue
        if is_live_trading_url(raw):
            problems.append(f"{key} points at the live trading host")
    for name in _TRADING_URL_ENV:
        raw = source.get(name, "")
        if not isinstance(raw, str) or raw.strip() == "":
            continue
        lowered = raw.lower()
        if "paper-api.alpaca.markets" not in lowered or is_live_trading_url(raw):
            problems.append(f"{name} is not the paper trading host")
    for name in ("ALPACA_PAPER", "APCA_API_PAPER", "PAPER_TRADING"):
        raw = source.get(name)
        if isinstance(raw, str) and raw.strip().lower() in _FALSEY:
            problems.append(f"{name}={raw} disables paper")
    live = source.get("ALPACA_LIVE", "")
    if isinstance(live, str) and live.strip().lower() in _TRUTHY:
        problems.append("ALPACA_LIVE is set")
    if problems:
        raise PaperOnlyError(
            "refusing to start; paper-only bot saw live configuration: " + "; ".join(problems)
        )


def load_credentials(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """Read only ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY. Never APCA_* fallbacks."""
    source = os.environ if env is None else env
    key = source.get("ALPACA_API_KEY_ID", "")
    secret = source.get("ALPACA_API_SECRET_KEY", "")
    if not isinstance(key, str) or not isinstance(secret, str):
        raise MissingCredentials("credentials must be strings")
    key = key.strip()
    secret = secret.strip()
    if not key or not secret:
        raise MissingCredentials(
            "set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY (paper keys); "
            "APCA_API_KEY_ID is not read"
        )
    return key, secret


def paper_client_kwargs(env: Mapping[str, str] | None = None) -> dict[str, object]:
    """Keyword arguments that force the Alpaca trading client onto the paper host."""
    assert_paper_environment(env)
    key, secret = load_credentials(env)
    return {
        "api_key": key,
        "secret_key": secret,
        "paper": True,
        "url_override": PAPER_BASE_URL,
    }
