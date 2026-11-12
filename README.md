# MT5 Trading Bots — README

*Updated 2026-11-12. Full evidence, decisions and rejected ideas live in the project's
**STRATEGY REGISTER — what works (keep updated)**. This file is the operating manual.*

All bots trade Joseph's Exness MT5 account, are written in Python with direct
MetaTrader5 calls, and are **stateless**: every decision is re-derived from MT5
(`positions_get()`, `orders_get()`, `history_deals_get()`, candles) on every poll.
A restart loses nothing.

**Plan:** wait out 2026, replay the strategies on the December data, start them
fresh on the live account in **January 2027**. **Risk rule:** 1% of the balance per
trade (minimum lot if 1% is smaller); the dip bot and pre-FOMC use 1% per 1-ATR move.

---

## The six strategies

| # | Strategy | Main file → strategy file | Symbols | Magic | `LIVE` |
|---|---|---|---|---|---|
| 1 | Gold straddle 00:00 + 01:00 UTC | `main_gold_straddle.py` → `strategies/gold_straddle_strategy.py` | XAUUSDm | 20261011 / 20261012 | as set (live) |
| 2 | Index dip buy, RSI(2) at 19:00 UTC | `main_index_dip.py` → `strategies/index_dip_strategy.py` | US30m, USTECm | 20261028 / 20261029 | True |
| 3 | Pre-FOMC drift (runs inside the dip bot) | `main_index_dip.py` → `strategies/pre_fomc_strategy.py` | US30m, USTECm, US500m | 20261034 / 35 / 36 | True |
| 4 | News straddle NFP / CPI / FOMC | `main_news_spike.py` → `strategies/news_spike_strategy.py` | XAUUSDm | 20260807 | False (demo) |
| 5 | Gold "hat" long, winter only | `main_gold_hat.py` → `strategies/gold_hat_strategy.py` | XAUUSDm | 20261037 | False (demo) |
| 6 | USDJPY Asian-range break | `main_usdjpy_asian_range.py` → `strategies/usdjpy_asian_range_strategy.py` | USDJPYm | 20261038 | False (demo) |

