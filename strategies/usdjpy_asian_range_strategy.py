# strategies/usdjpy_asian_range_strategy.py
"""
USDJPY Asian-Range Break (stateless)
====================================
Joseph's idea (2026-11-12); close time moved from 01:30 to 09:00 New York
after testing (01:30 made about zero in all three years).

Rules (exactly as replayed, asiarange/sim.py: close 09:00 NY, both live, no TP):
  RANGE : bid high / low from 19:00 to 00:00 New York (the evening before;
          Monday uses Sunday evening). Read from M1 bars (bid-based, so the
          same high/low as the ticks), two identical reads required. Fewer
          than MIN_RANGE_BARS M1 bars in the window -> no trade (holiday).
  ENTRY : at 00:00 New York (window 00:00-00:10): BUY STOP at the high,
          SELL STOP at the low, both GTC. If price is already at / through an
          edge (~5% of days, MT5 would refuse the stop), that side is bought /
          sold at market instead — the replay also filled immediately there.
  STOP  : each side's SL is the other edge (1R = range width). No TP, no trail.
  BOTH  : both orders stay live. If the first side stops out at the other
          edge, the other stop fills at the same level (stop-and-reverse).
          Each side trades at most once a day.
  EXIT  : 09:00 New York: every own position closed, every own order
          cancelled. Stateless: any own order / position found outside
          00:00-09:00 NY of the current day is closed/cancelled (missed-poll
          safety). Nothing is held through the 17:00 NY rollover (no swap).
  SIZE  : 1% of balance / (range x $ per 1.0 move per lot), rounded DOWN;
          volume_min if smaller (flagged). Median range ~45-50 pips -> about
          $3 risk at 0.01 lot.
  DAYS  : Mon-Fri, every week, no week/weekday/month filter (the best week
          and day changed between years).
  TIME  : New York time from the US DST rule in code (no tzdata on Windows);
          broker-corrected clock (ms tick time, gaps >= 5 s only).

EVIDENCE (Exness USDJPYm ticks, bid/ask replay, R = range width)
  2026 Jan-Oct: +31.8R, 231 trades (this version chosen from 30 tried).
  Unseen 2025 (frozen rules): +22.8R, 285 trades, DD 9.7R.
  Unseen 2024 (frozen rules): +45.9R, 304 trades, DD 9.4R.
  3 years: 820 trades, +100.5R, +0.123R/trade, t 3.0, max DD 9.7R,
  26 of 34 months green, worst month -6.7R (Sep 2024), worst day -2.1R.

KNOWN LIMITS: USDJPY only — the same rules lose on EURUSD / GBPUSD (2026);
approved by Joseph as a single-pair exception. Small edge per trade: wider
live spreads / slippage at 00:00 NY cut it. Jan-Feb weakest in all three
years. Up to 2 losing trades a day (about -2R). *** DEMO FIRST ***
"""

from __future__ import annotations

import datetime
import math
import time
from typing import Any, Dict, List, Optional, Tuple

import MetaTrader5 as mt5

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# MAGICs in use elsewhere: straddle 20260716, spike 20260807, confirm 20260801,
# reload 20260810, gold straddle 20261011/12, dip 20261028/29, reopen 20261030,
# dip DE30/UK100/JP225 20261031/32/33, pre-FOMC 20261034/35/36, gold hat 20261037.
SYMBOL = "USDJPYm"
MAGIC = 20261038
RISK_PCT = 1.0
RANGE_START_HOUR_NY = 19        # previous evening
ENTRY_HOUR_NY = 0               # range ends / orders placed
EXIT_HOUR_NY = 9
ENTRY_WINDOW_MINUTES = 10
MIN_RANGE_BARS = 120            # of 300 M1 bars in 19:00-00:00 (replay needed >= 200 ticks)
STALE_TICK = datetime.timedelta(minutes=10)
ENTRY_DEVIATION = 20            # points (USDJPYm: 1 point = 0.001)
EXIT_DEVIATION = 200            # points — exits must not be refused
READ_TRIES = 3
READ_PAUSE = 1.0
COMMENT_ENTRY = "ajr_entry"
COMMENT_EXIT = "ajr_exit"


def _ts(seconds: float) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(seconds, tz=datetime.timezone.utc)


