import time
from decouple import config, AutoConfig
from mt5.meter_trader_config import MetaTraderConfig
from strategies.news_spike_strategy import (
    NewsSpikeStrategy,
    NFP_SCHEDULE_UTC,
    CPI_SCHEDULE_UTC,
    FOMC_SCHEDULE_UTC,
    EARLY_ENTRY_SECONDS,
    GLOBAL_FLATTEN_RETRY_SECONDS,
    SYMBOL_CONFIG,
    _utc_now,
)
from datetime import datetime, timezone, timedelta
import os


def reload_decouple():
    KEYS = [
        "MT5_USERNAME",
        "MT5_PASSWORD",
        "MT5_SERVER",
        "MT5_USERNAME_TRIAL",
        "MT5_PASSWORD_TRIAL",
        "MT5_SERVER_TRIAL",
        "MT5_PATHWAY",
    ]
    for k in KEYS:
        os.environ.pop(k, None)
    AutoConfig._instances = {}


# ---------------------------------------------------------------------------
# Dynamic polling (added 2026-09-04, simplified 2026-09-12)
# ---------------------------------------------------------------------------
# news_spike_strategy.py's entry window is only EARLY_ENTRY_SECONDS (now
# 2s, widened from 1s on 2026-09-16 -- see that file's own CHANGE LOG)
# wide. At a flat 30s cadence, a poll cycle can step clean over that
# window without ever checking inside it, silently losing the event.
#
# Tight 1-second polling runs CONTINUOUSLY from TIGHT_BAND_MINUTES before
# each real release through max_hold_seconds + GLOBAL_FLATTEN_RETRY_
# SECONDS after it — one unbroken stretch, not a pre-event band that
# drops back to 30s at release and a separate post-event band. Per
# explicit decision: everything (entry, the release-anchored close, and
# the portfolio-wide flatten) should collapse to react promptly around
# ONE T+60s deadline, not be spread across a longer, separate post-event
# window.
TIGHT_POLL_SECONDS = 1
NORMAL_POLL_SECONDS = 30
TIGHT_BAND_MINUTES = 2  # pre-event tight-polling lead time


def _real_release_times() -> list[datetime]:
    """All real (not stored-early) release times across all three
    calendars, in one flat list. Shared by both the pre-event and
    post-event tight-polling checks below so they always agree on what
    "real release" means -- same EARLY_ENTRY_SECONDS constant imported
    from news_spike_strategy.py, no duplicated/guessed offset here."""
    return [
        e + timedelta(seconds=EARLY_ENTRY_SECONDS)
        for e in (NFP_SCHEDULE_UTC + CPI_SCHEDULE_UTC + FOMC_SCHEDULE_UTC)
    ]


def _seconds_to_next_event(now: datetime) -> float:
    """Seconds until the NEXT UPCOMING scheduled release across all three
    calendars — forward-looking only. Once `now` passes a release time,
    that event stops counting toward THIS function; whether tight polling
    should still apply because we're in the POST-event stretch is handled
    separately by _in_post_event_tight_window() below. Used ONLY to
    choose polling cadence — no trading/entry logic depends on this
    function; that logic lives entirely in news_spike_strategy.py itself."""
    future = [e for e in _real_release_times() if e > now]
    if not future:
        return float("inf")
    return (min(future) - now).total_seconds()


def _in_post_event_tight_window(now: datetime) -> bool:
    """True if `now` falls within [real_release, real_release +
    max_hold_seconds + GLOBAL_FLATTEN_RETRY_SECONDS] for ANY past event
    — i.e. we're still inside the event's own hold period or the short
    retry margin right after its close deadline, and should stay on
    tight polling rather than reverting to normal cadence early. Uses
    XAUUSDm's max_hold_seconds as the reference hold time (the only
    symbol configured in news_spike_strategy.py)."""
    hold_seconds = SYMBOL_CONFIG.get("XAUUSDm", {}).get("max_hold_seconds", 60.0)
    post_event_span = timedelta(seconds=hold_seconds + GLOBAL_FLATTEN_RETRY_SECONDS)
    for real_release in _real_release_times():
        if real_release <= now <= real_release + post_event_span:
            return True
    return False


def sleep_until_next_tick(now: datetime, interval: int):
    """Sleeps until the next interval-aligned second mark (:00/:01/:02...
    for interval=1, :00/:30 for interval=30), not just 'interval seconds
    from whenever this was called' — keeps cycles clock-aligned rather
    than drifting."""
    seconds_to_wait = interval - (now.second % interval)
    if seconds_to_wait <= 0:
        seconds_to_wait = interval
    time.sleep(seconds_to_wait)


reload_decouple()

LIVE = False  # NOT backtested at all for most symbols — see
# news_spike_strategy.py's module docstring "VALIDATED EVIDENCE" section.
# Demo only.

# Fourth standalone process, own magic number, no shared loop with
# straddle_strategy.py, news_confirm_strategy.py's own confirm mechanic,
# or news_reload_strategy.py -- EXCEPT for check_global_flatten() below,
# which is a deliberate, narrow, explicit exception: see
# news_spike_strategy.py's CHANGE LOG for why the portfolio-wide flatten
# intentionally crosses strategy boundaries.


