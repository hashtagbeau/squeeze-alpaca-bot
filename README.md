# squeeze-alpaca-bot

Deterministic small-cap momentum bot. It trades an Alpaca **paper** account only. Every scanner filter, exclusion, setup check, grade, size, and exit is ordinary Python. Nothing in the order path calls a model.

`SPEC.md` is the signed-off rule set. `params.md` is the owner's parameter note. Where they disagree after the spec's "v1 sign-off adds" section, the spec wins. The choices that the spec left open are listed under [Resolved ambiguities](#resolved-ambiguities).

How the pieces fit, with the formulas and made-up walkthroughs: [THE_SQUEEZE_LAB.md](THE_SQUEEZE_LAB.md).

## Setup

Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Put a paper key and secret in `.env`. The file is gitignored. `.env.example` has names only.

```
ALPACA_API_KEY_ID=
ALPACA_API_SECRET_KEY=
```

The trading base URL is hardcoded to `https://paper-api.alpaca.markets`. The process refuses to start if any environment value points at the live trading host (`api.alpaca.markets` without the `paper-` prefix), or if `ALPACA_PAPER` / `APCA_API_PAPER` / `PAPER_TRADING` is turned off, or if `ALPACA_LIVE` is turned on. `APCA_API_KEY_ID` is not read. Market data uses `https://data.alpaca.markets`, which is not a live trading URL.

Optional path and feed overrides (defaults shown):

| Variable | Default |
| --- | --- |
| `SQUEEZE_FLOAT_CSV` | `/home/box/squeeze-trader/float.csv` |
| `SQUEEZE_HALT_FILE` | `/home/box/squeeze-trader/HALT` |
| `SQUEEZE_LOG_CSV` | `/home/box/squeeze-trader/log.csv` |
| `SQUEEZE_CHECKLIST_PATH` | `/home/box/squeeze-trader/logs/checklist.jsonl` |
| `SQUEEZE_STATE_PATH` | `/home/box/squeeze-trader/state.json` |
| `SQUEEZE_DATA_FEED` | `iex` |

`float.csv` columns are `symbol,float,source,ts_mt`. The owner appends rows. This bot only reads the file.

`pytz` is a direct dependency because `alpaca-py` 0.42.2 imports it from `alpaca.data` and does not declare it.

## Tests

No network. Alpaca clients are mocked and bars are synthetic.

```bash
ruff check .
pytest
```

GitHub Actions (`.github/workflows/ci.yml`) runs the same two commands on Python 3.12.

## Dry-run

Replay pulls historical 1-minute bars from Alpaca's data API and runs the same checklist, order decision, and exit rules. It needs the paper keys. It does not submit orders, and it does not invent a result when the fetch fails.

```bash
python -m squeeze_bot replay --symbol TICKER --date 2026-10-28
```

`--date` is the America/New_York session date. Printed events are `fill`, `exit`, `cancel`, and `open` (still in a position when the bars end). Checklist and log rows still use `mode=paper`, with `source=replay` in `reasons`.

## Paper run

```bash
python -m squeeze_bot run
```

The loop uses the Alpaca clock. On startup it flattens any open equity position and records that as the day's trade so a restart cannot enter again. New entries are considered on weekdays from 07:30:00 ET up to, but not including, 10:00:00 ET, and only when the Alpaca calendar says the date is a trading day. Exit management keeps running until the position is flat. One filled trade per day.

Logs:

- `checklist.jsonl` — one object per checklist item: `timestamp` (America/Edmonton), `ticker`, `item`, `result` (`PASS` or `FAIL`), `source`, `detail`.
- `log.csv` — header `timestamp_mt,ticker,type,grade,price,pct_change,rel_vol,float,news_url,entry,stop,target,rr,exit,pnl,mode,reasons`. `mode` is always `paper`. `timestamp_mt` is America/Edmonton.

## Kill switch

```bash
touch /home/box/squeeze-trader/HALT
```

While that file exists, the bot writes a `kill_switch` FAIL and takes no new entries. Open positions are still exited by the stop, target, and other exit rules. Delete the file to allow entries again. Override the path with `SQUEEZE_HALT_FILE`.

## Resolved ambiguities

These are the readings used where the spec or `params.md` does not pin a formula, a boundary, or a data source. They are implemented as written here and covered by tests.

1. **Grade B.** The earlier `params.md` line "B: float 5-20M or R:R 2-3" is narrower than the later params line and the spec: "B = passes all but misses an A condition." The spec wins. Grade A is `float < 5M` and `R:R >= 3` and prior-day volume `< 100K` and `rvol >= 100`. Uncertain inputs are grade C and are not traded. A clean hard-rule failure is an empty grade and is not traded. A and B require every checklist item `PASS`.

2. **Relative volume.** The denominator is the mean of the prior 30 trading days' daily volume, excluding today. Fewer than 30 sessions is uncertain. The numerator is cumulative 1-minute volume from the 04:00 ET session, plus the forming bar, because the scan runs in the premarket and Alpaca daily bars are regular-hours volume.

3. **Float.** Disagreement is `abs(a - b) / min(a, b)`. Exactly 20% still agrees. When both sources exist and agree, the finviz number is the float. SEC shares outstanding is used only when finviz is missing. Both missing, or a disagreement above 20%, fails the checklist as uncertain. A `float.csv` row that is present and more than 20% off that number fails. A missing row does not. The bot does not write `float.csv`.

4. **IPO.** Age is the earliest Alpaca daily bar inside a 180-day lookback. Younger than 90 calendar days is excluded. Day 90 is allowed. If the earliest bar sits on the lookback start and the age is at least 90 days, the history is treated as truncated and the name is not an IPO.

5. **Second day.** The reference is the close before the prior session. The prior day fails the gate when its close is at least 50% above that reference, or when its high is. A missing reference is uncertain.

6. **Pop-and-fade.** The base is the prior close. A day qualifies when `(high - prior close) / prior close >= 50%` and `(high - close) / (high - prior close) >= 50%`. The window is the 30 calendar days before today, including day 30 and excluding day 31 and today.

7. **Red-to-green.** The session low is strictly below the prior close and the last trade is strictly above it. A name that is still red, or that never traded strictly below the prior close, is not excluded.

8. **LULD.** Alpaca has no REST halt endpoint. A halt is a quote condition or reason of `M`, `LULD`, `LUDP`, `T5`, `T12`, `VOLATILITY_PAUSE`, or `VOLATILITY TRADING PAUSE`, or a message containing `LULD` or `VOLATILITY TRADING PAUSE`. A failed quote read is unknown and is a no-trade.

9. **VWAP and daily dates.** VWAP is the session typical price `(H+L+C)/3`, reset at 04:00 ET. The pullback low must be strictly above the VWAP of the bar that printed that low. A daily bar's date is the Eastern calendar date of its timestamp. Midnight Eastern is not rolled back to the prior session.

10. **Leg, pullback, ABCD.** Only bars with `start >= news_time` belong to the leg. The leg low is the lowest low from the first of those bars through the high-of-day bar. Pullback bars are the bars after that high that do not print a strictly higher high. A new high of day after a pullback has started marks the symbol done for the day (first pullback only).

11. **Heavy selling before entry.** A red bar is `close < open`. A doji is neither red nor green. A red bar after the high of day whose volume is strictly greater than the largest green leg bar excludes the symbol for the rest of the day. If the leg has no green bar, the check is uncertain.

12. **MACD.** EMA is SMA-seeded with `k = 2/(n+1)`, computed as one fraction so the seed stays exact. A bearish cross is previous line `>=` signal and current line `<` signal. A cross before the news does not end the front side. The first cross on a bar that starts at or after `news_time` ends it for the day. Sitting below the signal without a new cross does not, by itself, end the front side. Entry still requires line `>` signal and line `>` 0. If no MACD value exists after the news, the front side is uncertain.

13. **Limit price and R:R.** The buy limit is the prior completed bar's high plus `min($0.02, 1% of that high)`, rounded up to the tick (`$0.01` at or above `$1`, otherwise `$0.0001`). Pre-order R:R uses that limit as the entry, not the last trade. The target is the high of day when `(HOD - limit) / risk >= 2`. Otherwise it is the single next `$0.50` increment strictly above the high of day, and only if that level pays at least 2R. Otherwise there is no trade. After the fill, the target level is not recomputed. The log's entry is the fill. Realized R:R and the 0.5R time stop use the fill.

14. **Exits.** On one closed bar, stop wins over target. Priority is stop, target, failure to reach `+0.5R` within 3 bars after the fill, a red bar heavier than the largest green leg bar, 3 bars in a row that do not trade the target, then a MACD bearish cross. The fill bar does not count toward those 3 bars. Exactly `+0.5R` counts as reached. An intrabar tick checks stop, then target. A target exit is a limit at the target. Other sells are marketable limits (reference minus the same buffer, rounded down) with `extended_hours=True` so a premarket position can flatten. The entry sets `extended_hours` only before 09:30 ET.

15. **Size and cash.** Risk uses account equity. Settled cash is `non_marginable_buying_power`, otherwise `cash`. Shares are `floor(0.01 * equity / (entry - stop))`, capped by `floor(settled cash / entry)`.

16. **Unfilled and partial orders.** No fill within 10 seconds: cancel, mark the symbol done for the day, and do not count it as the daily trade, so another symbol can still be tried. A partial fill counts as the daily trade; the remainder is cancelled; there is no chase.

17. **Clock.** The scan window is a weekday with `07:30:00 <= t < 10:00:00` ET, and the Alpaca calendar must list that date. `clock.is_open` is not used, because it is false in the premarket. Entries require a timestamp strictly before 10:00:00 ET. Exit management continues until flat. The spec names no end-of-day flatten. Replay fetches bars through 20:00 ET only as a data window. The scanner runs every 60 seconds. The position is checked about once a second.

18. **Freshness versus data loss.** At order time the quote or last trade must be at most 5 seconds old and the bars at most 60 seconds old. While a position is open, a failed fetch, or a newest closed bar older than 120 seconds, is data loss and the position is flattened.

19. **Restart.** Every open equity position is flattened with a marketable limit and logged as `restart_flatten`. `state.json` stores the session date, the trade count, and symbols that are done, so a restart cannot take a second trade.

20. **Universe and news.** The spec does not name the scan universe. The live loop uses Alpaca's top 50 stock gainers. Replay takes the symbol you pass. News must be strictly after the prior session's 16:00 ET and must have a URL. The earliest such article is `news_time`. A timestamp exactly at the cutoff does not qualify. Replay ignores articles published after the simulated clock, and ignores LULD timestamps after that clock.
