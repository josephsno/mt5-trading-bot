# RSI Trend Streak Strategy

A flexible and practical trading strategy based on **RSI trend confirmation**
with optional EMA slope guidance and **progressive lot sizing using winning streaks**.

Designed to work in both **backtesting** and **live MT5 trading** environments.

---

## 📌 Strategy Overview

This strategy focuses on:
- RSI strength to determine trade bias
- Optional EMA slope for directional guidance
- Optional momentum candle confirmation
- Optional volume validation (tick/real volume supported)
- Dynamic lot sizing after consecutive wins
- Capital protection via balance stop logic

Risk–Reward is fixed at **1:1**.

---

## 🧠 Entry Logic

### Buy Conditions
- EMA slope is positive (optional bias)
- RSI is above the buy threshold (default: 35)
- Bullish momentum candle (optional)
- Volume above its moving average (optional)

### Sell Conditions
- EMA slope is negative (optional bias)
- RSI is below the sell threshold (default: 65)
- Bearish momentum candle (optional)
- Volume above its moving average (optional)

---

## 📊 Indicators Used

- **RSI** (Relative Strength Index)
- **EMA** (for slope / trend guidance)
- **Volume** (auto-detected: volume, tick_volume, real_volume)
- **Price Action** (momentum candle)

---

## 💰 Risk Management

### Stop Loss / Take Profit
- Fixed pip-based SL and TP
- Default **1:1 Risk–Reward**

### Dynamic Lot Sizing
- Backtest mode:
  - Lot increases after **2 consecutive wins**
- Live MT5 mode:
  - Lot increases if **last two closed MT5 trades were profitable**
- Lot resets after any loss

---

## 🛑 Profit Retention

Trading is automatically stopped when:
- Account balance rises above **50% of starting balance**

This prevents account blow-up and enforces discipline.

---

## 📊 Recommended Symbols with the best win rate
**USDJPYm**
**EURJPYm**
**GBPJPYm**
**NZDJPYm**
**EURUSDm**
**GBPUSDm**
---



## ⚙️ Backtest vs Live Mode

| Feature            | Backtest Mode | Live MT5 |
|-------------------|--------------|----------|
| Balance Source     | Internal     | MT5 Account |
| Lot Scaling        | Winning streak | Last 2 MT5 trades |
| Trade Tracking     | Manual update | MT5 history |

---

## 🧩 Example Usage

```python
strategy = RSIFlexibleStrategy(
    sl_pips=20,
    starting_lot=0.01,
    backtest_mode=True,
    use_volume_filter=True
)

signal = strategy.generate_signal(price_data)
