"""
Monthly Candle Trail Strategy — Live Version
==============================================
Distinct from MonthlyTrendStrategy (the M15-managed bot). This strategy operates
entirely on the monthly (MN1) timeframe: one entry per cycle, held across multiple
months, managed with a trail that only updates once a month.

Edge   : Prior month direction predicts current month (58%+ WR, validated on 8yr
         real MN1 data, held up through the 2022 bear year on gold as a cross-check)
Entry  : Month open, direction = prior month's bias (close > open = buy, else sell)
SL     : Prior month's midpoint (high+low)/2. Must land on the correct side of entry
         and be at least min_sl_pips away, or the month is skipped entirely -- no
         forced/invalid trades.
Trail  : Immediate, from month one. Every new completed month, the SL moves to that
         month's midpoint -- only if more favorable, never loosens. No fixed TP;
         letting the market's own structure decide when the trend is over is what
         makes the wide initial stop survivable. (Tested and confirmed: removing the
         trail while keeping the same stop turns this into an account-blowing setup.)
Hold   : Multi-month by design. Average ~1-2 months; the largest winners ran 4-5
         months. Forcing an early exit at month-end even while profitable was tested
         and made results meaningfully worse on 2 of 3 validated pairs -- don't do it.

--- Validated (8yr backtest, $100 start, 1% risk per trade) ---
  EURUSDm: 39 trades, $280.40 final (+180.4%), 56.4% WR, max loss streak 4
  USDJPYm: 37 trades, $309.79 final (+209.8%), 54.1% WR, max loss streak 3
  GBPUSDm: 36 trades, $485.14 final (+385.1%), 55.6% WR

--- Rejected -- raw monthly-bias hit rate below 50%, do not enable ---
  CADJPYm (49.0%), AUDUSDm (43.8%, inverted), USDCADm (45.8%)

--- XAUUSDm -- edge is real (59.8%) but NOT enabled ---
  Max single-trade risk hit 32.8% of account at $100 starting balance (gold's
  monthly range has expanded ~4x since 2018; the 0.01 lot floor can't shrink to
  compensate). Needs a materially larger account, or a volatility-relative SL cap,
  before this is safe. Revisit once one of those is true -- the signal itself is
  good, this is purely a position-sizing problem.

--- Tested and rejected refinements (do not re-attempt without new evidence) ---
  - Waiting for a profit cushion before trailing (trigger > 0): consistently worse
    than trailing from month one, sometimes turning a strongly positive result
    negative.
  - SL-width cap: helped on the M15 strategy, backfired here -- it cuts the widest
    winning months along with the dangerous ones.
  - Multi-month rolling-average trail: produced a 181%-of-account single-trade loss
    in testing. Genuinely dangerous, not just underperforming.
  - Two-month bias confirmation, flip-after-losing-streak, first-trade-as-gate,
    conviction filters: all failed to generalize across pairs (helped one, hurt
    another) -- signature of overfitting, not real signal.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import MetaTrader5 as mt5


# ---------------------------------------------------------------------------
# Per-symbol configuration -- only pairs that passed raw signal validation
# ---------------------------------------------------------------------------

SYMBOL_CONFIG: Dict[str, Dict[str, Any]] = {
    "EURUSDm": {"pip": 0.0001, "contract_units": 100_000, "quote_is_usd": True,  "min_sl_pips": 35.0},
    "USDJPYm": {"pip": 0.01,   "contract_units": 100_000, "quote_is_usd": False, "min_sl_pips": 35.0},
    "GBPUSDm": {"pip": 0.0001, "contract_units": 100_000, "quote_is_usd": True,  "min_sl_pips": 35.0},
    # XAUUSDm and the rejected pairs (CADJPYm, AUDUSDm, USDCADm) intentionally
    # excluded -- see module docstring before adding anything here.
}


def _pip(symbol: str) -> float:
    return SYMBOL_CONFIG[symbol]["pip"]


def _min_sl_pips(symbol: str) -> float:
    return SYMBOL_CONFIG[symbol]["min_sl_pips"]


def _pip_value_per_lot(symbol: str, price: float) -> float:
    """
    USD value of one pip move, per 1.0 lot.
    Quote currency = USD (e.g. EURUSD, GBPUSD): no conversion needed.
    Quote currency = JPY (e.g. USDJPY): must divide by the live price to convert
    yen pip value into USD. Getting this wrong is a real, previously-found bug --
    it silently under-sizes JPY pairs once the account is large enough for sizing
    to matter (harmless at tiny balances since lot floors at 0.01 regardless).
    """
    cfg = SYMBOL_CONFIG[symbol]
    raw = cfg["pip"] * cfg["contract_units"]
    return raw if cfg["quote_is_usd"] else raw / price


def _round_price(price: float, symbol: str) -> float:
    return round(price, 3) if _pip(symbol) == 0.01 else round(price, 5)


def _market_is_open(symbol: str, max_tick_age_seconds: float = 300.0) -> bool:
    """
    A tick existing isn't enough -- MT5 can return the last known tick from
    before a weekend/holiday close without returning None. Check the tick's
    own timestamp instead: if it's stale (older than max_tick_age_seconds),
    the market isn't actively quoting right now, regardless of whether a
    tick object was returned.
    """
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return False
    tick_time = datetime.datetime.fromtimestamp(tick.time, datetime.timezone.utc)
    age = (datetime.datetime.now(datetime.timezone.utc) - tick_time).total_seconds()
    return age < max_tick_age_seconds


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class MonthlyCandleTrailStrategy:

    def __init__(
        self,
        risk_pct: float = 1.0,
        initial_balance: float = 100.0,
    ) -> None:
        self.risk_pct = risk_pct
        self.starting_balance = initial_balance

        # open trade state per symbol
        self._trades: Dict[str, Dict[str, Any]] = {}
        # No circuit breaker. Losses don't pause anything -- see module
        # docstring for why this was removed.

    # ---------------------------------------------------------------- data

    def _fetch_monthly(self, symbol: str, bars: int = 3) -> pd.DataFrame:
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_MN1, 0, bars)
        if rates is None or len(rates) == 0:
            raise ValueError(f"No monthly data for {symbol}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df

    # ---------------------------------------------------------------- balance

    def _balance(self) -> float:
        acc = mt5.account_info()
        return acc.balance if acc else self.starting_balance

    def _lot_size(self, symbol: str, sl_pips: float, price: float) -> float:
        risk = self._balance() * (self.risk_pct / 100.0)
        pip_val_per_lot = _pip_value_per_lot(symbol, price)
        lot = risk / (sl_pips * pip_val_per_lot)
        return max(0.01, round(lot, 2))

    # ---------------------------------------------------------------- bias / SL

    def _monthly_bias(self, monthly: pd.DataFrame) -> Optional[str]:
        if len(monthly) < 2:
            return None
        prior = monthly.iloc[-2]  # last fully completed month
        return "buy" if prior["close"] > prior["open"] else "sell"

    def _structural_sl(
        self, bias: str, monthly: pd.DataFrame, entry: float, symbol: str
    ) -> Optional[float]:
        if len(monthly) < 2:
            return None
        prior = monthly.iloc[-2]
        midpoint = (prior["high"] + prior["low"]) / 2.0
        pip = _pip(symbol)

        if bias == "buy" and midpoint >= entry:
            return None  # wrong side -- no valid stop, skip this cycle
        if bias == "sell" and midpoint <= entry:
            return None

        sl_pips = abs(entry - midpoint) / pip
        if sl_pips < _min_sl_pips(symbol):
            return None  # too tight, would be noise-stopped

        return _round_price(midpoint, symbol)

    # ---------------------------------------------------------------- history / diagnostics

    def _closed_deals(self, symbol: str, lookback_days: int = 400) -> List[Any]:
        """
        Pull this symbol's closing deals from MT5 history, most recent first.
        400-day lookback comfortably covers a full year even with the Nov/Dec
        gaps this strategy doesn't trade through.
        """
        date_from = datetime.datetime.utcnow() - datetime.timedelta(days=lookback_days)
        date_to = datetime.datetime.utcnow()
        deals = mt5.history_deals_get(date_from, date_to, group=symbol)
        if not deals:
            return []
        closing = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT]
        return sorted(closing, key=lambda d: d.time, reverse=True)

    def _consecutive_losses_from_history(self, symbol: str) -> int:
        """Count losses backward from the most recent closed trade until a win.
        Diagnostic only -- nothing currently acts on this (no circuit breaker)."""
        streak = 0
        for deal in self._closed_deals(symbol):
            if deal.profit < 0:
                streak += 1
            else:
                break
        return streak

    # ---------------------------------------------------------------- signal

    def _already_traded_this_month(self, symbol: str) -> bool:
        """
        True if a trade for this symbol already closed within the current
        calendar month. Matches the backtest exactly: a stop-out mid-month
        does NOT free up a fresh entry until the next calendar month begins.
        Derived from MT5 history, same reasoning as the circuit breaker --
        no in-memory flag that a restart could lose track of.
        """
        deals = self._closed_deals(symbol, lookback_days=40)
        now = datetime.datetime.now(datetime.timezone.utc)
        for d in deals:
            close_time = datetime.datetime.fromtimestamp(d.time, datetime.timezone.utc)
            if close_time.year == now.year and close_time.month == now.month:
                return True
        return False

    def generate_signal(self, symbol: str) -> Dict[str, Any]:

        if symbol not in SYMBOL_CONFIG:
            return self._no(f"{symbol} not configured or not validated -- see module docstring")

        if self._already_traded_this_month(symbol):
            return self._no(
                "Already traded this calendar month — next attempt opens next month, "
                "matching how this was backtested (a stop-out mid-month does not "
                "free up a same-month re-entry)"
            )

        if symbol in self._trades:
            return self._no("Position already open — managed monthly, not re-evaluated mid-cycle")

        try:
            monthly = self._fetch_monthly(symbol, 3)
        except ValueError as e:
            return self._no(str(e))

        bias = self._monthly_bias(monthly)
        if bias is None:
            return self._no("Cannot determine monthly bias")

        if not _market_is_open(symbol):
            return self._no("Market closed or not actively quoting — waiting, this doesn't count as a skipped attempt")

        tick = mt5.symbol_info_tick(symbol)
        entry = tick.ask if bias == "buy" else tick.bid
        entry = _round_price(entry, symbol)

        sl = self._structural_sl(bias, monthly, entry, symbol)
        if sl is None:
            return self._no("No valid structural SL this cycle — skipped, not forced")

        pip = _pip(symbol)
        sl_pips = abs(entry - sl) / pip
        lot = self._lot_size(symbol, sl_pips, entry)

        self._trades[symbol] = {
            "bias": bias,
            "entry": entry,
            "sl": sl,
            "lot": lot,
            "entry_month": datetime.datetime.utcnow().month,
        }

        return {
            "signal": bias,
            "entry_price": entry,
            "stop_loss": sl,
            "take_profit": 0,  # no fixed TP -- the monthly trail manages the exit
            "lot_size": lot,
            "sl_pips": round(sl_pips, 1),
            "reason": f"✅ {bias.upper()} | monthly bias | SL={sl_pips:.1f}p (prior month midpoint)",
        }

    # ---------------------------------------------------------------- trade management

    def manage_open_trade(self, symbol: str) -> str:
        """
        Call once per day (or on each bot loop) while a trade is open.
        Unlike the M15 strategy, this only needs to act on month boundaries --
        the trail level itself only changes once a new month completes.
        """
        trade = self._trades.get(symbol)
        if trade is None:
            return "No open trade"

        now = datetime.datetime.utcnow()
        if now.month == trade["entry_month"]:
            return "Holding — same month, no trail update yet"

        try:
            monthly = self._fetch_monthly(symbol, 3)
        except ValueError as e:
            return f"❌ {e}"

        prior_completed = monthly.iloc[-2]
        candidate = (prior_completed["high"] + prior_completed["low"]) / 2.0
        bias = trade["bias"]

        moved = False
        if bias == "buy" and candidate > trade["sl"]:
            trade["sl"] = _round_price(candidate, symbol)
            moved = True
        elif bias == "sell" and candidate < trade["sl"]:
            trade["sl"] = _round_price(candidate, symbol)
            moved = True

        trade["entry_month"] = now.month  # mark this month as processed

        if moved:
            ok = self._modify_sl(symbol, trade["sl"])
            return f"📈 Trail → SL {trade['sl']}" if ok else "❌ Trail modify failed"
        return "Holding — new month's midpoint not more favorable, SL unchanged"

    # ---------------------------------------------------------------- MT5 ops

    def _get_position(self, symbol: str):
        positions = mt5.positions_get(symbol=symbol)
        return positions[0] if positions else None

    def _modify_sl(self, symbol: str, new_sl: float) -> bool:
        pos = self._get_position(symbol)
        if pos is None:
            return False
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": pos.ticket,
            "sl": new_sl,
            "tp": pos.tp,
        }
        result = mt5.order_send(request)
        return result.retcode == mt5.TRADE_RETCODE_DONE

    # ---------------------------------------------------------------- restore

    def restore_open_trades(self, symbols: List[str]) -> None:
        """Call once on startup to rebuild state from any open MT5 positions."""
        for symbol in symbols:
            pos = self._get_position(symbol)
            if pos is None:
                continue
            bias = "buy" if pos.type == mt5.ORDER_TYPE_BUY else "sell"
            self._trades[symbol] = {
                "bias": bias,
                "entry": pos.price_open,
                "sl": pos.sl,
                "lot": pos.volume,
                "entry_month": datetime.datetime.utcnow().month,
            }
            print(f"  🔄 Restored {symbol} {bias.upper()} from MT5")

    # ---------------------------------------------------------------- result tracking

    def record_result(self, symbol: str, was_win: bool) -> None:
        """
        Call after a trade closes. No manual bookkeeping needed --
        MT5's own history already has the closed trade by the time this
        runs. This just clears local trade state and logs the result.
        No circuit breaker -- every loss is followed by normal re-evaluation
        next month, same as a win.
        """
        streak = self._consecutive_losses_from_history(symbol)
        if not was_win:
            print(f"  ❌ {symbol}: loss recorded ({streak} in a row, no pause applied)")
        self._trades.pop(symbol, None)

    def clear_trade(self, symbol: str) -> None:
        self._trades.pop(symbol, None)

    def get_performance_summary(self, symbol: str) -> Dict[str, Any]:
        return {
            "has_open_trade": symbol in self._trades,
            "consecutive_losses": self._consecutive_losses_from_history(symbol),
        }

    # ---------------------------------------------------------------- util

    def _no(self, reason: str) -> Dict[str, Any]:
        return {
            "signal": None, "entry_price": None, "stop_loss": None,
            "take_profit": None, "lot_size": None, "sl_pips": None,
            "reason": f"⏭ {reason}",
        }

    def __repr__(self) -> str:
        return f"MonthlyCandleTrailStrategy(risk={self.risk_pct}%, no circuit breaker)"


if __name__ == "__main__":
    s = MonthlyCandleTrailStrategy()
    print(s)
    print("\nValidated symbols:", list(SYMBOL_CONFIG.keys()))
    print("XAUUSDm and CADJPYm/AUDUSDm/USDCADm intentionally excluded -- see docstring.")