# strategies/news_spike_strategy.py
"""
News Spike Strategy — gold straddle at NFP / CPI / FOMC (stateless)
===================================================================
Entry  : 2 seconds before each scheduled release (NFP + CPI 08:30 New York,
         FOMC decision 14:00 New York), mid = (bid+ask)/2:
         buy-stop at mid + $3, sell-stop at mid - $3, GTC. No OCO: if price
         reaches both sides, both fill (backtested: 12 of 49 events, net
         -$6.35 per unit across those 12 — accepted).
SL     : $7 from the stop level; re-anchored to the REAL fill price the
         first poll after a fill (entry slippage correction).
Exit   : release + 60 s: every own position is closed at market and every
         own pending order cancelled. Nothing re-enters for that event.
         Stateless: at any poll OUTSIDE [release - 2 s, release + 60 s) the
         bot closes/cancels whatever of its own is still there — so a missed
         poll can never leave a trade running past its minute.
Risk   : 1% of balance per trade on the $7 stop (was 14% — the reason the
         strategy was stopped on 2026-10-06; Joseph, 2026-11-02). Minimum lot
         if 1% is smaller. A bad release can still cost ~2x the planned 1%
         (SL fills slip in a fast spike; MT5 has no deviation on a stop).
Scope  : touches ONLY its own magic (20260807) — the gold straddle (00:00/01:00),
         gold hat long, dip bot, pre-FOMC and USDJPY bots keep their trades
         through the release. FLATTEN_OTHER_BOTS = True brings back the old
         behaviour (close all gold 10 min before, whole account at +60 s),
         which none of the other backtests assumed.
Times  : release times are worked out from New York wall-clock time with the
         US daylight-saving rule in code (no tzdata needed on Windows) —
         no hand-typed "2 seconds early" UTC literals any more (they went
         wrong three times: wrong seconds, a stray 16:49 CPI, a #test entry
         that fired on the live account).
Clock  : `now` is broker-corrected: the freshest XAUUSDm tick (milliseconds)
         is compared with the VPS clock and only a real gap (>= 5 s, e.g. the
         64 s seen on 2026-09-14) is corrected. Keep Windows time sync on.

EVIDENCE (XAUUSDm)
  1-minute gold 2024-2025: NFP 59% win (22 events), CPI 55% (22), FOMC 67%
  (15); each beat 97-100% of 200 random-time tests.
  Exness tick replay Jan 2025 - Sep 2026: 49 events traded, 73.5% win.
  One-side fills win 89%; both-side fills win 25%. Net +$567 per unit at a
  $7 SL. Average slippage ~$1.40 in / $1.46 out, worst exit slip ~$9.57.
  2026 payoffs were much bigger than 2025's (volatile gold year) — don't
  assume 2026 dollar sizes repeat. The 2 s early entry was not separately
  re-backtested (earlier tests used 5 s / 3 s).

KNOWN LIMITS: ~3 trades a month, so a few bad releases make a losing
quarter. Dates must be added each year (the bot warns 40 days ahead).
Account must be in hedging mode (the gold straddle can hold gold at the
same time).
"""

from __future__ import annotations

import datetime
import math
from typing import Any, Dict, List, Optional, Tuple

import MetaTrader5 as mt5

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SYMBOL = "XAUUSDm"
MAGIC = 20260807               # straddle 20260716, confirm 20260801, reload 20260810,
                               # gold straddle 20261011/12, dip 20261028/29, pre-FOMC 20261034-36
OFFSET = 3.0                   # $ from mid to each stop
SL = 7.0                       # $ from the fill
RISK_PCT = 1.0                 # % of balance at the $7 stop
DECIMALS = 2
EARLY_ENTRY_SECONDS = 2.0      # straddle placed in [release - 2 s, release)
HOLD_SECONDS = 60.0            # everything closed at release + 60 s
EXIT_DEVIATION = 500           # points — exits must not be refused
STALE_TICK = datetime.timedelta(minutes=10)

