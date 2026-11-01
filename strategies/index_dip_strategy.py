"""
Index Dip Strategy — RSI(2) dip buy at 19:00 UTC (stateless)
============================================================
One class, IndexDipStrategy, runs every index in SETUPS. Each index has its
own MAGIC, so they never touch each other's positions, and each has its own
monthly breaker.

Once a day, Monday-Friday, at 19:00 UTC (20:00 Lagos):
  1. EXIT  : any position this setup opened at an earlier check is closed at
             market (held ~1 day; a Friday trade closes Monday 19:00).
  2. BREAKER: if this index had a LOSING trade entered this calendar month,
             it takes no more trades until the 1st (per index, independent).
  3. SIGNAL: build "daily" closes from the H1 bar that ends at 19:00 UTC on
             each weekday; RSI(2) of those closes < 5  ->  BUY. Decided from
             19:01, never at 19:00:00, and only on history that passes checks
             (warmed up 18:50-18:59, two identical reads, the 19:00 bar has
             started, no big gaps, fresh D1) — else "will retry" next poll.
  4. SIZE  : lot = balance * risk% / (ATR14(D1) * $ per 1.0 move per lot),
             rounded DOWN to volume_step; if below the symbol's volume_min
             (read live from MT5), the minimum lot is used and flagged.
  5. ORDER : market BUY, no TP. Long only. A DISASTER stop is set at
             fill price - 3 x ATR14 (insurance only — see below).
  6. HEAL  : any own position without an SL gets the disaster stop added
             on the next poll (covers a failed modify / restart).

DISASTER STOP (2026-10-28, H1 Jul 2021 - Oct 2026, 4 indices, 168 trades):
  SL        stops hit   total R   worst R
  none          0        +38.1     -2.7
  1.0 ATR      32        +27.8     -2.0     <- tight stops cut dips that recover
  2.0 ATR       6        +32.9     -2.0
  3.0 ATR       1        +36.4     -3.0     <- chosen: cheap flash-crash insurance
  Gaps (overnight / weekend) fill at the open, below the stop — it caps an
  intraday collapse, not a gap.

BACKTEST (2026-10-28, Exness H1, Jul 2021 - Oct 2026, entry/exit 19:00 UTC,
one-loss-per-month breaker, % of position per trade, spread + swap included)
  Index    trades  win%   avg/trade  total   max DD   years +
  US30m      45     60     +0.37%    +16.5%   -3.3%    5/6
  USTECm     46     63     +0.43%    +19.6%   -6.6%    5/6
  JP225m     41     59     +0.28%    +11.5%   -5.8%    6/6
  DE30m      36     64     +0.30%    +10.9%   -3.4%    5/6
  Cross-check (D1 closes, 10 indices, 2018-2026, no breaker): positive on
  10/10, pooled t 2.6. Selling the same dips lost on 10/10.
  UK100m: no edge at any real trading hour (only at the midnight close) ->
  kept here DISABLED.

Why it works: sharp index drops are partly forced selling (margin calls,
vol-targeting funds, dealer hedging) that over-shoots and is bought back
within a day. The breaker steps aside when a dip turns into a slide.

KNOWN LIMITS: only a wide disaster stop — a bad day can cost up to ~3x the
1% "risk" (risk here = one average day, ATR14, against you), more on a gap. Profit leans on a few big
rebound days. Below ~$3,000 balance most trades sit at volume_min, so the
real risk per trade is above 1%. *** DEMO FIRST ***
"""

from __future__ import annotations

import datetime
import math
from typing import Any, Dict, List, Optional

