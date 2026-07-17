# Straddle Strategy — Per-Symbol Documentation

*Last updated: July 2026*

---

## Shared mechanics (all symbols)

Every symbol follows the identical structure — only the numbers differ:

1. At the symbol's `trigger_hour` (UTC), place a **buy stop** and **sell stop**
   simultaneously, straddling the current price by `offset`.
2. Whichever side fills first is the trade. The other is cancelled (OCO).
3. If neither fills by `cancel_hour`, both are cancelled — no trade that day.
4. Stop-loss is fixed at `sl` from entry. No fixed take-profit.
5. Once price moves `be_trigger` in favor, stop moves to breakeven.
6. From there, stop trails `trail` behind the best price reached.
7. **Adaptive max-hold deadline** — every position is force-closed 1 hour
   before its *own* symbol's next scheduled trigger time, whichever that
   turns out to be for that specific trade. This replaced an earlier flat
   24-hour cap: the flat version let a still-open trade silently block the
   next day's entry (the bot would just skip placing a new straddle that
   day) — 6.2% of the time on EURUSD, 3.2% on GBPUSD, 0.4% on USDJPY. The
   original backtest never modeled this skip behavior at all. Backtesting
   the adaptive rule directly showed it fixes the skip problem completely
   (every symbol back to its full original trade count) and *improved*
   3 of 4 symbols outright — GBPUSD nearly doubled (625p → 1,173p), USDJPY
   improved (2,285p → 2,823p), only EURUSD dipped slightly (702p → 567p).
   A trade that fills right at the trigger hour gets ~23 hours of runway;
   one that fills near the cancel deadline gets much less, because it
   specifically needs to be clear before tomorrow's entry.
8. Position size: 1% of current balance ÷ (SL × pip value), floored at the
   broker's 0.01 lot minimum.

