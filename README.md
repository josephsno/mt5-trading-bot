# Monthly Trend Strategy

A position trading strategy that uses the **monthly timeframe for directional bias**
and **M15 bars for precise entry timing**.

Derived from 18 months of backtested M15 data across EURUSDm and USDJPYm on Exness.
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

## 📊 Backtest Results (Exness spreads, Jan 2025 – Jun 2026)

| Pair | Trades | Win rate | Net pips | Avg/trade | Entry days |
|------|--------|----------|----------|-----------|------------|
| EURUSDm | 20 | 45.0% | +1,078p | +53.9p | Mon + Tue |
| USDJPYm | 28 | 43.0% | +843p | +30.1p | Wed + Thu |
| **Combined** | **48** | | **+1,921p** | | |

---

## 🕒 Entry Windows

### EURUSDm — Monday and Tuesday only

| Window | UTC | WAT (UTC+1) |
|--------|-----|-------------|
| London open | 07:00–08:00 | 08:00–09:00 |
| NY overlap | 13:00–14:00 | 14:00–15:00 |

### USDJPYm — Wednesday and Thursday only

| Window | UTC | WAT (UTC+1) |
|--------|-----|-------------|
| Tokyo open | 00:00–01:00 | 01:00–02:00 |
| London open | 07:00–08:00 | 08:00–09:00 |
| NY overlap | 13:00–14:00 | 14:00–15:00 |

---

## 🚫 No-Trade Rules

- November and December — volatility too low across all pairs
- Outside the entry days above — no new entries
- 21:00–23:00 UTC — spread too wide
- Friday — no new entries, hard close any open trade at 14:00 UTC (15:00 WAT)
- Weekends — never

---

## ⚙️ Entry Logic (all must pass in order)

1. Symbol is in configured pair list (EURUSDm or USDJPYm)
2. Current month is not November or December
3. Current day is an allowed entry day for this symbol
4. Current hour is in the allowed entry window for this symbol
5. Prior month closed above its open → **BUY bias** | below → **SELL bias**
6. Current week moving in same direction as monthly bias
7. Current M15 bar closes in bias direction
8. Candle body ≥ 50% of total range (momentum candle, not a wick spike)
9. Close above EMA50 for buys / below EMA50 for sells
10. Structural SL (prior day low for buys, prior day high for sells) ≥ 35 pips away

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

## ⚙️ Symbol Configuration

```python
SYMBOL_CONFIG = {
    "EURUSDm": {
        "pip":         0.0001,
        "spread":      0.00008,           # 8 points (Exness)
        "entry_days":  [0, 1],            # Mon, Tue
        "entry_hours": [7, 8, 13, 14],    # London open + NY overlap
        "sl_buffer":   0.0003,
    },
    "USDJPYm": {
        "pip":         0.01,
        "spread":      0.018,             # 18 points (Exness)
        "entry_days":  [2, 3],            # Wed, Thu
        "entry_hours": [0, 1, 7, 8, 13, 14],  # Tokyo + London + NY overlap
        "sl_buffer":   0.03,
    },
}
```

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
- Exness spreads are exceptional — EURUSDm 0.8p, USDJPYm 0.18p, vs 8.8p assumed initially.
- Tight spread added 100+ pips to EURUSD net result vs earlier broker assumptions.
- Tokyo open (00:00 UTC) on USDJPY Wed+Thu: 50% WR, +35.5p expectancy — strongest single window.
- GBPUSD, EURJPY, CADJPY all tested negative — monthly bias WR too low on those pairs.
- Nov–Dec dead months. Mar, Apr, Jun strongest months (+420p, +505p, +439p on EURUSD).