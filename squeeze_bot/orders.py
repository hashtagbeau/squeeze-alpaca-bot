"""Entry order: marketable limit, 10s then cancel, no chase. Partial fill counts."""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from squeeze_bot.constants import ORDER_TIMEOUT_S


@dataclass
class BrokerOrder:
    id: str
    status: str
    filled_qty: int
    filled_avg_price: float | None


@dataclass
class FillResult:
    status: str
    filled_qty: int
    filled_avg_price: float | None
    counts_as_trade: bool
    symbol_done: bool


@dataclass
class AccountPosition:
    symbol: str
    qty: int
    avg_entry: float
    current: float


class OrderBroker:
    """Minimal surface the entry and exit manager needs. AlpacaBroker implements it."""

    def list_positions(self) -> list[AccountPosition]:
        return []

    def submit_limit_buy(
        self,
        symbol: str,
        qty: int,
        limit_price: Decimal,
        extended_hours: bool,
    ) -> BrokerOrder:
        raise NotImplementedError

    def submit_limit_sell(
        self,
        symbol: str,
        qty: int,
        limit_price: Decimal,
        extended_hours: bool,
    ) -> BrokerOrder:
        raise NotImplementedError

    def get_order(self, order_id: str) -> BrokerOrder:
        raise NotImplementedError

    def cancel(self, order_id: str) -> None:
        raise NotImplementedError


def execute_entry(
    broker: OrderBroker,
    *,
    symbol: str,
    qty: int,
    limit_price: Decimal,
    extended_hours: bool,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    timeout_s: float = ORDER_TIMEOUT_S,
) -> FillResult:
    """One buy. Unfilled after timeout is cancelled and the symbol is done.

    A zero fill does not count as the daily trade. Any fill (including a partial)
    counts, the remainder is cancelled, and nothing is chased.
    """
    order = broker.submit_limit_buy(symbol, qty, limit_price, extended_hours)
    start = clock()
    while True:
        current = broker.get_order(order.id)
        if current.status == "filled" and current.filled_qty > 0:
            return FillResult(
                "filled",
                current.filled_qty,
                current.filled_avg_price,
                True,
                True,
            )
        terminal = current.status in {"canceled", "cancelled", "rejected", "expired"}
        if terminal and current.filled_qty == 0:
            return FillResult("cancelled", 0, None, False, True)
        if clock() - start >= timeout_s:
            if current.filled_qty > 0:
                if current.status != "filled":
                    broker.cancel(order.id)
                    current = broker.get_order(order.id)
                return FillResult(
                    "filled",
                    current.filled_qty,
                    current.filled_avg_price,
                    True,
                    True,
                )
            broker.cancel(order.id)
            current = broker.get_order(order.id)
            if current.filled_qty > 0:
                return FillResult(
                    "filled",
                    current.filled_qty,
                    current.filled_avg_price,
                    True,
                    True,
                )
            return FillResult("cancelled", 0, None, False, True)
        sleep(0.5)
