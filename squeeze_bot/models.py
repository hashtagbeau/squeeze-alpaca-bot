"""Shared data records. Rules stay in the domain modules."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum


class Tri(Enum):
    OK = "ok"
    FAIL = "fail"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class MinuteBar:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if self.start.tzinfo is None:
            raise ValueError("bar start must be timezone-aware")

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=1)


@dataclass(frozen=True)
class DailyBar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class NewsItem:
    published_at: datetime
    url: str
    headline: str = ""


@dataclass(frozen=True)
class MacdPoint:
    line: Decimal
    signal: Decimal


@dataclass
class CheckItem:
    item: str
    result: str
    source: str
    detail: str
    kind: str = "hard"


@dataclass
class Decision:
    symbol: str
    trade: bool
    grade: str
    reasons: list[str]
    items: list[CheckItem]
    price: float | None = None
    pct_change: Decimal | None = None
    rel_vol: Decimal | None = None
    float_shares: float | None = None
    news_url: str = ""
    entry: Decimal | None = None
    stop: Decimal | None = None
    target: Decimal | None = None
    rr: Decimal | None = None
    shares: int = 0
    extended_hours: bool = False
    row_type: str = "no-trade"
    largest_green: Decimal | None = None


@dataclass
class Observation:
    symbol: str
    now: datetime
    is_trading_day: bool
    halt_file_present: bool
    trades_today: int
    symbol_done: bool
    last_price: float | None
    last_price_ts: datetime | None
    prior_close: float | None
    prior_high: float | None
    prior_prior_close: float | None
    prior_day_volume: float | None
    daily_bars: list[DailyBar] = field(default_factory=list)
    minute_bars: list[MinuteBar] = field(default_factory=list)
    forming_volume: float = 0
    finviz_float: float | None = None
    sec_shares: float | None = None
    csv_float: float | None = None
    csv_row_present: bool = False
    news: list[NewsItem] = field(default_factory=list)
    news_cutoff: datetime | None = None
    luld_halted: bool = False
    luld_known: bool = True
    first_trade_date: date | None = None
    ipo_history_truncated: bool = False
    equity: float | None = None
    settled_cash: float | None = None


@dataclass(frozen=True)
class ExitSignal:
    reason: str
    limit_price: Decimal


@dataclass
class ExitTracker:
    bars_seen: int = 0
    half_r_reached: bool = False
    stall: int = 0


@dataclass
class ManagedPosition:
    symbol: str
    qty: int
    fill_price: Decimal
    stop: Decimal
    target: Decimal
    largest_green: Decimal
    filled_at: datetime
    planned_entry: Decimal
    tracker: ExitTracker = field(default_factory=ExitTracker)
    last_bar_start: datetime | None = None
