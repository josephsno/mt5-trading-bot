# Monthly Trend Strategy

A position trading strategy that uses the **monthly timeframe for directional bias**
and **M15 bars for entry timing**.

Derived from 25 months of backtested M15 data (Jun 2024 – Jun 2026) across EURUSDm
and USDJPYm on Exness. Designed to run fully automated in **live MT5** with no manual
intervention.

---

## 📌 Strategy Overview

- Prior month candle direction sets the trade bias for the entire month
- Entry only in Week 1–2 of the month — weeks 3 and 4 trend exhausts
- No M15 candle filters — monthly bias alone drives direction
- Structural stop loss based on prior day high/low (min 35 pips)
- Trailing stop triggered at 1R moves SL to breakeven immediately
- After breakeven trail by 20 pips per bar — captures full monthly moves
- Trailing also reduces losses — once 1R is reached the trade cannot lose
- One trade per symbol at a time

No fixed take profit. The trail handles all exits.

---

## 📊 Backtest Results (Exness spreads, Jun 2024 – Jun 2026)

| Pair | Trades | Win rate | Net pips | Net $ (0.01 lots) | Entry weeks |
|------|--------|----------|----------|-------------------|-------------|
| EURUSDm | 124 | 44% | +1,182p | +$118 | Week 1 + 2 |
| USDJPYm | 78 | 50% | +3,541p | +$273 | Week 1 only |
| **Combined** | **202** | | **+4,723p** | **+$391** | |

Trail settings: trigger at 1R, trail 20 pips after BE.

---

## 🕒 Entry Windows

### EURUSDm — Week 1 and Week 2 of month, Mon–Thu

| Window | UTC | WAT (UTC+1) |
|--------|-----|-------------|
| London open | 07:00–08:00 | 08:00–09:00 |
| NY overlap | 13:00–14:00 | 14:00–15:00 |

### USDJPYm — Week 1 of month only, Mon–Thu

| Window | UTC | WAT (UTC+1) |
|--------|-----|-------------|
| Tokyo open | 00:00 | 01:00 |
| London open | 07:00–08:00 | 08:00–09:00 |
| NY overlap | 13:00–14:00 | 14:00–15:00 |

**Week of month:** Day 1–7 = Week 1, Day 8–14 = Week 2, Day 15+ = no trade.

---

## 🚫 No-Trade Rules

- November and December — volatility too low
- Week 3 and Week 4 of every month — trend exhausts, reversals increase
- Friday — no new entries, hard close any open trade at 14:00 UTC (15:00 WAT)
- 21:00–23:00 UTC — spread too wide
- Weekends — never

---

## ⚙️ Entry Logic (all must pass in order)

1. Symbol is configured (EURUSDm or USDJPYm)
2. Current month is not November or December
3. Current day is Mon–Thu (Friday blocked)
4. Current hour is in the allowed window for this symbol
5. Current date is in Week 1 or 2 (EURUSDm) or Week 1 only (USDJPYm)
6. Prior month closed above its open → **BUY** | below → **SELL**
7. Structural SL (prior day low for buys, prior day high for sells) ≥ 35 pips
8. Strategy not paused (monthly failure or regime monitor)

No M15 candle condition. No EMA. No body ratio. No weekly confirmation.
The monthly bias alone determines direction.

---

## 🛡️ Trade Management

| Event | Action |
|-------|--------|
| Price reaches 1R | Move SL to breakeven — trade now risk-free |
| After breakeven | Trail SL by 20 pips tracking best price |
| Friday 14:00 UTC (15:00 WAT) | Hard close — no weekend holds |
| SL hit by MT5 | Clear trade state, look for next signal |

**SL is never widened. Never add to a position. No fixed TP.**

---

## 🔒 Protection Systems

### Monthly failure tracker
If 3 losses occur during active weeks in the same month, the strategy skips
all remaining entries for that symbol for the rest of the month.
Resets automatically on the 1st of each new month.

### Rolling regime monitor
Tracks last 6 trades across all symbols. If fewer than 2 wins in the last 6,
strategy pauses all entries until the win rate recovers.

---

## 💰 Position Sizing

```
lot = (balance × risk_pct%) / (sl_pips × pip_value_per_lot)
minimum lot = 0.01
```

Default risk: **1% per trade**. One position per symbol at a time.

At $9 account: expect $3–10 loss / $6–30 gain per trade at 0.01 lots.
At $100 account: lot sizing formula kicks in properly.

---

## 🔧 Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `risk_pct` | 1.0 | % of balance risked per trade |
| `min_sl_pips` | 35.0 | Minimum structural SL distance |
| `trail_trigger_r` | 1.0 | Move to breakeven at this R level |
| `trail_pips` | 20.0 | Trail distance after breakeven (pips) |

---

## ⚙️ Symbol Configuration

```python
SYMBOL_CONFIG = {
    "EURUSDm": {
        "pip":         0.0001,
        "spread":      0.00008,              # 8 points (Exness)
        "sl_buffer":   0.0003,
        "entry_weeks": [1, 2],               # Week 1 and 2 of month
        "entry_hours": [7, 8, 13, 14],       # London open + NY overlap
    },
    "USDJPYm": {
        "pip":         0.01,
        "spread":      0.018,                # 18 points (Exness)
        "sl_buffer":   0.03,
        "entry_weeks": [1],                  # Week 1 only
        "entry_hours": [0, 7, 8, 13, 14],   # Tokyo + London + NY overlap
    },
}
```

---

## 📁 Usage

```python
strategy = MonthlyTrendStrategy(
    risk_pct=1.0,
    min_sl_pips=35.0,
    trail_trigger_r=1.0,
    trail_pips=20.0,
    backtest_mode=False,
    initial_balance=100.0,
)

# Entry — call on every M15 bar close
signal = strategy.generate_signal("EURUSDm")
if signal["signal"]:
    # place order: signal["entry_price"], ["stop_loss"], ["lot_size"]
    # take_profit is None — trail manages the exit

# Trade management — call on every M15 bar close while trade is open
status = strategy.manage_open_trade("EURUSDm")

# When trade closes (SL hit or trail exit)
strategy.record_result("EURUSDm", was_win=True, current_month=6)

# New month reset
strategy.reset_month("EURUSDm", new_month=7)

# Check status
print(strategy.get_performance_summary("EURUSDm"))
```

---

## 📦 Data Fetched Internally per Symbol

| Timeframe | Bars | Purpose |
|-----------|------|---------|
| M15 | 300 | Entry timing |
| D1 | 5 | Structural SL (prior day high/low) |
| MN1 | 3 | Monthly bias direction |

No external data needed. Pass only the symbol.

---

## ⚠️ Key Findings from Backtest

- M15 entry conditions (EMA, body ratio, RSI) add no value — removed entirely.
- Monthly bias follow-through: 58.3% WR on EURUSD over 25 months — the real edge.
- Week of month is critical: all edge in Weeks 1–2. Weeks 3–4 consistently negative.
- Trailing at 1R beats fixed 3R by 10x in net pips — captures the large monthly moves.
- Trailing also eliminates losses on trades that reach 1R then reverse (exit at BE).
- Exness spreads: EURUSDm 0.8p, USDJPYm 0.18p — negligible cost.
- GBPUSD, EURJPY, CADJPY all failed — monthly bias WR too low on those pairs.
- Nov–Dec dead months. Mar, Apr, Jun strongest (+420p, +505p, +439p on EURUSD).
- USDJPY Week 1 strongest single window across all tests (+3,541p net).