"""Decimal helpers so boundary comparisons stay exact."""

from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MT = ZoneInfo("America/Edmonton")


def D(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    text = str(value).strip()
    if text == "":
        raise ValueError("empty decimal")
    return Decimal(text)


def to_et(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("naive datetime")
    return moment.astimezone(ET)


def to_mt_iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("naive datetime")
    return moment.astimezone(MT).isoformat(timespec="seconds")


def format_num(value: object | None) -> str:
    if value is None:
        return ""
    dec = D(value)
    text = format(dec, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def quantize_tick(value: Decimal, *, up: bool) -> Decimal:
    tick = Decimal("0.01") if value >= 1 else Decimal("0.0001")
    rounding = ROUND_CEILING if up else ROUND_FLOOR
    ticks = (value / tick).to_integral_value(rounding=rounding)
    out = ticks * tick
    if out <= 0:
        return tick
    return out


def money_round(value: Decimal, places: int = 4) -> Decimal:
    quantum = Decimal("1").scaleb(-places)
    return value.quantize(quantum, rounding=ROUND_HALF_UP)