Run **five processes** (one per main file; #2 and #3 share one). Every file has
its path on line 1. Stop an old copy of a bot before starting the new one.

### 1. Gold straddle — `strategies/gold_straddle_strategy.py`
- 00:00 and 01:00 UTC (Tokyo open, Shanghai Gold Exchange open): buy-stop / sell-stop at mid ± $10, OCO.
- SL $20; breakeven at +$20 (current price at the poll), then trail $15 behind the best M15 high/low.
- Unfilled stops expire 8 h later; close 1 h before the next trigger, Friday 20:00 UTC at the latest. 5-minute polls.
- **From 1 Jan 2027 (`FILTERS_FROM`), two daily checks:**
  - *Movement filter:* skip the day if the median 00:00–08:00 UTC range of the last 20 weekdays is below $14 (H1 bars).
  - *Week pause B:* weeks of the month 1–7 / 8–14 / 15–21 / 22–28 / 29–31; a week that lost in its last 2 traded months (both setups together, scored per 0.01 lot) is skipped for the next calendar month.
- Backtest (2018 + 2022–Oct 2026, ticks): +$2,307 per 0.01 lot, max DD $472 with both checks (vs +$2,010 / $598 without).

### 2. Index dip buy — `strategies/index_dip_strategy.py`
- 19:01–19:30 UTC, Mon–Fri: RSI(2) of the 19:00 closes < 5 → BUY. Exit 19:00 the next trading day.
- Stop: US30 1.31 × ATR14, USTEC 3 × ATR14. Size: 1% per 1-ATR move. 3-day pause after a losing trade; no monthly breaker.
- History is primed 18:50–18:59 and must read the same twice; a price cross-check rejects half-synced data (29 Oct 2026 bug).

### 3. Pre-FOMC drift — `strategies/pre_fomc_strategy.py`
- BUY 14:00 New York the day before each scheduled Fed decision; close 13:59 NY on decision day.
- Size 1% per 1-ATR move; disaster stop 3 × ATR (never hit in 43 meetings).
- Dates in `FOMC_DATES` (9 Dec 2026 → 26 Jan 2028); the bot warns when they run out.

### 4. News straddle — `strategies/news_spike_strategy.py`
- 2 s before NFP / CPI (08:30 NY) and FOMC (14:00 NY): buy-stop / sell-stop at mid ± $3, SL $7 (re-anchored to the real fill; closed at market if price is already past it), no OCO.
- Everything closed / cancelled at release + 60 s; anything of its own found outside that span is closed.
- Risk **1%** (was 14% — why it was stopped in October).
- **Touches only its own magic.** The old version closed every gold position of any bot before each release and the whole account after it; that is now behind `FLATTEN_OTHER_BOTS = False`.
- Release times are computed from New York time (no hand-typed early-second literals); NFP / CPI dates from the BLS calendar through Dec 2027.
- 1-second polling from 2 min before a release; broker-corrected clock (the old main used the raw VPS clock).

### 5. Gold hat long — `strategies/gold_hat_strategy.py`
- Only while New York is on standard time (first Sunday of Nov → second Sunday of Mar), Mon–Thu.
- BUY 11:00 NY (16:00 UTC), SELL 02:00 NY next day (07:00 UTC). Stop 2 × ATR14 (average daily high–low).
- Skips nights touching a holiday closure (24/25 Dec, 31 Dec/1 Jan). ATR from weekday D1 bars only.
- Size 1% at the stop — with gold's ATR near $100 that is the minimum lot below roughly a $20,000 balance.

### 6. USDJPY Asian-range break — `strategies/usdjpy_asian_range_strategy.py`
- Range = bid high / low 19:00–00:00 NY (M1 bars, read twice). At 00:00 NY: buy-stop at the high, sell-stop at the low, SL at the other edge, both live (stop-and-reverse).
- If price is already through an edge at 00:00 (~5% of days), that side enters at market.
- Close everything at 09:00 NY. Mon–Fri; thin evenings (< 120 M1 bars) skipped. Size 1% of the range per side.
- USDJPY only — approved as a single-pair exception (the same rules lose on EURUSD / GBPUSD).

---

## Performance tracker — `performance_tracker.py`
Read-only; never places or changes a trade.
```
python performance_tracker.py                 # live account, from 1 Jan 2027
python performance_tracker.py --demo          # demo account
python performance_tracker.py --since 2026-11-01
```
Writes `reports/strategy_performance_<live|demo>_<date>.xlsx`: **Summary** (trades, win %
vs backtest win %, net $, avg win/loss, profit factor, max drawdown, best/worst, open now),
**Monthly** (net $ per strategy per month), **Trades** (every closed trade — wins green,
losses red), **Open**. Needs `pip install openpyxl`. Run once normally and once with
`--demo` while some bots are still on demo.

---

## Shared conventions (all bots)
- **Own magic only.** Every bot reads and touches only its own magic numbers. Trade history is
  read per position (an SL exit is a server deal and may not carry the magic).
- **Broker time, not the VPS clock.** The clock is corrected from the latest tick (milliseconds);
  only real gaps (≥ 5 s, e.g. the 64 s seen on 2026-09-14) are corrected. Keep Windows time sync on.
- **New York time without tzdata.** The US daylight-saving rule is in the code (Windows Python has
  no time-zone database by default). Verified against zoneinfo for 2021–2028.
- **History must be trusted.** Candle and deal reads are repeated until two reads agree; a failed or
  half-loaded read skips the decision instead of trading on bad data.
- **Missed-poll safety.** Every bot closes its own leftovers at the first poll after its exit time.
- **MT5 API:** every `order_send()` is checked for `None` before `.retcode`; filling mode comes from
  the `symbol_info().filling_mode` bitmask (the Python package has no `SYMBOL_FILLING_*` names —
  `mt5.SYMBOL_FILLING_IOC` crashed live); comments are kept short (31 characters was rejected live);
  pending orders use `ORDER_TIME_GTC` where a short broker-side expiry was refused (10022).
- **Hedging mode required.** Several bots can hold gold or indices at the same time.
- **Login:** `LIVE = True` uses `MT5_USERNAME / MT5_PASSWORD / MT5_SERVER`, `False` uses the
  `_TRIAL` keys (`.env`, read with python-decouple). Mains never print the password.

## Magic numbers in use
| Magic | Owner |
|---|---|
| 20260716 | old `straddle_strategy.py` (FX / gold session straddle) — not part of the 2027 plan |
| 20260801 / 20260810 | old news confirm / news reload — not part of the 2027 plan |
| 20260807 | news straddle (#4) |
| 20261003 / 20261009 / 20261010 | old gold scalp / USTEC bots |
| 20261011 / 20261012 | gold straddle 00:00 / 01:00 (#1) |
| 20261028 / 20261029 | dip US30 / USTEC (#2); 20261031–33 DE30 / UK100 / JP225 (off) |
| 20261030 | USTEC reopen straddle (demo lead) |
| 20261034 / 35 / 36 | pre-FOMC US30 / USTEC / US500 (#3) |
| 20261037 | gold hat long (#5) |
| 20261038 | USDJPY Asian range (#6) |

## Yearly upkeep
- **Calendars:** add next year's FOMC dates to `pre_fomc_strategy.py` (`FOMC_DATES`) and
  `news_spike_strategy.py` (`EVENT_DATES["FOMC"]`), and next year's NFP / CPI dates from
  bls.gov/schedule to `EVENT_DATES`. Both bots warn ahead of time.
- **Daily gold break:** XAUUSDm pauses about 20:58–22:02 UTC; "market likely closed" messages in
  that window are expected.

## Open items
- [ ] December 2026 data replay of all six before the January 2027 start.
- [ ] Recheck the gold straddle's $14 threshold on Nov–Dec 2026 data.
- [ ] Demo checks: news straddle on NFP Fri 4 Dec 2026 13:30 UTC; pre-FOMC on 8–9 Dec 2026; hat long and USDJPY range from now.
- [ ] Contract sizes for BTCUSDm / XAGUSDm (old straddle bot) were never verified — only matters if that bot is used again.