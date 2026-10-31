"""Signed-off thresholds. SPEC.md wins over params.md after the v1 sign-off section."""

from datetime import time
from decimal import Decimal

PAPER_BASE_URL = "https://paper-api.alpaca.markets"
DATA_BASE_URL = "https://data.alpaca.markets"

MAX_PRICE = Decimal("9")
MIN_DAY_CHANGE = Decimal("0.50")
MIN_RVOL = Decimal("100")
MAX_FLOAT = 20_000_000
GRADE_A_FLOAT = 5_000_000
FLOAT_DISAGREE = Decimal("0.20")
MIN_RR = Decimal("2")
GRADE_A_RR = Decimal("3")
GRADE_A_PRIOR_VOL = 100_000
PULLBACK_VOL_RATIO = Decimal("0.5")
MAX_RETRACE = Decimal("0.5")
RISK_FRACTION = Decimal("0.01")
LIMIT_CENTS = Decimal("0.02")
LIMIT_PCT = Decimal("0.01")
HALF_R = Decimal("0.5")
QUOTE_MAX_AGE_S = 5
BAR_MAX_AGE_S = 60
# Two missed minute bars. Order freshness stays at 60s; see README.
DATA_LOSS_AGE_S = 120
ORDER_TIMEOUT_S = 10
SCAN_INTERVAL_S = 60
IPO_MAX_AGE_DAYS = 90
IPO_LOOKBACK_DAYS = 180
POP_LOOKBACK_DAYS = 30
ADV_DAYS = 30
TIME_STOP_BARS = 3
STALL_BARS = 3
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

SCAN_START = time(7, 30, 0)
ENTRY_CUTOFF = time(10, 0, 0)
RTH_OPEN = time(9, 30, 0)
VWAP_ANCHOR = time(4, 0, 0)
PRIOR_CLOSE_TIME = time(16, 0, 0)
MANAGE_UNTIL = time(20, 0, 0)

CSV_HEADER = (
    "timestamp_mt,ticker,type,grade,price,pct_change,rel_vol,float,"
    "news_url,entry,stop,target,rr,exit,pnl,mode,reasons"
)
CSV_COLUMNS = CSV_HEADER.split(",")

# Quote / halt-reason tokens treated as an LULD pause. Alpaca has no halt REST endpoint.
LULD_TOKENS = frozenset(
    {
        "M",
        "LULD",
        "LUDP",
        "T5",
        "T12",
        "VOLATILITY_PAUSE",
        "VOLATILITY TRADING PAUSE",
    }
)
