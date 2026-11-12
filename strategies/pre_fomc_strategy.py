# strategies/pre_fomc_strategy.py
"""
Pre-FOMC Drift Strategy — buy 24h before the Fed decision (stateless)
=====================================================================
Runs inside the dip bot process (main_index_dip.py) but is its own class with
its own MAGICs, so it never touches the dip trades or the dip 3-day pause.

On each SCHEDULED Fed decision day T (statement at 14:00 New York):
  1. ENTRY : market BUY at T-24h = 14:00 New York the day before (window
             14:00-14:30 NY, broker time). Long only. No indicator, no pause.
  2. EXIT  : close at 13:59 New York on the decision day, just before the
             statement. If that is missed (bot down / market closed), the
             position is closed at the first poll after it.
  3. SIZE  : 1% of balance per 1-ATR move: lot = balance * 1% / (ATR14(D1) *
             $ per 1.0 move per lot), rounded DOWN; volume_min if smaller.
  4. STOP  : disaster stop at fill - 3 x ATR14 (insurance only: in 43 meetings
             2021-2026 the worst move against the trade was 2.06 ATR, so it
             was never hit). Re-added on the next poll if missing.
New York time is worked out with the US daylight-saving rule in code (no
zoneinfo/tzdata needed on Windows): 14:00 NY = 19:00 UTC in winter, 18:00 UTC
in summer.

BACKTEST (Exness H1, Jul 2021 - Oct 2026, 42 meetings, buy T-24h, sell at T,
entry spread charged, swap not included)
  Index   win    avg/trade   t
  US30    71%    +0.16%      1.9
  USTEC   67%    +0.43%      2.8
  US500   68%    +0.26%      2.5
  Same 24h window on normal days: about 0. D1 2018 - Jun 2021 (23 meetings,
  separate period): day before the Fed +0.18% / +0.57% / +0.28%.
  Sized so 1 ATR = $20: +$410 over 2021-26 for all 3; max DD $94 (Oct 2025 ->
  Jul 2026); worst meeting -$53 combined (29 Jul 2026).
  Source: Lucca & Moench (NY Fed SR 512) pre-FOMC announcement drift.

KNOWN LIMITS: only 8 trades a year; 2026 was the first losing year for US30
and US500 (expect about 1 losing year in 4). USTEC and US500 move almost
together on Fed days (correlation 0.94). Overnight swap (~0.02%) not in the
backtest. Unscheduled (emergency) meetings are NOT traded.
*** Dates must be added each year (FOMC_DATES) — the bot warns when the
list runs out. ***
"""

from __future__ import annotations

import datetime
import math
from typing import Any, Dict, List, Optional

import MetaTrader5 as mt5

from strategies.index_dip_strategy import atr_simple

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# MAGICs in use elsewhere: straddle 20260716, spike 20260807, confirm 20260801,
# reload 20260810, gold scalp 20261003, USTEC_E 20261009, USTEC_A 20261010,
# GOLD_0000 20261011, GOLD_0100 20261012, dip US30 20261028, dip USTEC 20261029,
# USTEC reopen straddle 20261030, dip DE30 20261031, dip UK100 20261032,
# dip JP225 20261033.
SETUPS: List[Dict[str, Any]] = [
    {"name": "FOMC_US30",  "enabled": True, "symbol": "US30m",  "magic": 20261034,
     "risk_pct": 1.0, "comment": "fomc_us30"},
    {"name": "FOMC_USTEC", "enabled": True, "symbol": "USTECm", "magic": 20261035,
     "risk_pct": 1.0, "comment": "fomc_ustec"},
    {"name": "FOMC_US500", "enabled": True, "symbol": "US500m", "magic": 20261036,
     "risk_pct": 1.0, "comment": "fomc_us500"},
]

# Scheduled FOMC DECISION days (statement 14:00 New York). Source: Federal
# Reserve calendar (2027 published 5 Sep 2025, tentative — re-check each year).
FOMC_DATES: List[datetime.date] = [datetime.date.fromisoformat(d) for d in (
    "2026-12-09",
    "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09",
    "2027-07-28", "2027-09-15", "2027-10-27", "2027-12-08",
    "2028-01-26",
)]