import MetaTrader5 as mt5

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# MAGICs in use elsewhere: straddle 20260716, spike 20260807, confirm 20260801,
# reload 20260810, gold scalp 20261003, USTEC_E 20261009, USTEC_A 20261010,
# GOLD_0000 20261011, GOLD_0100 20261012, USTEC reopen straddle 20261030.
SETUPS: List[Dict[str, Any]] = [
    {"name": "DIP_US30",  "enabled": True,  "symbol": "US30m",  "magic": 20261028,
     "risk_pct": 1.0, "comment": "dip_us30"},
    {"name": "DIP_USTEC", "enabled": True,  "symbol": "USTECm", "magic": 20261029,
     "risk_pct": 1.0, "comment": "dip_ustec"},
    # magic moved off 20261030 (also used by the USTEC reopen straddle). Switch only
    # while no JP225 dip position is open, or the old position won't be managed.
    {"name": "DIP_JP225", "enabled": True,  "symbol": "JP225m", "magic": 20261033,
     "risk_pct": 1.0, "comment": "dip_jp225"},
    {"name": "DIP_DE30",  "enabled": True,  "symbol": "DE30m",  "magic": 20261031,
     "risk_pct": 1.0, "comment": "dip_de30"},
    # Failed the 19:00 re-test (edge only at the midnight close). Off.
    {"name": "DIP_UK100", "enabled": False, "symbol": "UK100m", "magic": 20261032,
     "risk_pct": 1.0, "comment": "dip_uk100"},
]

CHECK_HOUR = 19                 # UTC. 18:00-20:00 all tested fine; 19:00 is the middle
CHECK_MINUTE = 0
CHECK_WINDOW_MINUTES = 10       # entries only in 19:01-19:10 (covers a missed poll)
ENTRY_DELAY_MINUTES = 1         # broker time; never decide at 19:00:00 — the 18:00 H1 bar may not be final
                                # and history may still be syncing (29 Oct: US30 RSI read 85.58
                                # at 19:00:00, 18.21 at 19:01 on the SAME close)
PRIME_MINUTES = 10              # 18:50-18:59: fetch H1/D1 every poll so MT5 syncs history first
MAX_MISSING_DAYS = 1            # of the broker's last 20 TRADING days (from D1) without an 18:00 bar
                                # (allows one early-close day; holidays have no D1 bar so don't count)
MIN_SERIES_DAYS = 30            # RSI(2) warm-up is ~10 days; 30 is ample
RSI_PERIOD = 2
RSI_THRESHOLD = 5.0             # buy when RSI(2) < 5
ATR_PERIOD = 14                 # D1, simple mean of true range (as backtested)
HISTORY_DAYS = 120              # H1 history pulled for the 19:00 closes (RSI warm-up)
STALE_TICK = datetime.timedelta(minutes=10)
ENTRY_DEVIATION = 200           # points
EXIT_DEVIATION = 500            # points — exits must not be refused
BREAKER_LOOKBACK_DAYS = 10      # deals fetched from before the 1st (cross-month positions)
DISASTER_SL_ATR = 3.0           # stop at fill - 3 x ATR14; 0 = no stop at all
PRICE_CHECK_PCT = 0.3           # 18:00-bar close must be within 0.3% of the 19:00 open
                                # (and 0.9% of the live bid) or the read is treated as bad


def enabled_setups() -> List[Dict[str, Any]]:
    return [s for s in SETUPS if s["enabled"]]


def _ts(seconds: int) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(seconds, tz=datetime.timezone.utc)


# ---------------------------------------------------------------------------
# Clock: reason in broker time, not the VPS clock (VPS was seen 64s off once)
# ---------------------------------------------------------------------------
_skew: Dict[str, Any] = {"value": datetime.timedelta(0), "checked": None}
SKEW_MIN_SECONDS = 30   # a tick's time is when the LAST trade happened, not "now": on quiet
                        # indices it is often 5-15 s old (29 Oct log). Only a gap this big
                        # is treated as a real VPS clock error (the one seen was 64 s).


