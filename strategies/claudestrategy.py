"""
Monthly Trend Strategy — Final Version
=======================================
Edge   : Prior month direction predicts current month direction (58%+ WR)
Filter : Week 1-2 of month only (weeks 3-4 trend exhausts)
Entry  : First bar in allowed window aligned with monthly bias
SL     : Prior day structural low/high (min 35 pips)
Exit   : Trail from 1R, 20 pips per bar — captures full monthly moves
"""

from __future__ import annotations

import datetime
import os
from typing import Any, Dict, List, Optional



import pandas as pd

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None


# ---------------------------------------------------------------------------
# Per-symbol configuration
# ---------------------------------------------------------------------------

SYMBOL_CONFIG: Dict[str, Dict[str, Any]] = {
    "EURUSDm": {
        "pip":            0.0001,
        "spread":         0.00008,          # 8 points Exness
        "sl_buffer":      0.0003,
        "entry_weeks":    [1, 2],           # Week 1 and 2 of month only
        "entry_hours":    [7, 8, 13, 14],   # London open + NY overlap
    },
    "USDJPYm": {
        "pip":            0.01,
        "spread":         0.018,            # 18 points Exness
        "sl_buffer":      0.03,
        "entry_weeks":    [1],              # Week 1 only
        "entry_hours":    [0, 7, 8, 13, 14],  # Tokyo + London + NY
    },
}


def _pip(symbol: str) -> float:
    return SYMBOL_CONFIG.get(symbol, {}).get("pip", 0.0001)


def _spread(symbol: str) -> float:
    return SYMBOL_CONFIG.get(symbol, {}).get("spread", 0.0001)


def _sl_buffer(symbol: str) -> float:
    return SYMBOL_CONFIG.get(symbol, {}).get("sl_buffer", 0.0003)


def _entry_weeks(symbol: str) -> List[int]:
    return SYMBOL_CONFIG.get(symbol, {}).get("entry_weeks", [1, 2])


def _entry_hours(symbol: str) -> List[int]:
    return SYMBOL_CONFIG.get(symbol, {}).get("entry_hours", [7, 8, 13, 14])


def _week_of_month(dt: pd.Timestamp) -> int:
    """Return week number within the month (1–4)."""
    return (dt.day - 1) // 7 + 1


