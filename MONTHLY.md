# Monthly Candle Trail Strategy

A monthly-timeframe trend strategy, separate and independent from
`MonthlyTrendStrategy` (the M15-managed EURUSD/USDJPY bot). One position at a
time, held across multiple months, managed with a stop that only updates once
a month.

---

## How it works

**Entry** — At the start of each month, direction comes from the prior
month's own candle: close above open = buy, close below open = sell.

**Initial stop** — The midpoint of the *prior* month's high/low range:
`(prior_high + prior_low) / 2`. If that midpoint doesn't land on the correct
side of the entry price, or the distance is under 35 pips, the month is
skipped entirely rather than forcing an invalid trade.

**Trail** — Starts immediately, from month one — there is no waiting period.
Every time a new month completes, the stop moves to *that* month's midpoint,
but only if it's more favorable; it never loosens. No fixed take-profit — the
trail is what decides when the trend is over, not a price target.

**Hold time** — Multi-month by design. Average roughly 1–2 months; the
largest winners ran 4–5 months. A trade is not closed early just because it's
currently profitable at a month's end — that was tested and made results
worse.

**One trade per month, per symbol** — If a position is stopped out mid-month,
the next entry attempt doesn't happen until the *following* calendar month,
even if that stop-out happens on day one. This is checked live against MT5's
own trade history, not an in-memory flag.

**No circuit breaker** — Tested with a 3-loss/1-month pause; it helped one
pair (GBPUSD) and did nothing on the other two, so it was removed for
simplicity. Losses are simply followed by normal re-evaluation next month.

---

## Validated pairs (8-year backtest, $100 start, 1% risk per trade)

| Pair | Trades | Final balance | Return | Win rate |
|------|--------|----------------|--------|----------|
| EURUSDm | 39 | $280.40 | +180.4% | 56.4% |
| USDJPYm | 37 | $309.79 | +209.8% | 54.1% |
| GBPUSDm | 36 | $485.14 | +385.1% | 55.6% |

All three passed a raw monthly-bias hit-rate check (~58%) before the full
backtest was ever run — that check should be run first on any new candidate
pair, since it's a fast, reliable filter.

## Rejected pairs — do not enable without new evidence

| Pair | Raw hit rate | Why |
|------|--------------|-----|
| CADJPYm | 49.0% | Coin flip, no real signal |
| AUDUSDm | 43.8% | Signal is actually inverted; only 1 of 12 tested configs was profitable |
| USDCADm | 45.8% | Coin flip; best config doesn't match the pattern the good pairs share |

## XAUUSDm — signal is real, sizing is not safe yet

Raw monthly bias hit rate is a strong 59.8% (8-year sample). Not enabled
because the max single-trade risk hit **32.8% of the account** at $100
starting balance — gold's monthly range has expanded roughly 4x since 2018,
and the 0.01 lot floor can't shrink to compensate. Revisit once the account
is large enough, or a volatility-relative SL cap is added.

---

## Position sizing — read this before funding an account

Lot size is risk-based: `lot = (balance × 1%) / (SL distance in pips × pip
value)`, floored at the broker minimum of 0.01. **Below roughly $1,000–1,500,
this floor overrides the calculation on nearly every trade** — the strategy
was backtested entirely at $100, and 0.01 lot was used on essentially every
single trade throughout. This means realized risk per trade tracks whatever
that trade's SL width happens to be, not a clean 1% — this is the same
mechanism behind the widest single-trade risk numbers found in testing
(GBPUSD 15.2%, gold 32.8%). Sizing only starts behaving as intended once the
account is large enough that the formula naturally clears 0.01 lot on a
typical trade.

---

## Files

- `monthly_candle_trail_strategy.py` — the strategy class
- `monthly_candle_strategy_findings.md` — full research log: every idea
  tested today, what worked, what failed and why, kept so nothing gets
  re-tried from scratch

## Known open items

- EURJPY not re-tested with this method (original rejection predates this
  strategy; GBPUSD's own rejection turned out to be wrong once tested here)
- USDCHF untested — best remaining candidate for genuine diversification
  (different macro driver than the three USD-major pairs currently used)
- No live or demo track record yet — everything above is backtest only