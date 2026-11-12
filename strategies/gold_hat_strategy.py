# strategies/gold_hat_strategy.py
"""
Gold "Hat" Long — winter only (stateless)
=========================================
Source: Donati & Jung, "Gold Price Dynamics Around the Clock" (CBS 2019,
COMEX gold 2001-2018): gold rises from the New York afternoon through Asian
hours and falls through London / NY morning; the effect is concentrated in
US standard time (Nov-Mar).

Rules (exactly as backtested, hat/hat2.py + stop test 2026-11-03):
  WHEN   : only on New York dates that are on standard (winter) time —
           first Sunday of November -> second Sunday of March. Mon-Thu
           entries only (a Friday entry would hold over the weekend). Nights
           touching a holiday closure (24/25 Dec, 31 Dec/1 Jan) are skipped.
  ENTRY  : market BUY at 11:00 New York (16:00 UTC / 17:00 Lagos), entry
           window 11:00-11:30 NY. One trade per night.
  EXIT   : close at 02:00 New York the next day (07:00 UTC / 08:00 Lagos).
           A missed exit is closed at the first poll after it.
  STOP   : 2 x ATR14 below the REAL fill. ATR14 = average daily high-low
           range of the last 14 finished WEEKDAY D1 bars (known before entry;
           Sunday stub bars left out). A
           disaster stop only: never hit in 5 winters.
  SIZE   : lot = 1% of balance / (2 x ATR14 x $ per 1.0 move per lot),
           rounded DOWN to the lot step; volume_min if smaller (flagged).
           Do NOT use a fixed-$ stop: gold's daily range went ~$20 (2021-24)
           -> ~$40 (2025) -> ~$100 (2026).
  TIME   : New York time from the US daylight-saving rule in code (no tzdata
           needed on Windows). Broker-corrected clock (ms tick time, gaps
           >= 5 s only).

BACKTEST (Exness XAUUSDm H1, Jul 2021 - Nov 2026, winter only, per 0.01 lot,
$0.30 spread + $0.50 swap per night)
  Total +$1,540, t 3.1, max DD $166, 56% win, worst night -$94.
  Every winter positive: 2021/22 +$93, 2022/23 +$3, 2023/24 +$173,
  2024/25 +$113, 2025/26 +$1,204. Full year (incl. summer) is weak
  (t 1.5, DD $880) — winter only.

KNOWN LIMITS: 2025/26 is 78% of the profit; only 5 winters of evidence;
the paper's authors dropped a winter strategy as possibly chance. Holds gold
through the 00:00/01:00 straddle hours and FOMC (19:00 UTC) nights — the
account must stay in hedging mode. Swap ~-$0.50 per night per 0.01 lot.
*** DEMO FIRST ***
"""

from __future__ import annotations

import datetime
import math
from typing import Any, Dict, List, Optional, Tuple

import MetaTrader5 as mt5

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# MAGICs in use elsewhere: straddle 20260716, spike 20260807, confirm 20260801,
# reload 20260810, gold straddle 20261011/12, dip 20261028/29, reopen 20261030,
# dip DE30/UK100/JP225 20261031/32/33, pre-FOMC 20261034/35/36.
SYMBOL = "XAUUSDm"
MAGIC = 20261037
RISK_PCT = 1.0
ATR_PERIOD = 14
STOP_ATR = 2.0
ENTRY_HOUR_NY = 11
EXIT_HOUR_NY = 2               # next New York day
ENTRY_WINDOW_MINUTES = 30
ENTRY_WEEKDAYS = (0, 1, 2, 3)  # Mon-Thu (New York date)
STALE_TICK = datetime.timedelta(minutes=10)
ENTRY_DEVIATION = 200          # points
EXIT_DEVIATION = 500           # points — exits must not be refused
COMMENT_ENTRY = "hat_entry"
COMMENT_EXIT = "hat_exit"


def _ts(seconds: float) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(seconds, tz=datetime.timezone.utc)