DECISION_HOUR_NY = 14           # statement time, New York
ENTRY_WINDOW_MINUTES = 30       # entry allowed 14:00-14:30 NY the day before
EXIT_HOUR_NY, EXIT_MINUTE_NY = 13, 59   # close just before the statement
ATR_PERIOD = 14
DISASTER_SL_ATR = 3.0
STALE_TICK = datetime.timedelta(minutes=10)
ENTRY_DEVIATION = 200           # points
EXIT_DEVIATION = 500            # points — exits must not be refused
DATES_WARN_DAYS = 60            # warn when no FOMC date is left within this many days


def enabled_setups() -> List[Dict[str, Any]]:
    return [s for s in SETUPS if s["enabled"]]


def _ts(seconds: float) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(seconds, tz=datetime.timezone.utc)


# ---------------------------------------------------------------------------
# New York time without zoneinfo (US rule since 2007)
# ---------------------------------------------------------------------------
def _nth_sunday(year: int, month: int, n: int) -> datetime.date:
    d = datetime.date(year, month, 1)
    d += datetime.timedelta(days=(6 - d.weekday()) % 7)      # first Sunday
    return d + datetime.timedelta(weeks=n - 1)


def ny_to_utc(day: datetime.date, hour: int, minute: int = 0) -> datetime.datetime:
    """New York wall-clock time -> UTC. DST from 2nd Sunday of March 02:00 to
    1st Sunday of November 02:00 (UTC-4), else UTC-5."""
    local = datetime.datetime(day.year, day.month, day.day, hour, minute)
    start = datetime.datetime.combine(_nth_sunday(day.year, 3, 2), datetime.time(2))
    end = datetime.datetime.combine(_nth_sunday(day.year, 11, 1), datetime.time(2))
    offset = 4 if start <= local < end else 5
    return (local + datetime.timedelta(hours=offset)).replace(tzinfo=datetime.timezone.utc)


def decision_time(d: datetime.date) -> datetime.datetime:
    return ny_to_utc(d, DECISION_HOUR_NY)


def entry_time(d: datetime.date) -> datetime.datetime:
    """T-24h: 14:00 New York the calendar day before the decision."""
    return ny_to_utc(d - datetime.timedelta(days=1), DECISION_HOUR_NY)


def exit_time(d: datetime.date) -> datetime.datetime:
    return ny_to_utc(d, EXIT_HOUR_NY, EXIT_MINUTE_NY)


def meeting_for_entry(now: datetime.datetime) -> Optional[datetime.date]:
    """The meeting whose entry window contains `now`, else None."""
    for d in FOMC_DATES:
        a = entry_time(d)
        if a <= now < a + datetime.timedelta(minutes=ENTRY_WINDOW_MINUTES):
            return d
    return None


