# Index dip bot: 19:00 UTC entry re-test and build (2026-10-28)

## Entry-hour re-test (`entry_hour.py`)
**Method:** RSI(2)<5 buy, using closes at a fixed UTC hour from Exness H1 bars. Spread is the median H1 spread at that hour, plus swap.
**Data:** H1 is dense only from **Jul 2021**. Before that the export has about 1 bar per day.

**Results:**
- 18:00–20:00 UTC works for US30, USTEC, JP225, DE30 (and US500, STOXX50).
- **UK100: no edge at any daytime hour.** Its D1 result came from the midnight close only. **Dropped and replaced by DE30.**

**19:00 UTC with the one-loss-per-month breaker (% of position, Jul 2021 – Oct 2026):**

| Index | Trades | Win% | Avg | Total | Max DD | Years positive |
|---|---|---|---|---|---|---|
| US30 | 45 | 60 | +0.37% | +16.5% | −3.3% | 5/6 |
| USTEC | 46 | 63 | +0.43% | +19.6% | −6.6% | 5/6 |
| JP225 | 41 | 59 | +0.28% | +11.5% | −5.8% | 6/6 |
| DE30 | 36 | 64 | +0.30% | +10.9% | −3.4% | 5/6 |

## Disaster stop test (`sl_test.py`, H1 path, 4 indices, 168 trades)
Results in R, where 1R = one ATR14 = the 1% risk.

| SL | Stops hit | Total | Worst trade | Max DD |
|---|---|---|---|---|
| none | 0 | +38.1R | −2.7R | −7.0R |
| 1.0 ATR | 32 | +27.8R | −2.0R | −6.8R |
| 2.0 ATR | 6 | +32.9R | −2.0R | −8.6R |
| **3.0 ATR** | 1 | +36.4R | −3.0R | −7.0R |

- Tight stops cut dips that later recover, and they do not reduce the drawdown.
- **Chosen: a 3 × ATR disaster stop as flash-crash insurance only.** Joseph agreed it should only trigger on abnormal moves.
- Gaps (overnight or weekend) fill at the open, below the stop.

## Bot (`strategies/index_dip_strategy.py` + `main_index_dip.py`)
**Indices and magics:** US30 20261028, USTEC 20261029, JP225 20261030, DE30 20261031. UK100 20261032 is disabled.

**Daily cycle, Mon–Fri at 19:00 UTC (20:00 Lagos):**
1. Exit the previous position.
2. Monthly breaker: a losing position entered this month blocks the index.
3. RSI(2) of the H1 closes ending at 19:00 must be < 5.
4. Lot = 1% of current balance ÷ (ATR14 D1 × $/pt/lot), floored to the lot step, never below the symbol's live volume_min.
5. Market BUY with no TP. The disaster SL is set at fill − `DISASTER_SL_ATR` (3.0) × ATR via TRADE_ACTION_SLTP right after the fill.
6. `manage()` re-adds the SL to any own position that has none (self-heal).

**Timing details:**
- Entry window is 19:00–19:10. Polling is every 60 s.
- Friday positions exit Monday 19:00. Weekend anchors map back to Friday.

**Verification:**
- All MT5 constants were checked against the MetaTrader5 5.0.6231 wheel.
- Replay through a fake MT5 on the H1 CSVs: the bot's entries match the backtest **162/162** (Sep 2021 – Oct 2026). This still holds after the SL change.
- SL placement and the self-heal path were both tested.

**Status:** delivered for **DEMO** (`LIVE = False`).