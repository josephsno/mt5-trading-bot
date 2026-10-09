"""
Gold Straddle Strategy — 00:00 + 01:00 UTC (stateless)
======================================================
One class, GoldStraddleStrategy, runs every setup in SETUPS. Each setup has
its own trigger time and its own MAGIC, so the two never touch each other's
orders or positions (a 00:00 trade still open at 01:00 does NOT block the
01:00 straddle).

Logic is a 1:1 copy of the XAUUSDm branch of straddle_strategy.py — the exact
code that was replayed on tick data — so live behaviour matches the backtest:
Entry  : At the trigger time, mid = (bid+ask)/2. Buy-stop at mid + $10,
         sell-stop at mid - $10. Whichever fills first is the trade; the
         other is cancelled at the next poll (OCO).
SL     : $20 from the STOP LEVEL (set on the pending order, not re-anchored).
BE     : When the CURRENT price at a poll is >= +$20 in profit, SL -> entry.
         (Tested: triggering on the best price since entry instead was WORSE,
         +$117 vs +$176 Jun/Jul/Oct — do not "fix" this.)
Trail  : After BE, SL trails $15 behind the best M15 high/low since entry.
Cancel : Unfilled stops expire at the setup's cancel hour (8h after trigger),
         broker-side (ORDER_TIME_SPECIFIED) AND bot-side.
Exit   : No TP. Hard close 1h before this setup's next trigger, capped at
         Friday 20:00 UTC so nothing rides the weekend.
Dual   : If both stops fill before the OCO cancel, the newer position is
         closed at market and the first fill is kept.
Polling: every 5 minutes, clock-aligned (main_gold_straddle.py) — the
         backtest assumed exactly this. Tested 1s/10s/30s/60s/15min: no speed
         is reliably better; 5 min was near the top.

BACKTEST (2026-10-09, Exness XAUUSDm ticks, Jan-Aug + Oct 1-9 2026, per 0.01 lot)
  Month     00:00      01:00      Both
  Jan     +167.35    +170.07    +337.42
  Feb     +348.41     +96.10    +444.51
  Mar      +19.93     -32.57     -12.64
  Apr     -100.50    +180.29     +79.79
  May     +171.12    +342.12    +513.24
  Jun      +57.27    +133.00    +190.27
  Jul     +100.62      -3.19     +97.43
  Aug      +14.84    +121.77    +136.61
  Oct1-9   +62.90     +46.16    +109.06
  Total   +841.94  +1,053.74  +1,895.68
  01:00: 183 trades, 55% wins, max DD $118, worst streak 5.
  00:00: 173 trades, 51% wins, max DD $189, worst streak 7.
  Both : max DD $185; daily correlation 0.10 (one lost while the other won on
         86 of 173 shared days; both lost on 38).
  Why it works: 01:00 UTC = Shanghai Gold Exchange day-session open (09:00
  Beijing); 00:00 UTC = Tokyo open. Same code at every other hour 02:00-15:00
  mostly lost (-$479..+$239). Buys and sells both profitable.

KNOWN LIMITS: 2026 only (a volatile gold year). 01:00's settings were chosen
in July and held up since, but Jul/Aug/Oct averaged ~+$3/trade vs ~+$7
earlier. 00:00 was found on 2026-10-09 and has no out-of-sample test yet.
Sep 2026 untested. *** 00:00 DEMO FIRST ***
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

import MetaTrader5 as mt5

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# MAGICs in use elsewhere: straddle 20260716, spike 20260807, confirm 20260801,
# reload 20260810, gold scalp 20261003, USTEC_E 20261009, USTEC_A 20261010.
SETUPS: List[Dict[str, Any]] = [
    {
        "name": "GOLD_0000",
        "enabled": True,
        "symbol": "XAUUSDm",
        "magic": 20261011,
        "trigger_hour": 0, "trigger_minute": 0,   # 00:00 UTC, Tokyo open
        "cancel_hour": 8,                          # unfilled stops expire 08:00
        "offset": 10.0, "sl": 20.0, "trail": 15.0, "be_trigger": 20.0,
        "risk_pct": 1.0,
        "comment": "gs0000",
    },
    {
        "name": "GOLD_0100",
        "enabled": True,
        "symbol": "XAUUSDm",
        "magic": 20261012,
        "trigger_hour": 1, "trigger_minute": 0,   # 01:00 UTC, SGE day open
        "cancel_hour": 9,                          # unfilled stops expire 09:00
        "offset": 10.0, "sl": 20.0, "trail": 15.0, "be_trigger": 20.0,
        "risk_pct": 1.0,
        "comment": "gs0100",
    },
]

ENTRY_WINDOW_SECONDS = 120     # straddle is only placed in the first 2 min
DEADLINE_BUFFER_HOURS = 1      # close 1h before this setup's next trigger
WEEKLY_CLOSE_HOUR = 20         # Friday hard close (UTC) — gold closes weekends
STALE_TICK = datetime.timedelta(minutes=10)
EXIT_DEVIATION = 200           # points, market closes
DECIMALS = 2                   # same rounding straddle_strategy.py uses for XAUUSDm


def enabled_setups() -> List[Dict[str, Any]]:
    return [s for s in SETUPS if s["enabled"]]


def _round(price: float) -> float:
    return round(price, DECIMALS)


def _ts(seconds: int) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(seconds, tz=datetime.timezone.utc)


# ---------------------------------------------------------------------------
# Clock: reason in broker time, not the VPS clock (VPS was seen 64s off once)
# ---------------------------------------------------------------------------
_skew: Dict[str, Any] = {"value": datetime.timedelta(0), "checked": None}


def _utc_now() -> datetime.datetime:
    local = datetime.datetime.now(datetime.timezone.utc)
    last = _skew["checked"]
    if last is None or (local - last).total_seconds() >= 60:
        for sym in dict.fromkeys(s["symbol"] for s in enabled_setups()):
            tick = mt5.symbol_info_tick(sym)
            if tick is None:
                continue
            skew = _ts(tick.time) - local
            # only trust it when the tick is fresh (market open)
            if abs(skew.total_seconds()) < 600:
                if abs(skew.total_seconds()) > 5:
                    print(f"  WARNING: VPS clock {skew.total_seconds():+.1f}s off broker — correcting")
                _skew["value"] = skew
                break
        _skew["checked"] = local
    return local + _skew["value"]


def anchor_at_or_before(s: Dict[str, Any], t: datetime.datetime) -> datetime.datetime:
    """Most recent trigger time (e.g. 01:00) at or before t."""
    a = t.replace(hour=s["trigger_hour"], minute=s["trigger_minute"], second=0, microsecond=0)
    if a > t:
        a -= datetime.timedelta(days=1)
    return a


def cancel_time(s: Dict[str, Any], anchor: datetime.datetime) -> datetime.datetime:
    c = anchor.replace(hour=s["cancel_hour"], minute=0)
    if c <= anchor:
        c += datetime.timedelta(days=1)
    return c


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
class GoldStraddleStrategy:
    """No state between calls — everything is read from MT5 every time.
    Every method takes the setup dict it is working on."""

    def __init__(self, fallback_balance: float = 200.0) -> None:
        self.fallback_balance = fallback_balance
        self.setups = enabled_setups()

    # ------------------------------------------------------------ MT5 helpers
    def _tick(self, symbol: str):
        """symbol_info_tick() returns None for a symbol not in Market Watch;
        symbol_select() adds it (2026-07-21 GBPUSDm miss)."""
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            mt5.symbol_select(symbol, True)
            tick = mt5.symbol_info_tick(symbol)
        return tick

    def _filling(self, symbol: str) -> int:
        """filling_mode bitmask: 1 = FOK, 2 = IOC. The Python package has no
        SYMBOL_FILLING_* names (mt5.SYMBOL_FILLING_IOC crashed live)."""
        info = mt5.symbol_info(symbol)
        if info is None:
            return mt5.ORDER_FILLING_IOC
        if info.filling_mode & 2:
            return mt5.ORDER_FILLING_IOC
        if info.filling_mode & 1:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

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

    def _positions(self, s: Dict[str, Any]) -> List[Any]:
        own = [p for p in (mt5.positions_get(symbol=s["symbol"]) or ()) if p.magic == s["magic"]]
        return sorted(own, key=lambda p: (p.time_msc, p.ticket))

    def _orders(self, s: Dict[str, Any]) -> List[Any]:
        return [o for o in (mt5.orders_get(symbol=s["symbol"]) or ()) if o.magic == s["magic"]]

    def _traded_since(self, s: Dict[str, Any], start: datetime.datetime,
                      now: datetime.datetime) -> bool:
        # No group= filter: it returned nothing on this account (2026-09-15 fix).
        deals = mt5.history_deals_get(start, now + datetime.timedelta(minutes=1)) or ()
        return any(d.magic == s["magic"] and d.symbol == s["symbol"] for d in deals)

    # ------------------------------------------------------------ sizing
    def _lot(self, s: Dict[str, Any]) -> float:
        """balance * risk% / (SL * $ per 1.0 move per lot), from the symbol's
        real tick value / tick size, rounded to volume_step, clamped to the
        broker's volume_min / volume_max (raised to the minimum if below)."""
        info = mt5.symbol_info(s["symbol"])
        if info is None or not info.trade_tick_size:
            return 0.01
        value_per_unit_per_lot = info.trade_tick_value / info.trade_tick_size
        acc = mt5.account_info()
        balance = acc.balance if acc else self.fallback_balance
        raw = balance * s["risk_pct"] / 100.0 / (s["sl"] * value_per_unit_per_lot)
        vmin = info.volume_min or 0.01
        vstep = info.volume_step or 0.01
        vmax = info.volume_max or raw
        lot = max(vmin, min(vmax, round(raw / vstep) * vstep))
        return round(lot, 2)

    # ------------------------------------------------------------ entry
    def check_and_place(self, s: Dict[str, Any],
                        now: Optional[datetime.datetime] = None) -> str:
        now = now or _utc_now()
        sym = s["symbol"]
        open_at = anchor_at_or_before(s, now)
        if now >= open_at + datetime.timedelta(seconds=ENTRY_WINDOW_SECONDS):
            return "Outside entry window"
        if self._positions(s) or self._orders(s):
            return "Already in a trade / straddle pending"
        if self._traded_since(s, open_at, now):
            return "Already traded this session"

        tick = self._tick(sym)
        if tick is None:
            return "No tick data"
        age = now - _ts(tick.time)
        if age > STALE_TICK:
            return f"Market likely closed — last tick {age} old"

        mid = (tick.bid + tick.ask) / 2.0
        buy_stop, sell_stop = _round(mid + s["offset"]), _round(mid - s["offset"])
        buy_sl, sell_sl = _round(buy_stop - s["sl"]), _round(sell_stop + s["sl"])
        lots = self._lot(s)
        filling = self._filling(sym)
        expiry = cancel_time(s, open_at)

        tickets: Dict[str, Optional[int]] = {"buy": None, "sell": None}
        for side, otype, price, stop in (
            ("buy", mt5.ORDER_TYPE_BUY_STOP, buy_stop, buy_sl),
            ("sell", mt5.ORDER_TYPE_SELL_STOP, sell_stop, sell_sl),
        ):
            request = {
                "action": mt5.TRADE_ACTION_PENDING, "symbol": sym, "volume": lots,
                "type": otype, "price": price, "sl": stop, "tp": 0.0,
                "magic": s["magic"], "comment": f"{s['comment']}_entry",
                # same as straddle_strategy.py: broker-side expiry at the cancel
                # hour; expiration must be an int Unix timestamp, never a datetime
                "type_time": mt5.ORDER_TIME_SPECIFIED,
                "expiration": int(expiry.timestamp()),
                "type_filling": filling,
            }
            res = self._send(request, f"{s['name']} {side.upper()} stop")
            if res is not None and res.retcode == 10022:
                # 10022 = invalid expiration: retry once as GTC — the bot-side
                # cancel in manage() still removes it at the cancel hour
                print(f"  {s['name']} {side.upper()} stop: expiration refused, retrying as GTC")
                request["type_time"] = mt5.ORDER_TIME_GTC
                request.pop("expiration", None)
                res = self._send(request, f"{s['name']} {side.upper()} stop (GTC)")
            if self._ok(res):
                tickets[side] = res.order

        if tickets["buy"] is None or tickets["sell"] is None:
            for t in tickets.values():
                if t is not None:
                    self._send({"action": mt5.TRADE_ACTION_REMOVE, "order": t}, "rollback")
            return "Order send failed — rolled back"
        return (f"Straddle placed: buy {buy_stop} / sell {sell_stop}, {lots} lots, "
                f"SL {buy_sl}/{sell_sl}, expires {expiry:%H:%M}")

    # ------------------------------------------------------------ management
    def _close_position(self, s: Dict[str, Any], pos, comment: str) -> bool:
        tick = self._tick(s["symbol"])
        if tick is None:
            return False
        is_buy = pos.type == mt5.POSITION_TYPE_BUY
        res = self._send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": s["symbol"], "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
            "position": pos.ticket, "price": tick.bid if is_buy else tick.ask,
            "deviation": EXIT_DEVIATION, "magic": s["magic"],
            "comment": f"{s['comment']}_{comment}",
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(s["symbol"]),
        }, f"{s['name']} close ticket {pos.ticket}")
        return self._ok(res)

    def _cancel(self, s: Dict[str, Any], order, why: str) -> str:
        ok = self._ok(self._send({"action": mt5.TRADE_ACTION_REMOVE, "order": order.ticket},
                                 f"{s['name']} {why}"))
        return f"{why} order {order.ticket}: {'ok' if ok else '!! FAILED'}"

    def _modify_sl(self, s: Dict[str, Any], pos, new_sl: float) -> bool:
        return self._ok(self._send({
            "action": mt5.TRADE_ACTION_SLTP, "symbol": s["symbol"], "position": pos.ticket,
            "sl": new_sl, "tp": pos.tp,
        }, f"{s['name']} SL modify ticket {pos.ticket}"))

    def _deadline(self, s: Dict[str, Any], fill_time: datetime.datetime) -> datetime.datetime:
        """1h before this setup's NEXT trigger after the fill, capped at this
        week's Friday WEEKLY_CLOSE_HOUR (same rule as straddle_strategy.py)."""
        today_trigger = fill_time.replace(hour=s["trigger_hour"], minute=s["trigger_minute"],
                                          second=0, microsecond=0)
        next_trigger = (today_trigger + datetime.timedelta(days=1)
                        if today_trigger <= fill_time else today_trigger)
        deadline = next_trigger - datetime.timedelta(hours=DEADLINE_BUFFER_HOURS)
        days_until_friday = (4 - fill_time.weekday()) % 7
        friday_close = (fill_time + datetime.timedelta(days=days_until_friday)).replace(
            hour=WEEKLY_CLOSE_HOUR, minute=0, second=0, microsecond=0)
        if friday_close < fill_time:
            friday_close += datetime.timedelta(days=7)
        if deadline.weekday() >= 5 or deadline > friday_close:
            return friday_close
        return deadline

    def _best_price_since_entry(self, s: Dict[str, Any], pos, now: datetime.datetime) -> float:
        """Re-derived from M15 bars every call (bars with open time >= entry),
        exactly like straddle_strategy.py — nothing stored."""
        bars = mt5.copy_rates_range(s["symbol"], mt5.TIMEFRAME_M15, _ts(pos.time), now)
        if bars is None or len(bars) == 0:
            return pos.price_open
        if pos.type == mt5.POSITION_TYPE_BUY:
            return max(bar["high"] for bar in bars)
        return min(bar["low"] for bar in bars)

    def manage(self, s: Dict[str, Any], now: Optional[datetime.datetime] = None) -> List[str]:
        """Call every poll, for every setup, BEFORE check_and_place:
        OCO / expiry cancels, dual-fill heal, deadline close, BE, trail."""
        now = now or _utc_now()
        msgs: List[str] = []
        positions, orders = self._positions(s), self._orders(s)

        # 1) Pending stops: OCO once a side has filled, else bot-side expiry
        #    (backup to the broker's own expiration).
        for o in orders:
            if positions:
                msgs.append(self._cancel(s, o, "OCO cancel"))
            elif now >= cancel_time(s, anchor_at_or_before(s, _ts(o.time_setup))):
                msgs.append(self._cancel(s, o, "expiry cancel"))

        if not positions:
            return msgs

        # 2) Dual fill: keep the first fill, close the rest.
        for extra in positions[1:]:
            ok = self._close_position(s, extra, "dual")
            msgs.append(f"dual-fill: closed extra ticket {extra.ticket}: {'ok' if ok else '!! FAILED'}")
        pos = positions[0]

        # 3) Adaptive deadline / Friday close.
        if now >= self._deadline(s, _ts(pos.time)):
            ok = self._close_position(s, pos, "deadline")
            msgs.append(f"deadline close ticket {pos.ticket}: {'ok' if ok else '!! FAILED'}")
            return msgs

        tick = self._tick(s["symbol"])
        if tick is None:
            msgs.append("No tick data")
            return msgs

        is_buy = pos.type == mt5.POSITION_TYPE_BUY
        entry = pos.price_open
        current = tick.bid if is_buy else tick.ask
        # be_done inferred from the SL itself; both sides rounded first
        # (float mismatch caused endless retcode=10025 "No changes" before)
        entry_r, sl_r = _round(entry), _round(pos.sl)
        be_done = (sl_r >= entry_r) if is_buy else (sl_r <= entry_r and pos.sl > 0)
        favorable = (current - entry) if is_buy else (entry - current)

        # 4) Breakeven — on the CURRENT price at this poll (see docstring).
        if not be_done and favorable >= s["be_trigger"]:
            new_sl = _round(entry)
            ok = self._modify_sl(s, pos, new_sl)
            msgs.append(f"BE -> SL {new_sl}" if ok else "BE modify failed")
            return msgs

        # 5) Trail after BE.
        if be_done:
            best = self._best_price_since_entry(s, pos, now)
            if is_buy:
                new_sl = _round(best - s["trail"])
                if new_sl > sl_r:
                    ok = self._modify_sl(s, pos, new_sl)
                    msgs.append(f"Trail -> SL {new_sl}" if ok else "Trail failed")
            else:
                new_sl = _round(best + s["trail"])
                if new_sl < sl_r:
                    ok = self._modify_sl(s, pos, new_sl)
                    msgs.append(f"Trail -> SL {new_sl}" if ok else "Trail failed")
        if not msgs:
            msgs.append(f"Holding {'BUY' if is_buy else 'SELL'} {pos.ticket}: "
                        f"{favorable:+.2f}, SL {pos.sl}")
        return msgs

    def __repr__(self) -> str:
        lines = ["GoldStraddleStrategy:"]
        for s in SETUPS:
            lines.append(
                f"  [{'ON ' if s['enabled'] else 'OFF'}] {s['name']}: {s['symbol']} "
                f"{s['trigger_hour']:02d}:{s['trigger_minute']:02d} UTC, +/-${s['offset']} stops, "
                f"SL ${s['sl']}, BE +${s['be_trigger']}, trail ${s['trail']}, "
                f"expire {s['cancel_hour']:02d}:00, risk {s['risk_pct']}%, magic {s['magic']}")
        return "\n".join(lines)