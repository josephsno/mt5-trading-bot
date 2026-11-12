# strategies/gold_straddle_strategy.py
"""
Gold Straddle Strategy — 00:00 + 01:00 UTC (stateless)
======================================================
One class, GoldStraddleStrategy, runs every setup in SETUPS. Each setup has
its own trigger time and its own MAGIC, so the two never touch each other's
orders or positions (a 00:00 trade still open at 01:00 does NOT block the
01:00 straddle).

Entry  : At the trigger time, mid = (bid+ask)/2. Buy-stop at mid + $10,
         sell-stop at mid - $10. Whichever fills first is the trade; the
         other is cancelled at the next poll (OCO).
SL     : $20 from the STOP LEVEL (set on the pending order, not re-anchored).
BE     : When the CURRENT price at a poll is >= +$20 in profit, SL -> entry.
         (Tested: triggering on the best price since entry instead was WORSE
         — do not "fix" this.)
Trail  : After BE, SL trails $15 behind the best M15 high/low since entry.
Cancel : Unfilled stops expire at the setup's cancel hour (8h after trigger),
         broker-side (ORDER_TIME_SPECIFIED) AND bot-side.
Exit   : No TP. Hard close 1h before this setup's next trigger, capped at
         Friday 20:00 UTC so nothing rides the weekend.
Dual   : If both stops fill before the OCO cancel, the newer position is
         closed at market and the first fill is kept.
Polling: every 5 minutes, clock-aligned (main_gold_straddle.py).

TWO DAILY CHECKS before placing (decided 2026-11-06, active from FILTERS_FROM)
  1) MOVEMENT FILTER — skip the day (both setups) if the median 00:00-08:00
     UTC range of the last 20 finished weekdays is below $14. Range = highest
     H1 high - lowest H1 low of the 00:00..07:00 UTC H1 bars (bid bars).
     Fewer than 20 days of bars -> skip (same as the backtest).
  2) WEEK PAUSE (version B) — weeks of the month: days 1-7 / 8-14 / 15-21 /
     22-28 / 29-31, by the trade's open date (UTC). Both setups are judged
     together (net P&L of both magics, scaled to 0.01 lot like the backtest).
     If a week lost in 2 traded months in a row, it is skipped for the next
     calendar month, then resumes; the count restarts after a pause. Rebuilt
     every time from MT5 deal history (last 12 months) — nothing stored.
  Both checks read MT5 history twice and need identical results (up to 3
  retries, 1s apart) — a half-synced read (dip bot, 29 Oct 2026) or a failed
  read skips the day instead of trading on bad data.

BACKTEST (Exness XAUUSDm ticks, bot-faithful replay, per 0.01 lot)
  No checks, 2018 + 2022-Oct 2026 : +$2,010, max DD $598, PF 1.21, win 50%
  Filter + week pause B (same)    : +$2,307, max DD $472, PF 1.39, win 54%
  By year (filter + pause B): no year worse than -$13 (2018/2022/2023 were
  -$96/-$225/-$103 without). Unseen 2021: -$13 vs +$77 without (protective
  in quiet years, does not add profit there).
  Balance to survive any start month in the data: about $300 per 0.01 lot.
  Why it works: 00:00 UTC = Tokyo open, 01:00 UTC = Shanghai Gold Exchange
  day-session open. It earns when gold moves early (~$14+ range) and bleeds
  slowly in quiet markets (~$10 or less) — the filter switches it off then.

KNOWN LIMITS: the $14 threshold was partly chosen in-sample; recheck on
Nov-Dec 2026 data. Busy-market whipsaw drawdowns (May-Jul 2025, $472) are not
removed by either check.
"""

from __future__ import annotations

import datetime
import statistics
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

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

# --- daily checks (movement filter + week pause B) --------------------------
FILTERS_FROM = datetime.date(2027, 1, 1)   # checks switch on from this UTC date
FILTER_SYMBOL = "XAUUSDm"
FILTER_MIN_RANGE = 14.0        # $ — skip the day if the median range is below
FILTER_DAYS = 20               # finished weekdays in the median
FILTER_END_HOUR = 8            # window 00:00..07:59 UTC
FILTER_LOOKBACK_DAYS = 45      # calendar days of H1 bars to pull (>= 20 weekdays)
PAUSE_LOSING_MONTHS = 2        # losing traded months in a row -> pause
PAUSE_LOOKBACK_MONTHS = 12     # months of deal history replayed
STABLE_READ_TRIES = 3          # history reads must match twice in a row (29 Oct dip-bot lesson)
STABLE_READ_PAUSE = 1.0        # seconds between reads
FILTER_MAX_GAP_DAYS = 4        # newest finished day older than this -> history not synced, skip


