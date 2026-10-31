"""MACD 12/26/9 and session VWAP. EMA is SMA-seeded with k = 2/(n+1)."""

from decimal import Decimal

from squeeze_bot.clockutil import session_date
from squeeze_bot.constants import MACD_FAST, MACD_SIGNAL, MACD_SLOW
from squeeze_bot.models import MacdPoint, MinuteBar
from squeeze_bot.money import D


def ema_series(values: list[Decimal], period: int) -> list[Decimal | None]:
    out: list[Decimal | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    # k = 2/(n+1), written as one fraction so the SMA seed stays exact in Decimal.
    prev = sum(values[:period], D(0)) / D(period)
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = (values[i] * D(2) + prev * D(period - 1)) / D(period + 1)
        out[i] = prev
    return out


def macd_series(
    closes: list[float],
    fast: int = MACD_FAST,
    slow: int = MACD_SLOW,
    signal: int = MACD_SIGNAL,
) -> list[MacdPoint | None]:
    """MACD line and signal. None until both exist (slow EMA and signal EMA warmed up)."""
    vals = [D(close) for close in closes]
    fast_ema = ema_series(vals, fast)
    slow_ema = ema_series(vals, slow)
    line: list[Decimal | None] = [None] * len(vals)
    line_vals: list[Decimal] = []
    line_index: list[int] = []
    for i in range(len(vals)):
        if fast_ema[i] is None or slow_ema[i] is None:
            continue
        point = fast_ema[i] - slow_ema[i]
        line[i] = point
        line_vals.append(point)
        line_index.append(i)
    sig = ema_series(line_vals, signal)
    out: list[MacdPoint | None] = [None] * len(vals)
    for j, idx in enumerate(line_index):
        if sig[j] is None or line[idx] is None:
            continue
        out[idx] = MacdPoint(line=line[idx], signal=sig[j])
    return out


def bearish_cross(series: list[MacdPoint | None], index: int) -> bool:
    """Previous line >= signal and current line < signal. Equal-then-below counts."""
    if index <= 0:
        return False
    prev = series[index - 1]
    cur = series[index]
    if prev is None or cur is None:
        return False
    return prev.line >= prev.signal and cur.line < cur.signal


def session_vwap(bars: list[MinuteBar]) -> list[Decimal | None]:
    """Typical-price VWAP reset at the 04:00 ET session boundary."""
    out: list[Decimal | None] = []
    cum_pv = D(0)
    cum_v = D(0)
    current: object = None
    for bar in bars:
        key = session_date(bar.start)
        if key != current:
            cum_pv = D(0)
            cum_v = D(0)
            current = key
        typical = (D(bar.high) + D(bar.low) + D(bar.close)) / D(3)
        volume = D(bar.volume)
        cum_pv += typical * volume
        cum_v += volume
        if cum_v <= 0:
            out.append(None)
        else:
            out.append(cum_pv / cum_v)
    return out