# ---------------------------------------------------------------------------
# New York time without zoneinfo (US rule since 2007)
# ---------------------------------------------------------------------------
def _nth_sunday(year: int, month: int, n: int) -> datetime.date:
    d = datetime.date(year, month, 1)
    d += datetime.timedelta(days=(6 - d.weekday()) % 7)
    return d + datetime.timedelta(weeks=n - 1)


def _dst_utc_bounds(year: int) -> Tuple[datetime.datetime, datetime.datetime]:
    """DST in New York runs from 2nd Sunday of March 02:00 EST (07:00 UTC) to
    1st Sunday of November 02:00 EDT (06:00 UTC)."""
    utc = datetime.timezone.utc
    start = datetime.datetime.combine(_nth_sunday(year, 3, 2), datetime.time(7), tzinfo=utc)
    end = datetime.datetime.combine(_nth_sunday(year, 11, 1), datetime.time(6), tzinfo=utc)
    return start, end


def utc_to_ny(t: datetime.datetime) -> datetime.datetime:
    """UTC -> New York wall-clock (naive)."""
    start, end = _dst_utc_bounds(t.year)
    offset = 4 if start <= t < end else 5
    return (t - datetime.timedelta(hours=offset)).replace(tzinfo=None)


def ny_to_utc(day: datetime.date, hour: int, minute: int = 0) -> datetime.datetime:
    local = datetime.datetime(day.year, day.month, day.day, hour, minute)
    start = datetime.datetime.combine(_nth_sunday(day.year, 3, 2), datetime.time(2))
    end = datetime.datetime.combine(_nth_sunday(day.year, 11, 1), datetime.time(2))
    offset = 4 if start <= local < end else 5
    return (local + datetime.timedelta(hours=offset)).replace(tzinfo=datetime.timezone.utc)


def is_winter(ny_day: datetime.date) -> bool:
    """New York is on standard time at 11:00 on this date."""
    t = ny_to_utc(ny_day, ENTRY_HOUR_NY)
    start, end = _dst_utc_bounds(ny_day.year)
    return not (start <= t < end)


def entry_time(ny_day: datetime.date) -> datetime.datetime:
    return ny_to_utc(ny_day, ENTRY_HOUR_NY)


def exit_time(ny_day: datetime.date) -> datetime.datetime:
    """02:00 New York the day after the entry date."""
    return ny_to_utc(ny_day + datetime.timedelta(days=1), EXIT_HOUR_NY)


def market_closed(d: datetime.date) -> bool:
    """Gold holiday closures that fall on a weekday: 25 Dec, 1 Jan, and the
    observed day when they fall on a weekend (24 Dec Fri, 26 Dec Mon, 2 Jan Mon).
    Matches every missing entry/exit candle in the 2021-2026 H1 data."""
    if (d.month, d.day) in ((12, 25), (1, 1)):
        return True
    if (d.month, d.day) == (12, 24) and d.weekday() == 4:
        return True
    if (d.month, d.day) in ((12, 26), (1, 2)) and d.weekday() == 0:
        return True
    return False


def trading_night(ny_day: datetime.date) -> bool:
    """Mon-Thu, NY winter time, and neither the entry day nor the exit day
    (next NY day) is a holiday closure — the backtest skipped those nights
    (no 11:00 or 02:00 candle); the bot would otherwise be stuck in a trade
    over the holiday."""
    nxt = ny_day + datetime.timedelta(days=1)
    return (ny_day.weekday() in ENTRY_WEEKDAYS and is_winter(ny_day)
            and not market_closed(ny_day) and not market_closed(nxt))


