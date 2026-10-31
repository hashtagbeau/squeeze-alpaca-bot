"""Limit price, target choice, grade, and share size. Pre-order R:R uses the limit."""

from decimal import ROUND_FLOOR, Decimal

from squeeze_bot.constants import (
    GRADE_A_FLOAT,
    GRADE_A_PRIOR_VOL,
    GRADE_A_RR,
    LIMIT_CENTS,
    LIMIT_PCT,
    MIN_RR,
    RISK_FRACTION,
)
from squeeze_bot.money import D, quantize_tick


def buy_limit(prior_high: Decimal | float) -> Decimal:
    """Marketable buy limit = prior bar high + min($0.02, 1% of that high), rounded up to tick."""
    px = D(prior_high)
    buffer = min(LIMIT_CENTS, px * LIMIT_PCT)
    return quantize_tick(px + buffer, up=True)


def sell_limit(reference: Decimal | float) -> Decimal:
    """Marketable sell limit = reference - min($0.02, 1% of reference), rounded down to tick."""
    px = D(reference)
    buffer = min(LIMIT_CENTS, px * LIMIT_PCT)
    return quantize_tick(px - buffer, up=False)


def next_half_or_whole(price: Decimal | float) -> Decimal:
    """Next $0.50 increment strictly above price (the next half-dollar or whole-dollar)."""
    px = D(price)
    step = Decimal("0.50")
    floored = (px / step).to_integral_value(rounding=ROUND_FLOOR)
    return (floored + 1) * step


def choose_target(
    entry: Decimal | float,
    stop: Decimal | float,
    hod: Decimal | float,
) -> tuple[Decimal, Decimal] | None:
    """HOD if it pays >= 2R from the limit.

    Otherwise the single next half-dollar or whole-dollar above HOD, if that pays >= 2R.
    """
    entry_d = D(entry)
    stop_d = D(stop)
    hod_d = D(hod)
    risk = entry_d - stop_d
    if risk <= 0:
        return None
    hod_reward = hod_d - entry_d
    if hod_reward > 0 and hod_reward / risk >= MIN_RR:
        return hod_d, hod_reward / risk
    level = next_half_or_whole(hod_d)
    reward = level - entry_d
    if reward > 0 and reward / risk >= MIN_RR:
        return level, reward / risk
    return None


def realized_rr(
    fill: Decimal | float,
    stop: Decimal | float,
    target: Decimal | float,
) -> Decimal | None:
    risk = D(fill) - D(stop)
    if risk <= 0:
        return None
    return (D(target) - D(fill)) / risk


def grade_setup(
    *,
    float_shares: float | None,
    rr: Decimal | None,
    prior_day_volume: float | None,
    rvol: Decimal | None,
    uncertain: bool,
    hard_fail: bool,
) -> str:
    """A, B, or C. Empty string means a clean hard-rule failure (also no trade).

    A = float < 5M and R:R >= 3 and prior-day volume < 100K and rvol >= 100.
    B = every hard rule passed and at least one A condition missed.
    C = marginal or uncertain inputs. C is never traded.
    """
    if uncertain:
        return "C"
    if hard_fail:
        return ""
    if float_shares is None or rr is None or prior_day_volume is None or rvol is None:
        return "C"
    is_a = (
        D(float_shares) < D(GRADE_A_FLOAT)
        and rr >= GRADE_A_RR
        and D(prior_day_volume) < D(GRADE_A_PRIOR_VOL)
        and rvol >= D(100)
    )
    if is_a:
        return "A"
    return "B"


def size_shares(
    equity: float | None,
    settled_cash: float | None,
    entry: Decimal | float,
    stop: Decimal | float,
) -> int:
    """floor(1% equity / risk), capped at floor(settled cash / entry)."""
    if equity is None or settled_cash is None:
        return 0
    entry_d = D(entry)
    risk = entry_d - D(stop)
    if risk <= 0 or entry_d <= 0 or D(equity) <= 0 or D(settled_cash) < 0:
        return 0
    budget = D(equity) * RISK_FRACTION
    by_risk = (budget / risk).to_integral_value(rounding=ROUND_FLOOR)
    by_cash = (D(settled_cash) / entry_d).to_integral_value(rounding=ROUND_FLOOR)
    shares = min(by_risk, by_cash)
    if shares < 1:
        return 0
    return int(shares)


def dollar_pnl(fill: Decimal | float, exit_price: Decimal | float, qty: int) -> Decimal:
    return (D(exit_price) - D(fill)) * D(qty)