# ---------------------------------------------------------------------------
# New York time without zoneinfo (US rule since 2007)
# ---------------------------------------------------------------------------
def _nth_sunday(year: int, month: int, n: int) -> datetime.date:
    d = datetime.date(year, month, 1)
    d += datetime.timedelta(days=(6 - d.weekday()) % 7)
    return d + datetime.timedelta(weeks=n - 1)


def utc_to_ny(t: datetime.datetime) -> datetime.datetime:
    utc = datetime.timezone.utc
    start = datetime.datetime.combine(_nth_sunday(t.year, 3, 2), datetime.time(7), tzinfo=utc)
    end = datetime.datetime.combine(_nth_sunday(t.year, 11, 1), datetime.time(6), tzinfo=utc)
    offset = 4 if start <= t < end else 5
    return (t - datetime.timedelta(hours=offset)).replace(tzinfo=None)


def ny_to_utc(day: datetime.date, hour: int, minute: int = 0) -> datetime.datetime:
    local = datetime.datetime(day.year, day.month, day.day, hour, minute)
    start = datetime.datetime.combine(_nth_sunday(day.year, 3, 2), datetime.time(2))
    end = datetime.datetime.combine(_nth_sunday(day.year, 11, 1), datetime.time(2))
    offset = 4 if start <= local < end else 5
    return (local + datetime.timedelta(hours=offset)).replace(tzinfo=datetime.timezone.utc)


def session(ny_day: datetime.date) -> Tuple[datetime.datetime, datetime.datetime, datetime.datetime]:
    """(range start, range end = entry, exit) in UTC for a New York trade date."""
    prev = ny_day - datetime.timedelta(days=1)
    return (ny_to_utc(prev, RANGE_START_HOUR_NY), ny_to_utc(ny_day, ENTRY_HOUR_NY),
            ny_to_utc(ny_day, EXIT_HOUR_NY))


def range_from_bars(bars, start: datetime.datetime, end: datetime.datetime) -> Tuple[Optional[float], Optional[float], int]:
    """(high, low, bar count) of M1 bars opening in [start, end)."""
    hi = lo = None
    n = 0
    s, e = start.timestamp(), end.timestamp()
    for b in bars:
        t = int(b["time"])
        if s <= t < e:
            n += 1
            h, l = float(b["high"]), float(b["low"])
            hi = h if hi is None else max(hi, h)
            lo = l if lo is None else min(lo, l)
    return hi, lo, n


# ---------------------------------------------------------------------------
# Clock: broker-corrected UTC
# ---------------------------------------------------------------------------
SKEW_MIN_SECONDS = 5.0
SKEW_MAX_SECONDS = 600.0
_skew: Dict[str, Any] = {"value": datetime.timedelta(0), "checked": None}


