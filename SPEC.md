# squeeze-alpaca spec v1 (signed off by squeeze trader) (paper only: paper-api.alpaca.markets)
All times ET. Source of truth: squeeze trader rules + /home/box/squeeze-trader/params.md.

## scanner (07:30:00-09:59:59 weekdays, every 60s)
- price <= 9.00
- day change vs prior close >= +50%
- rvol = cum vol today / 30d avg daily vol >= 100
- float < 20M: finviz free, SEC shares-outstanding fallback; disagree >20% or both missing = FAIL
- news: alpaca news for symbol published after prior 16:00 close; url stored; none = FAIL
- exclusions (any = FAIL): IPO < 90d; second-day (prior day >= +50% close-to-close OR prior-day HOD >= +50%);
  pop-and-fade (any day in last 30 cal days with intraday gain >= 50% that gave back >= 50% by close);
  LULD halt today before entry; red-to-green today

## per-symbol state (1-min bars)
- news_time = first qualifying article ts
- squeeze leg = leg low after news_time -> HOD
- front side ends at first MACD(12,26,9) line<signal cross after news_time (permanent for day)
- ABCD: if a pullback happened and price then made a new HOD, symbol done for day (first pullback only)

## entry (conditions on last CLOSED bar; trigger intrabar)
- front side live; first pullback after leg
- avg pullback bar vol <= 0.5 * avg leg bar vol
- pullback low > VWAP; retrace <= 50% of leg
- no red bar after HOD with vol > largest green leg bar vol
- MACD line > signal and > 0
- trigger: last trade > prior completed bar high
- time < 10:00:00; trades today == 0
- R:R >= 2 to HOD, else next half/whole dollar if >= 2R, else no trade
- grade: A = float<5M & R:R>=3 & prior-day vol<100K & rvol>=100; B = passes all but misses an A cond; C (anything marginal/uncertain) = no trade
- freshness at order time: quote/last <= 5s old, bars <= 60s; price + R:R recomputed just before order
## order
- marketable limit @ prior bar high + min($0.02, 1% price); extended_hours=true pre-09:30
- not filled in 10s -> cancel, no trade, no chase
- size = floor(0.01*equity/(entry-stop)), capped at settled cash (cash account)
## stop + exits (software-managed pre-open; first hit wins, one exit)
- stop = lowest pullback low
- target hit
- not +0.5R within 3 bars after fill -> market(able) exit
- red bar vol > largest green leg bar vol
- 3 straight bars fail to break HOD/target
- MACD bearish cross
- data loss/restart while in position -> flatten via marketable limit, log
## logging
- every checklist item PASS/FAIL + source + ts -> logs/checklist.jsonl
- trades/no-trades -> /home/box/squeeze-trader/log.csv (squeeze trader columns, mode=paper)
## env
ALPACA_API_KEY_ID, ALPACA_API_SECRET_KEY (paper; base url hardcoded)

## v1 sign-off adds (squeeze trader)
1. pre-order R:R uses limit price as entry; log actual fill + real R:R after.
2. cancelled/unfilled order -> symbol done for day (does not count as daily trade).
3. single exit; target = the level that gave >= 2R (HOD or half/whole $). no partials/trailing.
4. entries stop 10:00:00; exit management runs until flat.
5. alpaca clock/calendar: no holidays, nothing outside hours above.
6. float.csv (/home/box/squeeze-trader/float.csv: symbol,float,source,ts_mt): row present & >20% off = FAIL; row missing = use own two sources.
7. kill switch: /home/box/squeeze-trader/HALT exists -> no new entries.