FLATTEN_OTHER_BOTS = False     # True = old behaviour (closes OTHER bots' trades) — not recommended
PRE_FLATTEN_MINUTES = 10       # only used when FLATTEN_OTHER_BOTS is True
GLOBAL_FLATTEN_RETRY_SECONDS = 15.0

# MT5 rejects long comments (31 chars failed live, 2026-09-14) — keep these short
COMMENT_ENTRY = "spike_entry"
COMMENT_CLOSE = "spike_close"
COMMENT_FLATTEN = "spike_global_flatten"

# Release DATES (New York). Source: BLS release calendar (2026 + 2027 published,
# checked 2026-11-12) and the Federal Reserve FOMC calendar (2027 tentative).
EVENT_NY_TIME = {"NFP": (8, 30), "CPI": (8, 30), "FOMC": (14, 0)}
EVENT_DATES: Dict[str, List[str]] = {
    "NFP": ["2026-12-04",
            "2027-01-08", "2027-02-05", "2027-03-05", "2027-04-02", "2027-05-07", "2027-06-04",
            "2027-07-02", "2027-08-06", "2027-09-03", "2027-10-08", "2027-11-05", "2027-12-03"],
    "CPI": ["2026-12-10",
            "2027-01-13", "2027-02-11", "2027-03-10", "2027-04-13", "2027-05-12", "2027-06-10",
            "2027-07-14", "2027-08-11", "2027-09-14", "2027-10-14", "2027-11-10", "2027-12-10"],
    "FOMC": ["2026-12-09",
             "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09",
             "2027-07-28", "2027-09-15", "2027-10-27", "2027-12-08"],
}
DATES_WARN_DAYS = 40


def _ts(seconds: float) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(seconds, tz=datetime.timezone.utc)


def _round(price: float) -> float:
    return round(price, DECIMALS)


# ---------------------------------------------------------------------------
# New York time without zoneinfo (US rule since 2007)
# ---------------------------------------------------------------------------
def _nth_sunday(year: int, month: int, n: int) -> datetime.date:
    d = datetime.date(year, month, 1)
    d += datetime.timedelta(days=(6 - d.weekday()) % 7)
    return d + datetime.timedelta(weeks=n - 1)


def ny_to_utc(day: datetime.date, hour: int, minute: int = 0) -> datetime.datetime:
    """New York wall-clock -> UTC. DST 2nd Sunday of March 02:00 to 1st Sunday
    of November 02:00 (UTC-4), else UTC-5."""
    local = datetime.datetime(day.year, day.month, day.day, hour, minute)
    start = datetime.datetime.combine(_nth_sunday(day.year, 3, 2), datetime.time(2))
    end = datetime.datetime.combine(_nth_sunday(day.year, 11, 1), datetime.time(2))
    offset = 4 if start <= local < end else 5
    return (local + datetime.timedelta(hours=offset)).replace(tzinfo=datetime.timezone.utc)


def releases() -> List[Tuple[datetime.datetime, str]]:
    """[(real release time UTC, event type)], sorted."""
    out = []
    for kind, dates in EVENT_DATES.items():
        h, m = EVENT_NY_TIME[kind]
        for d in dates:
            out.append((ny_to_utc(datetime.date.fromisoformat(d), h, m), kind))
    return sorted(out)


RELEASES = releases()


def entry_window(now: datetime.datetime) -> Optional[Tuple[datetime.datetime, str]]:
    """(release, kind) if now is in [release - EARLY_ENTRY_SECONDS, release)."""
    for rel, kind in RELEASES:
        if rel - datetime.timedelta(seconds=EARLY_ENTRY_SECONDS) <= now < rel:
            return rel, kind
    return None


def live_span(now: datetime.datetime) -> Optional[Tuple[datetime.datetime, str]]:
    """(release, kind) if now is in [release - EARLY_ENTRY_SECONDS, release +
    HOLD_SECONDS) — the only time this strategy may hold anything."""
    for rel, kind in RELEASES:
        if rel - datetime.timedelta(seconds=EARLY_ENTRY_SECONDS) <= now < rel + datetime.timedelta(seconds=HOLD_SECONDS):
            return rel, kind
    return None