def _round_price(price: float, symbol: str) -> float:
    pip = _pip(symbol)
    if pip == 0.01:
        return round(price, 3)
    return round(price, 5)


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class MonthlyTrendStrategy:

    def __init__(
        self,
        risk_pct: float = 1.0,
        min_sl_pips: float = 35.0,
        trail_trigger_r: float = 1.0,   # move to BE at 1R
        trail_pips: float = 20.0,       # trail 20 pips after BE
        backtest_mode: bool = False,
        initial_balance: float = 100.0,
    ) -> None:

        self.risk_pct = risk_pct
        self.min_sl_pips = min_sl_pips
        self.trail_trigger_r = trail_trigger_r
        self.trail_pips = trail_pips
        self.backtest_mode = backtest_mode
        self.starting_balance = initial_balance
        self.current_balance = initial_balance

        # one state dict per symbol
        self._trades: Dict[str, Dict[str, Any]] = {}

        # rolling performance monitor — auto-pause if regime shifts
        self._results: List[str] = []       # 'win' or 'loss'
        self._monitor_window: int = 6       # look at last N trades
        self._min_wins_in_window: int = 2   # pause if wins drop below this

        # monthly bias failure tracker
        # if 3 trades in the active week window all lose → skip rest of month
        self._month_trades: Dict[str, List[str]] = {}  # symbol → ['win'/'loss'] this month
        self._skip_month: Dict[str, int] = {}          # symbol → month number to skip

    # ---------------------------------------------------------------- data

    def _fetch(self, symbol: str, timeframe, bars: int) -> pd.DataFrame:
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
        if rates is None or len(rates) == 0:
            raise ValueError(f"No data for {symbol}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df

    def _fetch_all(self, symbol: str):
        m15     = self._fetch(symbol, mt5.TIMEFRAME_M15,  300)  # entry timing
        daily   = self._fetch(symbol, mt5.TIMEFRAME_D1,   5)    # structural SL
        monthly = self._fetch(symbol, mt5.TIMEFRAME_MN1,  3)    # monthly bias
        return m15, daily, monthly

    # ------------------------------------------------------------ balance

    def _balance(self) -> float:
        if self.backtest_mode:
            return self.current_balance
        return mt5.account_info().balance

    def _lot_size(self, symbol: str, sl_pips: float) -> float:
        risk = self._balance() * (self.risk_pct / 100.0)
        pip_val_per_lot = _pip(symbol) * 100_000
        lot = risk / (sl_pips * pip_val_per_lot)
        return max(0.01, round(lot, 2))

    # ------------------------------------------------------------ bias

    def _monthly_bias(self, monthly: pd.DataFrame) -> Optional[str]:
        """Prior completed month direction."""
        if len(monthly) < 2:
            return None
        prior = monthly.iloc[-2]
        return "buy" if prior["close"] > prior["open"] else "sell"

    def _structural_sl(
        self, bias: str, daily: pd.DataFrame, entry: float, symbol: str
    ) -> Optional[float]:
        """SL = prior day low (buy) or prior day high (sell)."""
        if len(daily) < 2:
            return None
        prior = daily.iloc[-2]
        buf = _sl_buffer(symbol)
        pip = _pip(symbol)
        sl = (prior["low"] - buf) if bias == "buy" else (prior["high"] + buf)
        if abs(entry - sl) / pip < self.min_sl_pips:
            return None
        return _round_price(sl, symbol)

    # ---------------------------------------------------------- signal

    def generate_signal(self, symbol: str) -> Dict[str, Any]:

        if symbol not in SYMBOL_CONFIG:
            return self._no(f"{symbol} not configured")

        # regime monitor — pause if win rate has dropped
        if self._is_paused():
            recent = self._results[-self._monitor_window:]
            wins = recent.count('win')
            return self._no(
                f"⚠️ Regime pause — last {len(recent)} trades: {wins}W/{len(recent)-wins}L. "
                f"Need {self._min_wins_in_window}+ wins to resume."
            )

        # fetch all data internally
        try:
            m15, daily, monthly = self._fetch_all(symbol)
        except ValueError as e:
            return self._no(str(e))

        bar = m15.iloc[-1]
        now = pd.to_datetime(bar["time"], utc=True)

        # ── no-trade month ───────────────────────────────────────────────
        if now.month in (11, 12):
            return self._no("No-trade month (Nov/Dec)")

        # ── dead hours ───────────────────────────────────────────────────
        if now.hour in (21, 22, 23):
            return self._no("Dead hour (21-23 UTC)")

        # ── entry hour window ────────────────────────────────────────────
        if now.hour not in _entry_hours(symbol):
            return self._no(f"Outside entry window: {now.hour}:00 UTC")

        # ── week of month filter ─────────────────────────────────────────
        wom = _week_of_month(now)
        if wom not in _entry_weeks(symbol):
            return self._no(f"Week {wom} of month — no-trade week")

        # ── Friday no new entries ────────────────────────────────────────
        if now.weekday() == 4:
            return self._no("Friday — no new entries")

        # ── monthly bias ─────────────────────────────────────────────────
        bias = self._monthly_bias(monthly)
        if bias is None:
            return self._no("Cannot determine monthly bias")

        # ── monthly failure check ─────────────────────────────────────────
        # if 3 losses in active weeks this month → skip rest of month
        if self._skip_month.get(symbol) == now.month:
            return self._no(
                f"Monthly bias failed — 3 losses in active weeks. "
                f"Skipping rest of month {now.strftime('%B')}"
            )

        # ── entry price ──────────────────────────────────────────────────
        if self.backtest_mode:
            entry = bar["close"]
        else:
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                return self._no("No tick data")
            entry = tick.ask if bias == "buy" else tick.bid
        entry = _round_price(entry, symbol)

        # ── structural SL ────────────────────────────────────────────────
        sl = self._structural_sl(bias, daily, entry, symbol)
        if sl is None:
            return self._no(f"Structural SL < {self.min_sl_pips}p — skip")

        pip = _pip(symbol)
        sl_pips = abs(entry - sl) / pip
        trail_dist = self.trail_pips * pip

        # BE level = 1R in trade direction
        be_level = _round_price(
            entry + self.trail_trigger_r * sl_pips * pip if bias == "buy"
            else entry - self.trail_trigger_r * sl_pips * pip,
            symbol,
        )

        # store trade state
        self._trades[symbol] = {
            "bias":      bias,
            "entry":     entry,
            "sl":        sl,
            "sl_pips":   sl_pips,
            "be_done":   False,
            "be_level":  be_level,
            "best_price": entry,
        }

        return {
            "signal":      bias,
            "entry_price": entry,
            "stop_loss":   sl,
            "take_profit": None,           # no fixed TP — trail handles exit
            "lot_size":    self._lot_size(symbol, sl_pips),
            "sl_pips":     round(sl_pips, 1),
            "entry_date":  now.isoformat(),
            "reason": (
                f"✅ {bias.upper()} | monthly={bias} | "
                f"week={wom} | SL={sl_pips:.1f}p | trail@1R+20p"
            ),
        }

    # ------------------------------------------------ live trade management

    def _get_position(self, symbol: str):
        positions = mt5.positions_get(symbol=symbol)
        return positions[0] if positions else None

    def _close_position(self, symbol: str) -> bool:
        pos = self._get_position(symbol)
        if pos is None:
            return False
        tick = mt5.symbol_info_tick(symbol)
        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       symbol,
            "volume":       pos.volume,
            "type":         mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY
                            else mt5.ORDER_TYPE_BUY,
            "position":     pos.ticket,
            "price":        tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask,
            "deviation":    10,
            "magic":        0,
            "comment":      "monthly_trend_close",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        return result.retcode == mt5.TRADE_RETCODE_DONE

    def _modify_sl(self, symbol: str, new_sl: float) -> bool:
        pos = self._get_position(symbol)
        if pos is None:
            return False
        request = {
            "action":   mt5.TRADE_ACTION_SLTP,
            "symbol":   symbol,
            "position": pos.ticket,
            "sl":       new_sl,
            "tp":       pos.tp,
        }
        result = mt5.order_send(request)
        return result.retcode == mt5.TRADE_RETCODE_DONE

    def manage_open_trade(self, symbol: str) -> str:
        """
        Call on every M15 bar close while a trade is open.
        Fetches current price internally.
        Handles all exit logic directly via MT5.

        Priority:
          1. Friday 14:00 UTC  → hard close
          2. Price reaches 1R  → move SL to breakeven
          3. After BE          → trail SL by 20 pips per bar
        """
        trade = self._trades.get(symbol)
        if trade is None:
            return "No open trade"

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return "❌ No tick data"

        bias = trade["bias"]
        entry = trade["entry"]
        now = datetime.datetime.utcnow()
        pip = _pip(symbol)
        trail_dist = self.trail_pips * pip
        current_price = tick.bid if bias == "buy" else tick.ask

        # 1. Friday hard close
        if now.weekday() == 4 and now.hour >= 14:
            ok = self._close_position(symbol)
            if ok:
                self.clear_trade(symbol)
            return "🚨 Friday close" if ok else "❌ Friday close failed"

        # 2. BE trigger at 1R
        if not trade["be_done"]:
            hit = (
                current_price >= trade["be_level"] if bias == "buy"
                else current_price <= trade["be_level"]
            )
            if hit:
                new_sl = _round_price(entry, symbol)
                ok = self._modify_sl(symbol, new_sl)
                if ok:
                    trade["sl"] = new_sl
                    trade["be_done"] = True
                return f"✅ BE → SL {new_sl}" if ok else "❌ BE modify failed"

        # 3. Trail 20 pips after BE
        if trade["be_done"]:
            # track best price
            if bias == "buy":
                if current_price > trade["best_price"]:
                    trade["best_price"] = current_price
                new_sl = _round_price(trade["best_price"] - trail_dist, symbol)
                if new_sl > trade["sl"]:
                    ok = self._modify_sl(symbol, new_sl)
                    if ok:
                        trade["sl"] = new_sl
                    return f"📈 Trail → SL {new_sl}" if ok else "❌ Trail failed"
            else:
                if current_price < trade["best_price"]:
                    trade["best_price"] = current_price
                new_sl = _round_price(trade["best_price"] + trail_dist, symbol)
                if new_sl < trade["sl"]:
                    ok = self._modify_sl(symbol, new_sl)
                    if ok:
                        trade["sl"] = new_sl
                    return f"📉 Trail → SL {new_sl}" if ok else "❌ Trail failed"

        return "Holding"

    def clear_trade(self, symbol: str) -> None:
        """Call when a trade closes — SL hit, trail hit, or Friday close."""
        self._trades.pop(symbol, None)

    # ------------------------------------------------ regime monitor

    def record_result(self, symbol: str, was_win: bool, current_month: int) -> None:
        """
        Call after every trade closes with the outcome and current month number.

        Two tracking systems:
        1. Rolling window (6 trades) — general regime monitor
        2. Monthly failure tracker — if 3 losses in active weeks this month,
           skip the rest of the month and wait for a new monthly candle
        """
        outcome = 'win' if was_win else 'loss'

        # rolling window
        self._results.append(outcome)
        if len(self._results) > 20:
            self._results = self._results[-20:]

        # monthly tracker
        if symbol not in self._month_trades:
            self._month_trades[symbol] = []
        self._month_trades[symbol].append(outcome)

        # check monthly failure: 3 losses in active weeks → skip rest of month
        month_losses = self._month_trades[symbol].count('loss')
        if month_losses >= 3 and not was_win:
            self._skip_month[symbol] = current_month
            print(
                f"  ⚠️ {symbol}: 3 losses in active weeks this month — "
                f"skipping rest of {current_month}. Waiting for new monthly candle."
            )

        self.clear_trade(symbol)

        result_str = '✅ WIN' if was_win else '❌ LOSS'
        recent = self._results[-self._monitor_window:]
        wins = recent.count('win')
        print(f"  [{result_str}] | Last {len(recent)} trades: {wins}W/{len(recent)-wins}L")
        if self._is_paused():
            print(f"  ⚠️ REGIME SHIFT DETECTED — strategy paused until win rate recovers")

    def reset_month(self, symbol: str, new_month: int) -> None:
        """
        Call at the start of each new month to reset the monthly tracker.
        The skip is also cleared so the strategy can trade the new month.
        """
        self._month_trades[symbol] = []
        # only clear skip if we are in a new month
        if self._skip_month.get(symbol, -1) != new_month:
            self._skip_month.pop(symbol, None)
        print(f"  🔄 {symbol}: New month — monthly tracker reset")

    def _is_paused(self) -> bool:
        """
        Returns True if the rolling win rate has dropped below threshold.
        Strategy will not enter new trades while paused.
        """
        if len(self._results) < self._monitor_window:
            return False  # not enough history yet
        recent = self._results[-self._monitor_window:]
        wins = recent.count('win')
        return wins < self._min_wins_in_window

    def get_performance_summary(self, symbol: str = None) -> Dict[str, Any]:
        """Return current rolling performance metrics."""
        if not self._results:
            return {"trades": 0, "status": "No trades yet"}
        recent = self._results[-self._monitor_window:]
        wins = recent.count('win')
        total = len(self._results)
        all_wins = self._results.count('win')

        summary = {
            "total_trades":    total,
            "overall_wr":      f"{all_wins/total*100:.1f}%",
            "recent_trades":   len(recent),
            "recent_wins":     wins,
            "recent_wr":       f"{wins/len(recent)*100:.1f}%",
            "regime_status":   "⚠️ PAUSED" if self._is_paused() else "✅ ACTIVE",
        }

        if symbol:
            month_results = self._month_trades.get(symbol, [])
            month_losses  = month_results.count('loss')
            skip_month    = self._skip_month.get(symbol)
            summary["month_trades"]  = len(month_results)
            summary["month_losses"]  = month_losses
            summary["month_status"]  = (
                f"⚠️ SKIPPING MONTH" if skip_month else
                f"{'⚠️ 2 losses — 1 more skips month' if month_losses == 2 else '✅ OK'}"
            )

        return summary

    # ---------------------------------------------------------------- util

    def _no(self, reason: str) -> Dict[str, Any]:
        return {
            "signal": None, "entry_price": None,
            "stop_loss": None, "take_profit": None,
            "lot_size": None, "sl_pips": None,
            "entry_date": None, "reason": f"⏭ {reason}",
        }

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "name":             "MonthlyTrendStrategy",
            "risk_pct":         self.risk_pct,
            "min_sl_pips":      self.min_sl_pips,
            "trail_trigger_r":  self.trail_trigger_r,
            "trail_pips":       self.trail_pips,
            "symbols":          list(SYMBOL_CONFIG.keys()),
        }

    def __repr__(self) -> str:
        return (
            f"MonthlyTrendStrategy("
            f"risk={self.risk_pct}%, SL≥{self.min_sl_pips}p, "
            f"trail@{self.trail_trigger_r}R+{self.trail_pips}p)"
        )


if __name__ == "__main__":
    s = MonthlyTrendStrategy(backtest_mode=True)
    print(s)
    print()
    for sym, cfg in SYMBOL_CONFIG.items():
        days = {0:'Mon',1:'Tue',2:'Wed',3:'Thu',4:'Fri'}
        print(f"  {sym}:")
        print(f"    Entry weeks : {cfg['entry_weeks']}")
        print(f"    Entry hours : {cfg['entry_hours']} UTC")
        print(f"    Pip         : {cfg['pip']}")
        print(f"    Spread      : {cfg['spread']}")