def atr_hilo(d1_bars, period: int = ATR_PERIOD) -> Optional[float]:
    """Average daily high-low range of the last `period` finished D1 bars
    (as backtested — plain high-low, not true range)."""
    if d1_bars is None or len(d1_bars) < period:
        return None
    last = d1_bars[-period:]
    return sum(float(b["high"]) - float(b["low"]) for b in last) / period


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
class GoldHatStrategy:
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

    def _fresh_tick(self, now: datetime.datetime):
        tick = self._tick()
        if tick is None:
            return None, "no tick data"
        age = now - _ts(tick.time)
        if age > STALE_TICK:
            return None, f"market likely closed — last tick {age} old"
        return tick, ""

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
        own = [p for p in (mt5.positions_get(symbol=SYMBOL) or ()) if p.magic == MAGIC]
        return sorted(own, key=lambda p: (p.time_msc, p.ticket))

    def _atr(self, now: datetime.datetime) -> Optional[float]:
        """Finished weekday D1 bars only. Exness opens gold on Sunday 23:00 UTC,
        so MT5 has a 1-hour Sunday D1 bar most weeks; counting it would shrink
        the ATR (the backtest used Mon-Fri days only)."""
        d1 = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_D1, 1, ATR_PERIOD + 10)
        if d1 is None or len(d1) == 0:
            return None
        week = [b for b in d1 if _ts(int(b["time"])).weekday() < 5]
        if len(week) < ATR_PERIOD:
            return None
        if now - _ts(int(week[-1]["time"])) > datetime.timedelta(days=5):
            return None                                   # stale D1 history
        return atr_hilo(week)

    def _lot(self, atr: float) -> Tuple[float, float, bool]:
        """(lot, $ lost if the stop is hit, forced_to_min)."""
        info = mt5.symbol_info(SYMBOL)
        if info is None or not info.trade_tick_size:
            return 0.0, 0.0, False
        per_unit = info.trade_tick_value / info.trade_tick_size
        acc = mt5.account_info()
        balance = acc.balance if acc else self.fallback_balance
        stop_dist = STOP_ATR * atr
        raw = balance * RISK_PCT / 100.0 / (stop_dist * per_unit)
        vmin, vstep = info.volume_min or 0.01, info.volume_step or 0.01
        vmax = info.volume_max or raw
        lot = math.floor(raw / vstep + 1e-9) * vstep
        forced = lot < vmin
        lot = round(min(vmax, max(vmin, lot)), 2)
        return lot, lot * stop_dist * per_unit, forced

    def _set_stop(self, pos, now: datetime.datetime, atr: Optional[float] = None) -> str:
        atr = atr or self._atr(now)
        info = mt5.symbol_info(SYMBOL)
        if not atr or info is None:
            return "Stop: ATR / symbol info unavailable — will retry"
        sl = round(pos.price_open - STOP_ATR * atr, info.digits)
        ok = self._ok(self._send({
            "action": mt5.TRADE_ACTION_SLTP, "symbol": SYMBOL, "position": pos.ticket,
            "sl": sl, "tp": 0.0,
        }, f"hat stop ticket {pos.ticket}"))
        return (f"Stop set at {sl:,.2f} ({STOP_ATR:g} x ATR {atr:,.2f})"
                if ok else "Stop modify FAILED — will retry next poll")

    # ------------------------------------------------------------ management
    def manage(self, now: datetime.datetime) -> List[str]:
        """Every poll, BEFORE check_and_place: close positions whose exit time
        (02:00 NY the day after their entry) has come; add a missing stop."""
        msgs: List[str] = []
        for pos in self._positions():
            ny_day = utc_to_ny(_ts(pos.time)).date()
            due = exit_time(ny_day)
            if now < due:
                if not pos.sl:
                    msgs.append(self._set_stop(pos, now))
                continue
            tick, why = self._fresh_tick(now)
            if tick is None:
                msgs.append(f"exit due for ticket {pos.ticket} but {why} — will retry")
                continue
            ok = self._ok(self._send({
                "action": mt5.TRADE_ACTION_DEAL, "symbol": SYMBOL, "volume": pos.volume,
                "type": mt5.ORDER_TYPE_SELL, "position": pos.ticket, "price": tick.bid,
                "deviation": EXIT_DEVIATION, "magic": MAGIC, "comment": COMMENT_EXIT,
                "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(),
            }, f"hat close ticket {pos.ticket}"))
            msgs.append(f"EXIT ticket {pos.ticket} {pos.volume} lots, open {pos.price_open} -> "
                        f"~{tick.bid}, P&L ~{pos.profit + pos.swap:+.2f}: {'ok' if ok else '!! FAILED'}")
        return msgs

    # ------------------------------------------------------------ entry
    def check_and_place(self, now: datetime.datetime) -> str:
        ny = utc_to_ny(now)
        day = ny.date()
        start = entry_time(day)
        if not (start <= now < start + datetime.timedelta(minutes=ENTRY_WINDOW_MINUTES)):
            return "Outside entry window"
        head = f"[HAT] {day:%a %d %b %Y} 11:00 NY ({start:%H:%M} UTC)"
        if not trading_night(day):
            if not is_winter(day):
                why = "summer time (winter only)"
            elif day.weekday() not in ENTRY_WEEKDAYS:
                why = "Friday — no weekend hold"
            else:
                why = "holiday — market closed today or at 02:00 tomorrow"
            return f"{head}\n   => NO TRADE — {why}"
        if self._positions():
            return f"{head}\n   => NO TRADE — position still open"
        deals = mt5.history_deals_get(start, now + datetime.timedelta(minutes=1))
        if deals is None:
            return f"{head}\n   => WAIT — deal history unavailable — will retry"
        if any(d.magic == MAGIC and d.symbol == SYMBOL and d.entry == mt5.DEAL_ENTRY_IN for d in deals):
            return f"{head}\n   => NO TRADE — already entered tonight"
        tick, why = self._fresh_tick(now)
        if tick is None:
            return f"{head}\n   => WAIT — {why}"
        atr = self._atr(now)
        if not atr:
            return f"{head}\n   => WAIT — ATR / D1 history unavailable — will retry"
        lot, risk_usd, forced = self._lot(atr)
        if lot <= 0:
            return f"{head}\n   => NO TRADE — lot size failed"
        res = self._send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": SYMBOL, "volume": lot,
            "type": mt5.ORDER_TYPE_BUY, "price": tick.ask, "sl": 0.0, "tp": 0.0,
            "deviation": ENTRY_DEVIATION, "magic": MAGIC, "comment": COMMENT_ENTRY,
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(),
        }, "hat BUY")
        if not self._ok(res):
            return f"{head}\n   => BUY REJECTED — see the line above"
        pos = next((p for p in self._positions() if p.ticket == res.order), None)
        stop_msg = self._set_stop(pos, now, atr) if pos is not None \
            else "Stop: position not visible yet — manage() will add it"
        note = "  [MIN LOT — risk above 1%]" if forced else ""
        return (f"{head}\n   => BUY {lot} lots @ {res.price:,.2f}{note}\n"
                f"   ATR {atr:,.2f}, risk at stop ${risk_usd:,.2f}; exit {exit_time(day):%a %d %b %H:%M} UTC\n"
                f"   {stop_msg}")

    # ------------------------------------------------------------ info
    def next_entry(self, now: datetime.datetime) -> Optional[datetime.datetime]:
        day = utc_to_ny(now).date()
        for i in range(0, 400):
            d = day + datetime.timedelta(days=i)
            if trading_night(d) and entry_time(d) + datetime.timedelta(minutes=ENTRY_WINDOW_MINUTES) > now:
                return entry_time(d)
        return None

    def __repr__(self) -> str:
        now = datetime.datetime.now(datetime.timezone.utc)
        nxt = self.next_entry(now)
        return (f"GoldHatStrategy: {SYMBOL} BUY 11:00 NY -> SELL 02:00 NY next day, Mon-Thu, "
                f"winter (NY standard time) only, stop {STOP_ATR:g} x ATR{ATR_PERIOD} (high-low), "
                f"risk {RISK_PCT}% at the stop, magic {MAGIC}"
                + (f"\n  Next entry: {nxt:%a %d %b %Y %H:%M} UTC" if nxt else ""))