# Monthly Trend Strategy

A position trading strategy that uses the **monthly timeframe for directional bias**
and **M15 bars for precise entry timing**.

Derived from 18 months of backtested EURUSD M15 data on a real broker account.
Designed to run fully automated in **live MT5** with no manual intervention.

---

## 📌 Strategy Overview

- Monthly candle direction sets the trade bias for the entire month
- Weekly candle confirms the bias before any entry is allowed
- M15 entry trigger fires only during high-volume session windows
- Structural stop loss based on prior day high/low
- Trailing exit captures large monthly moves
- One trade per symbol at a time

Risk–Reward is fixed at **1:3 minimum** with trailing after 2R.

---

## 📊 Backtest Results (EURUSDm, Jan 2025 – Jun 2026)

| Metric | Value |
|--------|-------|
| Win rate | 58.8% |
| Net pips | +964p |
| Avg per trade | +56.7p |
| Total trades | 17 |
| Monthly average | +57p |

---

## 🕒 Entry Windows (UTC)

| Window | UTC | WAT (UTC+1) |
|--------|-----|-------------|
| London open | 07:00–08:00 | 08:00–09:00 |
| NY overlap | 13:00–14:00 | 14:00–15:00 |

**Entry days: Monday and Tuesday only.**

---

## 🚫 No-Trade Rules

- November and December — volatility too low
- Wednesday, Thursday, Friday — no new entries
- 21:00–23:00 UTC — spread too wide (18–29 pips)
- Weekends — never

---

## ⚙️ Entry Logic (all must pass)

1. Prior month closed above its open → **BUY bias** | below → **SELL bias**
2. Current week moving in same direction as monthly bias
3. Current M15 bar closes in bias direction
4. Candle body ≥ 50% of total range (momentum candle)
5. Close above EMA50 for buys / below EMA50 for sells
6. Structural SL (prior day low for buys, prior day high for sells) ≥ 35 pips away

---

## 🛡️ Trade Management

| Event | Action |
|-------|--------|
| Price reaches 2R | Move SL to breakeven |
| After breakeven | Trail SL by 30 pips per M15 bar close |
| Friday 14:00 UTC (15:00 WAT) | Hard close — no weekend holds |
| SL or TP hit by MT5 | Clear trade state, look for next signal |

**SL is never widened. Never add to a position.**

---

## 💰 Position Sizing

```
lot = (balance × risk_pct%) / (sl_pips × pip_value_per_lot)
minimum lot = 0.01
```

Default risk: **1% per trade**. One position per symbol at a time.

---

## 🔧 Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `risk_pct` | 1.0 | % of balance risked per trade |
| `min_sl_pips` | 35.0 | Minimum structural SL distance |
| `tp_rr` | 3.0 | Take profit as multiple of SL |
| `trail_trigger_r` | 2.0 | R level that triggers breakeven |
| `trail_pips` | 30.0 | Trail distance after breakeven |
| `ema_period` | 50 | EMA for trend confirmation |

---

## 📁 Usage

```python
strategy = MonthlyTrendStrategy(
    risk_pct=1.0,
    min_sl_pips=35.0,
    tp_rr=3.0,
    trail_trigger_r=2.0,
    trail_pips=30.0,
    ema_period=50,
    backtest_mode=False,
    initial_balance=100.0,
)

# Entry — call on every M15 bar close
signal = strategy.generate_signal("EURUSDm")
if signal["signal"]:
    # place order with signal["entry_price"], ["stop_loss"], ["take_profit"], ["lot_size"]

# Trade management — call on every M15 bar close while trade is open
status = strategy.manage_open_trade("EURUSDm")

# When MT5 closes trade via SL or TP
strategy.clear_trade("EURUSDm")
```

---

## 📦 Data Fetched Internally per Symbol

| Timeframe | Bars | Purpose |
|-----------|------|---------|
| M15 | 300 | Entry trigger + EMA50 |
| D1 | 5 | Structural SL (prior day high/low) |
| W1 | 3 | Weekly bias confirmation |
| MN1 | 3 | Monthly bias direction |

No external data needs to be passed. Pass only the symbol.

---

## ⚠️ Key Findings from Backtest

- M15 continuation rate is 49.3% — a coin flip. No intraday scalp strategy works.
- Average adverse excursion = 27.7 pips. Any SL under 35 pips gets eaten by noise.
- Spread at best hours = 8.8 pips. Minimum 3:1 R:R required to overcome spread drag.
- Only positive-EV approach across 14 tested strategies: monthly trend alignment.
- Nov–Dec dead months. Mar, Apr, Jun strongest months (+420p, +505p, +439p).