def _utc_now() -> datetime.datetime:
    local = datetime.datetime.now(datetime.timezone.utc)
    last = _skew["checked"]
    if last is None or (local - last).total_seconds() >= 60:
        tick = mt5.symbol_info_tick(SYMBOL)
        if tick is not None and getattr(tick, "time_msc", 0):
            skew = _ts(tick.time_msc / 1000) - local
            s = abs(skew.total_seconds())
            new = skew if SKEW_MIN_SECONDS <= s < SKEW_MAX_SECONDS else datetime.timedelta(0)
            if abs((new - _skew["value"]).total_seconds()) >= 1:
                print(f"  WARNING: VPS clock {new.total_seconds():+.1f}s off broker — correcting"
                      if new else "  VPS clock back in line with broker")
            _skew["value"] = new
        _skew["checked"] = local
    return local + _skew["value"]


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
class UsdJpyAsianRangeStrategy:
    """No state between calls — everything is read from MT5 every time."""

    def __init__(self, fallback_balance: float = 200.0) -> None:
        self.fallback_balance = fallback_balance
        if mt5.symbol_info(SYMBOL) is None and not mt5.symbol_select(SYMBOL, True):
            print(f"  WARNING: symbol '{SYMBOL}' NOT FOUND on this server — check Market Watch.")

    # ------------------------------------------------------------ MT5 helpers
    def _tick(self):
        tick = mt5.symbol_info_tick(SYMBOL)
        if tick is None:
            mt5.symbol_select(SYMBOL, True)
            tick = mt5.symbol_info_tick(SYMBOL)
        return tick

    def _filling(self) -> int:
        """filling_mode bitmask: 1 = FOK, 2 = IOC (no SYMBOL_FILLING_* names in
        the Python package — mt5.SYMBOL_FILLING_IOC crashed live)."""
        info = mt5.symbol_info(SYMBOL)
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

    def _positions(self) -> List[Any]:
        return [p for p in (mt5.positions_get(symbol=SYMBOL) or ()) if p.magic == MAGIC]

    def _orders(self) -> List[Any]:
        return [o for o in (mt5.orders_get(symbol=SYMBOL) or ()) if o.magic == MAGIC]

    def _m1_stable(self, start: datetime.datetime, end: datetime.datetime):
        """M1 bars for [start, end), read until two reads agree (half-synced
        history guard, dip bot 29 Oct 2026). None = not trusted."""
        prev = None
        for i in range(READ_TRIES + 1):
            cur = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M1, start, end - datetime.timedelta(seconds=1))
            if cur is not None and len(cur) and prev is not None and len(cur) == len(prev) \
                    and int(cur[-1]["time"]) == int(prev[-1]["time"]) \
                    and float(cur[-1]["high"]) == float(prev[-1]["high"]):
                return cur
            prev = cur
            if i < READ_TRIES:
                time.sleep(READ_PAUSE)
        return None

    def _lot(self, rng: float) -> Tuple[float, float, bool]:
        """(lot, $ lost at the stop, forced_to_min)."""
        info = mt5.symbol_info(SYMBOL)
        if info is None or not info.trade_tick_size:
            return 0.0, 0.0, False
        per_unit = info.trade_tick_value / info.trade_tick_size
        acc = mt5.account_info()
        balance = acc.balance if acc else self.fallback_balance
        raw = balance * RISK_PCT / 100.0 / (rng * per_unit)
        vmin, vstep = info.volume_min or 0.01, info.volume_step or 0.01
        vmax = info.volume_max or raw
        lot = math.floor(raw / vstep + 1e-9) * vstep
        forced = lot < vmin
        lot = round(min(vmax, max(vmin, lot)), 2)
        return lot, lot * rng * per_unit, forced

    def _close(self, pos) -> bool:
        tick = self._tick()
        if tick is None:
            return False
        is_buy = pos.type == mt5.POSITION_TYPE_BUY
        return self._ok(self._send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": SYMBOL, "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
            "position": pos.ticket, "price": tick.bid if is_buy else tick.ask,
            "deviation": EXIT_DEVIATION, "magic": MAGIC, "comment": COMMENT_EXIT,
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(),
        }, f"close ticket {pos.ticket}"))

    # ------------------------------------------------------------ management
    def manage(self, now: datetime.datetime) -> List[str]:
        """Every poll, BEFORE check_and_place. Outside 00:00-09:00 NY of the
        current New York day: close own positions, cancel own orders."""
        msgs: List[str] = []
        day = utc_to_ny(now).date()
        _, entry, exit_ = session(day)
        if entry <= now < exit_:
            return msgs
        for o in self._orders():
            ok = self._ok(self._send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket},
                                     f"cancel order {o.ticket}"))
            msgs.append(f"cancel {'BUY' if o.type == mt5.ORDER_TYPE_BUY_STOP else 'SELL'} stop "
                        f"{o.ticket}: {'ok' if ok else '!! FAILED — will retry'}")
        for pos in self._positions():
            ok = self._close(pos)
            msgs.append(f"EXIT {'BUY' if pos.type == mt5.POSITION_TYPE_BUY else 'SELL'} ticket "
                        f"{pos.ticket} {pos.volume} lots, P&L ~{pos.profit:+.2f}: "
                        f"{'ok' if ok else '!! FAILED — will retry'}")
        return msgs

    # ------------------------------------------------------------ entry
    def check_and_place(self, now: datetime.datetime) -> str:
        day = utc_to_ny(now).date()
        r_start, entry, exit_ = session(day)
        if not (entry <= now < entry + datetime.timedelta(minutes=ENTRY_WINDOW_MINUTES)):
            return "Outside entry window"
        head = f"[USDJPY range] {day:%a %d %b %Y} 00:00 NY ({entry:%H:%M} UTC)"
        if day.weekday() >= 5:
            return f"{head}\n   => NO TRADE — weekend"
        if self._positions() or self._orders():
            return f"{head}\n   => NO TRADE — orders/positions already placed"
        deals = mt5.history_deals_get(entry, now + datetime.timedelta(minutes=1))
        if deals is None:
            return f"{head}\n   => WAIT — deal history unavailable — will retry"
        if any(d.magic == MAGIC and d.symbol == SYMBOL for d in deals):
            return f"{head}\n   => NO TRADE — already traded today"

        bars = self._m1_stable(r_start, entry)
        if bars is None:
            return f"{head}\n   => WAIT — M1 history not stable/available — will retry"
        hi, lo, n = range_from_bars(bars, r_start, entry)
        if hi is None or n < MIN_RANGE_BARS:
            return f"{head}\n   => NO TRADE — only {n} M1 bars in 19:00-00:00 NY (holiday / thin market)"
        rng = hi - lo
        if rng <= 0:
            return f"{head}\n   => NO TRADE — zero range"
        tick = self._tick()
        if tick is None or now - _ts(tick.time) > STALE_TICK:
            return f"{head}\n   => WAIT — no fresh tick"
        info = mt5.symbol_info(SYMBOL)
        digits = info.digits if info else 3
        point = info.point if info and info.point else 0.001
        stops = (getattr(info, "trade_stops_level", 0) or 0) * point
        hi, lo = round(hi, digits), round(lo, digits)
        lots, risk_usd, forced = self._lot(rng)
        if lots <= 0:
            return f"{head}\n   => NO TRADE — lot size failed"

        placed: List[str] = []
        tickets: List[int] = []
        failed = False
        for side in ("buy", "sell"):
            if side == "buy":
                at_market = tick.ask >= hi - stops
                req = {"type": mt5.ORDER_TYPE_BUY if at_market else mt5.ORDER_TYPE_BUY_STOP,
                       "price": tick.ask if at_market else hi, "sl": lo}
            else:
                at_market = tick.bid <= lo + stops
                req = {"type": mt5.ORDER_TYPE_SELL if at_market else mt5.ORDER_TYPE_SELL_STOP,
                       "price": tick.bid if at_market else lo, "sl": hi}
            req.update({"action": mt5.TRADE_ACTION_DEAL if at_market else mt5.TRADE_ACTION_PENDING,
                        "symbol": SYMBOL, "volume": lots, "tp": 0.0, "magic": MAGIC,
                        "comment": COMMENT_ENTRY, "type_time": mt5.ORDER_TIME_GTC,
                        "type_filling": self._filling()})
            if at_market:
                req["deviation"] = ENTRY_DEVIATION
            res = self._send(req, f"{side.upper()} {'market' if at_market else 'stop'}")
            if self._ok(res):
                tickets.append(res.order)
                placed.append(f"{side.upper()} {'at market' if at_market else 'stop'} "
                              f"{req['price']:.{digits}f} SL {req['sl']:.{digits}f}")
            else:
                failed = True
        if failed:
            # roll back pending orders only; a market fill stays and is closed at 09:00
            for o in self._orders():
                if o.ticket in tickets:
                    self._send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket}, "rollback")
            return f"{head}\n   => ORDER SEND FAILED — pending orders rolled back ({'; '.join(placed) or 'none placed'})"
        note = "  [MIN LOT — risk above 1%]" if forced else ""
        return (f"{head}\n   range 19:00-00:00 NY: high {hi:.{digits}f} / low {lo:.{digits}f} "
                f"= {rng * 100:.1f} pips ({n} M1 bars)\n"
                f"   => {' | '.join(placed)}, {lots} lots each{note}, risk ${risk_usd:,.2f} per side\n"
                f"   close all at {exit_:%H:%M} UTC (09:00 NY)")

    # ------------------------------------------------------------ info
    def __repr__(self) -> str:
        now = datetime.datetime.now(datetime.timezone.utc)
        day = utc_to_ny(now).date()
        nxt = None
        for i in range(0, 8):
            d = day + datetime.timedelta(days=i)
            e = session(d)[1]
            if d.weekday() < 5 and e + datetime.timedelta(minutes=ENTRY_WINDOW_MINUTES) > now:
                nxt = e
                break
        return (f"UsdJpyAsianRangeStrategy: {SYMBOL} range 19:00-00:00 NY, buy stop high / sell stop "
                f"low at 00:00 NY, SL other edge, both live, close 09:00 NY, Mon-Fri, risk "
                f"{RISK_PCT}% of the range, magic {MAGIC}"
                + (f"\n  Next entry: {nxt:%a %d %b %Y %H:%M} UTC" if nxt else ""))