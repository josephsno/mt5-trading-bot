"""
Gold 06:00 UTC Straddle Scalp — Live Version (stateless)
=========================================================
Edge   : At 06:00 UTC (quiet pre-London hour) gold tends to make one clean
         push. A tight straddle catches it; a fixed $5 target banks it
         before a typical swing back.
Entry  : 06:00 UTC, buy-stop at mid + $2 and sell-stop at mid - $2.
         Whichever fills first is the trade; the other is cancelled (OCO).
         Orders are only placed between 06:00:00 and 06:02:00 UTC —
         if the bot misses that window, it sits the day out.
TP/SL  : $5 / $5 from the REAL fill price (re-anchored after fill, so
         entry slippage doesn't distort the 1:1 shape).
Exit   : TP, SL, or hard close at 07:00 UTC — whatever is still open or
         pending at 07:00 (own MAGIC only) is closed / cancelled.
Backup : Pending stops are sent with ORDER_TIME_DAY when the symbol supports
         it (symbol_info().expiration_mode bit 2), so if the bot is OFFLINE
         at 07:00 the broker still cancels any unfilled stop at the end of
         the trading day instead of leaving it live forever. Falls back to
         GTC if DAY isn't supported, or if the broker rejects it with 10022
         'Invalid expiration' (the error the spike bot hit with
         ORDER_TIME_SPECIFIED) — placement can never fail because of the
         backup. The bot's own 07:00 cancel remains the main mechanism.
Sizing : RISK_PCT of balance per trade (lot = balance * risk / ($5 SL value)),
         clamped to the broker's volume_min/step/max. Below ~$250 balance this
         is always 0.01 lot (~$5 risk).
Kill switch (backtested, see BACKTEST below):
         Stop placing NEW trades if, counting this strategy's own closed
         trades since KILL_RESET_AFTER:
           - the last 40 trades won fewer than 50%, OR
           - the last 8 trades were all losses.
         Open trades are still managed and closed normally. To resume after
         a review, set KILL_RESET_AFTER to the review date — only trades
         after it are counted. Fully derived from MT5 deal history.

BACKTEST (2026-10-03, real Exness data, spread included):
  - Ticks, 49 days Jan 2025-Jul 2026 (0.7s latency): 71% wins, +$86/0.01 lot
  - M1, every day 13 Aug-2 Oct 2026 (cautious bar ordering): 74% wins,
    +$85/0.01 lot; unseen last-2-weeks test 70% wins.
  - Combined 84 trades: 72.6% wins, avg +$2.04/trade/0.01 lot, worst -$5.24.
  - 06:00 is specific: neighbouring hours 04:00/05:00/07:00 failed the same
    tick test (0-2 of 54 setups profitable vs 46/54 at 06:00).
  - Breakeven win rate incl. spread ~53%.
  - Kill switch: wrongly stops a working (72%) strategy within a year ~3%;
    stops a dead (53%) one 100% of the time after a median 55 trades; a
    45%-win strategy is stopped after ~40 trades at about -6R.
  - Tested and REJECTED for this style: XAGUSDm, GBPUSDm, USDJPYm, BTCUSDm
    (negative on average at every hour), US30m (03:00 weak; 13:30 untestable
    on M1). Gold only.

KNOWN LIMITS: M1 sample is only 7 weeks; tick sample is news days only.
Not yet validated on every day of 2024-2026 at M1/M5 resolution.
*** DEMO FIRST ***
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Tuple

import MetaTrader5 as mt5

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SYMBOL = "XAUUSDm"
MAGIC = 20261003  # must not collide: straddle 20260716, spike 20260807,
# confirm 20260801, reload 20260810

ENTRY_HOUR = 6  # 06:00 UTC, quiet pre-London hour
ENTRY_WINDOW_SECONDS = 120  # place only between 06:00:00 and 06:02:00 UTC
CLOSE_HOUR = 7  # everything own-MAGIC is closed/cancelled from 07:00 UTC

OFFSET = 2.0  # $ from 06:00 mid to each stop order
TP = 5.0  # $ from real fill
SL = 5.0  # $ from real fill
DECIMALS = 2

RISK_PCT = 6.0

# Kill switch (backtested — see module docstring)
KILL_WINDOW = 40
KILL_MIN_WIN_RATE = 0.50
KILL_MAX_LOSS_STREAK = 8
KILL_RESET_AFTER: Optional[datetime.datetime] = None  # e.g.
# datetime.datetime(2026, 12, 1, tzinfo=datetime.timezone.utc) after a review
KILL_LOOKBACK_DAYS = 365

COMMENT_ENTRY = "scalp06_entry"
COMMENT_CLOSE = "scalp06_close"
COMMENT_FIX = "scalp06_fix"
EXIT_DEVIATION = 200  # points; max tolerance on the 07:00 market close


# ORDER_TIME_DAY should exist in the MetaTrader5 package, but this project has
# been burned by assuming constants before (mt5.SYMBOL_FILLING_IOC crashed live).
# getattr + the documented MQL5 enum value (ORDER_TIME_DAY = 1) means a missing
# name can never crash the bot.
ORDER_TIME_DAY = getattr(mt5, "ORDER_TIME_DAY", 1)


def _round(price: float) -> float:
    return round(price, DECIMALS)


# ---------------------------------------------------------------------------
# Clock: reason in broker time, not the VPS clock (VPS was seen 64s off once)
# ---------------------------------------------------------------------------
_skew: Dict[str, Any] = {"value": datetime.timedelta(0), "checked": None}


def _utc_now() -> datetime.datetime:
    local = datetime.datetime.now(datetime.timezone.utc)
    last = _skew["checked"]
    if last is None or (local - last).total_seconds() >= 60:
        tick = mt5.symbol_info_tick(SYMBOL)
        if tick is not None:
            server = datetime.datetime.fromtimestamp(tick.time, tz=datetime.timezone.utc)
            skew = server - local
            # only trust it when the tick is fresh (market open)
            if abs(skew.total_seconds()) < 600:
                if abs(skew.total_seconds()) > 5:
                    print(f"  WARNING: VPS clock {skew.total_seconds():+.1f}s off broker — correcting")
                _skew["value"] = skew
            _skew["checked"] = local
    return local + _skew["value"]


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
class Scalp0600Strategy:
    """No state between calls — everything is read from MT5 every time."""

    def __init__(self, fallback_balance: float = 200.0) -> None:
        self.fallback_balance = fallback_balance

    # ------------------------------------------------------------ MT5 helpers
    def _tick(self):
        tick = mt5.symbol_info_tick(SYMBOL)
        if tick is None:
            mt5.symbol_select(SYMBOL, True)
            tick = mt5.symbol_info_tick(SYMBOL)
        return tick

    def _filling(self) -> int:
        info = mt5.symbol_info(SYMBOL)
        if info is None:
            return mt5.ORDER_FILLING_IOC
        if info.filling_mode & 2:  # IOC bit
            return mt5.ORDER_FILLING_IOC
        if info.filling_mode & 1:  # FOK bit
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    def _time_type(self) -> int:
        """ORDER_TIME_DAY if the broker supports it for this symbol, else GTC.
        symbol_info().expiration_mode is a bitmask (1=GTC, 2=DAY, 4=SPECIFIED,
        8=SPECIFIED_DAY); the Python package doesn't expose SYMBOL_EXPIRATION_*
        names, so raw ints are used — same approach as _filling()."""
        info = mt5.symbol_info(SYMBOL)
        if info is not None and getattr(info, "expiration_mode", 0) & 2:
            return ORDER_TIME_DAY
        return mt5.ORDER_TIME_GTC

    def _send(self, request: Dict[str, Any], what: str):
        result = mt5.order_send(request)
        if result is None:
            print(f"  {what}: order_send returned None — {mt5.last_error()}")
            return None
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"  {what}: rejected retcode={result.retcode} comment='{result.comment}'")
        return result

    def _ok(self, result) -> bool:
        return result is not None and result.retcode == mt5.TRADE_RETCODE_DONE

    def _positions(self) -> List[Any]:
        return [p for p in (mt5.positions_get(symbol=SYMBOL) or ()) if p.magic == MAGIC]

    def _orders(self) -> List[Any]:
        return [o for o in (mt5.orders_get(symbol=SYMBOL) or ()) if o.magic == MAGIC]

    def _closed_trades(self, since: datetime.datetime, until: datetime.datetime) -> List[Any]:
        # No group= filter: it returned nothing on this account (2026-09-15 fix).
        deals = mt5.history_deals_get(since, until) or ()
        own = [
            d for d in deals
            if d.magic == MAGIC and d.symbol == SYMBOL and d.entry == mt5.DEAL_ENTRY_OUT
        ]
        return sorted(own, key=lambda d: d.time)

    # ------------------------------------------------------------ sizing
    def _lot(self) -> float:
        info = mt5.symbol_info(SYMBOL)
        acc = mt5.account_info()
        balance = acc.balance if acc else self.fallback_balance
        if info is None or not info.trade_tick_size:
            return 0.01
        value_per_unit_per_lot = info.trade_tick_value / info.trade_tick_size
        raw = balance * RISK_PCT / 100.0 / (SL * value_per_unit_per_lot)
        vmin = info.volume_min or 0.01
        vstep = info.volume_step or 0.01
        vmax = info.volume_max or raw
        lot = max(vmin, min(vmax, round(raw / vstep) * vstep))
        return round(lot, 2)

    # ------------------------------------------------------------ kill switch
    def kill_switch(self, now: Optional[datetime.datetime] = None) -> Tuple[bool, str]:
        """(tripped, explanation). Recomputed from deal history every call."""
        now = now or _utc_now()
        since = KILL_RESET_AFTER or (now - datetime.timedelta(days=KILL_LOOKBACK_DAYS))
        closes = self._closed_trades(since, now + datetime.timedelta(minutes=1))
        results = [d.profit + d.commission + d.swap for d in closes]
        n = len(results)

        streak = 0
        for r in reversed(results):
            if r > 0:
                break
            streak += 1
        if streak >= KILL_MAX_LOSS_STREAK:
            return True, f"{streak} losses in a row (limit {KILL_MAX_LOSS_STREAK})"

        if n >= KILL_WINDOW:
            last = results[-KILL_WINDOW:]
            wr = sum(1 for r in last if r > 0) / KILL_WINDOW
            if wr < KILL_MIN_WIN_RATE:
                return True, (f"win rate {wr:.0%} over last {KILL_WINDOW} trades "
                              f"(limit {KILL_MIN_WIN_RATE:.0%})")
            return False, f"OK — last {KILL_WINDOW} win rate {wr:.0%}, loss streak {streak}"
        wins = sum(1 for r in results if r > 0)
        return False, f"OK — {n}/{KILL_WINDOW} trades so far ({wins} wins), loss streak {streak}"

    # ------------------------------------------------------------ entry
    def _traded_today(self, now: datetime.datetime) -> bool:
        start = now.replace(hour=ENTRY_HOUR, minute=0, second=0, microsecond=0)
        deals = mt5.history_deals_get(start, now + datetime.timedelta(minutes=1)) or ()
        return any(d.magic == MAGIC and d.symbol == SYMBOL for d in deals)

    def check_and_place(self, now: Optional[datetime.datetime] = None) -> str:
        now = now or _utc_now()
        open_at = now.replace(hour=ENTRY_HOUR, minute=0, second=0, microsecond=0)
        if not (open_at <= now < open_at + datetime.timedelta(seconds=ENTRY_WINDOW_SECONDS)):
            return "Outside entry window"
        if self._positions() or self._orders():
            return "Already in a trade / straddle pending"
        if self._traded_today(now):
            return "Already traded today"
        tripped, why = self.kill_switch(now)
        if tripped:
            return f"KILL SWITCH — no new trades: {why}"

        tick = self._tick()
        if tick is None:
            return "No tick data"
        age = now - datetime.datetime.fromtimestamp(tick.time, tz=datetime.timezone.utc)
        if age > datetime.timedelta(minutes=10):
            return f"Market likely closed — last tick {age} old"

        mid = (tick.bid + tick.ask) / 2.0
        buy_stop, sell_stop = _round(mid + OFFSET), _round(mid - OFFSET)
        lots = self._lot()
        filling = self._filling()
        time_type = self._time_type()

        tickets: Dict[str, Optional[int]] = {"buy": None, "sell": None}
        used_types: List[int] = []
        for side, otype, price, sl, tp in (
            ("buy", mt5.ORDER_TYPE_BUY_STOP, buy_stop, _round(buy_stop - SL), _round(buy_stop + TP)),
            ("sell", mt5.ORDER_TYPE_SELL_STOP, sell_stop, _round(sell_stop + SL), _round(sell_stop - TP)),
        ):
            request = {
                "action": mt5.TRADE_ACTION_PENDING, "symbol": SYMBOL, "volume": lots,
                "type": otype, "price": price, "sl": sl, "tp": tp, "magic": MAGIC,
                "comment": COMMENT_ENTRY,
                # DAY = broker-side backup expiry (end of trading day) in case
                # the bot is offline at 07:00. Never SPECIFIED — Exness
                # rejected short SPECIFIED expirations (10022) on the spike bot.
                "type_time": time_type, "type_filling": filling,
            }
            res = self._send(request, f"{side.upper()} stop")
            if (res is not None and res.retcode == 10022
                    and time_type == ORDER_TIME_DAY):
                # 10022 = TRADE_RETCODE_INVALID_EXPIRATION: DAY refused after
                # all — retry once as GTC so the backup can never block entry.
                print(f"  {side.upper()} stop: DAY expiration refused, retrying as GTC")
                request["type_time"] = mt5.ORDER_TIME_GTC
                res = self._send(request, f"{side.upper()} stop (GTC)")
            if self._ok(res):
                tickets[side] = res.order
                used_types.append(request["type_time"])

        if tickets["buy"] is None or tickets["sell"] is None:
            for t in tickets.values():
                if t is not None:
                    self._send({"action": mt5.TRADE_ACTION_REMOVE, "order": t}, "rollback")
            return "Order send failed — rolled back"
        expiry = "DAY expiry" if all(t == ORDER_TIME_DAY for t in used_types) else "GTC"
        return f"Straddle placed: buy {buy_stop} / sell {sell_stop}, {lots} lots ({expiry} + 07:00 cancel)"

    # ------------------------------------------------------------ management
    def _close_position(self, pos) -> bool:
        tick = self._tick()
        if tick is None:
            return False
        is_buy = pos.type == mt5.POSITION_TYPE_BUY
        res = self._send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": SYMBOL, "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
            "position": pos.ticket, "price": tick.bid if is_buy else tick.ask,
            "deviation": EXIT_DEVIATION, "magic": MAGIC, "comment": COMMENT_CLOSE,
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(),
        }, f"close ticket {pos.ticket}")
        return self._ok(res)

    def _fix_tp_sl(self, pos) -> Optional[str]:
        """Re-anchor TP/SL to the real fill (entry slippage)."""
        is_buy = pos.type == mt5.POSITION_TYPE_BUY
        want_sl = _round(pos.price_open - SL if is_buy else pos.price_open + SL)
        want_tp = _round(pos.price_open + TP if is_buy else pos.price_open - TP)
        tol = 10 ** -DECIMALS / 2
        if abs(pos.sl - want_sl) < tol and abs(pos.tp - want_tp) < tol:
            return None
        res = self._send({
            "action": mt5.TRADE_ACTION_SLTP, "symbol": SYMBOL, "position": pos.ticket,
            "sl": want_sl, "tp": want_tp,
        }, f"TP/SL fix ticket {pos.ticket}")
        return f"ticket {pos.ticket}: TP/SL -> {want_tp}/{want_sl}" if self._ok(res) else None

    def manage(self, now: Optional[datetime.datetime] = None) -> List[str]:
        """Call every poll. OCO cancel, TP/SL re-anchor, and the 07:00 cleanup.
        Outside 06:00-07:00 anything own-MAGIC still around is closed — so a
        restart at any time can never leave a scalp hanging."""
        now = now or _utc_now()
        msgs: List[str] = []
        positions, orders = self._positions(), self._orders()
        in_session = ENTRY_HOUR <= now.hour < CLOSE_HOUR

        if not in_session:
            for p in positions:
                msgs.append(f"07:00 close ticket {p.ticket}: {'ok' if self._close_position(p) else '!! FAILED'}")
            for o in orders:
                ok = self._ok(self._send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket}, "cancel"))
                msgs.append(f"07:00 cancel order {o.ticket}: {'ok' if ok else '!! FAILED'}")
            return msgs

        # OCO: a position on one side cancels the pending order on the other
        has_buy = any(p.type == mt5.POSITION_TYPE_BUY for p in positions)
        has_sell = any(p.type == mt5.POSITION_TYPE_SELL for p in positions)
        for o in orders:
            opposite = (has_buy and o.type == mt5.ORDER_TYPE_SELL_STOP) or (
                has_sell and o.type == mt5.ORDER_TYPE_BUY_STOP)
            if opposite:
                ok = self._ok(self._send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket}, "OCO cancel"))
                msgs.append(f"OCO cancel {o.ticket}: {'ok' if ok else '!! FAILED'}")

        for p in positions:
            fix = self._fix_tp_sl(p)
            if fix:
                msgs.append(fix)
        return msgs

    def __repr__(self) -> str:
        return (f"Scalp0600Strategy({SYMBOL} {ENTRY_HOUR:02d}:00 UTC, +/-${OFFSET} stops, "
                f"TP ${TP} / SL ${SL}, close {CLOSE_HOUR:02d}:00, risk {RISK_PCT}%, "
                f"kill: last {KILL_WINDOW} < {KILL_MIN_WIN_RATE:.0%} or {KILL_MAX_LOSS_STREAK} losses in a row, "
                f"magic {MAGIC})")