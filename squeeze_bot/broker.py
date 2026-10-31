"""Alpaca paper adapter. Trading base URL is hardcoded to paper-api.alpaca.markets."""

from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_FLOOR

from squeeze_bot.clockutil import prior_session_close
from squeeze_bot.config import Settings
from squeeze_bot.constants import DATA_BASE_URL, IPO_LOOKBACK_DAYS, PAPER_BASE_URL
from squeeze_bot.floats import fetch_finviz_float, fetch_sec_shares, load_float_csv
from squeeze_bot.guard import PaperOnlyError, is_live_trading_url, paper_client_kwargs
from squeeze_bot.models import DailyBar, MinuteBar, NewsItem, Observation
from squeeze_bot.money import ET, D, to_et
from squeeze_bot.orders import AccountPosition, BrokerOrder
from squeeze_bot.scanner import earliest_daily, is_luld, prior_day_stats
from squeeze_bot.state import DayState


def _as_et(moment: datetime, *, naive_is_eastern: bool) -> datetime:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ET if naive_is_eastern else UTC)
    return to_et(moment)


def _whole(value: object) -> int:
    if value is None or value == "":
        return 0
    return int(D(value).to_integral_value(rounding=ROUND_FLOOR))


def minute_from_api(bar: object) -> MinuteBar:
    start = _as_et(bar.timestamp, naive_is_eastern=False)
    return MinuteBar(
        start=start,
        open=float(bar.open),
        high=float(bar.high),
        low=float(bar.low),
        close=float(bar.close),
        volume=float(bar.volume),
    )


def daily_from_api(bar: object) -> DailyBar:
    """Daily bar day is the ET calendar date. Do not roll midnight ET back to the prior session."""
    stamp = _as_et(bar.timestamp, naive_is_eastern=False)
    return DailyBar(
        day=stamp.date(),
        open=float(bar.open),
        high=float(bar.high),
        low=float(bar.low),
        close=float(bar.close),
        volume=float(bar.volume),
    )


def _order_view(order: object) -> BrokerOrder:
    status = getattr(order.status, "value", order.status)
    avg = order.filled_avg_price
    return BrokerOrder(
        id=str(order.id),
        status=str(status),
        filled_qty=_whole(order.filled_qty),
        filled_avg_price=None if avg is None else float(avg),
    )