def next_release(now: datetime.datetime) -> Optional[Tuple[datetime.datetime, str]]:
    return next(((r, k) for r, k in RELEASES if r > now), None)


# ---------------------------------------------------------------------------
# Clock: broker-corrected UTC
# ---------------------------------------------------------------------------
SKEW_MIN_SECONDS = 5.0         # smaller gaps = tick age / rounding, not a clock error
SKEW_MAX_SECONDS = 600.0       # bigger = market closed (old tick), ignore
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
                print(f"  WARNING: VPS clock {new.total_seconds():+.1f}s off broker — correcting "
                      "(resync Windows time)" if new else "  VPS clock back in line with broker")
            _skew["value"] = new
        _skew["checked"] = local
    return local + _skew["value"]


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
class NewsSpikeStrategy:
    """No state between calls — everything is read from MT5 every time."""

    def __init__(self, fallback_balance: float = 90.0) -> None:
        self.fallback_balance = fallback_balance
        acc = mt5.account_info()
        mode = getattr(acc, "margin_mode", None) if acc else None
        hedging = getattr(mt5, "ACCOUNT_MARGIN_MODE_RETAIL_HEDGING", 2)
        if mode is not None and mode != hedging:
            print(f"  WARNING: account is NOT in hedging mode (margin_mode={mode}) — "
                  "this bot and the gold straddle can hold gold at the same time.")

    # ------------------------------------------------------------ MT5 helpers
    def _tick(self):
        tick = mt5.symbol_info_tick(SYMBOL)
        if tick is None:
            mt5.symbol_select(SYMBOL, True)
            tick = mt5.symbol_info_tick(SYMBOL)
        return tick

    def _filling(self, symbol: str = SYMBOL) -> int:
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

    def _positions(self) -> List[Any]:
        return [p for p in (mt5.positions_get(symbol=SYMBOL) or ()) if p.magic == MAGIC]

    def _orders(self) -> List[Any]:
        return [o for o in (mt5.orders_get(symbol=SYMBOL) or ()) if o.magic == MAGIC]

    def _lot(self) -> Tuple[float, bool]:
        """balance * 1% / ($7 * $ per 1.0 move per lot), rounded DOWN to the
        lot step; volume_min if smaller (flagged)."""
        info = mt5.symbol_info(SYMBOL)
        if info is None or not info.trade_tick_size:
            return 0.01, True
        per_unit = info.trade_tick_value / info.trade_tick_size
        acc = mt5.account_info()
        balance = acc.balance if acc else self.fallback_balance
        raw = balance * RISK_PCT / 100.0 / (SL * per_unit)
        vmin, vstep = info.volume_min or 0.01, info.volume_step or 0.01
        vmax = info.volume_max or raw
        lot = math.floor(raw / vstep + 1e-9) * vstep
        forced = lot < vmin
        return round(min(vmax, max(vmin, lot)), 2), forced

    def _close(self, pos, comment: str) -> bool:
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            mt5.symbol_select(pos.symbol, True)
            tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            return False
        is_buy = pos.type == mt5.POSITION_TYPE_BUY
        return self._ok(self._send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol, "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
            "position": pos.ticket, "price": tick.bid if is_buy else tick.ask,
            "deviation": EXIT_DEVIATION, "magic": pos.magic, "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(pos.symbol),
        }, f"close {pos.symbol} ticket {pos.ticket}"))

    def _cancel(self, order) -> bool:
        return self._ok(self._send({"action": mt5.TRADE_ACTION_REMOVE, "order": order.ticket},
                                   f"cancel {order.symbol} order {order.ticket}"))

    # ------------------------------------------------------------ management
    def _correct_sl(self, pos) -> Optional[str]:
        """Re-anchor the SL to the REAL fill (entry slippage) — idempotent."""
        is_buy = pos.type == mt5.POSITION_TYPE_BUY
        old = pos.sl
        want = _round(pos.price_open - SL if is_buy else pos.price_open + SL)
        if abs(want - old) < (10 ** -DECIMALS) / 2:
            return None
        ok = self._ok(self._send({
            "action": mt5.TRADE_ACTION_SLTP, "symbol": SYMBOL, "position": pos.ticket,
            "sl": want, "tp": pos.tp,
        }, f"SL correct ticket {pos.ticket}"))
        return (f"ticket {pos.ticket}: SL {old} -> {want} (fill {pos.price_open})"
                if ok else f"ticket {pos.ticket}: SL correction FAILED — will retry")

    def manage(self, now: datetime.datetime) -> List[str]:
        """Every poll, BEFORE check_and_place. Inside [release - 2 s,
        release + 60 s): keep SLs anchored to the real fill. Anywhere else:
        close own positions and cancel own orders (the 60 s exit, and the
        safety net if a poll was missed)."""
        msgs: List[str] = []
        span = live_span(now)
        positions, orders = self._positions(), self._orders()
        if span is not None:
            for pos in positions:
                fix = self._correct_sl(pos)
                if fix:
                    msgs.append(fix)
            if positions:
                msgs.append(f"{len(positions)} position(s) open — close at "
                            f"{span[0] + datetime.timedelta(seconds=HOLD_SECONDS):%H:%M:%S} UTC")
        else:
            for o in orders:
                msgs.append(f"cancel order {o.ticket}: {'ok' if self._cancel(o) else '!! FAILED'}")
            for pos in positions:
                ok = self._close(pos, COMMENT_CLOSE)
                msgs.append(f"CLOSE ticket {pos.ticket} {pos.volume} lots, P&L ~{pos.profit:+.2f}: "
                            f"{'ok' if ok else '!! FAILED — will retry'}")
        if FLATTEN_OTHER_BOTS:
            msgs.extend(self._legacy_flatten(now))
        return msgs

    def _legacy_flatten(self, now: datetime.datetime) -> List[str]:
        """OLD behaviour, only if FLATTEN_OTHER_BOTS is True: close every gold
        position/order of ANY bot in the 10 min before a release, and every
        position/order on the WHOLE account at release + 60 s (15 s retry)."""
        msgs: List[str] = []
        for rel, kind in RELEASES:
            start = rel - datetime.timedelta(seconds=EARLY_ENTRY_SECONDS)
            if start - datetime.timedelta(minutes=PRE_FLATTEN_MINUTES) <= now < start:
                for p in mt5.positions_get(symbol=SYMBOL) or ():
                    msgs.append(f"[pre-{kind} flatten] close {p.ticket} magic {p.magic}: "
                                f"{'ok' if self._close(p, COMMENT_FLATTEN) else '!! FAILED'}")
                for o in mt5.orders_get(symbol=SYMBOL) or ():
                    msgs.append(f"[pre-{kind} flatten] cancel {o.ticket} magic {o.magic}: "
                                f"{'ok' if self._cancel(o) else '!! FAILED'}")
            dl = rel + datetime.timedelta(seconds=HOLD_SECONDS)
            if dl <= now <= dl + datetime.timedelta(seconds=GLOBAL_FLATTEN_RETRY_SECONDS):
                for p in mt5.positions_get() or ():
                    msgs.append(f"[post-{kind} GLOBAL flatten] close {p.symbol} {p.ticket} magic {p.magic}: "
                                f"{'ok' if self._close(p, COMMENT_FLATTEN) else '!! FAILED'}")
                for o in mt5.orders_get() or ():
                    msgs.append(f"[post-{kind} GLOBAL flatten] cancel {o.symbol} {o.ticket}: "
                                f"{'ok' if self._cancel(o) else '!! FAILED'}")
        return msgs

    # ------------------------------------------------------------ entry
    def check_and_place(self, now: datetime.datetime) -> str:
        win = entry_window(now)
        if win is None:
            return "Outside entry window"
        rel, kind = win
        head = f"[{kind} {rel:%a %d %b %H:%M:%S} UTC]"
        if self._positions() or self._orders():
            return f"{head} straddle already placed"
        start = rel - datetime.timedelta(seconds=EARLY_ENTRY_SECONDS)
        deals = mt5.history_deals_get(start - datetime.timedelta(seconds=5),
                                      now + datetime.timedelta(minutes=1))
        if deals is None:
            return f"{head} deal history unavailable — skipping this event (safe side)"
        if any(d.magic == MAGIC and d.symbol == SYMBOL for d in deals):
            return f"{head} already traded this event — no re-entry"
        tick = self._tick()
        if tick is None:
            return f"{head} no tick data"
        if now - _ts(tick.time) > STALE_TICK:
            return f"{head} market likely closed — last tick {now - _ts(tick.time)} old"

        mid = (tick.bid + tick.ask) / 2.0
        buy_stop, sell_stop = _round(mid + OFFSET), _round(mid - OFFSET)
        buy_sl, sell_sl = _round(buy_stop - SL), _round(sell_stop + SL)
        lots, forced = self._lot()
        filling = self._filling()
        tickets: Dict[str, Optional[int]] = {"buy": None, "sell": None}
        for side, otype, price, stop in (
            ("buy", mt5.ORDER_TYPE_BUY_STOP, buy_stop, buy_sl),
            ("sell", mt5.ORDER_TYPE_SELL_STOP, sell_stop, sell_sl),
        ):
            # GTC on purpose: Exness refused a ~60 s ORDER_TIME_SPECIFIED expiry
            # (10022, 2026-09-14). manage() cancels at release + 60 s instead.
            res = self._send({
                "action": mt5.TRADE_ACTION_PENDING, "symbol": SYMBOL, "volume": lots,
                "type": otype, "price": price, "sl": stop, "tp": 0.0,
                "magic": MAGIC, "comment": COMMENT_ENTRY,
                "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling,
            }, f"{kind} {side.upper()} stop")
            if self._ok(res):
                tickets[side] = res.order
        if tickets["buy"] is None or tickets["sell"] is None:
            for t in tickets.values():
                if t is not None:
                    self._send({"action": mt5.TRADE_ACTION_REMOVE, "order": t}, "rollback")
            return f"{head} order send failed — rolled back"
        note = " [MIN LOT — risk above 1%]" if forced else ""
        return (f"{head} straddle placed: buy {buy_stop} / sell {sell_stop}, {lots} lots{note}, "
                f"SL {buy_sl}/{sell_sl}, close at "
                f"{rel + datetime.timedelta(seconds=HOLD_SECONDS):%H:%M:%S} UTC")

    # ------------------------------------------------------------ info
    def dates_warning(self, now: datetime.datetime) -> Optional[str]:
        out = []
        for kind, dates in EVENT_DATES.items():
            last = max(datetime.date.fromisoformat(d) for d in dates)
            if (last - now.date()).days < DATES_WARN_DAYS:
                out.append(kind)
        if out:
            return (f"WARNING: news calendar runs out soon for {', '.join(out)} — add next "
                    "year's dates to EVENT_DATES in news_spike_strategy.py")
        return None

    def __repr__(self) -> str:
        now = datetime.datetime.now(datetime.timezone.utc)
        nxt = next_release(now)
        return (f"NewsSpikeStrategy: {SYMBOL} NFP/CPI/FOMC straddle ±${OFFSET}, SL ${SL}, "
                f"placed {EARLY_ENTRY_SECONDS:g}s before release, closed at +{HOLD_SECONDS:g}s, "
                f"risk {RISK_PCT}%, magic {MAGIC}, "
                f"{'FLATTENS OTHER BOTS' if FLATTEN_OTHER_BOTS else 'own trades only'}"
                + (f"\n  Next: {nxt[1]} {nxt[0]:%a %d %b %Y %H:%M:%S} UTC" if nxt else ""))