def _utc_now() -> datetime.datetime:
    local = datetime.datetime.now(datetime.timezone.utc)
    last = _skew["checked"]
    if last is None or (local - last).total_seconds() >= 60:
        # freshest tick across all symbols = best estimate of broker "now"
        newest = None
        for sym in dict.fromkeys(s["symbol"] for s in enabled_setups()):
            tick = mt5.symbol_info_tick(sym)
            if tick is not None and tick.time_msc:
                t = datetime.datetime.fromtimestamp(tick.time_msc / 1000, tz=datetime.timezone.utc)
                newest = t if newest is None or t > newest else newest
        new_skew = datetime.timedelta(0)
        if newest is not None:
            skew = newest - local
            if SKEW_MIN_SECONDS <= abs(skew.total_seconds()) < 600:
                new_skew = skew
        if abs((new_skew - _skew["value"]).total_seconds()) >= 1:
            print(f"  WARNING: VPS clock {new_skew.total_seconds():+.1f}s off broker — correcting"
                  if new_skew else "  VPS clock back in line with broker — correction removed")
        _skew["value"] = new_skew
        _skew["checked"] = local
    return local + _skew["value"]


def last_check_at_or_before(t: datetime.datetime) -> datetime.datetime:
    """Most recent WEEKDAY 19:00 UTC at or before t. Saturday/Sunday and
    Monday before 19:00 map to Friday 19:00 — so a Friday trade is not
    closed in the thin Sunday-night reopen."""
    a = t.replace(hour=CHECK_HOUR, minute=CHECK_MINUTE, second=0, microsecond=0)
    if a > t:
        a -= datetime.timedelta(days=1)
    while a.weekday() >= 5:
        a -= datetime.timedelta(days=1)
    return a


# ---------------------------------------------------------------------------
# Pure helpers (no MT5) — same maths as the backtest
# ---------------------------------------------------------------------------
def closes_at_check_hour(h1_bars, now: datetime.datetime) -> List[tuple]:
    """[(date, close)] using the H1 bar that ENDS at 19:00 (opens 18:00) on
    each weekday. Only fully closed bars (end <= now). One value per date."""
    out: Dict[datetime.date, float] = {}
    for b in h1_bars:
        end = _ts(int(b["time"])) + datetime.timedelta(hours=1)
        if end > now or end.weekday() >= 5:
            continue
        if end.hour == CHECK_HOUR and end.minute == CHECK_MINUTE:
            out.setdefault(end.date(), float(b["close"]))
    return sorted(out.items())


def rsi_last(closes: List[float], period: int = RSI_PERIOD) -> Optional[float]:
    """Wilder-style RSI (EWM alpha=1/period, adjust=False) — identical to the
    pandas formula used in the backtest. Returns the latest value."""
    if len(closes) < period + 10:
        return None
    a = 1.0 / period
    up = dn = None
    for prev, cur in zip(closes[:-1], closes[1:]):
        d = cur - prev
        u, l = max(d, 0.0), max(-d, 0.0)
        up = u if up is None else (1 - a) * up + a * u
        dn = l if dn is None else (1 - a) * dn + a * l
    if dn == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + up / dn)