def meeting_for_position(open_time: datetime.datetime) -> Optional[datetime.date]:
    """The first decision after the position was opened."""
    for d in FOMC_DATES:
        if decision_time(d) > open_time:
            return d
    return None


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
class PreFomcStrategy:
    """No state between calls — everything is read from MT5 every time."""

    def __init__(self, fallback_balance: float = 200.0) -> None:
        self.fallback_balance = fallback_balance
        self.setups = enabled_setups()
        for s in self.setups:
            if not self._ensure_symbol(s["symbol"]):
                print(f"  WARNING: {s['name']}: symbol '{s['symbol']}' NOT FOUND on this server "
                      f"— check the name in Market Watch. It will be skipped until it exists.")

    # ------------------------------------------------------------ MT5 helpers
    def _ensure_symbol(self, symbol: str) -> bool:
        info = mt5.symbol_info(symbol)
        if info is not None and getattr(info, "visible", True):
            return True
        return bool(mt5.symbol_select(symbol, True))

    def _tick(self, symbol: str):
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            mt5.symbol_select(symbol, True)
            tick = mt5.symbol_info_tick(symbol)
        return tick

    def _broker_now(self, symbol: str, fallback: datetime.datetime) -> datetime.datetime:
        """Broker time from the symbol's last tick (as the dip bot does)."""
        tick = self._tick(symbol)
        if tick is not None and tick.time_msc:
            t = _ts(tick.time_msc / 1000)
            # a quiet/closed market has an old tick: never let time run backwards
            return max(t, fallback) if abs((t - fallback).total_seconds()) < 600 else fallback
        return fallback

    def _filling(self, symbol: str) -> int:
        """filling_mode bitmask: 1 = FOK, 2 = IOC (no SYMBOL_FILLING_* names in
        the Python package — mt5.SYMBOL_FILLING_IOC crashed live)."""
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

    def _entered_since(self, s: Dict[str, Any], start: datetime.datetime,
                       now: datetime.datetime) -> Optional[bool]:
        """True if this setup has an entry deal since `start`. None = history
        read failed (caller waits). No group= filter (2026-09-15 fix)."""
        deals = mt5.history_deals_get(start, now + datetime.timedelta(minutes=1))
        if deals is None:
            return None
        return any(d.magic == s["magic"] and d.symbol == s["symbol"]
                   and d.entry == mt5.DEAL_ENTRY_IN for d in deals)

    def _fresh_tick(self, s: Dict[str, Any], now: datetime.datetime):
        tick = self._tick(s["symbol"])
        if tick is None:
            return None, "No tick data"
        age = now - _ts(tick.time)
        if age > STALE_TICK:
            return None, f"Market likely closed — last tick {age} old"
        return tick, ""

    def _atr(self, symbol: str, now: datetime.datetime) -> Optional[float]:
        d1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 1, ATR_PERIOD + 1)  # completed days
        if d1 is None or len(d1) == 0:
            return None
        if now - _ts(int(d1[-1]["time"])) > datetime.timedelta(days=5):
            return None                                   # stale D1 history
        return atr_simple(d1, ATR_PERIOD)

    def _lot(self, s: Dict[str, Any], atr: float):
        """balance * risk% / (ATR14 * $ per 1.0 move per lot), rounded DOWN to
        volume_step; volume_min if below. Returns (lot, $ at 1 ATR, forced)."""
        info = mt5.symbol_info(s["symbol"])
        if info is None or not info.trade_tick_size:
            return 0.0, 0.0, False
        value_per_unit_per_lot = info.trade_tick_value / info.trade_tick_size
        acc = mt5.account_info()
        balance = acc.balance if acc else self.fallback_balance
        raw = balance * s["risk_pct"] / 100.0 / (atr * value_per_unit_per_lot)
        vmin = info.volume_min or 0.01
        vstep = info.volume_step or 0.01
        vmax = info.volume_max or raw
        lot = math.floor(raw / vstep + 1e-9) * vstep
        forced = lot < vmin
        lot = round(min(vmax, max(vmin, lot)), 2)
        return lot, lot * atr * value_per_unit_per_lot, forced

    def _set_disaster_sl(self, s: Dict[str, Any], pos, now: datetime.datetime,
                         atr: Optional[float] = None) -> str:
        atr = atr or self._atr(s["symbol"], now)
        info = mt5.symbol_info(s["symbol"])
        if not atr or info is None:
            return "Disaster SL: ATR/symbol info unavailable — will retry"
        sl = round(pos.price_open - DISASTER_SL_ATR * atr, info.digits)
        ok = self._ok(self._send({
            "action": mt5.TRADE_ACTION_SLTP, "symbol": s["symbol"], "position": pos.ticket,
            "sl": sl, "tp": 0.0,
        }, f"{s['name']} disaster SL ticket {pos.ticket}"))
        return (f"Stop loss set at {sl:,.{info.digits}f} ({DISASTER_SL_ATR} x ATR {atr:,.1f})"
                if ok else "Disaster SL modify FAILED — will retry next poll")

    def _close(self, s: Dict[str, Any], pos, tick) -> bool:
        return self._ok(self._send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": s["symbol"], "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL, "position": pos.ticket, "price": tick.bid,
            "deviation": EXIT_DEVIATION, "magic": s["magic"],
            "comment": f"{s['comment']}_exit",
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(s["symbol"]),
        }, f"{s['name']} close ticket {pos.ticket}"))

    # ------------------------------------------------------------ management
    def manage(self, s: Dict[str, Any], now: datetime.datetime) -> List[str]:
        """Every poll, BEFORE check_and_place: close positions whose exit time
        (13:59 NY on their decision day) has come; add a missing stop."""
        now = self._broker_now(s["symbol"], now)
        msgs: List[str] = []
        for pos in self._positions(s):
            d = meeting_for_position(_ts(pos.time))
            due = exit_time(d) if d else _ts(pos.time) + datetime.timedelta(hours=24)
            if now < due:
                if DISASTER_SL_ATR > 0 and not pos.sl:
                    msgs.append(f"{s['name']}: " + self._set_disaster_sl(s, pos, now))
                continue
            tick, why = self._fresh_tick(s, now)
            if tick is None:
                msgs.append(f"{s['name']}: exit due for ticket {pos.ticket} but {why} — will retry")
                continue
            ok = self._close(s, pos, tick)
            msgs.append(f"{s['name']}: EXIT ticket {pos.ticket} {pos.volume} lots, open "
                        f"{pos.price_open} -> ~{tick.bid}, P&L ~{pos.profit + pos.swap:+.2f} "
                        f"(Fed decision {d}): {'ok' if ok else '!! FAILED'}")
        return msgs

    # ------------------------------------------------------------ entry
    def check_and_place(self, s: Dict[str, Any], now: datetime.datetime) -> str:
        sym = s["symbol"]
        now = self._broker_now(sym, now)
        d = meeting_for_entry(now)
        if d is None:
            return "Outside entry window"
        head = f"[{s['name']}] {sym}  Fed decision {d:%a %d %b %Y} 14:00 NY — entry window"
        if not self._ensure_symbol(sym):
            return f"{head}\n   => symbol not found on this server — check Market Watch"
        if self._positions(s):
            return f"{head}\n   => NO TRADE — position already open"
        done = self._entered_since(s, entry_time(d), now)
        if done is None:
            return f"{head}\n   => WAIT — deal history unavailable — will retry"
        if done:
            return f"{head}\n   => NO TRADE — already entered for this meeting"
        tick, why = self._fresh_tick(s, now)
        if tick is None:
            return f"{head}\n   => WAIT — {why}"
        atr = self._atr(sym, now)
        if not atr:
            return f"{head}\n   => WAIT — ATR / D1 history unavailable — will retry"
        lot, risk_usd, forced = self._lot(s, atr)
        if lot <= 0:
            return f"{head}\n   => NO TRADE — lot size failed"
        res = self._send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": lot,
            "type": mt5.ORDER_TYPE_BUY, "price": tick.ask, "sl": 0.0, "tp": 0.0,
            "deviation": ENTRY_DEVIATION, "magic": s["magic"],
            "comment": f"{s['comment']}_entry",
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(sym),
        }, f"{s['name']} BUY")
        if not self._ok(res):
            return f"{head}\n   => BUY REJECTED — see the line above"
        pos = next((p for p in self._positions(s) if p.ticket == res.order), None)
        sl_msg = self._set_disaster_sl(s, pos, now, atr) if pos is not None \
            else "Disaster SL: position not visible yet — manage() will add it"
        note = "  [MIN LOT — risk above target]" if forced else ""
        return (f"{head}\n   => BUY {lot} lots @ {res.price:,.2f}{note}\n"
                f"   ATR {atr:,.1f}, 1-ATR risk ${risk_usd:,.2f}; exit {exit_time(d):%a %d %b %H:%M} UTC\n"
                f"   {sl_msg}")

    # ------------------------------------------------------------ info
    def next_meeting(self, now: datetime.datetime) -> Optional[datetime.date]:
        return next((d for d in FOMC_DATES if decision_time(d) > now), None)

    def dates_warning(self, now: datetime.datetime) -> Optional[str]:
        nxt = self.next_meeting(now)
        if nxt is None or (nxt - now.date()).days > DATES_WARN_DAYS:
            if FOMC_DATES and FOMC_DATES[-1] < (now + datetime.timedelta(days=DATES_WARN_DAYS)).date():
                return ("WARNING: Pre-FOMC date list runs out soon — add next year's Fed "
                        "decision dates to FOMC_DATES in pre_fomc_strategy.py")
        return None

    def __repr__(self) -> str:
        now = datetime.datetime.now(datetime.timezone.utc)
        nxt = self.next_meeting(now)
        lines = ["PreFomcStrategy:"]
        for s in enabled_setups():
            lines.append(f"  [ON ] {s['name']}: {s['symbol']} BUY 14:00 NY the day before each Fed "
                         f"decision, close 13:59 NY decision day, risk {s['risk_pct']}% / ATR{ATR_PERIOD}, "
                         f"SL {DISASTER_SL_ATR} x ATR, magic {s['magic']}")
        if nxt:
            lines.append(f"  Next: decision {nxt:%a %d %b %Y} -> buy {entry_time(nxt):%a %d %b %H:%M} UTC, "
                         f"sell {exit_time(nxt):%a %d %b %H:%M} UTC")
        return "\n".join(lines)