class AlpacaBroker:
    """Paper trading client plus the market-data reads the checklist needs."""

    def __init__(self, trading, data, news, screener, settings: Settings) -> None:
        base = str(getattr(trading, "_base_url", ""))
        if "paper-api.alpaca.markets" not in base or is_live_trading_url(base):
            raise PaperOnlyError(f"trading client base URL is not paper: {base}")
        self.trading = trading
        self.data = data
        self.news = news
        self.screener = screener
        self.settings = settings
        self._float_cache: dict[str, tuple[float | None, float | None]] = {}
        self._calendar: list[date] | None = None

    @classmethod
    def from_env(cls, env, settings: Settings) -> "AlpacaBroker":
        kwargs = paper_client_kwargs(env)
        from alpaca.data.historical.news import NewsClient
        from alpaca.data.historical.screener import ScreenerClient
        from alpaca.data.historical.stock import StockHistoricalDataClient
        from alpaca.trading.client import TradingClient

        trading = TradingClient(
            kwargs["api_key"],
            kwargs["secret_key"],
            paper=True,
            url_override=PAPER_BASE_URL,
        )
        data = StockHistoricalDataClient(
            kwargs["api_key"],
            kwargs["secret_key"],
            url_override=DATA_BASE_URL,
        )
        news = NewsClient(kwargs["api_key"], kwargs["secret_key"], url_override=DATA_BASE_URL)
        screener = ScreenerClient(
            kwargs["api_key"],
            kwargs["secret_key"],
            url_override=DATA_BASE_URL,
        )
        return cls(trading, data, news, screener, settings)

    def now_et(self) -> datetime:
        clock = self.trading.get_clock()
        return _as_et(clock.timestamp, naive_is_eastern=True)

    def trading_dates(self, today: date) -> list[date]:
        if self._calendar is not None:
            return self._calendar
        from alpaca.trading.requests import GetCalendarRequest

        rows = self.trading.get_calendar(
            GetCalendarRequest(start=today - timedelta(days=14), end=today + timedelta(days=1))
        )
        self._calendar = [row.date for row in rows]
        return self._calendar

    def is_trading_day(self, today: date) -> bool:
        return today in set(self.trading_dates(today))

    def gainers(self) -> list[str]:
        from alpaca.data.enums import MarketType
        from alpaca.data.requests import MarketMoversRequest

        movers = self.screener.get_market_movers(
            MarketMoversRequest(market_type=MarketType.STOCKS, top=50)
        )
        return [row.symbol for row in movers.gainers]

    def list_positions(self) -> list[AccountPosition]:
        out = []
        for pos in self.trading.get_all_positions():
            qty = _whole(pos.qty)
            current = float(pos.current_price or pos.avg_entry_price)
            out.append(
                AccountPosition(
                    symbol=pos.symbol,
                    qty=qty,
                    avg_entry=float(pos.avg_entry_price),
                    current=current,
                )
            )
        return out

    def submit_limit_buy(self, symbol, qty, limit_price, extended_hours) -> BrokerOrder:
        return self._submit(symbol, qty, limit_price, extended_hours, "buy")

    def submit_limit_sell(self, symbol, qty, limit_price, extended_hours) -> BrokerOrder:
        return self._submit(symbol, qty, limit_price, extended_hours, "sell")

    def _submit(self, symbol, qty, limit_price, extended_hours, side: str) -> BrokerOrder:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest

        request = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            limit_price=float(limit_price),
            extended_hours=bool(extended_hours),
        )
        return _order_view(self.trading.submit_order(request))

    def get_order(self, order_id: str) -> BrokerOrder:
        return _order_view(self.trading.get_order_by_id(order_id))

    def cancel(self, order_id: str) -> None:
        self.trading.cancel_order_by_id(order_id)

    def account_equity(self) -> tuple[float, float]:
        account = self.trading.get_account()
        equity = float(account.equity or 0)
        settled_raw = account.non_marginable_buying_power or account.cash or 0
        return equity, float(settled_raw)

    def _feed(self):
        from alpaca.data.enums import DataFeed

        try:
            return DataFeed(self.settings.data_feed)
        except ValueError as exc:
            raise PaperOnlyError(f"unknown SQUEEZE_DATA_FEED {self.settings.data_feed}") from exc

    def minute_bars(self, symbol: str, start: datetime, end: datetime) -> list[MinuteBar]:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        result = self.data.get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Minute,
                start=start,
                end=end,
                feed=self._feed(),
            )
        )
        rows = result.data.get(symbol, [])
        return [minute_from_api(bar) for bar in rows]

    def daily_bars(self, symbol: str, start: date, end: date) -> list[DailyBar]:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        result = self.data.get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                start=datetime.combine(start, datetime.min.time(), tzinfo=ET),
                end=datetime.combine(end, datetime.min.time(), tzinfo=ET) + timedelta(days=1),
                feed=self._feed(),
            )
        )
        rows = result.data.get(symbol, [])
        return [daily_from_api(bar) for bar in rows]

    def latest_trade(self, symbol: str):
        from alpaca.data.requests import StockLatestTradeRequest

        request = StockLatestTradeRequest(symbol_or_symbols=symbol)
        payload = self.data.get_stock_latest_trade(request)
        return payload[symbol]

    def news_since(self, symbol: str, cutoff: datetime) -> list[NewsItem]:
        from alpaca.data.requests import NewsRequest

        result = self.news.get_news(NewsRequest(symbols=symbol, start=cutoff, limit=50))
        articles = result.data.get("news", [])
        items = []
        for article in articles:
            published = article.created_at
            if published.tzinfo is None:
                published = published.replace(tzinfo=UTC)
            items.append(
                NewsItem(
                    published_at=published,
                    url=article.url or "",
                    headline=article.headline or "",
                )
            )
        return items

    def luld_times(
        self, symbol: str, start: datetime, end: datetime
    ) -> tuple[list[datetime], bool]:
        """LULD quote timestamps in the window. known is False when the read fails."""
        from alpaca.data.requests import StockQuotesRequest

        try:
            result = self.data.get_stock_quotes(
                StockQuotesRequest(
                    symbol_or_symbols=symbol,
                    start=start,
                    end=end,
                    feed=self._feed(),
                )
            )
        except (OSError, RuntimeError, ValueError):
            return [], False
        stamps = []
        for quote in result.data.get(symbol, []):
            if not is_luld(getattr(quote, "conditions", None)):
                continue
            stamp = quote.timestamp
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
            stamps.append(stamp)
        return stamps, True

    def luld_today(self, symbol: str, start: datetime, end: datetime) -> tuple[bool, bool]:
        """Return (halted, known). known is False when the quote read fails."""
        stamps, known = self.luld_times(symbol, start, end)
        return (len(stamps) > 0, known)

    def floats_for(self, symbol: str) -> tuple[float | None, float | None, float | None, bool]:
        symbol = symbol.upper()
        if symbol not in self._float_cache:
            self._float_cache[symbol] = (
                fetch_finviz_float(symbol),
                fetch_sec_shares(symbol),
            )
        finviz, sec = self._float_cache[symbol]
        table = load_float_csv(self.settings.float_csv)
        if symbol not in table:
            return finviz, sec, None, False
        return finviz, sec, table[symbol], True

    def observe(
        self,
        symbol: str,
        now: datetime,
        *,
        halt: bool,
        trading_day: bool,
        state: DayState,
    ) -> Observation:
        local = to_et(now)
        today = local.date()
        dates = self.trading_dates(today)
        cutoff = prior_session_close(dates, today)
        session_start = datetime.combine(today, datetime.min.time(), tzinfo=ET).replace(hour=4)
        minutes = self.minute_bars(symbol, session_start, local)
        dailies = self.daily_bars(symbol, today - timedelta(days=IPO_LOOKBACK_DAYS), today)
        trade = self.latest_trade(symbol)
        trade_ts = trade.timestamp
        if trade_ts.tzinfo is None:
            trade_ts = trade_ts.replace(tzinfo=UTC)
        news = self.news_since(symbol, cutoff) if cutoff is not None else []
        halted, known = self.luld_today(symbol, session_start, local)
        finviz, sec, csv_value, csv_present = self.floats_for(symbol)
        prior_close, prior_high, prior_prior, prior_vol = prior_day_stats(dailies, today)
        first = earliest_daily(dailies)
        truncated = False
        if first is not None:
            request_start = today - timedelta(days=IPO_LOOKBACK_DAYS)
            truncated = first <= request_start
        equity, settled = self.account_equity()
        return Observation(
            symbol=symbol.upper(),
            now=local,
            is_trading_day=trading_day,
            halt_file_present=halt,
            trades_today=state.trades,
            symbol_done=state.is_done(symbol),
            last_price=float(trade.price),
            last_price_ts=trade_ts,
            prior_close=prior_close,
            prior_high=prior_high,
            prior_prior_close=prior_prior,
            prior_day_volume=prior_vol,
            daily_bars=dailies,
            minute_bars=minutes,
            finviz_float=finviz,
            sec_shares=sec,
            csv_float=csv_value,
            csv_row_present=csv_present,
            news=news,
            news_cutoff=cutoff,
            luld_halted=halted,
            luld_known=known,
            first_trade_date=first,
            ipo_history_truncated=truncated,
            equity=equity,
            settled_cash=settled,
        )
