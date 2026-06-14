"""
Monthly Trend Strategy — Live Version
======================================
Edge   : Prior month direction predicts current month (58%+ WR)
Filter : Week 1-2 of month only (weeks 3-4 trend exhausts)
Entry  : First bar in allowed window aligned with monthly bias
SL     : Prior day structural low/high (min 35 pips)
Exit   : Trail from 1R, 20 pips per bar — captures full monthly moves
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import MetaTrader5 as mt5


# ---------------------------------------------------------------------------
# Per-symbol configuration
# ---------------------------------------------------------------------------

SYMBOL_CONFIG: Dict[str, Dict[str, Any]] = {
    "EURUSDm": {
        "pip":          0.0001,
        "sl_buffer":    0.0003,
        "entry_weeks":  [1, 2],
        "entry_hours":  [7, 8, 13, 14],
    },
    "USDJPYm": {
        "pip":          0.01,
        "sl_buffer":    0.03,
        "entry_weeks":  [1],
        "entry_hours":  [0, 7, 8, 13, 14],
    },
}


def _pip(symbol: str) -> float:
    return SYMBOL_CONFIG.get(symbol, {}).get("pip", 0.0001)


def _sl_buffer(symbol: str) -> float:
    return SYMBOL_CONFIG.get(symbol, {}).get("sl_buffer", 0.0003)


def _entry_weeks(symbol: str) -> List[int]:
    return SYMBOL_CONFIG.get(symbol, {}).get("entry_weeks", [1, 2])


def _entry_hours(symbol: str) -> List[int]:
    return SYMBOL_CONFIG.get(symbol, {}).get("entry_hours", [7, 8, 13, 14])


def _week_of_month(dt: pd.Timestamp) -> int:
    return (dt.day - 1) // 7 + 1


def _round_price(price: float, symbol: str) -> float:
    return round(price, 3) if _pip(symbol) == 0.01 else round(price, 5)


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class MonthlyTrendStrategy:

    def __init__(
        self,
        risk_pct: float = 1.0,
        min_sl_pips: float = 35.0,
        trail_trigger_r: float = 1.0,
        trail_pips: float = 20.0,
        initial_balance: float = 100.0,
    ) -> None:

        self.risk_pct = risk_pct
        self.min_sl_pips = min_sl_pips
        self.trail_trigger_r = trail_trigger_r
        self.trail_pips = trail_pips
        self.starting_balance = initial_balance

        # open trade state per symbol
        self._trades: Dict[str, Dict[str, Any]] = {}

        # monthly failure tracker
        self._month_trades: Dict[str, List[str]] = {}
        self._skip_month: Dict[str, int] = {}

        # rolling regime monitor
        self._results: List[str] = []
        self._monitor_window: int = 6
        self._min_wins_in_window: int = 2

    # ---------------------------------------------------------------- data

    def _fetch(self, symbol: str, timeframe, bars: int) -> pd.DataFrame:
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
        if rates is None or len(rates) == 0:
            raise ValueError(f"No data for {symbol}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df

    def _fetch_all(self, symbol: str):
        m15     = self._fetch(symbol, mt5.TIMEFRAME_M15,  300)
        daily   = self._fetch(symbol, mt5.TIMEFRAME_D1,   5)
        monthly = self._fetch(symbol, mt5.TIMEFRAME_MN1,  3)
        return m15, daily, monthly

    # ---------------------------------------------------------------- balance

    def _balance(self) -> float:
        acc = mt5.account_info()
        return acc.balance if acc else self.starting_balance

    def _lot_size(self, symbol: str, sl_pips: float) -> float:
        risk = self._balance() * (self.risk_pct / 100.0)
        pip_val_per_lot = _pip(symbol) * 100_000
        lot = risk / (sl_pips * pip_val_per_lot)
        return max(0.01, round(lot, 2))

    # ---------------------------------------------------------------- bias

    def _monthly_bias(self, monthly: pd.DataFrame) -> Optional[str]:
        if len(monthly) < 2:
            return None
        prior = monthly.iloc[-2]
        return "buy" if prior["close"] > prior["open"] else "sell"

    def _structural_sl(
        self, bias: str, daily: pd.DataFrame, entry: float, symbol: str
    ) -> Optional[float]:
        if len(daily) < 2:
            return None
        prior = daily.iloc[-2]
        buf = _sl_buffer(symbol)
        pip = _pip(symbol)
        sl = (prior["low"] - buf) if bias == "buy" else (prior["high"] + buf)
        if abs(entry - sl) / pip < self.min_sl_pips:
            return None
        return _round_price(sl, symbol)

    # ---------------------------------------------------------------- protection

    def _is_paused(self) -> bool:
        if len(self._results) < self._monitor_window:
            return False
        recent = self._results[-self._monitor_window:]
        return recent.count("win") < self._min_wins_in_window

    # ---------------------------------------------------------------- signal

    def generate_signal(self, symbol: str) -> Dict[str, Any]:

        if symbol not in SYMBOL_CONFIG:
            return self._no(f"{symbol} not configured")

        # regime monitor
        if self._is_paused():
            recent = self._results[-self._monitor_window:]
            wins = recent.count("win")
            return self._no(
                f"⚠️ Regime pause — {wins}W/{len(recent)-wins}L in last {len(recent)}"
            )

        # fetch data
        try:
            m15, daily, monthly = self._fetch_all(symbol)
        except ValueError as e:
            return self._no(str(e))

        bar = m15.iloc[-1]
        now = pd.to_datetime(bar["time"], utc=True)

        # no-trade month
        if now.month in (11, 12):
            return self._no("No-trade month (Nov/Dec)")

        # dead hours
        if now.hour in (21, 22, 23):
            return self._no("Dead hour (21-23 UTC)")

        # entry hour
        if now.hour not in _entry_hours(symbol):
            return self._no(f"Outside entry window: {now.hour}:00 UTC")

        # week of month
        wom = _week_of_month(now)
        if wom not in _entry_weeks(symbol):
            return self._no(f"Week {wom} — no-trade week")

        # Friday blocked
        if now.weekday() == 4:
            return self._no("Friday — no new entries")

        # monthly failure check
        if self._skip_month.get(symbol) == now.month:
            return self._no(
                f"Monthly bias failed — 3 losses this month. "
                f"Resuming in {now.strftime('%B')} next month."
            )

        # monthly bias
        bias = self._monthly_bias(monthly)
        if bias is None:
            return self._no("Cannot determine monthly bias")

        # entry price from live tick
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return self._no("No tick data")
        entry = tick.ask if bias == "buy" else tick.bid
        entry = _round_price(entry, symbol)

        # structural SL
        sl = self._structural_sl(bias, daily, entry, symbol)
        if sl is None:
            return self._no(f"Structural SL < {self.min_sl_pips}p — skip")

        pip = _pip(symbol)
        sl_pips = abs(entry - sl) / pip
        be_level = _round_price(
            entry + self.trail_trigger_r * sl_pips * pip if bias == "buy"
            else entry - self.trail_trigger_r * sl_pips * pip,
            symbol,
        )

        # store trade state
        self._trades[symbol] = {
            "bias":       bias,
            "entry":      entry,
            "sl":         sl,
            "sl_pips":    sl_pips,
            "be_done":    False,
            "be_level":   be_level,
            "best_price": entry,
        }

        return {
            "signal":      bias,
            "entry_price": entry,
            "stop_loss":   sl,
            "take_profit": 0,          # no fixed TP — trail manages exit
            "lot_size":    self._lot_size(symbol, sl_pips),
            "sl_pips":     round(sl_pips, 1),
            "entry_date":  now.isoformat(),
            "reason": (
                f"✅ {bias.upper()} | monthly={bias} | "
                f"week={wom} | SL={sl_pips:.1f}p | trail@1R+20p"
            ),
        }

    # ---------------------------------------------------------------- restore

    def restore_open_trades(self, symbols: List[str]) -> None:
        """
        Call once on startup to rebuild trade state from any
        positions already open in MT5 (e.g. after a bot restart).
        """
        for symbol in symbols:
            pos = self._get_position(symbol)
            if pos is None:
                continue
            bias = "buy" if pos.type == mt5.ORDER_TYPE_BUY else "sell"
            pip = _pip(symbol)
            sl_pips = abs(pos.price_open - pos.sl) / pip if pos.sl else self.min_sl_pips
            be_level = _round_price(
                pos.price_open + self.trail_trigger_r * sl_pips * pip if bias == "buy"
                else pos.price_open - self.trail_trigger_r * sl_pips * pip,
                symbol,
            )
            be_done = (
                pos.sl >= pos.price_open if bias == "buy"
                else pos.sl <= pos.price_open
            ) if pos.sl else False

            self._trades[symbol] = {
                "bias":       bias,
                "entry":      pos.price_open,
                "sl":         pos.sl,
                "sl_pips":    sl_pips,
                "be_done":    be_done,
                "be_level":   be_level,
                "best_price": pos.price_open,
            }
            print(f"  🔄 Restored {symbol} {bias.upper()} from MT5 | SL={sl_pips:.1f}p")

    # ---------------------------------------------------------------- MT5 ops

    def _get_position(self, symbol: str):
        positions = mt5.positions_get(symbol=symbol)
        if not positions:
            return None
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

    # ---------------------------------------------------------------- trade management

    def manage_open_trade(self, symbol: str) -> str:
        """
        Call on every M15 bar close while a trade is open.
        Fetches current price internally.
        Handles all exit logic directly via MT5.

        Priority:
          1. Friday 14:00 UTC → hard close
          2. Price reaches 1R  → move SL to breakeven
          3. After BE          → trail SL by 20 pips tracking best price
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

    # ---------------------------------------------------------------- result tracking

    def record_result(self, symbol: str, was_win: bool, current_month: int) -> None:
        """Call after every trade closes with the outcome."""
        outcome = "win" if was_win else "loss"

        # rolling window
        self._results.append(outcome)
        if len(self._results) > 20:
            self._results = self._results[-20:]

        # monthly tracker
        if symbol not in self._month_trades:
            self._month_trades[symbol] = []
        self._month_trades[symbol].append(outcome)

        # monthly failure: 3 losses in active weeks → skip rest of month
        if self._month_trades[symbol].count("loss") >= 3:
            self._skip_month[symbol] = current_month
            print(
                f"  ⚠️ {symbol}: 3 losses this month — "
                f"skipping remaining entries until next month"
            )

        self.clear_trade(symbol)

        recent = self._results[-self._monitor_window:]
        wins = recent.count("win")
        print(f"  {'✅ WIN' if was_win else '❌ LOSS'} | "
              f"Last {len(recent)}: {wins}W/{len(recent)-wins}L")
        if self._is_paused():
            print("  ⚠️ Regime pause — strategy paused until win rate recovers")

    def reset_month(self, symbol: str, new_month: int) -> None:
        """Call at the start of each new month."""
        self._month_trades[symbol] = []
        if self._skip_month.get(symbol, -1) != new_month:
            self._skip_month.pop(symbol, None)
        print(f"  🔄 {symbol}: Month reset")

    def clear_trade(self, symbol: str) -> None:
        """Call when trade closes for any reason."""
        self._trades.pop(symbol, None)

    def get_performance_summary(self, symbol: str = None) -> Dict[str, Any]:
        if not self._results:
            return {"trades": 0, "status": "No trades yet"}
        recent = self._results[-self._monitor_window:]
        wins = recent.count("win")
        total = len(self._results)
        summary = {
            "total_trades": total,
            "overall_wr":   f"{self._results.count('win')/total*100:.1f}%",
            "recent_wr":    f"{wins/len(recent)*100:.1f}%",
            "status":       "⚠️ PAUSED" if self._is_paused() else "✅ ACTIVE",
        }
        if symbol:
            month_results = self._month_trades.get(symbol, [])
            summary["month_losses"] = month_results.count("loss")
            summary["month_status"] = (
                "⚠️ SKIPPING" if self._skip_month.get(symbol)
                else f"{month_results.count('loss')}/3 losses this month"
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

    def __repr__(self) -> str:
        return (
            f"MonthlyTrendStrategy("
            f"risk={self.risk_pct}%, SL≥{self.min_sl_pips}p, "
            f"trail@{self.trail_trigger_r}R+{self.trail_pips}p)"
        )


if __name__ == "__main__":
    s = MonthlyTrendStrategy()
    print(s)
    print()
    for sym, cfg in SYMBOL_CONFIG.items():
        print(f"  {sym}: weeks={cfg['entry_weeks']} hours={cfg['entry_hours']}")