def main():

    # ── MT5 ──────────────────────────────────────────────────────────────
    mt5_config = MetaTraderConfig()
    mt5_settings = {
        "username": config("MT5_USERNAME" if LIVE else "MT5_USERNAME_TRIAL"),
        "password": config("MT5_PASSWORD" if LIVE else "MT5_PASSWORD_TRIAL"),
        "server": config("MT5_SERVER" if LIVE else "MT5_SERVER_TRIAL"),
        "mt5_pathway": config("MT5_PATHWAY"),
    }

    print(f"Mode: {'LIVE' if LIVE else 'DEMO'}{mt5_settings}")

    if not mt5_config.start_mt5(mt5_settings):
        print("MT5 failed to start")
        return

    # ── Strategy ─────────────────────────────────────────────────────────
    strategy = NewsSpikeStrategy(initial_balance=90.0)

    print(f"{strategy}\n")

    # ── Main loop ─────────────────────────────────────────────────────────
    while True:
        # ONE timestamp per cycle, passed into every symbol's check this
        # cycle. Fixes the gold-traded/silver-didn't gap: previously each
        # check_and_place(symbol) call fetched its own fresh `now`
        # internally, so a slower symbol earlier in the loop (real
        # order_send() round-trips, slower during a volatile print) could
        # push the clock far enough that a later symbol in the SAME cycle,
        # for the SAME event, saw its window already closed. All symbols
        # checked this cycle are now judged against the exact same
        # instant, regardless of loop position or how long earlier symbols
        # took.
        #
        # REAL BUG FIXED 2026-09-16: this used to be
        # datetime.now(timezone.utc) -- the RAW, uncorrected local clock.
        # manage_open_trade() (called per-symbol below) always uses
        # news_spike_strategy.py's own _utc_now(), which applies the
        # measured local-vs-broker clock skew correction (see that file's
        # "Server clock sync" section -- built specifically after the
        # 2026-09-14 incident where a ~64s-behind VPS clock caused live
        # order rejections). Every OTHER call in this loop
        # (check_and_place, check_global_flatten, and event_countdown)
        # was silently using the UNCORRECTED clock instead, defeating
        # that protection for everything except manage_open_trade().
        # Observed live: the same ticket's deadline countdown showed 20s
        # remaining via event_countdown() (uncorrected clock) and 0s
        # remaining via manage_open_trade() (corrected clock) in the SAME
        # cycle -- a ~20s real skew, same class of drift as 2026-09-14,
        # just a different magnitude. Fixed by using the SAME
        # skew-corrected _utc_now() everywhere in this loop, matching
        # manage_open_trade()'s clock exactly.
        now = _utc_now()

        print("=" * 55)
        print(f"{now.strftime('%A %d %B %Y — %H:%M:%S UTC')}")
        print("=" * 55)

        for symbol in strategy.traded_symbols:
            print(f"\n{symbol}")

            pending_status = strategy.manage_pending_orders(symbol)
            if pending_status not in ("No pending straddle", "Pending"):
                print(f"   {pending_status}")

            # has_own_open_trade() filters by this strategy's own MAGIC
            # before answering, and checks for ANY number of open
            # positions, not just one — see news_spike_strategy.py
            # docstring for both the shared-symbol bug this originally
            # fixed and the dual-fill-aware update.
            if strategy.has_own_open_trade(symbol):
                status = strategy.manage_open_trade(symbol)
                print(f"   {status}")
                continue

            if pending_status == "Pending":
                print(f"   {pending_status}")
                continue

            signal = strategy.check_and_place(symbol, now)
            print(f"   {signal['reason']}")

        # ── Event countdown (informational only) ────────────────────────
        # Purely a display of the same release+60s deadline that
        # manage_open_trade()/check_global_flatten() already enforce on
        # their own — printed once per cycle regardless of whether a
        # trade actually exists, so the countdown is visible even if
        # nothing filled this event. Does NOT affect trading logic.
        countdown = strategy.event_countdown(now)
        if countdown:
            print(f"\n{countdown}")

        # ── Portfolio-wide global flatten ────────────────────────────────
        # Called ONCE PER CYCLE, OUTSIDE the per-symbol loop above --
        # unlike manage_pending_orders()/manage_open_trade()/
        # check_and_place(), which are per-symbol. Targets a SINGLE
        # deadline (real_release + max_hold_seconds, i.e. T+60s) with
        # only a short GLOBAL_FLATTEN_RETRY_SECONDS safety margin for
        # retries -- not a separate, longer post-event window. Closes
        # EVERY open position and cancels EVERY pending order on the
        # WHOLE ACCOUNT (any symbol, any magic) at that moment. No-ops
        # entirely outside that narrow band. See news_spike_strategy.py's
        # CHANGE LOG for the full rationale -- this deliberately touches
        # positions opened by OTHER strategies (e.g. straddle_strategy.py,
        # MAGIC=20260716), a narrow, explicit exception to this project's
        # usual strategy-isolation pattern.
        flatten_status = strategy.check_global_flatten(now)
        if flatten_status:
            print(f"\n{flatten_status}")

        print("\nCycle done")

        gap_seconds = _seconds_to_next_event(now)
        post_event = _in_post_event_tight_window(now)
        interval = (
            TIGHT_POLL_SECONDS
            if (gap_seconds <= TIGHT_BAND_MINUTES * 60 or post_event)
            else NORMAL_POLL_SECONDS
        )
        sleep_until_next_tick(datetime.now(timezone.utc), interval)


if __name__ == "__main__":
    main()