def enabled_setups() -> List[Dict[str, Any]]:
    return [s for s in SETUPS if s["enabled"]]


def _round(price: float) -> float:
    return round(price, DECIMALS)


def _ts(seconds: int) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(seconds, tz=datetime.timezone.utc)


def week_of_month(day: int) -> int:
    """1 = days 1-7, 2 = 8-14, 3 = 15-21, 4 = 22-28, 5 = 29-31."""
    return min((day - 1) // 7 + 1, 5)


def _month_index(d: datetime.date) -> int:
    return d.year * 12 + d.month - 1


# ---------------------------------------------------------------------------
# Daily-check logic as plain functions (no MT5 inside -> testable offline)
# ---------------------------------------------------------------------------
def median_early_range(bars: Sequence[Tuple[int, float, float]],
                       today: datetime.date) -> Tuple[Optional[float], int]:
    """bars = (open_time_unix_utc, high, low) H1 bars. Uses only finished
    weekdays BEFORE `today`, hours 00..FILTER_END_HOUR-1. Returns
    (median of the last FILTER_DAYS daily ranges, days available)."""
    hi: Dict[datetime.date, float] = {}
    lo: Dict[datetime.date, float] = {}
    for t, h, l in bars:
        ts = _ts(int(t))
        d = ts.date()
        if d >= today or ts.hour >= FILTER_END_HOUR or d.weekday() >= 5:
            continue
        hi[d] = max(hi.get(d, h), h)
        lo[d] = min(lo.get(d, l), l)
    days = sorted(hi)
    if len(days) < FILTER_DAYS:
        return None, len(days)
    last = days[-FILTER_DAYS:]
    return statistics.median(hi[d] - lo[d] for d in last), len(days)


def paused_weeks(trades: Sequence[Tuple[datetime.date, float]],
                 today: datetime.date) -> Dict[int, str]:
    """trades = (open date UTC, net P&L) for BOTH setups, already closed.
    Replays the finished months before today's month in order. Returns
    {week: reason} for the weeks paused in today's month."""
    cur = _month_index(today)
    first = cur - PAUSE_LOOKBACK_MONTHS
    pnl: Dict[Tuple[int, int], float] = {}
    for d, v in trades:
        m = _month_index(d)
        if first <= m < cur:
            key = (m, week_of_month(d.day))
            pnl[key] = pnl.get(key, 0.0) + v
    out: Dict[int, str] = {}
    for wk in range(1, 6):
        streak: List[int] = []
        pause_month: Optional[int] = None
        for m in range(first, cur):
            if m == pause_month:
                continue                      # the bot sat this week out
            v = pnl.get((m, wk))
            if v is None:
                continue                      # week not traded that month
            if v < 0:
                streak.append(m)
                if len(streak) >= PAUSE_LOSING_MONTHS:
                    pause_month, streak = m + 1, []
                    last_two = (m - 1, m)
            else:
                streak = []
        if pause_month == cur:
            names = [datetime.date(x // 12, x % 12 + 1, 1).strftime("%b %Y") for x in last_two]
            out[wk] = f"lost {names[0]} + {names[1]}"
    return out


def stable_read(read, key) -> Optional[Any]:
    """Call read() until two consecutive results have the same key(); None
    results are retried. Returns the agreed result, or None (= not trusted).
    Guards against MT5 returning a half-synced history (dip bot, 29 Oct 2026)."""
    prev = None
    for i in range(STABLE_READ_TRIES + 1):
        cur = read()
        if cur is not None and prev is not None and key(cur) == key(prev):
            return cur
        prev = cur
        if i < STABLE_READ_TRIES:
            time.sleep(STABLE_READ_PAUSE)
    return None


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

    # ------------------------------------------------------------ daily checks
    def _movement_check(self, now: datetime.datetime) -> Tuple[bool, str]:
        """(ok_to_trade, message). H1 bars re-read from MT5 every call."""
        start = now - datetime.timedelta(days=FILTER_LOOKBACK_DAYS)
        # finished bars only: stop 1s before today 00:00, so the live 00:00
        # bar (still changing) never makes two reads differ
        end = now.replace(hour=0, minute=0, second=0, microsecond=0) - datetime.timedelta(seconds=1)

        def read():
            r = mt5.copy_rates_range(FILTER_SYMBOL, mt5.TIMEFRAME_H1, start, end)
            return None if r is None or len(r) == 0 else r

        rates = stable_read(read, lambda r: (len(r), int(r[-1]["time"]), float(r[-1]["high"])))
        if rates is None:
            return False, f"Movement filter: H1 history not stable/available ({mt5.last_error()}) — skipping"
        bars = [(int(r["time"]), float(r["high"]), float(r["low"])) for r in rates]
        newest = max((_ts(t).date() for t, _, _ in bars if _ts(t).date() < now.date()), default=None)
        if newest is None or (now.date() - newest).days > FILTER_MAX_GAP_DAYS:
            return False, f"Movement filter: H1 history looks stale (newest day {newest}) — skipping"
        med, n = median_early_range(bars, now.date())
        if med is None:
            return False, f"Movement filter: only {n} days of H1 bars (< {FILTER_DAYS}) — skipping"
        if med < FILTER_MIN_RANGE:
            return False, f"Quiet market: median 00-08 UTC range ${med:.2f} < ${FILTER_MIN_RANGE:.0f} — skipping"
        return True, f"median range ${med:.2f}"

    def _closed_trades(self, now: datetime.datetime) -> Optional[List[Tuple[datetime.date, float]]]:
        """(open date UTC, net P&L per 0.01 lot) per closed position of ALL gold
        straddle setups, from MT5 deal history. Positions are found by the magic
        on their deals, then every deal of that position is summed (profit +
        swap + commission + fee), whatever magic the exit deal carries. P&L is
        scaled to 0.01 lot (the backtest unit) so growing lots don't change a
        month's sign. Returns None if the history read is not trusted."""
        months = PAUSE_LOOKBACK_MONTHS + 1
        start = now - datetime.timedelta(days=31 * months + 7)
        end = now + datetime.timedelta(minutes=1)
        # history_deals_get returns None on error, () when there are no deals
        deals = stable_read(lambda: mt5.history_deals_get(start, end),
                            lambda d: (len(d), max((x.ticket for x in d), default=0)))
        if deals is None:
            return None
        magics = {s["magic"] for s in SETUPS}
        ours = {d.position_id for d in deals
                if d.magic in magics and d.symbol == FILTER_SYMBOL and d.position_id}
        still_open = {p.ticket for p in (mt5.positions_get(symbol=FILTER_SYMBOL) or ())}
        first: Dict[int, Any] = {}
        net: Dict[int, float] = {}
        for d in deals:
            pid = d.position_id
            if pid not in ours or pid in still_open:
                continue
            if pid not in first or d.time < first[pid].time:
                first[pid] = d                      # opening deal (earliest)
            net[pid] = net.get(pid, 0.0) + d.profit + d.swap + d.commission + getattr(d, "fee", 0.0)
        out = []
        for pid, v in net.items():
            vol = first[pid].volume or 0.01
            out.append((_ts(first[pid].time).date(), v * 0.01 / vol))
        return out

    def _week_check(self, now: datetime.datetime) -> Tuple[bool, str]:
        wk = week_of_month(now.day)
        trades = self._closed_trades(now)
        if trades is None:
            return False, f"Week pause: deal history not stable/available ({mt5.last_error()}) — skipping"
        paused = paused_weeks(trades, now.date())
        if wk in paused:
            return False, f"Week {wk} paused this month ({paused[wk]}) — skipping"
        return True, f"week {wk} active"

    def daily_checks(self, now: Optional[datetime.datetime] = None) -> Tuple[bool, str]:
        """Both checks; same answer for both setups on a given day."""
        now = now or _utc_now()
        if now.date() < FILTERS_FROM:
            return True, f"daily checks off until {FILTERS_FROM}"
        ok, msg_m = self._movement_check(now)
        if not ok:
            return False, msg_m
        ok, msg_w = self._week_check(now)
        if not ok:
            return False, msg_w
        return True, f"{msg_m}, {msg_w}"

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

        ok, why = self.daily_checks(now)
        if not ok:
            return why

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
        return (f"Straddle placed ({why}): buy {buy_stop} / sell {sell_stop}, {lots} lots, "
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
        lines.append(
            f"  Daily checks from {FILTERS_FROM}: skip if median 00-{FILTER_END_HOUR:02d} UTC range "
            f"of last {FILTER_DAYS} days < ${FILTER_MIN_RANGE:.0f}; week pause B "
            f"({PAUSE_LOSING_MONTHS} losing months in a row -> skip next month)")
        return "\n".join(lines)