def atr_simple(d1_bars, period: int = ATR_PERIOD) -> Optional[float]:
    """Simple mean of the last `period` true ranges from completed D1 bars."""
    if d1_bars is None or len(d1_bars) < period + 1:
        return None
    trs = []
    for prev, cur in zip(d1_bars[:-1], d1_bars[1:]):
        pc = float(prev["close"])
        trs.append(max(float(cur["high"]) - float(cur["low"]),
                       abs(float(cur["high"]) - pc), abs(float(cur["low"]) - pc)))
    return sum(trs[-period:]) / period


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
class IndexDipStrategy:
    """No state between calls — everything is read from MT5 every time.
    Every method takes the setup dict it is working on."""

    def __init__(self, fallback_balance: float = 200.0) -> None:
        self.fallback_balance = fallback_balance
        self.setups = enabled_setups()
        # Put every symbol in Market Watch up front and say so loudly if a
        # name doesn't exist on this server (demo/live names can differ).
        for s in self.setups:
            if not self._ensure_symbol(s["symbol"]):
                print(f"  WARNING: {s['name']}: symbol '{s['symbol']}' NOT FOUND on this server "
                      f"— check the name in Market Watch. It will be skipped until it exists.")

    # ------------------------------------------------------------ MT5 helpers
    def _ensure_symbol(self, symbol: str) -> bool:
        """copy_rates_*() / symbol_info_tick() can come back empty for a symbol
        that isn't in Market Watch (2026-07-21 GBPUSDm miss). symbol_select()
        adds it. False = the symbol doesn't exist on this server at all."""
        info = mt5.symbol_info(symbol)
        if info is not None and getattr(info, "visible", True):
            return True
        return bool(mt5.symbol_select(symbol, True))

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

    def _own_deals(self, s: Dict[str, Any], start: datetime.datetime,
                   now: datetime.datetime) -> List[Any]:
        # No group= filter: it returned nothing on this account (2026-09-15 fix).
        deals = mt5.history_deals_get(start, now + datetime.timedelta(minutes=1)) or ()
        return [d for d in deals if d.magic == s["magic"] and d.symbol == s["symbol"]]

    def _fresh_tick(self, s: Dict[str, Any], now: datetime.datetime):
        tick = self._tick(s["symbol"])
        if tick is None:
            return None, "No tick data"
        age = now - _ts(tick.time)
        if age > STALE_TICK:
            return None, f"Market likely closed — last tick {age} old"
        return tick, ""

    # ------------------------------------------------------------ breaker
    def _lost_this_month(self, s: Dict[str, Any], now: datetime.datetime) -> Optional[str]:
        """A closed position ENTERED this calendar month with net P&L <= 0
        (profit + swap + commission + fee over all its deals) blocks the
        index until the 1st. Read from deal history every call — nothing stored."""
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        deals = self._own_deals(s, month_start - datetime.timedelta(days=BREAKER_LOOKBACK_DAYS), now)
        by_pos: Dict[int, Dict[str, Any]] = {}
        for d in deals:
            p = by_pos.setdefault(d.position_id, {"entry": None, "closed": False, "pnl": 0.0})
            p["pnl"] += d.profit + d.swap + d.commission + getattr(d, "fee", 0.0)
            if d.entry == mt5.DEAL_ENTRY_IN:
                t = _ts(d.time)
                p["entry"] = t if p["entry"] is None else min(p["entry"], t)
            elif d.entry == mt5.DEAL_ENTRY_OUT:
                p["closed"] = True
        for pid, p in by_pos.items():
            if p["closed"] and p["entry"] is not None and p["entry"] >= month_start and p["pnl"] <= 0:
                return f"position {pid} entered {p['entry']:%d %b} lost {p['pnl']:+.2f}"
        return None

    # ------------------------------------------------------------ sizing
    def _lot(self, s: Dict[str, Any], atr: float):
        """balance * risk% / (ATR14 * $ per 1.0 move per lot), from the symbol's
        real tick value / tick size, rounded DOWN to volume_step (as backtested).
        If that is below the symbol's own volume_min (read live), use volume_min.
        Returns (lot, risk_$_at_1_ATR, forced_to_min)."""
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

    # ------------------------------------------------------------ exit
    def _close_position(self, s: Dict[str, Any], pos, tick, comment: str) -> bool:
        res = self._send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": s["symbol"], "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL, "position": pos.ticket, "price": tick.bid,
            "deviation": EXIT_DEVIATION, "magic": s["magic"],
            "comment": f"{s['comment']}_{comment}",
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(s["symbol"]),
        }, f"{s['name']} close ticket {pos.ticket}")
        return self._ok(res)

    def _atr(self, symbol: str, now: Optional[datetime.datetime] = None) -> Optional[float]:
        d1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 1, ATR_PERIOD + 1)  # completed days
        if d1 is None or len(d1) == 0:
            return None
        # stale-history guard: the last COMPLETED day must be recent
        if now is not None and now - _ts(int(d1[-1]["time"])) > datetime.timedelta(days=5):
            return None
        return atr_simple(d1)

    def _h1(self, symbol: str, now: datetime.datetime):
        return mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H1,
                                    now - datetime.timedelta(days=HISTORY_DAYS), now)

    def prime(self, s: Dict[str, Any], now: Optional[datetime.datetime] = None) -> None:
        """18:50-18:59 UTC: request the same history the 19:00 check will use, so
        MT5 has synced it before the decision (the first request after hours of
        no charts can return a partial/stale set and start a download)."""
        now = now or _utc_now()
        check_at = last_check_at_or_before(now + datetime.timedelta(minutes=PRIME_MINUTES))
        if not (check_at - datetime.timedelta(minutes=PRIME_MINUTES) <= now < check_at):
            return
        if self._ensure_symbol(s["symbol"]):
            self._h1(s["symbol"], now)
            mt5.copy_rates_from_pos(s["symbol"], mt5.TIMEFRAME_D1, 1, ATR_PERIOD + 1)

    def _set_disaster_sl(self, s: Dict[str, Any], pos, atr: Optional[float] = None) -> str:
        """SL = fill price - DISASTER_SL_ATR x ATR14, anchored to the REAL fill
        (pos.price_open), rounded to the symbol's digits. TP stays empty."""
        atr = atr or self._atr(s["symbol"])
        info = mt5.symbol_info(s["symbol"])
        if not atr or info is None:
            return "Disaster SL: ATR/symbol info unavailable — will retry"
        sl = round(pos.price_open - DISASTER_SL_ATR * atr, info.digits)
        ok = self._ok(self._send({
            "action": mt5.TRADE_ACTION_SLTP, "symbol": s["symbol"], "position": pos.ticket,
            "sl": sl, "tp": 0.0,
        }, f"{s['name']} disaster SL ticket {pos.ticket}"))
        return (f"Disaster SL set at {sl:,.{info.digits}f} ({DISASTER_SL_ATR} x ATR {atr:,.1f})"
                if ok else "Disaster SL modify FAILED — will retry next poll")

    def manage(self, s: Dict[str, Any], now: Optional[datetime.datetime] = None) -> List[str]:
        """Call every poll, for every setup, BEFORE check_and_place.
        Closes any position opened before the latest weekday 19:00 check
        (held one trading day). If the market is closed, retries next poll.
        Adds the disaster stop to any own position that has none (self-heal).
        Uses BROKER time (last tick), not the VPS clock."""
        now = self._broker_now(s["symbol"])   # broker time; passed-in `now` ignored
        msgs: List[str] = []
        positions = self._positions(s)
        if not positions:
            return msgs
        if not self._ensure_symbol(s["symbol"]):
            return [f"Symbol '{s['symbol']}' not found on this server — cannot manage "
                    f"{len(positions)} open position(s)"]
        due = last_check_at_or_before(now)
        for pos in positions:
            if _ts(pos.time) >= due:
                if DISASTER_SL_ATR > 0 and not pos.sl:
                    msgs.append(self._set_disaster_sl(s, pos))
                continue
            tick, why = self._fresh_tick(s, now)
            if tick is None:
                msgs.append(f"Exit due for ticket {pos.ticket} but {why} — will retry")
                continue
            pnl = pos.profit + pos.swap
            ok = self._close_position(s, pos, tick, "exit")
            msgs.append(f"EXIT ticket {pos.ticket} {pos.volume} lots, open {pos.price_open} -> "
                        f"~{tick.bid}, P&L ~{pnl:+.2f}: {'ok' if ok else '!! FAILED'}")
        return msgs

    # ------------------------------------------------------------ entry
    def _broker_now(self, symbol: str) -> datetime.datetime:
        """Broker time from the symbol's last tick — the VPS clock is never used for
        the decision (29 Oct 2026: a BUY was sent at broker 19:00:03 on a bad read)."""
        tick = self._tick(symbol)
        if tick is not None and tick.time_msc:
            return datetime.datetime.fromtimestamp(tick.time_msc / 1000, tz=datetime.timezone.utc)
        return _utc_now()

    def signal_for(self, s: Dict[str, Any], check_at: datetime.datetime) -> Dict[str, Any]:
        """Look BACK at finished bars only: the 19:00 price of each weekday is the
        close of the H1 bar that opened at 18:00. Same answer whenever it is run
        after 19:00. Returns a dict; 'ok' False means history not trustworthy yet."""
        sym = s["symbol"]
        out: Dict[str, Any] = {"ok": False, "why": "", "check_at": check_at}
        start = check_at - datetime.timedelta(days=HISTORY_DAYS)
        h1 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H1, start, check_at)
        h1b = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H1, start, check_at)
        if h1 is None or len(h1) == 0 or h1b is None or len(h1b) != len(h1) \
                or int(h1[-1]["time"]) != int(h1b[-1]["time"]):
            out["why"] = "H1 history not ready (empty or two reads differ)"
            return out
        series = closes_at_check_hour(h1, check_at)
        if not series or series[-1][0] != check_at.date():
            out["why"] = f"no finished 18:00 bar for {check_at:%a %d %b} in history yet"
            return out
        # every weekday the broker traded (D1 bar exists) in the last 20 must be present
        d1 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_D1, check_at - datetime.timedelta(days=40), check_at)
        traded = sorted({_ts(int(b["time"])).date() for b in (d1 if d1 is not None else [])
                         if _ts(int(b["time"])).weekday() < 5 and _ts(int(b["time"])).date() < check_at.date()})[-20:]
        have = {d for d, _ in series}
        missing = [d for d in traded if d not in have]
        if len(traded) < 15 or len(missing) > MAX_MISSING_DAYS:
            out["why"] = (f"history incomplete: {len(missing)} trading day(s) without an 18:00 bar "
                          f"({', '.join(f'{d:%d %b}' for d in missing[:5])})")
            return out
        closes = [c for _, c in series]
        rsi = rsi_last(closes)
        if rsi is None:
            out["why"] = f"not enough history for RSI ({len(series)} days)"
            return out
        out.update(ok=True, rsi=rsi, close_19=closes[-1], last5=series[-5:], days=len(series))
        return out

    @staticmethod
    def _report(s: Dict[str, Any], sig: Dict[str, Any], verdict: str, extra: str = "") -> str:
        lines = [f"[{s['name']}] {s['symbol']}  check {sig['check_at']:%a %d %b %Y 19:00} UTC (broker)"]
        if sig.get("ok"):
            lines.append("   19:00 prices: " + " | ".join(f"{d:%a %d %b} {c:,.2f}" for d, c in sig["last5"]))
            lines.append(f"   RSI(2) = {sig['rsi']:.2f}   (buy only if < {RSI_THRESHOLD:g})   history {sig['days']} days")
        lines.append(f"   => {verdict}")
        if extra:
            lines.append("   " + extra)
        return "\n".join(lines)

    def check_and_place(self, s: Dict[str, Any],
                        now: Optional[datetime.datetime] = None) -> str:
        sym = s["symbol"]
        if not self._ensure_symbol(sym):
            return f"[{s['name']}] symbol '{sym}' not found on this server — check Market Watch"
        bnow = self._broker_now(sym)   # always BROKER time; a `now` passed in by the runner is ignored
        check_at = last_check_at_or_before(bnow)
        if not (check_at + datetime.timedelta(minutes=ENTRY_DELAY_MINUTES) <= bnow
                < check_at + datetime.timedelta(minutes=CHECK_WINDOW_MINUTES)):
            return "Outside check window"
        sig: Dict[str, Any] = {"check_at": check_at}
        if self._positions(s):
            return self._report(s, sig, "NO TRADE — position still open (exit pending)")
        if any(d.entry == mt5.DEAL_ENTRY_IN for d in self._own_deals(s, check_at, bnow)):
            return self._report(s, sig, "NO TRADE — already traded at this check")
        blocked = self._lost_this_month(s, bnow)
        if blocked:
            return self._report(s, sig, f"NO TRADE — BREAKER: {blocked}; off until the 1st")
        tick, why = self._fresh_tick(s, bnow)
        if tick is None:
            return self._report(s, sig, f"WAIT — {why}")

        sig = self.signal_for(s, check_at)
        if not sig["ok"]:
            return self._report(s, sig, f"WAIT — {sig['why']} — will retry")
        close_19, rsi = sig["close_19"], sig["rsi"]
        # history must agree with the live market (stale/half-synced data fails this)
        cur = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 1)
        live_open = float(cur[0]["open"]) if cur is not None and len(cur) else tick.bid
        tol = max(close_19, tick.bid) * PRICE_CHECK_PCT / 100.0
        if abs(close_19 - live_open) > tol or abs(close_19 - tick.bid) > 3 * tol:
            return self._report(s, sig, "WAIT — history does not match live price — will retry",
                                f"19:00 close {close_19:,.2f} vs 19:00 open {live_open:,.2f} / bid {tick.bid:,.2f}")
        if rsi >= RSI_THRESHOLD:
            return self._report(s, sig, "NO TRADE")
        now = bnow

        # --- size
        atr = self._atr(sym, now)
        if not atr:
            return self._report(s, sig, "WAIT — SIGNAL, but ATR / D1 history unavailable — will retry")
        lot, risk_usd, forced = self._lot(s, atr)
        if lot <= 0:
            return self._report(s, sig, "NO TRADE — SIGNAL, but lot size failed")

        # --- order: market BUY, no TP. The disaster SL is added right after
        # the fill, anchored to the real fill price (manage() re-adds it if
        # this modify fails or the bot restarts).
        res = self._send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": lot,
            "type": mt5.ORDER_TYPE_BUY, "price": tick.ask, "sl": 0.0, "tp": 0.0,
            "deviation": ENTRY_DEVIATION, "magic": s["magic"],
            "comment": f"{s['comment']}_entry",
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(sym),
        }, f"{s['name']} BUY")
        if not self._ok(res):
            return self._report(s, sig, "SIGNAL, but BUY was REJECTED — see the line above")
        note = "  [MIN LOT — risk above target]" if forced else ""
        sl_msg = ""
        if DISASTER_SL_ATR > 0:
            pos = next((p for p in self._positions(s) if p.ticket == res.order), None) \
                or (self._positions(s) or [None])[-1]
            sl_msg = ("\n   " + self._set_disaster_sl(s, pos, atr)) if pos is not None \
                else "\n   Disaster SL: position not visible yet — manage() will add it"
        return self._report(s, sig, f"BUY {lot} lots @ {res.price:,.2f}{note}",
                            f"ATR {atr:,.1f}, 1-ATR risk ${risk_usd:,.2f}{sl_msg}")

    def __repr__(self) -> str:
        lines = ["IndexDipStrategy:"]
        for s in SETUPS:
            lines.append(
                f"  [{'ON ' if s['enabled'] else 'OFF'}] {s['name']}: {s['symbol']} "
                f"check {CHECK_HOUR:02d}:{CHECK_MINUTE:02d} UTC Mon-Fri, BUY if RSI({RSI_PERIOD}) "
                f"< {RSI_THRESHOLD}, hold 1 day, no TP, disaster SL {DISASTER_SL_ATR} x ATR, "
                f"risk {s['risk_pct']}% / ATR{ATR_PERIOD}, "
                f"1 loss/month breaker, magic {s['magic']}")
        return "\n".join(lines)