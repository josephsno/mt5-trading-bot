"""
Monthly Trend Position Strategy
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Tuple

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
        "pip":          0.0001,
        "spread":       0.00008,   # 8 points
        "entry_days":   [0, 1],    # Mon, Tue
        "entry_hours":  [7, 8, 13, 14],  # London open + NY overlap
        "sl_buffer":    0.0003,
    },
    "USDJPYm": {
        "pip":          0.01,
        "spread":       0.018,     # 18 points
        "entry_days":   [2, 3],    # Wed, Thu
        "entry_hours":  [0, 1, 7, 8, 13, 14],  # Tokyo open + London open + NY overlap
        "sl_buffer":    0.03,
    },
}


def _pip(symbol: str) -> float:
    return SYMBOL_CONFIG.get(symbol, {}).get("pip", 0.0001)


def _spread(symbol: str) -> float:
    return SYMBOL_CONFIG.get(symbol, {}).get("spread", 0.0001)


def _entry_days(symbol: str) -> List[int]:
    return SYMBOL_CONFIG.get(symbol, {}).get("entry_days", [0, 1])


def _entry_hours(symbol: str) -> List[int]:
    return SYMBOL_CONFIG.get(symbol, {}).get("entry_hours", [7, 8, 13, 14])


def _sl_buffer(symbol: str) -> float:
    return SYMBOL_CONFIG.get(symbol, {}).get("sl_buffer", 0.0003)


def _round_price(price: float, symbol: str) -> float:
    pip = _pip(symbol)
    if pip == 0.01:
        return round(price, 3)
    return round(price, 5)


class MonthlyTrendStrategy:

    def __init__(
        self,
        risk_pct: float = 1.0,
        min_sl_pips: float = 35.0,
        tp_rr: float = 3.0,
        trail_trigger_r: float = 2.0,
        trail_pips: float = 30.0,
        ema_period: int = 50,
        backtest_mode: bool = False,
        initial_balance: float = 100.0,
    ) -> None:

        self.risk_pct = risk_pct
        self.min_sl_pips = min_sl_pips
        self.tp_rr = tp_rr
        self.trail_trigger_r = trail_trigger_r
        self.trail_pips = trail_pips
        self.ema_period = ema_period
        self.backtest_mode = backtest_mode
        self.starting_balance = initial_balance
        self.current_balance = initial_balance

        # one state dict per symbol
        self._trades: Dict[str, Dict[str, Any]] = {}

    # ---------------------------------------------------------------- data

    def _fetch(self, symbol: str, timeframe, bars: int) -> pd.DataFrame:
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
        if rates is None or len(rates) == 0:
            raise ValueError(f"No data returned for {symbol}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df

    def _fetch_all(self, symbol: str) -> Tuple:
        """Fetch all required timeframes for a symbol in one place."""
        m15     = self._fetch(symbol, mt5.TIMEFRAME_M15, 300)  # entry trigger + EMA50
        daily   = self._fetch(symbol, mt5.TIMEFRAME_D1,  5)    # structural SL
        weekly  = self._fetch(symbol, mt5.TIMEFRAME_W1,  3)    # weekly bias
        monthly = self._fetch(symbol, mt5.TIMEFRAME_MN1, 3)    # monthly bias
        return m15, daily, weekly, monthly

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
        if len(monthly) < 2:
            return None
        prior = monthly.iloc[-2]
        return "buy" if prior["close"] > prior["open"] else "sell"

    def _weekly_confirmed(self, weekly: pd.DataFrame, bias: str) -> bool:
        if len(weekly) < 2:
            return False
        prior = weekly.iloc[-2]
        direction = "buy" if prior["close"] > prior["open"] else "sell"
        return direction == bias

    def _structural_sl(self, bias: str, daily: pd.DataFrame, entry: float, symbol: str) -> Optional[float]:
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
            return self._no(f"Symbol {symbol} not configured")

        try:
            m15, daily, weekly, monthly = self._fetch_all(symbol)
        except ValueError as e:
            return self._no(str(e))

        bar = m15.iloc[-1]
        now = pd.to_datetime(bar["time"], utc=True)

        # no-trade month
        if now.month in (11, 12):
            return self._no("No-trade month")

        # dead hours
        if now.hour in (21, 22, 23) and now.hour not in _entry_hours(symbol):
            return self._no("Dead hour")

        # entry days — per symbol
        if now.weekday() not in _entry_days(symbol):
            return self._no(f"Not an entry day for {symbol}: {now.strftime('%A')}")

        # entry hours — per symbol
        if now.hour not in _entry_hours(symbol):
            return self._no(f"Outside entry window: {now.hour}:00 UTC")

        # monthly bias
        bias = self._monthly_bias(monthly)
        if bias is None:
            return self._no("Cannot determine monthly bias")

        # weekly confirmation
        if not self._weekly_confirmed(weekly, bias):
            return self._no(f"Weekly does not confirm monthly bias ({bias})")

        # EMA50
        m15 = m15.copy()
        m15["ema50"] = m15["close"].ewm(span=self.ema_period, adjust=False).mean()
        close, open_ = bar["close"], bar["open"]
        high, low, ema50 = bar["high"], bar["low"], m15["ema50"].iloc[-1]

        # candle direction
        if bias == "buy" and close <= open_:
            return self._no("Candle not bullish")
        if bias == "sell" and close >= open_:
            return self._no("Candle not bearish")

        # body >= 50% of range
        rng = high - low
        body = abs(close - open_)
        if rng == 0 or body / rng < 0.50:
            return self._no(f"Weak body: {body/rng*100:.0f}%")

        # price vs EMA50
        if bias == "buy" and close <= ema50:
            return self._no("Close below EMA50")
        if bias == "sell" and close >= ema50:
            return self._no("Close above EMA50")

        # entry price
        if self.backtest_mode:
            entry = close
        else:
            tick = mt5.symbol_info_tick(symbol)
            entry = tick.ask if bias == "buy" else tick.bid
        entry = _round_price(entry, symbol)

        # structural SL
        sl = self._structural_sl(bias, daily, entry, symbol)
        if sl is None:
            return self._no(f"Structural SL < {self.min_sl_pips} pips — skip")

        pip = _pip(symbol)
        sl_pips = abs(entry - sl) / pip
        tp = _round_price(
            entry + sl_pips * self.tp_rr * pip if bias == "buy"
            else entry - sl_pips * self.tp_rr * pip,
            symbol,
        )

        # store state
        self._trades[symbol] = {
            "bias":     bias,
            "entry":    entry,
            "sl":       sl,
            "tp":       tp,
            "sl_pips":  sl_pips,
            "be_done":  False,
            "be_level": _round_price(
                entry + self.trail_trigger_r * sl_pips * pip if bias == "buy"
                else entry - self.trail_trigger_r * sl_pips * pip,
                symbol,
            ),
        }

        return {
            "signal":      bias,
            "entry_price": entry,
            "stop_loss":   sl,
            "take_profit": tp,
            "lot_size":    self._lot_size(symbol, sl_pips),
            "sl_pips":     round(sl_pips, 1),
            "tp_pips":     round(sl_pips * self.tp_rr, 1),
            "entry_date":  now.isoformat(),
            "reason": (
                f"✅ {bias.upper()} | monthly={bias} weekly=confirmed | "
                f"body={body/rng*100:.0f}% | SL={sl_pips:.1f}p TP={sl_pips*self.tp_rr:.1f}p"
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
            "type":         mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
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
        Fetches price and time internally.
        Closes or modifies MT5 position directly.

        Priority:
          1. Friday 14:00 UTC → hard close
          2. Price reaches 2R  → move SL to breakeven
          3. After BE          → trail SL by 30 pips per bar
        """
        trade = self._trades.get(symbol)
        if trade is None:
            return "No open trade"

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return "❌ No tick data"

        bias = trade["bias"]
        entry = trade["entry"]
        current_price = tick.bid if bias == "buy" else tick.ask
        now = datetime.datetime.utcnow()
        pip = _pip(symbol)
        trail_dist = self.trail_pips * pip

        # 1. Friday hard close
        if now.weekday() == 4 and now.hour >= 14:
            ok = self._close_position(symbol)
            if ok:
                self.clear_trade(symbol)
            return "🚨 Friday close — position closed" if ok else "❌ Friday close failed"

        # 2. BE trigger
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
                return f"✅ BE → SL moved to {new_sl}" if ok else "❌ BE modify failed"

        # 3. Trail
        if trade["be_done"]:
            if bias == "buy":
                new_sl = _round_price(current_price - trail_dist, symbol)
                if new_sl > trade["sl"]:
                    ok = self._modify_sl(symbol, new_sl)
                    if ok:
                        trade["sl"] = new_sl
                    return f"📈 Trail → SL {new_sl}" if ok else "❌ Trail modify failed"
            else:
                new_sl = _round_price(current_price + trail_dist, symbol)
                if new_sl < trade["sl"]:
                    ok = self._modify_sl(symbol, new_sl)
                    if ok:
                        trade["sl"] = new_sl
                    return f"📉 Trail → SL {new_sl}" if ok else "❌ Trail modify failed"

        return "Holding"

    def clear_trade(self, symbol: str) -> None:
        """Call when a trade closes for any reason."""
        self._trades.pop(symbol, None)

    # ---------------------------------------------------------------- util

    def _no(self, reason: str) -> Dict[str, Any]:
        return {
            "signal": None, "entry_price": None, "stop_loss": None,
            "take_profit": None, "lot_size": None, "sl_pips": None,
            "tp_pips": None, "entry_date": None, "reason": f"⏭ {reason}",
        }

    def __repr__(self) -> str:
        return (
            f"MonthlyTrendStrategy("
            f"risk={self.risk_pct}%, SL≥{self.min_sl_pips}p, "
            f"TP={self.tp_rr}R, trail@{self.trail_trigger_r}R+{self.trail_pips}p)"
        )


if __name__ == "__main__":
    s = MonthlyTrendStrategy(backtest_mode=True)
    print(s)
    print("\nConfigured symbols:")
    for sym, cfg in SYMBOL_CONFIG.items():
        days = {0:'Mon',1:'Tue',2:'Wed',3:'Thu',4:'Fri'}
        day_names = [days[d] for d in cfg['entry_days']]
        print(f"  {sym}: entry days={day_names}, pip={cfg['pip']}, spread={cfg['spread']}")