**Cross-pair circuit breaker** (not symbol-specific): if 2 or more traded
symbols each show 2+ consecutive losses at the same time, all new entries
pause across every symbol until fewer than 2 remain flagged, or 96 hours
pass (hard fallback so it can't freeze permanently). A per-symbol version
of this breaker was tested and made results *worse* — it cuts a symbol off
right before its own recovery. The cross-pair version specifically targets
a real, evidenced failure: GBPUSD and USDJPY failed simultaneously for two
months in the 2024-2026 backtest, and this is what would have caught it.

All state — positions, pending orders, loss streaks — is read fresh from
MT5 on every call. Nothing is stored in memory.

---

## EURUSDm

| Parameter | Value |
|---|---|
| Trigger hour | 08:00 UTC |
| Cancel hour | 16:00 UTC |
| Offset | 15 pips |
| Stop-loss | 25 pips |
| Breakeven trigger | 25 pips |
| Trail | 20 pips |

**Why this hour:** 08:00 UTC sits at the front edge of London's peak
window (09:00-13:00 UTC clean-move probability 20-27%), catching the
session's real order flow and Eurozone data (mostly 07:00-10:00 UTC)
before the window fades.

**Backtest (Jun 2024 - Jun 2026, real Exness spread, $90 start, 1% risk):**
- 503 trades, 51.1% win rate, 567 pips, $146.67 final (adaptive deadline applied)
- Mean R:R 1.06:1 (median win 19.1p vs fixed 25p risk) — edge comes from
  win rate, not payoff asymmetry, padded by a thin tail of 50-150+ pip
  trend trades

**Validation status:** Strong. Independently landed on 08:00 UTC without
being tuned to match USDJPY — the single strongest piece of cross-pair
evidence in the whole strategy.

---

## USDJPYm

| Parameter | Value |
|---|---|
| Trigger hour | 08:00 UTC |
| Cancel hour | 16:00 UTC |
| Offset | 15 pips |
| Stop-loss | 25 pips |
| Breakeven trigger | 25 pips |
| Trail | 20 pips |
| Pip value | Dynamic — `1000 / price` USD per 1.0 lot (JPY-quoted) |

**Why this hour:** Same London-window logic as EURUSD. Landed on the
identical 08:00 UTC trigger independently, with no re-tuning — this
agreement across two unrelated pairs is the core evidence the London-open
timing effect is structural, not curve-fit.

**Backtest (same window/costs as above):**
- 513 trades, 54.0% win rate, 2823 pips, $278.22 final (adaptive deadline applied)
- Best-performing of the three FX pairs; fattest average edge (+4.45p vs
  EUR's +1.48p) — plausibly reflects JPY's typically larger absolute pip
  ranges relative to a fixed 25-pip stop, not necessarily a "better" edge
  in relative terms

**Validation status:** Strong, same basis as EURUSD.

---

## GBPUSDm

| Parameter | Value |
|---|---|
| Trigger hour | **04:00 UTC** — not 08:00, see below |
| Cancel hour | 12:00 UTC |
| Offset | 15 pips |
| Stop-loss | 25 pips |
| Breakeven trigger | 25 pips |
| Trail | 20 pips |

**Why this hour — read before changing it:** 08:00 UTC, which works for
EUR and JPY, **loses money on GBPUSD** (−377 pips backtested, would take
$90 to $52). UK economic data (GDP, CPI, employment) releases from the ONS
at 7:00am UK local time — 06:00 UTC in summer (BST), 07:00 UTC in winter
(GMT) — earlier than EU or US data. A straddle at 04:00 UTC sits ahead of
that catalyst and catches the real move; one at 08:00 UTC sits after it
and catches the retest/reversal instead. This was verified against actual
UK data release times, not just inferred from the backtest numbers.

**Backtest (same window/costs as above):**
- 504 trades, 51.0% win rate, 1173 pips, $207.26 final (adaptive deadline applied —
  nearly doubled the flat-24h-cap result of 625 pips, since GBPUSD was the pair
  most hurt by the old rule's silent day-skipping)
- Fast-stop (whipsaw) rate at 04:00 UTC: 6.2%, rising to 16.0% by 12:00
  UTC — direct evidence of entry quality degrading as the day moves past
  the UK catalyst window

**Validation status:** Weaker basis than EUR/JPY — needed pair-specific
re-tuning to be profitable at all, which is normally a red flag for
overfitting. Partially redeemed by the UK-data-timing explanation holding
up against real release times, not just fitting the numbers after the
fact. Was also one of the two pairs behind the worst drawdown in the
combined portfolio (Dec 2025-Feb 2026, alongside USDJPY) — treat as the
most fragile of the three FX legs.

---

## XAUUSDm

| Parameter | Value |
|---|---|
| Trigger hour | 01:00 UTC |
| Cancel hour | 09:00 UTC |
| Offset | $10 |
| Stop-loss | $20 |
| Breakeven trigger | $20 |
| Trail | $15 |
| Pip value | Fixed $100 per 1.0 lot (1.0 lot = 100 oz) |

**Why this hour:** Best of a 22-hour sweep. Sits in the early Asian
session, plausibly tied to Shanghai Gold Exchange / Asian physical demand
flows ahead of London/COMEX activity — this rationale is weaker than
GBPUSD's (not independently verified against a specific catalyst calendar
the way UK data releases were).

**Backtest (May 2024 - Jun 2026):**
- 473 trades, 60.0% win rate, $2460.45 final, at 240-point fixed spread,
  $90 start, adaptive deadline applied
- **Out-of-sample check passed:** win rate improved train (56.4%) → test
  (64.1%), and held up after normalizing for gold's price roughly doubling
  over the window (year-by-year % return: 2024 +0.125%, 2025 +0.068%,
  2026 +0.267% per trade) — the EURUSD pattern (strengthening
  out-of-sample), not the GBPUSD pattern (decaying)

**⚠️ Position-sizing risk — read this before trading gold live:**
At the broker's 0.01 lot minimum, a single stop-loss costs
`$20 × 100oz/lot × 0.01 lot = $20.00` — **~22% of a $90 account on one
trade.** This is a hard structural floor, not a tunable parameter; there
is no offset/SL combination that fixes it at this account size, because
0.01 lots is already the smallest position the broker allows. The signal
itself tested as real; the position size available at this balance does
not. `GOLD_MIN_BALANCE = 2000.0` in the code is the rough threshold where
this risk would fall back to a sane ~1%.

**Validation status:** Signal is genuinely promising and passed a real
out-of-sample check — but was enabled in the live code at explicit user
instruction, against the standing recommendation to park it until balance
supports safe sizing. **`GOLD_ENABLED = True` is live; the 22%-per-trade
risk above is not hypothetical, it will happen on the first gold
stop-loss.**

---

## Summary table

| Symbol | Hour (UTC) | Win rate | Result ($90 start) | Risk/trade at $90 | Confidence |
|---|---|---|---|---|---|
| EURUSDm | 08:00 | 51.1% | $146.67 | ~2.8% (lot floor) | High |
| USDJPYm | 08:00 | 54.0% | $278.22 | ~2.5% (lot floor) | High |
| GBPUSDm | 04:00 | 51.0% | $207.26 | ~2.8% (lot floor) | Moderate |
| XAUUSDm | 01:00 | 60.0% | $2460.45 | **~22% (lot floor)** | Signal: high / Sizing: unsafe |

## Portfolio-level notes

- All four combined on one shared $90 account backtest to +3,485% — but
  71% of that profit is XAUUSD alone, an artifact of its oversized
  position, not evidence of a better strategy. Max drawdown with gold
  included is worse (−30.7%) than without it (−24.1%), confirming it adds
  concentration risk, not diversification, at this account size.
- EUR/JPY/GBP without gold: +320.7% combined, −24.1% max drawdown.
- Monthly bias (from the original monthly-trend strategy) does **not**
  improve straddle entries — tested directly, no statistically significant
  difference, and actively backwards on USDJPY (counter-trend fills
  outperformed aligned ones). Keep the two strategies conceptually
  separate; do not try to combine the signals.