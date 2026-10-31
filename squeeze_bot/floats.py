"""Finviz free-page float, SEC shares-outstanding fallback, float.csv cross-check."""

import csv
import json
import re
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from squeeze_bot.constants import FLOAT_DISAGREE, MAX_FLOAT
from squeeze_bot.money import D

_UA = "squeeze-alpaca-bot paper-trader (local research)"
_FINVIZ = "https://finviz.com/quote.ashx?t={ticker}"
_TICKERS = "https://www.sec.gov/files/company_tickers.json"
_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
_FLOAT_RE = re.compile(
    r"Shs\s*Float\s*(?:</[^>]+>\s*)*<[^>]+>\s*(?:<b>)?\s*([0-9][0-9.,]*)\s*([KMB])?",
    re.IGNORECASE,
)
_SUFFIX = {"K": Decimal(1_000), "M": Decimal(1_000_000), "B": Decimal(1_000_000_000)}


@dataclass(frozen=True)
class FloatResolution:
    value: float | None
    status: str
    source: str


def parse_suffixed_number(text: str) -> float | None:
    raw = text.replace(",", "").strip().upper()
    if raw in {"", "-", "N/A", "NA"}:
        return None
    suffix = raw[-1] if raw[-1] in _SUFFIX else ""
    number = raw[:-1] if suffix else raw
    try:
        value = D(number)
    except (InvalidOperation, ValueError):
        return None
    if suffix:
        value *= _SUFFIX[suffix]
    if value < 0:
        return None
    return float(value)


def parse_finviz_float(html: str) -> float | None:
    match = _FLOAT_RE.search(html)
    if not match:
        return None
    token = match.group(1)
    if match.group(2):
        token += match.group(2)
    return parse_suffixed_number(token)


def parse_sec_shares(facts: dict) -> float | None:
    dei = facts.get("facts", {}).get("dei", {})
    node = dei.get("EntityCommonStockSharesOutstanding")
    if not isinstance(node, dict):
        return None
    rows = node.get("units", {}).get("shares") or []
    best: tuple[str, str, float] | None = None
    for row in rows:
        if not isinstance(row, dict) or "val" not in row:
            continue
        end = str(row.get("end") or "")
        filed = str(row.get("filed") or "")
        try:
            val = float(row["val"])
        except (TypeError, ValueError):
            continue
        if best is None or (end, filed) > (best[0], best[1]):
            best = (end, filed, val)
    if best is None:
        return None
    return best[2]


def relative_gap(left: float, right: float) -> Decimal:
    """abs(a-b)/min(a,b). Exactly 0.20 is agreement; greater than 0.20 disagrees."""
    a = D(left)
    b = D(right)
    small = min(a, b)
    if small <= 0:
        return Decimal("Infinity")
    return abs(a - b) / small


def sources_disagree(left: float, right: float) -> bool:
    return relative_gap(left, right) > FLOAT_DISAGREE


def resolve_float(
    finviz: float | None,
    sec: float | None,
    csv_value: float | None,
    csv_row_present: bool,
) -> FloatResolution:
    """Finviz is primary. SEC is the fallback when finviz is missing. Both missing fails.

    When both exist they must agree within 20% or the symbol fails. The value used on
    agreement is the finviz float. A present float.csv row more than 20% off fails;
    a missing row does not.
    """
    if finviz is None and sec is None:
        return FloatResolution(None, "missing", "none")
    if finviz is not None and sec is not None:
        if sources_disagree(finviz, sec):
            return FloatResolution(None, "disagree", "finviz+sec")
        value = finviz
        source = "finviz"
    elif finviz is not None:
        value = finviz
        source = "finviz"
    else:
        value = sec
        source = "sec_companyfacts"
    if csv_row_present:
        if csv_value is None or sources_disagree(value, csv_value):
            return FloatResolution(value, "csv_disagree", source)
    if D(value) >= D(MAX_FLOAT):
        return FloatResolution(value, "too_large", source)
    return FloatResolution(value, "ok", source)


def load_float_csv(path: Path) -> dict[str, float]:
    """symbol -> float. Missing file or missing row is an empty lookup, not an error."""
    if not path.exists():
        return {}
    rows: dict[str, float] = {}
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            symbol = (row.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            raw = (row.get("float") or "").strip()
            if raw == "":
                continue
            try:
                rows[symbol] = float(D(raw))
            except (InvalidOperation, ValueError):
                continue
    return rows


def _read(opener, url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with opener(req, timeout=10) as resp:
        return resp.read()


def fetch_finviz_float(symbol: str, opener=urllib.request.urlopen) -> float | None:
    url = _FINVIZ.format(ticker=symbol.upper())
    try:
        html = _read(opener, url).decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError, TimeoutError, ValueError):
        return None
    return parse_finviz_float(html)


def fetch_sec_shares(symbol: str, opener=urllib.request.urlopen) -> float | None:
    try:
        payload = json.loads(_read(opener, _TICKERS).decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return None
    records = payload.values() if isinstance(payload, dict) else payload
    cik = None
    wanted = symbol.upper()
    for row in records:
        if not isinstance(row, dict):
            continue
        if str(row.get("ticker", "")).upper() == wanted:
            try:
                cik = int(row["cik_str"])
            except (KeyError, TypeError, ValueError):
                return None
            break
    if cik is None:
        return None
    try:
        facts = json.loads(_read(opener, _FACTS.format(cik=cik)).decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return None
    return parse_sec_shares(facts)
