"""
Straddle Strategy — Live Version (stateless)
=============================================
Edge   : Direction is close to a coin flip at session-open timescale, but
         WHEN a genuine breakout is likely (session/hour) is predictable
         (AUC 0.75 across 4 walk-forward folds). Rather than guess
         direction, place orders on both sides at each pair's own best
         entry hour and let the market pick.
Entry  : Buy-stop + sell-stop straddle at each symbol's trigger hour.
         Whichever fills first is the trade; the other is cancelled (OCO).
SL     : Fixed distance from entry (25 pips FX / $20 XAU).
Exit   : Trail from 1R, 20 pips (FX) / $15 (XAU) per bar — no fixed TP,
         EXCEPT an adaptive max-hold rule: every position is force-closed
         1 hour before its OWN symbol's NEXT scheduled trigger time — capped
         so it can NEVER bridge the weekend. A Friday fill always closes by
         20:00 UTC that same Friday, regardless of how early it filled,
         rather than riding through to Monday's trigger and taking on
         weekend gap risk (mirrors the monthly bot's own Friday hard-close
         rule). This replaced an earlier flat 24-hour cap after backtesting
         showed the flat version let a still-open trade silently block the
         next day's entry ~0.4-6.2% of the time depending on symbol (worst
         on EURUSD, 6.2%) — the bot would just skip placing a new straddle
         that day, which the original backtest never modeled. The adaptive
         version fixes the skip problem completely and improved 3 of 4
         symbols outright — GBPUSD nearly doubled: 625p -> 1,173p.
Breaker: Cross-pair — pauses ALL new entries if 2+ symbols each show 2+
         consecutive losses. A per-pair breaker was tested and made
         results worse (cuts a pair off right before its own recovery);
         the cross-pair version targets a real, evidenced failure mode —
         GBPUSD and USDJPY failed simultaneously for two months in the
         2024-2026 backtest, and this catches exactly that.

STATELESS BY DESIGN: this file keeps no trade, order, or result state in
memory anywhere. Every method re-derives what it needs from MT5 directly —
open positions via positions_get(), pending orders via orders_get(), win/
loss history via history_deals_get(). A bot restart loses nothing, because
nothing lives outside MT5 in the first place. This also removes the
"permanent pause freeze" risk a naive in-memory circuit breaker can hit:
since streaks are recomputed from real deal history on every call, and the
breaker has a hard max-pause fallback below, it cannot get stuck open
forever the way an in-memory-only version can.

Backtest summary (2024-2026, Exness M15 data, $90 start, 1% risk/trade,
adaptive per-symbol deadline applied — no silently skipped days):
  EURUSDm @ 08:00 UTC : 503 trades, 51.1% win rate, 567 pips, $146.67 final
  USDJPYm @ 08:00 UTC : 513 trades, 54.0% win rate, 2823 pips, $278.22 final
  GBPUSDm @ 04:00 UTC : 504 trades, 51.0% win rate, 1173 pips, $207.26 final
    (08:00 UTC — which works for EUR/JPY — LOSES money on GBPUSD; UK data
    releases at 06:00-07:00 UTC, so 04:00 sits ahead of the catalyst while
    08:00 catches the retest/reversal instead of the move itself)
  XAUUSDm @ 01:00 UTC : 473 trades, 60.0% win rate, $2460.45 final — parked
    by default, see GOLD_ENABLED (enabled here at explicit user instruction)
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

import MetaTrader5 as mt5

# ---------------------------------------------------------------------------
# Per-symbol configuration
# ---------------------------------------------------------------------------

SYMBOL_CONFIG: Dict[str, Dict[str, Any]] = {
    "EURUSDm": {
        "pip": 0.0001,
        "offset": 15,
        "sl": 25,
        "trail": 20,
        "be_trigger": 25,
        "trigger_hour": 8,
        "trigger_minute": 0,  # e.g. set to 30 to test 08:30 instead of 08:00
        "cancel_hour": 16,
    },
    "USDJPYm": {
        "pip": 0.01,
        "offset": 15,
        "sl": 25,
        "trail": 20,
        "be_trigger": 25,
        "trigger_hour": 8,
        "trigger_minute": 0,
        "cancel_hour": 16,
    },
    "GBPUSDm": {
        "pip": 0.0001,
        "offset": 15,
        "sl": 25,
        "trail": 20,
        "be_trigger": 25,
        "trigger_hour": 4,  # NOT 08:00 — see module docstring, do not "fix" this
        "trigger_minute": 0,
        "cancel_hour": 12,
    },
    "XAUUSDm": {
        "pip": 1.0,  # working directly in USD, not pips, for gold
        "offset": 10,
        "sl": 20,
        "trail": 15,
        "be_trigger": 20,
        "trigger_hour": 1,
        "trigger_minute": 0,
        "cancel_hour": 9,
    },
}

# Gold's out-of-sample win rate improved 56.4% -> 64.1% and held up after
# normalizing for gold's price roughly doubling over the test period — the
# signal looks real. But at the 0.01 lot floor, SL=$20 * pip_value($100/lot)
# * 0.01 lot = $20 risk per trade, which is ~22% of a $90-150 account on a
# single trade. Do not enable until balance actually supports sane sizing —
# check the math again at that point, not just this flag.
GOLD_ENABLED = True
GOLD_MIN_BALANCE = 2000.0

MAGIC = 20260716  # unique to this strategy, keeps it from colliding with the M15 bot

DEADLINE_BUFFER_HOURS = 1  # how far ahead of the NEXT trigger hour a trade
# must be closed by — see _next_trigger_deadline()
WEEKLY_CLOSE_HOUR = 20  # UTC, Friday — hard cap so no position ever rides
# through the weekend gap. Mirrors the existing
# monthly bot's own Friday hard-close rule.

# Cross-pair circuit breaker
BREAKER_LOSS_STREAK = 2
BREAKER_MIN_SYMBOLS_FLAGGED = 2
BREAKER_MAX_PAUSE_HOURS = 96  # hard fallback: never pause longer than this,
# regardless of streak state — see _is_paused()


def _pip(symbol: str) -> float:
    return SYMBOL_CONFIG.get(symbol, {}).get("pip", 0.0001)


def _pip_value_per_lot(symbol: str, price: float) -> float:
    """USD value of a 1-pip move at 1.0 lot. Fixed $10 for USD-quoted FX
    pairs, dynamic for USDJPY (JPY-quoted), and $100/point for XAUUSD
    (1.0 lot = 100 oz)."""
    if symbol == "USDJPYm":
        return 1000.0 / price
    if symbol == "XAUUSDm":
        return 100.0
    return 10.0


def _round_price(price: float, symbol: str) -> float:
    if symbol == "USDJPYm":
        return round(price, 3)
    if symbol == "XAUUSDm":
        return round(price, 2)
    return round(price, 5)


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


class StraddleStrategy:
    """No __init__ state beyond configuration — see module docstring.
    Every method queries MT5 fresh; nothing is cached between calls."""

    def __init__(
        self,
        risk_pct: float = 1.0,
        min_lot: float = 0.01,
        lot_step: float = 0.01,
        initial_balance: float = 90.0,
    ) -> None:
        self.risk_pct = risk_pct
        self.min_lot = min_lot
        self.lot_step = lot_step
        self.starting_balance = initial_balance
        self.traded_symbols: List[str] = [
            s for s in SYMBOL_CONFIG if s != "XAUUSDm" or GOLD_ENABLED
        ]

    # ---------------------------------------------------------------- balance

    def _balance(self) -> float:
        acc = mt5.account_info()
        return acc.balance if acc else self.starting_balance

    def _lot_size(self, symbol: str, price: float, sl_units: float) -> float:
        """Risk-based lot size, floored at the broker minimum. At small
        account sizes this floor is almost always the binding constraint,
        not the risk_pct target — expected, not a bug."""
        pip_value = _pip_value_per_lot(symbol, price)
        risk_dollar = self._balance() * (self.risk_pct / 100.0)
        lot = risk_dollar / (sl_units * pip_value)
        return max(self.min_lot, round(lot / self.lot_step) * self.lot_step)

    # ---------------------------------------------------------------- MT5 reads

    def _filling_mode(self, symbol: str) -> int:
        """Different brokers/symbols support different fill modes — hardcoding
        ORDER_FILLING_IOC everywhere was wrong and is exactly what caused the
        None-return crash on XAUUSDm previously. Ask MT5 what this symbol
        actually supports and pick accordingly, every call, no caching.

        symbol_info().filling_mode is a bitmask: bit 1 (value 1) = FOK
        supported, bit 2 (value 2) = IOC supported, both bits set = both
        supported. These are documented on the MQL5 side as SYMBOL_FILLING_FOK
        and SYMBOL_FILLING_IOC, but the Python MetaTrader5 package does NOT
        expose those as module attributes — only the ORDER_FILLING_* request
        constants exist in mt5.*. Referencing mt5.SYMBOL_FILLING_IOC crashed
        with AttributeError; the raw integers below are the correct fix,
        not a workaround."""
        info = mt5.symbol_info(symbol)
        if info is None:
            return mt5.ORDER_FILLING_IOC  # best-effort fallback, shouldn't happen
        mode = info.filling_mode
        SYMBOL_FILLING_FOK = 1
        SYMBOL_FILLING_IOC = 2
        if mode & SYMBOL_FILLING_IOC:
            return mt5.ORDER_FILLING_IOC
        if mode & SYMBOL_FILLING_FOK:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    def _safe_order_send(self, request: Dict[str, Any]):
        """mt5.order_send() returns None — not a result object — when the
        request never reaches the trade server at all: AutoTrading disabled
        in the terminal, the symbol not in Market Watch, an unsupported
        filling mode, or a dropped connection. Every direct order_send call
        in this file used to skip checking for that and crashed trying to
        read .retcode off None. This wrapper is the only place that talks
        to order_send from here on — logs the real reason via
        mt5.last_error() and returns None cleanly instead of crashing."""
        result = mt5.order_send(request)
        if result is None:
            print(
                f"  order_send returned None — request never reached the "
                f"server. mt5.last_error(): {mt5.last_error()}"
            )
            return None
        return result

    def _get_position(self, symbol: str):
        """Fetch this strategy's open position for `symbol` directly from
        MT5, or None. No caching — call fresh every time.

        Also self-heals a rare dual-fill race: if both straddle sides
        happened to fill (both orders crossed before the previous poll
        could cancel the leftover one), MT5 will show 2 open positions for
        this symbol/magic. Every field used here — how many positions
        exist, which opened first — comes straight from positions_get() on
        this exact call; nothing is remembered between calls. The newer
        position is closed at market immediately, keeping the one that
        was genuinely first to fill."""
        positions = mt5.positions_get(symbol=symbol)
        if not positions:
            return None
        own = sorted(
            [p for p in positions if p.magic == MAGIC],
            key=lambda p: p.time,
        )
        if not own:
            return None
        if len(own) > 1:
            for extra in own[1:]:
                self._close_position_at_market(symbol, extra)
        return own[0]

    def _get_pending_orders(self, symbol: str) -> Dict[str, Any]:
        """Fetch this strategy's pending stop orders for `symbol` directly
        from MT5. Returns {'buy': order|None, 'sell': order|None}."""
        orders = mt5.orders_get(symbol=symbol) or ()
        own = [o for o in orders if o.magic == MAGIC]
        buy = next((o for o in own if o.type == mt5.ORDER_TYPE_BUY_STOP), None)
        sell = next((o for o in own if o.type == mt5.ORDER_TYPE_SELL_STOP), None)
        return {"buy": buy, "sell": sell}

    def _consecutive_losses(self, symbol: str, lookback_days: int = 14) -> tuple:
        """Count consecutive losses for `symbol` from the most recent closed
        deal backwards, reading MT5 history directly. Returns
        (streak, time_of_most_recent_loss_or_None) — the timestamp feeds the
        breaker's max-pause fallback below."""
        since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=lookback_days
        )
        deals = mt5.history_deals_get(
            since, datetime.datetime.now(datetime.timezone.utc), group=f"*{symbol}*"
        )
        if not deals:
            return 0, None
        closes = sorted(
            [d for d in deals if d.magic == MAGIC and d.entry == mt5.DEAL_ENTRY_OUT],
            key=lambda d: d.time,
            reverse=True,
        )
        streak = 0
        most_recent_loss_time = None
        for d in closes:
            if d.profit < 0:
                streak += 1
                if most_recent_loss_time is None:
                    most_recent_loss_time = d.time
            else:
                break
        return streak, most_recent_loss_time

    def _is_paused(self) -> bool:
        """Cross-pair breaker, fully derived from MT5 history: True if 2+
        traded symbols each currently show BREAKER_LOSS_STREAK+ consecutive
        losses. Backtest: cut max drawdown from -24.1% to -15.6% on the
        validated 3-pair portfolio, at the cost of skipping ~13% of trades.

        Hard fallback: if the most recent flagged loss is older than
        BREAKER_MAX_PAUSE_HOURS, the breaker is treated as expired and
        entries resume regardless of streak state. Without this, a symbol
        that never gets a new trade (because the breaker is blocking it)
        can never post the win that would reset its own streak — an
        in-memory-only version of this breaker can freeze permanently for
        exactly that reason; deriving everything from real MT5 timestamps,
        plus this fallback, prevents that."""
        flagged_times: List[float] = []
        for symbol in self.traded_symbols:
            streak, last_loss_time = self._consecutive_losses(symbol)
            if streak >= BREAKER_LOSS_STREAK and last_loss_time is not None:
                flagged_times.append(last_loss_time)

        if len(flagged_times) < BREAKER_MIN_SYMBOLS_FLAGGED:
            return False

        most_recent_flag = max(flagged_times)
        age_hours = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.datetime.fromtimestamp(
                most_recent_flag, tz=datetime.timezone.utc
            )
        ).total_seconds() / 3600.0
        if age_hours > BREAKER_MAX_PAUSE_HOURS:
            return False  # fallback expired — do not stay paused forever

        return True

    # ---------------------------------------------------------------- entry

    def check_and_place(self, symbol: str) -> Dict[str, Any]:
        """
        Call once per day at cfg['trigger_hour']:00 UTC. Places a buy stop
        and a sell stop simultaneously. Fetches current price, existing
        positions, and existing pending orders directly from MT5 — no
        state is stored or read from memory.
        """
        if symbol not in self.traded_symbols:
            return self._no(f"{symbol} not enabled")

        if self._get_position(symbol) is not None:
            return self._no("Position already open")

        pending = self._get_pending_orders(symbol)
        if pending["buy"] is not None or pending["sell"] is not None:
            return self._no("Straddle already pending")

        if self._is_paused():
            return self._no("Cross-pair circuit breaker active — no new entries")

        cfg = SYMBOL_CONFIG[symbol]
        now = datetime.datetime.now(datetime.timezone.utc)
        if now.hour != cfg["trigger_hour"] or now.minute != cfg.get(
            "trigger_minute", 0
        ):
            return self._no(f"Outside trigger window: {now.hour}:{now.minute:02d} UTC")

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return self._no("No tick data")

        # Market-closed guard: a genuinely stale tick (no new price in the
        # last few minutes) is the most reliable sign from this API that
        # the symbol isn't actively trading right now — e.g. XAUUSDm before
        # its Sunday 22:05 GMT weekly open, or during a daily break window.
        # Catching this here gives a clear "market closed" message instead
        # of order_send returning None with no explanation.
        tick_age = datetime.datetime.now(
            datetime.timezone.utc
        ) - datetime.datetime.fromtimestamp(tick.time, tz=datetime.timezone.utc)
        if tick_age > datetime.timedelta(minutes=10):
            return self._no(f"Market likely closed — last tick is {tick_age} old")

        # Mid price as anchor for both sides, matching the backtest's
        # bar-open reference rather than bid/ask (which would bias one side).
        anchor = (tick.bid + tick.ask) / 2.0
        pip = _pip(symbol)
        offset = cfg["offset"] * pip
        sl = cfg["sl"] * pip

        buy_stop = _round_price(anchor + offset, symbol)
        sell_stop = _round_price(anchor - offset, symbol)
        buy_sl = _round_price(buy_stop - sl, symbol)
        sell_sl = _round_price(sell_stop + sl, symbol)

        lots = self._lot_size(symbol, anchor, cfg["sl"])
        expiration = self._cancel_deadline(cfg["cancel_hour"])
        filling_mode = self._filling_mode(symbol)

        tickets: Dict[str, Optional[int]] = {"buy": None, "sell": None}
        for side, order_type, price, stop in (
            ("buy", mt5.ORDER_TYPE_BUY_STOP, buy_stop, buy_sl),
            ("sell", mt5.ORDER_TYPE_SELL_STOP, sell_stop, sell_sl),
        ):
            result = self._safe_order_send(
                {
                    "action": mt5.TRADE_ACTION_PENDING,
                    "symbol": symbol,
                    "volume": lots,
                    "type": order_type,
                    "price": price,
                    "sl": stop,
                    "tp": 0.0,  # no fixed TP — trail manages the exit
                    "magic": MAGIC,
                    "comment": "straddle_entry",
                    "type_time": mt5.ORDER_TIME_SPECIFIED,
                    "expiration": int(expiration.timestamp()),
                    "type_filling": filling_mode,
                }
            )
            if result is None:
                continue  # _safe_order_send already logged mt5.last_error() for this case
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                tickets[side] = result.order
            else:
                # THIS is the previously-silent case: MT5 responded, but
                # rejected the order (bad stops distance, no money, requote,
                # market closed for this order type, etc.). result.retcode
                # and result.comment carry the real broker-side reason —
                # log both instead of just leaving tickets[side] as None
                # with no explanation.
                print(
                    f"  {side.upper()} {symbol} rejected — retcode={result.retcode} "
                    f"comment='{result.comment}' request_id={result.request_id}"
                )

        if tickets["buy"] is None or tickets["sell"] is None:
            # partial failure — clean up whichever side DID go through so we
            # don't leave a naked one-sided pending order
            if tickets["buy"] is not None:
                self._safe_order_send(
                    {"action": mt5.TRADE_ACTION_REMOVE, "order": tickets["buy"]}
                )
            if tickets["sell"] is not None:
                self._safe_order_send(
                    {"action": mt5.TRADE_ACTION_REMOVE, "order": tickets["sell"]}
                )
            return self._no(
                f"Order send failed — buy={tickets['buy']} sell={tickets['sell']}, rolled back"
            )

        return {
            "signal": "straddle",
            "buy_stop": buy_stop,
            "sell_stop": sell_stop,
            "lot_size": lots,
            "cancel_at": expiration.isoformat(),
            "reason": f"Straddle placed | buy={buy_stop} sell={sell_stop} | lots={lots}",
        }

    def _cancel_deadline(self, cancel_hour: int) -> datetime.datetime:
        now = datetime.datetime.now(datetime.timezone.utc)
        deadline = now.replace(hour=cancel_hour, minute=0, second=0, microsecond=0)
        if deadline <= now:
            deadline += datetime.timedelta(days=1)
        return deadline

    # ---------------------------------------------------------------- OCO / cleanup

    def manage_pending_orders(self, symbol: str) -> str:
        """
        Call on every poll while a straddle might be pending. Reads
        positions and orders straight from MT5:
          1. OCO — if a position now exists, cancel whichever pending order
             is still sitting there (the untriggered side).
          2. Timeout — each pending order carries its own MT5 expiration
             (set at send-time in check_and_place); if MT5 hasn't already
             expired it, cancel it explicitly past that deadline.
        """
        pending = self._get_pending_orders(symbol)
        if pending["buy"] is None and pending["sell"] is None:
            return "No pending straddle"

        pos = self._get_position(symbol)
        if pos is not None:
            leftover = (
                pending["sell"] if pos.type == mt5.ORDER_TYPE_BUY else pending["buy"]
            )
            if leftover is not None:
                self._safe_order_send(
                    {"action": mt5.TRADE_ACTION_REMOVE, "order": leftover.ticket}
                )
            bias = "buy" if pos.type == mt5.ORDER_TYPE_BUY else "sell"
            return f"{bias.upper()} filled — opposite order cancelled"

        now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
        for order in (pending["buy"], pending["sell"]):
            if (
                order is not None
                and order.time_expiration
                and now_ts >= order.time_expiration
            ):
                self._safe_order_send(
                    {"action": mt5.TRADE_ACTION_REMOVE, "order": order.ticket}
                )
        remaining = self._get_pending_orders(symbol)
        if remaining["buy"] is None and remaining["sell"] is None:
            return "Neither side filled — straddle cancelled"
        return "Pending"

    # ---------------------------------------------------------------- trade management

    def _best_price_since_entry(self, symbol: str, position) -> float:
        """No best-price is stored anywhere — re-derive it from actual M15
        history between the position's open time and now, every call."""
        entry_time = datetime.datetime.fromtimestamp(
            position.time, tz=datetime.timezone.utc
        )
        bars = mt5.copy_rates_range(
            symbol,
            mt5.TIMEFRAME_M15,
            entry_time,
            datetime.datetime.now(datetime.timezone.utc),
        )
        if bars is None or len(bars) == 0:
            return position.price_open
        if position.type == mt5.POSITION_TYPE_BUY:
            return max(bar["high"] for bar in bars)
        return min(bar["low"] for bar in bars)

    def _next_trigger_deadline(
        self, symbol: str, fill_time: datetime.datetime
    ) -> datetime.datetime:
        """1 hour before the NEXT occurrence of this symbol's own trigger
        hour after `fill_time` — capped so it can never land on or after
        Saturday. Fully deterministic from fill_time and SYMBOL_CONFIG —
        no MT5 call needed, no state stored.

        Without the cap, a naive version of this would push a Friday fill's
        deadline all the way to Monday's trigger, letting the position ride
        through the entire weekend and take on gap risk — exactly what the
        monthly bot's Friday hard-close rule exists to avoid. So instead:
        whatever the normal next-trigger math computes, if it would land on
        Saturday/Sunday, the deadline is capped at this week's Friday
        WEEKLY_CLOSE_HOUR instead. A Friday fill gets compressed into
        however many hours remain before that cutoff, however early or
        late in the day it filled — it never gets the usual ~23h runway.
        """
        trigger_hour = SYMBOL_CONFIG[symbol]["trigger_hour"]
        trigger_minute = SYMBOL_CONFIG[symbol].get("trigger_minute", 0)
        today_trigger = fill_time.replace(
            hour=trigger_hour, minute=trigger_minute, second=0, microsecond=0
        )
        next_trigger = (
            today_trigger + datetime.timedelta(days=1)
            if today_trigger <= fill_time
            else today_trigger
        )
        deadline = next_trigger - datetime.timedelta(hours=DEADLINE_BUFFER_HOURS)

        days_until_friday = (4 - fill_time.weekday()) % 7  # Monday=0 ... Friday=4
        friday_close = (fill_time + datetime.timedelta(days=days_until_friday)).replace(
            hour=WEEKLY_CLOSE_HOUR, minute=0, second=0, microsecond=0
        )
        if (
            friday_close < fill_time
        ):  # shouldn't happen given trigger hours are all < 20, but guard anyway
            friday_close += datetime.timedelta(days=7)

        if deadline.weekday() >= 5 or deadline > friday_close:
            return friday_close
        return deadline

    def _close_position_at_market(self, symbol: str, position) -> bool:
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return False
        is_buy = position.type == mt5.POSITION_TYPE_BUY
        result = self._safe_order_send(
            {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": position.volume,
                "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                "position": position.ticket,
                "price": tick.bid if is_buy else tick.ask,
                "deviation": 10,
                "magic": MAGIC,
                "comment": "straddle_deadline_close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": self._filling_mode(symbol),
            }
        )
        if result is not None and result.retcode != mt5.TRADE_RETCODE_DONE:
            print(
                f"  Close {symbol} rejected — retcode={result.retcode} comment='{result.comment}'"
            )
        return result is not None and result.retcode == mt5.TRADE_RETCODE_DONE

    def manage_open_trade(self, symbol: str) -> str:
        """
        Call on every M15 bar close while a position is open. Position
        details (entry, current SL), whether breakeven has already been
        applied, and the best price reached are all re-derived from MT5 —
        nothing is stored between calls. No fixed TP — five alternatives
        (fixed TP, tighter SL, wider offset, ATR-scaled sizing, partial
        profit-taking) were all backtested and all underperformed this
        plain breakeven+trail version, on every pair tested.

        Priority:
          0. Past this trade's adaptive deadline (1h before this symbol's
             next trigger) → close at market, no exception. Guarantees the
             symbol is always clear for its next entry — see
             _next_trigger_deadline() and the module docstring for why
             this replaced a flat 24h cap. For trades already past
             breakeven when the deadline hits, this locks in the current
             profit rather than cutting a loss.
          1. Price reaches be_trigger → move SL to breakeven
          2. After BE                 → trail SL tracking best price
        """
        pos = self._get_position(symbol)
        if pos is None:
            return "No open trade"

        entry_time = datetime.datetime.fromtimestamp(pos.time, tz=datetime.timezone.utc)
        deadline = self._next_trigger_deadline(symbol, entry_time)
        if datetime.datetime.now(datetime.timezone.utc) >= deadline:
            ok = self._close_position_at_market(symbol, pos)
            return (
                "Past adaptive deadline — closed at market"
                if ok
                else "Deadline close FAILED"
            )

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return "No tick data"

        cfg = SYMBOL_CONFIG[symbol]
        pip = _pip(symbol)
        is_buy = pos.type == mt5.POSITION_TYPE_BUY
        entry = pos.price_open
        be_trigger = cfg["be_trigger"] * pip
        trail_dist = cfg["trail"] * pip
        current_price = tick.bid if is_buy else tick.ask

        # infer be_done from current SL vs entry — the only source of truth,
        # re-derived every call rather than tracked anywhere
        be_done = (pos.sl >= entry) if is_buy else (pos.sl <= entry and pos.sl > 0)

        favorable_move = (current_price - entry) if is_buy else (entry - current_price)

        # 1. BE trigger
        if not be_done and favorable_move >= be_trigger:
            new_sl = _round_price(entry, symbol)
            ok = self._modify_sl(symbol, pos, new_sl)
            return f"BE -> SL {new_sl}" if ok else "BE modify failed"

        # 2. Trail after BE
        if be_done:
            best_price = self._best_price_since_entry(symbol, pos)
            if is_buy:
                new_sl = _round_price(best_price - trail_dist, symbol)
                if new_sl > pos.sl:
                    ok = self._modify_sl(symbol, pos, new_sl)
                    return f"Trail -> SL {new_sl}" if ok else "Trail failed"
            else:
                new_sl = _round_price(best_price + trail_dist, symbol)
                if new_sl < pos.sl:
                    ok = self._modify_sl(symbol, pos, new_sl)
                    return f"Trail -> SL {new_sl}" if ok else "Trail failed"

        return "Holding"

    def _modify_sl(self, symbol: str, pos, new_sl: float) -> bool:
        result = self._safe_order_send(
            {
                "action": mt5.TRADE_ACTION_SLTP,
                "symbol": symbol,
                "position": pos.ticket,
                "sl": new_sl,
                "tp": pos.tp,
            }
        )
        if result is not None and result.retcode != mt5.TRADE_RETCODE_DONE:
            print(
                f"  SL modify {symbol} rejected — retcode={result.retcode} comment='{result.comment}'"
            )
        return result is not None and result.retcode == mt5.TRADE_RETCODE_DONE

    # ---------------------------------------------------------------- reporting

    def get_performance_summary(
        self, symbol: Optional[str] = None, lookback_days: int = 90
    ) -> Dict[str, Any]:
        """Reads win rate and streak status straight from MT5 deal history —
        nothing tracked separately, so this is always consistent with what
        actually happened on the account, including trades placed manually
        or by a previous, now-dead process."""
        symbols = [symbol] if symbol else self.traded_symbols
        since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=lookback_days
        )
        all_closes = []
        for sym in symbols:
            deals = (
                mt5.history_deals_get(
                    since,
                    datetime.datetime.now(datetime.timezone.utc),
                    group=f"*{sym}*",
                )
                or ()
            )
            all_closes.extend(
                d for d in deals if d.magic == MAGIC and d.entry == mt5.DEAL_ENTRY_OUT
            )

        if not all_closes:
            return {"trades": 0, "status": "No trades yet"}

        wins = sum(1 for d in all_closes if d.profit > 0)
        summary: Dict[str, Any] = {
            "total_trades": len(all_closes),
            "win_rate": f"{wins/len(all_closes)*100:.1f}%",
            "status": "PAUSED (circuit breaker)" if self._is_paused() else "ACTIVE",
        }
        if symbol:
            streak, _ = self._consecutive_losses(symbol)
            summary["current_loss_streak"] = streak
        return summary

    # ---------------------------------------------------------------- util

    def _no(self, reason: str) -> Dict[str, Any]:
        return {
            "signal": None,
            "buy_stop": None,
            "sell_stop": None,
            "lot_size": None,
            "cancel_at": None,
            "reason": reason,
        }

    def __repr__(self) -> str:
        return (
            f"StraddleStrategy("
            f"risk={self.risk_pct}%, symbols={self.traded_symbols}, "
            f"breaker={BREAKER_MIN_SYMBOLS_FLAGGED}x{BREAKER_LOSS_STREAK}L, "
            f"adaptive_deadline=-{DEADLINE_BUFFER_HOURS}h_before_next_trigger, "
            f"stateless=True)"
        )


if __name__ == "__main__":
    s = StraddleStrategy()
    print(s)
    print()
    for sym, cfg in SYMBOL_CONFIG.items():
        enabled = sym in s.traded_symbols
        print(
            f"  {sym}: trigger={cfg['trigger_hour']}:00 UTC  "
            f"cancel={cfg['cancel_hour']}:00 UTC  "
            f"{'ENABLED' if enabled else 'disabled (GOLD_ENABLED=False)'}"
        )
