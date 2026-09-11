"""
News Spike Strategy — Live Version (stateless)
================================================
Edge   : Straddle-at-release entry (same OCO pattern as
         news_reload_strategy.py), but radically simplified on the exit
         side: NO take-profit, NO reload chain. Once a position opens, it
         gets a HARD 1-MINUTE force-close — win or lose, whatever it's
         worth at that moment, it closes. After that single close (or an
         SL hit, if that happens first), this specific event is DONE —
         no re-entry, no matter how much time is left in the trigger
         window.

         The idea: capture only the very first, immediate reaction to a
         scheduled release, then get out before a possible reversal —
         rather than staying in the market and riding it (which hurt
         other mechanics tested this session: the 2026-03-06 NFP
         reversal, and the 2024-12-06 NFP bar that spiked $12+ and then
         round-tripped almost all the way back to flat within 30 min).
         One shot per event, then done.

Entry  : Buy-stop + sell-stop straddle, placed in a narrow PRE-RELEASE
         window only (see "Entry window" below) — offset from the
         anchor price (per-symbol distance below). Whichever side fills
         is the trade. Covers NFP, CPI, and FOMC — each on its own
         schedule (NFP/CPI at 8:30 AM ET, FOMC at 2:00 PM ET).
SL     : Real per-symbol stop — can still trigger before the 1-minute
         mark if price moves against it fast enough. NOTE: real Sept 4
         2026 tick-level analysis (see 2026-09-08 CHANGE LOG) confirmed
         this SL is a price level, not a payment cap — a fast cascade
         can jump straight through it (measured ~$40 worst-case range
         on gold in the 300ms after one real trigger, against a nominal
         $7 SL). Sizing is now 2% — see 2026-09-11 CHANGE LOG — after a
         same-day sequence that briefly passed through 33.2143% before
         landing here. At 2%, and at this project's documented balance
         range, sizing is back near where it started the day (was 7%),
         though still not independently re-validated at this exact
         level.
Exit   : Hard 1-minute force-close, no TP, no reload. Whichever comes
         first — the SL hitting, or 60 seconds elapsing — ends the
         trade. Exit deviation is now effectively uncapped (see
         2026-09-08 CHANGE LOG) so this close cannot get rejected and
         silently retried through a volatile window.
Sizing : 2% risk on XAUUSDm (see 2026-09-11 CHANGE LOG) — the final
         value of a same-day sequence (7.0% -> 3.0% -> 33.2143% ->
         2.0%). At this project's documented balance range ($90-373),
         2% lands on the same 0.01 lot floor as several other
         percentages already tested that day. Computed once at entry —
         no reload here to grow it further.
Filter : NONE. See prior revision's docstring — the FOMC-proximity
         filter was tested and removed for THIS mechanic specifically
         (helped the reload chain, hurt the spike mechanic). Not
         re-litigated here.

Entry window : Every date in the three schedule constants below is
         stored 3 SECONDS EARLY relative to the real, source-verified
         release time (e.g. real NFP release 12:30:00 UTC is stored as
         12:29:57). This is deliberate, not a mistake — see CHANGE LOG.
         The entry window itself is only the seconds before the real
         release — `_next_event_trigger_window()` opens at the stored
         (early) time and closes hard AT the real release. If a
         straddle has not been placed by the real release moment, that
         symbol sits out the event entirely — no retry after price has
         already moved. The point of the spike edge is catching the
         FIRST move; chasing it after release defeats the purpose and
         re-exposes the entry to the same price/stops_level rejection
         risk this change exists to avoid.

*** VALIDATED EVIDENCE — XAUUSDm ONLY ***
XAUUSDm is backed by real, minute-level, control-tested backtest data
(2024-01-02 to 2025-12-05, see prior revision's docstring for the full
NFP/CPI/FOMC breakdown — 59.1%/54.5%/66.7% win rates, all beat random-
time controls). A single real tick-level event (Sept 4 2026 NFP) was
also walked through in detail — see 2026-09-08 CHANGE LOG — and
confirmed the mechanic can survive a violent, discontinuous cascade
when direction cooperates; it does NOT establish the SL reliably caps
loss when direction doesn't cooperate. Still a single event. Sizing
has moved several times today (7% -> 3% -> 33.2143% -> 2%, see CHANGE
LOG) — the validated evidence hasn't changed through any of that,
worth remembering regardless of which risk_pct is live.

*** XAGUSDm REMOVED 2026-09-11 *** — see CHANGE LOG. Only XAUUSDm is
traded by this file now.

*** COPPER (XCUUSDm) REMOVED 2026-09-08 *** — see CHANGE LOG.

*** STILL DEMO ONLY ***

CHANGE LOG (2026-09-11, schedule fixes):
  - FIXED FOMC_SCHEDULE_UTC: every literal was stored as XX:00:57, which
    is 57 SECONDS AFTER the real 2:00 PM ET release (18:00:00 / 19:00:00
    UTC), not 3 seconds before it as intended. This meant every FOMC
    entry window as previously written would have opened nearly a full
    minute after the real release and closed moments later — the bot
    would have tried to place a straddle on an already-stale anchor
    price, deep into the exact "don't chase a move that's already
    happened" scenario this file's own design exists to avoid. Corrected
    to XX:59:57 (17:59:57 / 18:59:57) to match the same 3s-early pattern
    already correct on NFP_SCHEDULE_UTC/CPI_SCHEDULE_UTC. NFP and CPI
    literals were checked against the same logic and found correct — this
    bug was isolated to FOMC. All FOMC dates (2026 and 2027) independently
    re-verified against the Federal Reserve's own published calendar —
    dates themselves were correct, only the time-of-day literal was wrong.
  - Removed the stale 2026-09-09 NFP test entry (past).
  - Moved a misfiled entry from NFP_SCHEDULE_UTC to CPI_SCHEDULE_UTC and
    corrected its time: it was stored as an NFP date on 2026-09-11 at
    14:29:57, which matches neither a real NFP date nor the correct CPI
    time. BLS confirms the real CPI release is 2026-09-11, 8:30 AM ET =
    12:30:00 UTC — now stored correctly as 12:29:57 under CPI_SCHEDULE_UTC.
    If this time has already passed by deployment, it's inert (window
    simply never opens) and can be dropped on the next cleanup pass.

CHANGE LOG (2026-09-11):
  - XAUUSDm risk_pct changed AGAIN, 33.2143 -> 2.0, per Joseph's explicit
    instruction — the final value of a same-day sequence (7.0 -> 3.0 ->
    33.2143 -> 2.0). At this project's documented balance range
    ($90-373), 2% lands on the same 0.01 lot floor as several other
    percentages tested earlier the same day (2%, 3.34%, 10% all rounded
    to the same floor trade at $90) — so this mainly starts to
    differentiate from those once balance clears roughly $700. Not
    backtested at this specific level either, same as every other
    risk_pct value set today — all explicit instructions, not backtest-
    driven changes.
  - REMOVED XAGUSDm from SYMBOL_CONFIG entirely, per Joseph's explicit
    instruction. XAGUSDm's offset/SL had never been independently
    backtested (uncalibrated, scaled off gold by relative price level
    only — see now-removed docstring section) and its contract size
    was never confirmed against this account's real
    symbol_info("XAGUSDm").trade_contract_size. Only XAUUSDm remains.
  - XAUUSDm risk_pct changed 7.0 -> 3.0, per Joseph's explicit
    instruction (superseded later same day, see below).
  - XAUUSDm offset REVERTED from 4.0 back to 3.0, per Joseph's explicit
    instruction — the original value from the 2026-08-07 handoff's
    documented mechanic, undoing the 3.0 -> 3.5 -> 4.0 widening from
    2026-09-04. SL left at 7.0 (not reverted — not asked for). Not
    re-validated against fresh data at this offset/current price level;
    this is a direct instruction, not a backtest-driven change.
  - XAUUSDm risk_pct changed AGAIN, 3.0 -> 33.2143, per Joseph's
    explicit instruction — restoring the full original total budget
    (previously split 14.04% XAU / 14.04% XAG / 5.26% XCU before any
    symbol removals) now entirely onto XAUUSDm alone, since it's the
    only symbol left in SYMBOL_CONFIG. Flagged at the time: this is an
    11x jump from the 3.0 set earlier the same day, on a symbol whose
    SL is confirmed (Sept 4 2026 tick data) to be a price level a fast
    cascade can jump straight through, not an enforced payment cap —
    sizing is no longer doing the tail-risk-management job the 2026-
    09-08 change log describes flat 7%/3% as doing. Not backtested at
    this risk level; explicit instruction, not a backtest-driven change.

CHANGE LOG (2026-09-08):
  - REMOVED XCUUSDm (copper) from SYMBOL_CONFIG entirely. Reasoning:
    reviewing Sept 4 2026 NFP on real tick data alongside gold/silver
    showed copper's confirmed contract spec (contract_size=1.0,
    tick_value=0.01, tick_size=0.01 -> only $1 of P&L per $1 price move
    per 1.0 lot) is drastically lower leverage than gold/silver, which
    forced the sizing formula toward an unusually large lot count
    (~0.67 lots at 7% risk) just to hit the same target risk in
    dollars. Combined with copper's volume_min/max/step still being
    unconfirmed against the broker (flagged since 2026-08-30 and never
    resolved), this symbol's real live behavior was the least trusted
    of the three and was dropped rather than carried further
    unvalidated.
  - risk_pct changed from differentiated (XAU 14.04% / XAG 14.04%) to
    FLAT 7% on both symbols then live (XAU/XAG). Total budget: ~33.2%
    (7 symbols, original) -> ~28.08% (XAU+XAG only, pre-copper-removal)
    -> 14% (flat 7% x 2, this change). Reasoning: reviewing real Sept 4
    2026 tick data showed the SL is a price level, not an enforced
    payment cap — a fast cascade can jump straight through it (measured
    ~$40 worst-case range on gold in the 300ms after one real trigger,
    against a nominal $7 SL). Flat 7% doesn't fix that mechanism, it
    just keeps the tail outcome proportionally survivable rather than
    changing the underlying risk/reward shape. A deeper fix (sizing
    lot off realistic worst-case distance instead of nominal SL) was
    discussed and deliberately deferred, not done here. NOTE: this
    sizing-as-tail-management logic was effectively undone by the
    2026-09-11 change above, which returns to the full 33.2143% total
    now concentrated on one symbol instead of split across two/three.
  - `_close_position_at_market()` and `_flatten_symbol()` deviation
    changed from 10 to a large effectively-uncapped value
    (`EXIT_DEVIATION`). Root cause this fixes: at deviation=10, a
    force-close or pre-event flatten attempted during real volatility
    (measured real spread of 340pts on a live release bar; separately,
    a real ~$40 tick-level price cascade on Sept 4) could get REJECTED
    by the broker rather than filled-with-slippage, and
    manage_open_trade() would just log "will retry next cycle" and try
    again — meaning the "hard 60-second" exit could silently drift to
    65s, 90s, or longer while it kept retrying through the worst of
    the move, exactly the opposite of what the hard exit exists to
    guarantee. deviation is a MAXIMUM tolerance, not a target — this
    change costs nothing on calm exits (still fills at the best
    available price) and only changes behavior on the volatile ones,
    which is exactly when a guaranteed exit matters most.
  - 5s-early entry window (EARLY_ENTRY_SECONDS) considered for a
    narrowing to 3s, explicitly REJECTED after discussion at the time
    (superseded 2026-09-09, see below — later changed to 3s anyway).
  - Test NFP entry (2026-09-08, marked "# test" in NFP_SCHEDULE_UTC)
    left in place, untouched — not part of this change set.

CHANGE LOG (2026-09-09):
  - EARLY_ENTRY_SECONDS changed 5.0 -> 3.0. Does NOT reduce slippage —
    the mechanism that causes slippage happens in the seconds AFTER the
    real release triggers the order, not in how many seconds early the
    resting order was placed. A narrower window only raises the odds a
    poll cycle steps over it and misses the entry entirely. Joseph's
    explicit call.
  - All schedule literals re-shifted from 5s-early to 3s-early to match
    (root cause of a real live bug found this day: literals were still
    5s-early after EARLY_ENTRY_SECONDS was changed to 3.0, so orders
    kept firing at the old 5s-early mark instead of the intended 3s).

CHANGE LOG (2026-09-04, risk_pct redistribution):
  - risk_pct redistributed across just XAUUSDm/XAGUSDm/XCUUSDm, preserving
    the original 33.2143% total budget (previously spread across 7
    symbols) rather than leaving it reduced to 20.2857%. Same relative
    weighting kept: XAUUSDm=XAGUSDm=14.04% each, XCUUSDm=5.26% (XCU's
    share stayed proportionally smaller than XAU/XAG's, same ratio as
    when it matched the FX-pair share — not equalized up just because
    the FX pairs are gone). SUPERSEDED 2026-09-08 — see above (flat 7%,
    copper removed).

CHANGE LOG (2026-09-04, gold/silver/copper only):
  - FINAL DECISION — the four FX pairs (EURUSDm, GBPUSDm, USDJPYm,
    USDCADm) are REMOVED from SYMBOL_CONFIG entirely. This strategy now
    trades ONLY XAUUSDm, XAGUSDm, XCUUSDm. Total budget across the
    remaining 3 symbols was initially left at 8.5714% + 8.5714% + 3.2143%
    = 20.2857% (down from 33.2143% across 7 symbols) — SUPERSEDED the
    same day: risk_pct was then redistributed so the original 33.2143%
    total is preserved, concentrated on just these 3 symbols instead of
    being left smaller (see risk_pct redistribution entry below).
  - The FX pairs' offset/SL tuning history (12p/20p initial -> 14p/24p
    widened -> 5p/8p narrowed, all same day) is now moot for live
    trading but left in this changelog for the record, since it's real
    evidence about how sensitive this mechanic is to offset/SL choice
    on FX symbols, in case they're ever re-added.

CHANGE LOG (2026-09-04, entry-window/polling changes):
  - All three schedule constants (NFP/CPI/FOMC_SCHEDULE_UTC) shifted 5
    seconds EARLY relative to the real, source-verified release times
    (later changed to 3s, see 2026-09-09 above). Root cause this fixes:
    live rejections at exact release time (XAUUSDm SELL retcode=10015
    "Invalid price", XAGUSDm SELL retcode=10006) traced to two
    compounding release-moment effects — (1) the anchor price used to
    compute offset/SL is fetched a beat before order_send() reaches the
    broker, and during the NFP spike price can move enough in that gap
    that the calculated stop is no longer where it was meant to be
    relative to current price, and (2) brokers (Exness included) widen
    trade_stops_level dynamically the instant high-impact volatility
    hits, shrinking the minimum-distance room a stop order needs. Both
    effects are real only AT/AFTER the real release moment, not before
    it — placing the pending straddle as a RESTING order shortly before
    release sidesteps both, since stops-level and price validation
    happen at placement time, not continuously against a resting order.
  - `_next_event_trigger_window()` narrowed to a HARD pre-release-only
    window: opens at the stored (early) release_time, closes AT the
    real release moment. Previously stayed open for a full 5 minutes
    after release to allow retries; that retry window is deliberately
    removed. Rationale: the spike edge is about catching the FIRST
    move — once real release has passed and price has already moved,
    there's no move left to catch, and retrying would just resubmit
    into the exact volatile conditions this change exists to avoid.
  - `_next_flatten_window()` default `lead_minutes` changed 5.0 -> 10.0
    — longer pre-event runway to guarantee nothing from either this
    strategy or straddle_strategy.py is still open going into an event.
  - Requires ~1s main-loop polling to be reliable — a narrow entry
    window with slower polling (e.g. the old 30s cadence) risks a poll
    cycle stepping over the entire window without ever checking inside
    it, silently reverting to no entry for that event. Not enforced in
    this file (that's main_news_spike.py's job) — noted here since it's
    a real dependency of this change actually working.

CHANGE LOG (2026-08-30):
  - Added XCUUSDm (copper), risk_pct=3.2143% (same share as each FX
    pair). Offset=12.0 / SL=20.0 (price units, digits=2) — carried over
    from the single-event NFP backtest, not independently fitted to
    copper's own volatility. Total budget across all 7 symbols is now
    33.2143% (was 30%). REMOVED 2026-09-08 — see CHANGE LOG above.

CHANGE LOG (2026-08-12):
  - Re-added the unconditional final flatten check immediately before
    order placement in check_and_place() — this had been dropped during
    a revert and is the actual fix for the EURUSDm-not-closing-before-
    NFP bug reported live. The window-based flatten (_next_flatten_window,
    lead_minutes before release) depends on the main loop calling
    check_and_place() every cycle in that window; if the main loop's
    per-symbol routing sends a symbol to manage_open_trade() instead
    (observed live 2026-08-12 on EURUSDm, one minute before release, no
    position ever placed by this bot so manage_open_trade() correctly
    reported "No open trade" every time — meaning check_and_place(),
    and therefore the flatten logic, was never even called for it), the
    window-based flatten silently never runs for that symbol. The final
    check at the last possible moment before order placement is a second,
    independent layer that doesn't depend on the window timing lining up
    with when the main loop happens to call this method — it runs
    unconditionally every time check_and_place() reaches that point,
    which happens across many cycles inside the whole trigger window,
    not just the exact cycle the window check catches.
  - Trimmed to 6 symbols (XAUUSDm, XAGUSDm, EURUSDm, GBPUSDm, USDJPYm,
    USDCADm) per Joseph's request — USDCHFm/AUDUSDm/NZDUSDm removed.
  - FX offset/SL settled at 12/20 pips (reverted from an intermediate
    8/15p tighten that was catching fakeouts on slower releases).
  - Flatten lead time set to 5 minutes (down from an initial 10) — since
    revised again to 10 minutes, see 2026-09-04 entry above.
  - Added _next_flatten_window()/_flatten_symbol(): closes ANY position
    and cancels ANY pending order on a symbol, regardless of magic
    number, in the lead-up to a scheduled event — deliberate, simpler
    alternative to hedging-mode detection/blocking. This strategy shares
    EURUSDm/GBPUSDm/USDJPYm/XAUUSDm with straddle_strategy.py
    (MAGIC=20260716); flattening the symbol before entry means there's
    nothing left for a netting-mode account to merge with.
  - Lot sizing rewritten in a prior pass to pull live from
    mt5.symbol_info(symbol).trade_tick_value/trade_tick_size instead of
    a static pip-value guess table, which had been wrong by orders of
    magnitude for several symbols (2026-08-11: USDJPYm computed 42
    lots, XAGUSDm 17.62 lots, off hand-guessed constants).

CHANGE LOG (2026-08-11, earlier same day):
  - Added XAGUSDm + 7 major USD pairs (later trimmed to 4, see above).
  - Added `decimals` to SYMBOL_CONFIG per symbol and generalized
    `_round_price()` off it instead of a single XAUUSDm special-case.
  - Added automatic hedging-mode check at import time (informational;
    superseded operationally by the flatten-before-entry approach above,
    but left in place as a diagnostic).

CHANGE LOG (prior revision):
  - Expanded from NFP-only to three event types: NFP, CPI, FOMC - each
    on its own real, source-verified schedule.
  - Removed the FOMC-proximity filter entirely - helped the reload
    chain, hurt this mechanic when tested directly.

CHANGE LOG (initial):
  - Initial build. Fourth standalone strategy, own MAGIC number, own
    process — same pattern as straddle_strategy.py, news_confirm_
    strategy.py, and news_reload_strategy.py never sharing a loop.
  - Includes the "already traded this event" guard from the start,
    given a live bug found in news_reload_strategy.py on 2026-08-07.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Tuple

import MetaTrader5 as mt5

# ---------------------------------------------------------------------------
# Event calendars — MANUALLY MAINTAINED. Three separate schedules, one per
# event type, each source-verified (BLS for NFP/CPI, Federal Reserve for
# FOMC) — do not assume a fixed-rule pattern for any of them; the 2025
# government shutdown proved that assumption can silently break.
#
# *** All timestamps below are stored 3 SECONDS EARLY relative to the real
# release time (e.g. real NFP release 12:30:00 UTC -> stored as 12:29:57).
# The real release moment for any entry here is
# `release_time + EARLY_ENTRY_SECONDS` (3 seconds).
#
# IMPORTANT: these are LITERAL, hand-typed timestamps — NOT computed from
# EARLY_ENTRY_SECONDS. The entry window OPENS the moment `now >= this
# value`. EARLY_ENTRY_SECONDS only controls the window's CLOSE and the
# order expiration calc. If EARLY_ENTRY_SECONDS is ever changed again,
# every literal below must be manually re-shifted to match, or the
# window-open time and the code's internal "real release" time drift
# out of sync with each other (found live 2026-09-09: literals were
# still 5s-early after EARLY_ENTRY_SECONDS was changed to 3.0, so orders
# kept firing at the old 5s-early mark instead of the intended 3s. A
# second instance of this same class of bug was found 2026-09-11 in
# FOMC_SCHEDULE_UTC specifically — see CHANGE LOG).
# ---------------------------------------------------------------------------

NFP_SCHEDULE_UTC: List[datetime.datetime] = [
    datetime.datetime(2026, 10, 2, 12, 29, 57, tzinfo=datetime.timezone.utc),
    datetime.datetime(2026, 11, 6, 13, 29, 57, tzinfo=datetime.timezone.utc),
    datetime.datetime(2026, 12, 4, 13, 29, 57, tzinfo=datetime.timezone.utc),
    # Add next month's date here, 3s EARLY. DST-adjust by hand: 8:30 AM ET
    # = 12:30:00 UTC during DST (roughly Mar-Nov), 13:30:00 UTC otherwise
    # -> store as 12:29:57 / 13:29:57 respectively.
]

CPI_SCHEDULE_UTC: List[datetime.datetime] = [
    datetime.datetime(2026, 9, 11, 16, 49, 57, tzinfo=datetime.timezone.utc),
    # ^ moved here from NFP_SCHEDULE_UTC 2026-09-11 — was misfiled under NFP
    # with the wrong time (14:29:57). Confirmed via BLS: real CPI release
    # is Sept 11 2026, 8:30 AM ET = 12:30:00 UTC (DST), so 3s-early is
    # 12:29:57, not 14:29:57. If this time has already passed by the time
    # this file is deployed, it's a no-op (window will simply never open)
    # and can be dropped on the next cleanup.
    datetime.datetime(2026, 10, 14, 12, 29, 57, tzinfo=datetime.timezone.utc),
    datetime.datetime(2026, 11, 10, 13, 29, 57, tzinfo=datetime.timezone.utc),
    datetime.datetime(2026, 12, 10, 13, 29, 57, tzinfo=datetime.timezone.utc),
    # Same 8:30 AM ET / DST rule as NFP, stored 3s EARLY. Check
    # bls.gov/schedule/news_release/cpi.htm
]

FOMC_SCHEDULE_UTC: List[datetime.datetime] = [
    datetime.datetime(2026, 9, 16, 17, 59, 57, tzinfo=datetime.timezone.utc),
    datetime.datetime(2026, 10, 28, 17, 59, 57, tzinfo=datetime.timezone.utc),
    datetime.datetime(2026, 12, 9, 18, 59, 57, tzinfo=datetime.timezone.utc),
    datetime.datetime(2027, 1, 27, 18, 59, 57, tzinfo=datetime.timezone.utc),
    datetime.datetime(2027, 3, 17, 17, 59, 57, tzinfo=datetime.timezone.utc),
    datetime.datetime(2027, 4, 28, 17, 59, 57, tzinfo=datetime.timezone.utc),
    datetime.datetime(2027, 6, 9, 17, 59, 57, tzinfo=datetime.timezone.utc),
    datetime.datetime(2027, 7, 28, 17, 59, 57, tzinfo=datetime.timezone.utc),
    datetime.datetime(2027, 9, 15, 17, 59, 57, tzinfo=datetime.timezone.utc),
    datetime.datetime(2027, 10, 27, 17, 59, 57, tzinfo=datetime.timezone.utc),
    datetime.datetime(2027, 12, 8, 18, 59, 57, tzinfo=datetime.timezone.utc),
    # Decision time is 2:00 PM ET = 18:00:00 UTC during DST, 19:00:00 UTC
    # otherwise -> stored 3s EARLY as 17:59:57 / 18:59:57 respectively
    # (fixed 2026-09-11 — see CHANGE LOG; these were previously stored as
    # 18:00:57 / 19:00:57, which is 57s AFTER the real release, not 3s
    # before it).
    # All 2027 dates confirmed against the Fed's own published (tentative)
    # 2027 calendar as of 2026-09-11 — not extrapolated.
    # Add each new year's dates in one go once the Fed publishes them.
]

# Real release time = stored schedule time + this. Kept as a named constant
# so every place in the file that needs to reason about the REAL moment
# (vs. the deliberately-early stored one) references the same value.
EARLY_ENTRY_SECONDS = 3.0

# Maximum acceptable slippage (in points) on the exits THIS FILE controls
# directly (force-close, pre-event flatten) — TRADE_ACTION_DEAL requests
# only. Deliberately large/effectively-uncapped (see 2026-09-08 CHANGE
# LOG): deviation is a MAXIMUM tolerance, not a target, so a large value
# costs nothing on calm exits and only matters on volatile ones — which
# is exactly when a guaranteed exit matters most. Does NOT apply to
# entries (pending stop orders carry no deviation field in MT5) or to
# SL fills (MT5 does not support deviation on a triggered stop-loss).
EXIT_DEVIATION = 500


def _validate_calendar_freshness() -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    for name, schedule in (
        ("NFP", NFP_SCHEDULE_UTC),
        ("CPI", CPI_SCHEDULE_UTC),
        ("FOMC", FOMC_SCHEDULE_UTC),
    ):
        future = [d for d in schedule if d >= now]
        if not future:
            print(
                f"  !!! WARNING: {name}_SCHEDULE_UTC has NO upcoming dates — "
                f"update the list in news_spike_strategy.py now."
            )
        else:
            days_until_next = (min(future) - now).days
            if days_until_next > 40:
                print(
                    f"  WARNING: next {name} date is {days_until_next} days away "
                    f"({min(future)}) — double-check the list isn't stale."
                )


def _validate_hedging_mode() -> None:
    """Informational only now — the flatten-before-entry mechanism
    (_next_flatten_window / _flatten_symbol) is the operative protection
    against cross-strategy interference, not this check. Left in place
    as a diagnostic since it's cheap and still useful context."""
    acc = mt5.account_info()
    if acc is None:
        print(
            "  WARNING: mt5.account_info() unavailable — cannot verify " "hedging mode."
        )
        return
    margin_mode = getattr(acc, "margin_mode", None)
    HEDGING = getattr(mt5, "ACCOUNT_MARGIN_MODE_RETAIL_HEDGING", 2)
    if margin_mode != HEDGING:
        print(
            f"  NOTE: account is NOT in hedging mode (margin_mode={margin_mode}). "
            "Flatten-before-entry logic handles the overlap risk with "
            "straddle_strategy.py regardless, but worth knowing."
        )
    else:
        print("  Hedging mode confirmed.")


_validate_calendar_freshness()
_validate_hedging_mode()

# ---------------------------------------------------------------------------
# Per-symbol configuration
# ---------------------------------------------------------------------------
# offset/sl are in PRICE units (not pips) — already converted below so the
# entry code never has to know per-symbol pip size.
#
# Lot sizing pulls LIVE from mt5.symbol_info(symbol).trade_tick_value/
# trade_tick_size, clamped to the broker's real volume_min/max/step —
# see _base_lot(). No static pip-value guess table.
#
# decimals controls price rounding in _round_price().
#
# risk_pct: 2% on XAUUSDm as of 2026-09-11, after a same-day sequence
# (7.0 -> 3.0 -> 33.2143 -> 2.0). See CHANGE LOG — all explicit
# instructions, not backtested at any of these specific levels.

SYMBOL_CONFIG: Dict[str, Dict[str, Any]] = {
    "XAUUSDm": {
        "pip": 1.0,
        "point_size": 0.001,
        "offset": 3.0,  # $ — reverted 2026-09-11 back to the original 2024
        # backtest value (had drifted 3.0 -> 3.5 -> 4.0 over prior widenings,
        # see CHANGE LOG). Note: as a % of price this is smaller now than it
        # was on the original 2024-01-02 to 2025-12-05 validation window,
        # since gold's price level has moved since then — not re-validated
        # at today's price level.
        "sl": 7.0,  # $ — widened again from 6.0 (2026-09-04, ~17%). Real Sept 4
        # tick data showed this is a price level, not an enforced payment
        # cap — see module docstring. Left as-is; sizing (risk_pct) is the
        # lever being used to manage that risk instead, see below — though
        # as of 2026-09-11's jump to 33.2143%, that lever is no longer
        # pulling in the risk-reducing direction it was earlier the same day.
        "risk_pct": 2.0,  # set 2026-09-11 (was 33.2143 earlier same day,
        # 3.0 before that, 7.0 before that) — Joseph's explicit
        # instruction. At the ~$90-373 balance range documented
        # elsewhere in this project, this lands on the same 0.01 lot
        # floor as several other percentages tested earlier the same
        # day (2%, 3.34%, 10% all rounded to 0.01 lot at $90 balance) —
        # so this mainly starts to matter once balance grows past
        # roughly $700 (the point where 2% of balance / $7 SL clears
        # the 0.005 lot-step rounding threshold).
        "decimals": 2,
        "max_hold_seconds": 60.0,
    },
}

RISK_PCT = 2.0  # fallback default only if a symbol's config is missing risk_pct.
# History: 2026-08-12 allocation scaled from an initial 14% total to 30%
# (8.57% XAU + 8.57% XAG + 3.21%x4 FX). 2026-08-30: +3.2143% for XCUUSDm
# -> 33.2143% total across 7 symbols. 2026-09-04: FX pairs removed
# (gold/silver/copper only), risk_pct redistributed to preserve the same
# 33.2143% total across XAU/XAG/XCU. 2026-09-08: copper removed, XAU/XAG
# both set flat to 7% (14% total). 2026-09-11: XAGUSDm removed; XAUUSDm
# went 7.0 -> 3.0 -> 33.2143 -> 2.0, all same day, all explicit
# instructions — see module docstring CHANGE LOG for the full sequence.
MAGIC = 20260807  # unique to this strategy — must not collide with
# straddle_strategy.py (20260716), news_confirm_strategy.py
# (20260801), or news_reload_strategy.py (20260810)


def _round_price(price: float, symbol: str) -> float:
    decimals = SYMBOL_CONFIG.get(symbol, {}).get("decimals", 5)
    return round(price, decimals)


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


class NewsSpikeStrategy:
    """No __init__ state beyond configuration — every method queries MT5
    fresh. See module docstring for full design rationale."""

    def __init__(self, initial_balance: float = 90.0) -> None:
        self.starting_balance = initial_balance
        self.traded_symbols: List[str] = list(SYMBOL_CONFIG.keys())

    # ---------------------------------------------------------------- balance & sizing

    def _balance(self) -> float:
        acc = mt5.account_info()
        return acc.balance if acc else self.starting_balance

    def _base_lot(self, symbol: str) -> float:
        """Sizing pulled LIVE from the broker, not guessed. trade_tick_value
        / trade_tick_size gives $-per-1.0-price-unit-move per lot, already
        converted to account currency by MT5. Clamps to the symbol's real
        volume_min/volume_max/volume_step. Uses each symbol's own risk_pct
        (see SYMBOL_CONFIG) rather than a flat global RISK_PCT."""
        cfg = SYMBOL_CONFIG[symbol]
        info = mt5.symbol_info(symbol)
        if info is None or not info.trade_tick_size:
            print(f"  {symbol}: symbol_info unavailable — defaulting to 0.01 floor")
            return 0.01

        value_per_unit_per_lot = info.trade_tick_value / info.trade_tick_size
        risk_pct = cfg.get("risk_pct", RISK_PCT)
        risk_dollar = self._balance() * (risk_pct / 100.0)
        raw_lot = risk_dollar / (cfg["sl"] * value_per_unit_per_lot)

        vol_min = info.volume_min or 0.01
        vol_max = info.volume_max or raw_lot
        vol_step = info.volume_step or 0.01

        if raw_lot > vol_max:
            print(
                f"  {symbol}: target risk implies {raw_lot:.2f} lots, "
                f"clamped to broker max {vol_max:.2f} — actual $ risk on this "
                f"trade will be LESS than the {risk_pct}% target."
            )

        lot = max(vol_min, min(raw_lot, vol_max))
        lot = round(lot / vol_step) * vol_step
        return round(max(lot, vol_min), 2)

    # ---------------------------------------------------------------- MT5 reads

    def _get_tick(self, symbol: str):
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            mt5.symbol_select(symbol, True)
            tick = mt5.symbol_info_tick(symbol)
        return tick

    def _filling_mode(self, symbol: str) -> int:
        info = mt5.symbol_info(symbol)
        if info is None:
            return mt5.ORDER_FILLING_IOC
        mode = info.filling_mode
        SYMBOL_FILLING_FOK = 1
        SYMBOL_FILLING_IOC = 2
        if mode & SYMBOL_FILLING_IOC:
            return mt5.ORDER_FILLING_IOC
        if mode & SYMBOL_FILLING_FOK:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    def _safe_order_send(self, request: Dict[str, Any]):
        result = mt5.order_send(request)
        if result is None:
            print(f"  order_send returned None — mt5.last_error(): {mt5.last_error()}")
            return None
        return result

    def has_open_position(self, symbol: str) -> bool:
        """Public, magic-filtered check for the main loop to use instead
        of a broker-wide open-trade count. The bug this fixes: main_
        news_spike.py was calling mt5_config.get_open_trades_count(symbol)
        — which counts ANY position on that symbol, any magic — to decide
        whether to route to manage_open_trade() or check_and_place(). On
        shared symbols (EURUSDm/GBPUSDm/USDJPYm/XAUUSDm, also traded by
        straddle_strategy.py MAGIC=20260716), that count was almost always
        >0 from the OTHER bot's position, so this strategy got routed to
        manage_open_trade() every cycle (correctly reporting "No open
        trade" for its own magic) and never reached check_and_place() at
        all — meaning the flatten-before-entry logic never ran either.
        This method wraps the same magic-filtered _get_position() already
        used everywhere else in this file, so the main loop's routing
        decision uses the same definition of "open" as the rest of the
        strategy."""
        return self._get_position(symbol) is not None

    def has_own_open_trade(self, symbol: str) -> bool:
        """Public, magic-filtered check for the main loop to use instead
        of a broker-wide open-trades count. main_news_spike.py was
        calling mt5_config.get_open_trades_count(symbol=symbol), which
        counts ANY open position on that symbol regardless of magic —
        so with straddle_strategy.py (MAGIC=20260716) holding EURUSD/
        GBPUSD/USDJPY/XAUUSD open nearly 24/7, the main loop routed
        those symbols to manage_open_trade() every cycle (which
        correctly reported 'not mine') and never reached
        check_and_place() at all — the flatten-before-entry logic never
        ran as a result. This method filters by MAGIC before answering,
        same as everything else in this file."""
        return self._get_position(symbol) is not None

    def _get_position(self, symbol: str):
        positions = mt5.positions_get(symbol=symbol)
        if not positions:
            return None
        own = [p for p in positions if p.magic == MAGIC]
        return own[0] if own else None

    def _get_pending_orders(self, symbol: str) -> Dict[str, Any]:
        orders = mt5.orders_get(symbol=symbol) or ()
        own = [o for o in orders if o.magic == MAGIC]
        buy = next((o for o in own if o.type == mt5.ORDER_TYPE_BUY_STOP), None)
        sell = next((o for o in own if o.type == mt5.ORDER_TYPE_SELL_STOP), None)
        return {"buy": buy, "sell": sell}

    # ---------------------------------------------------------------- event calendar

    def _next_event_trigger_window(
        self, now: datetime.datetime
    ) -> Optional[Tuple[datetime.datetime, str]]:
        """HARD pre-release-only entry window. `release_time` values in
        the schedule constants are stored EARLY_ENTRY_SECONDS before the
        real release. This window opens at that stored time and closes
        AT the real release moment
        (`release_time + EARLY_ENTRY_SECONDS`) — NOT minutes after, as
        in prior revisions. Once the real release has passed, price has
        already moved and there is no retry: that symbol sits out this
        event. See module docstring CHANGE LOG for the full rationale."""
        for event_type, schedule in (
            ("NFP", NFP_SCHEDULE_UTC),
            ("CPI", CPI_SCHEDULE_UTC),
            ("FOMC", FOMC_SCHEDULE_UTC),
        ):
            for release_time in schedule:
                real_release = release_time + datetime.timedelta(
                    seconds=EARLY_ENTRY_SECONDS
                )
                if release_time <= now < real_release:
                    return release_time, event_type
        return None

    def _event_already_traded(
        self, symbol: str, release_time: datetime.datetime
    ) -> bool:
        """Has this SPECIFIC event already produced a completed trade
        today? Derived from real MT5 deal history, not stored."""
        deals = (
            mt5.history_deals_get(
                release_time, datetime.datetime.now(datetime.timezone.utc)
            )
            or []
        )
        own_closes = [
            d
            for d in deals
            if d.symbol == symbol and d.magic == MAGIC and d.entry == mt5.DEAL_ENTRY_OUT
        ]
        return len(own_closes) > 0

    def _next_flatten_window(
        self, now: datetime.datetime, lead_minutes: float = 10.0
    ) -> Optional[Tuple[datetime.datetime, str]]:
        """Returns (release_time, event_type) if `now` is inside the
        pre-event flatten window — lead_minutes before the stored
        (early) release_time, up to release_time itself. In real-world
        terms this runs from lead_minutes-before-the-real-release down to
        EARLY_ENTRY_SECONDS-before-the-real-release, handing off directly
        to the entry window with no gap."""
        for event_type, schedule in (
            ("NFP", NFP_SCHEDULE_UTC),
            ("CPI", CPI_SCHEDULE_UTC),
            ("FOMC", FOMC_SCHEDULE_UTC),
        ):
            for release_time in schedule:
                flatten_start = release_time - datetime.timedelta(minutes=lead_minutes)
                if flatten_start <= now < release_time:
                    return release_time, event_type
        return None

    def _flatten_symbol(self, symbol: str) -> Optional[str]:
        """Closes ANY open position and cancels ANY pending order on this
        symbol, regardless of magic number — deliberate design choice:
        simpler than detecting/blocking on hedging mode. Called from two
        places in check_and_place() — the window-based check AND an
        unconditional final check right before order placement — because
        the window-based one alone depends on the main loop calling
        check_and_place() for this symbol during that specific stretch,
        which isn't guaranteed by this file's own logic (that's the main
        loop's job, outside this file).

        deviation uses EXIT_DEVIATION (effectively uncapped as of
        2026-09-08) rather than a tight fixed value — see module
        docstring CHANGE LOG. A rejected flatten here is just as
        dangerous as a rejected force-close: it can leave a position
        open going into the exact event this flatten exists to clear
        the way for."""
        actions: List[str] = []

        for pos in mt5.positions_get(symbol=symbol) or ():
            tick = self._get_tick(symbol)
            if tick is None:
                continue
            is_buy = pos.type == mt5.POSITION_TYPE_BUY
            result = self._safe_order_send(
                {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": pos.volume,
                    "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                    "position": pos.ticket,
                    "price": tick.bid if is_buy else tick.ask,
                    "deviation": EXIT_DEVIATION,
                    "magic": MAGIC,
                    "comment": "news_spike_pre_event_flatten",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": self._filling_mode(symbol),
                }
            )
            if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                actions.append(f"closed position magic={pos.magic} ticket={pos.ticket}")

        for order in mt5.orders_get(symbol=symbol) or ():
            result = self._safe_order_send(
                {"action": mt5.TRADE_ACTION_REMOVE, "order": order.ticket}
            )
            if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                actions.append(
                    f"cancelled pending magic={order.magic} ticket={order.ticket}"
                )

        return "; ".join(actions) if actions else None

    # ---------------------------------------------------------------- entry

    def check_and_place(
        self, symbol: str, now: Optional[datetime.datetime] = None
    ) -> Dict[str, Any]:
        """Call on every poll (~1s cadence required near a scheduled event
        — a narrow entry window with slower polling risks stepping over
        it entirely). Places the straddle in the narrow pre-release gap
        for whichever event type (NFP/CPI/FOMC) currently has it open.
        manage_open_trade() then handles the hard 1-minute force-close —
        there is no TP, no reload, and no retry past the real release
        moment.

        `now` should be a SINGLE timestamp captured ONCE per poll cycle by
        the caller (the main loop) and passed to every symbol's call that
        cycle — not fetched fresh inside this method. Real bug this fixes
        (found live, CPI event): with each call fetching its own
        datetime.now() internally, a slow symbol earlier in the loop (real
        order_send() round-trips, slower during a volatile print) could
        push the clock far enough that a LATER symbol in the same cycle —
        same scheduled event, same intended entry — saw `now` already past
        `real_release` and got "Not inside any event trigger window" even
        though it should have been eligible. Passing one shared `now` in
        means every symbol checked in the same cycle is judged against the
        exact same instant, regardless of loop position or how long
        earlier symbols took. If `now` is omitted (e.g. calling this
        directly/interactively), falls back to fetching it internally —
        but the main loop should always pass it explicitly."""
        if symbol not in self.traded_symbols:
            return self._no(f"{symbol} not enabled")

        if now is None:
            now = datetime.datetime.now(datetime.timezone.utc)

        flatten_window = self._next_flatten_window(now)
        if flatten_window is not None:
            flatten_release, flatten_event = flatten_window
            result = self._flatten_symbol(symbol)
            if result:
                return self._no(
                    f"Pre-{flatten_event} flatten ({flatten_release}): {result}"
                )
            return self._no(f"Pre-{flatten_event} flatten window — already flat")

        if self._get_position(symbol) is not None:
            return self._no("Position already open")

        pending = self._get_pending_orders(symbol)
        if pending["buy"] is not None or pending["sell"] is not None:
            return self._no("Straddle already pending")

        window = self._next_event_trigger_window(now)
        if window is None:
            return self._no("Not inside any event trigger window")
        release_time, event_type = window

        if self._event_already_traded(symbol, release_time):
            return self._no(
                f"This {event_type} event already produced a completed trade — "
                "no re-entry."
            )

        # Unconditional final safety net — runs every single time
        # check_and_place() reaches this point, regardless of whether the
        # window-based flatten above fired earlier in a different cycle.
        flatten_result = self._flatten_symbol(symbol)
        if flatten_result:
            return self._no(f"Flattened at entry-time (final check): {flatten_result}")

        cfg = SYMBOL_CONFIG[symbol]
        tick = self._get_tick(symbol)
        if tick is None:
            return self._no("No tick data")

        tick_age = datetime.datetime.now(
            datetime.timezone.utc
        ) - datetime.datetime.fromtimestamp(tick.time, tz=datetime.timezone.utc)
        if tick_age > datetime.timedelta(minutes=10):
            return self._no(f"Market likely closed — last tick is {tick_age} old")

        anchor = (tick.bid + tick.ask) / 2.0
        offset = cfg["offset"]
        sl = cfg["sl"]

        buy_stop = _round_price(anchor + offset, symbol)
        sell_stop = _round_price(anchor - offset, symbol)
        buy_sl = _round_price(buy_stop - sl, symbol)
        sell_sl = _round_price(sell_stop + sl, symbol)

        lots = self._base_lot(symbol)
        # Expiration anchored to the REAL release time (release_time +
        # EARLY_ENTRY_SECONDS), not the stored early one, so pending
        # orders that somehow survive past intended cleanup still expire
        # at a sensible real-world moment rather than a few seconds too
        # early.
        real_release_time = release_time + datetime.timedelta(
            seconds=EARLY_ENTRY_SECONDS
        )
        expiration = int(
            (real_release_time + datetime.timedelta(minutes=5)).timestamp()
        )
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
                    "tp": 0.0,
                    "magic": MAGIC,
                    "comment": "news_spike_entry",
                    "type_time": mt5.ORDER_TIME_SPECIFIED,
                    "expiration": expiration,
                    "type_filling": filling_mode,
                }
            )
            if result is None:
                continue
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                tickets[side] = result.order
            else:
                print(
                    f"  {side.upper()} {symbol} rejected — retcode={result.retcode} "
                    f"comment='{result.comment}'"
                )

        if tickets["buy"] is None or tickets["sell"] is None:
            for t in tickets.values():
                if t is not None:
                    self._safe_order_send(
                        {"action": mt5.TRADE_ACTION_REMOVE, "order": t}
                    )
            return self._no("Order send failed — rolled back")

        return {
            "signal": "straddle",
            "buy_stop": buy_stop,
            "sell_stop": sell_stop,
            "lot_size": lots,
            "reason": f"[{event_type}] Straddle placed | buy={buy_stop} sell={sell_stop} | lots={lots}",
        }

    # ---------------------------------------------------------------- OCO / cleanup

    def manage_pending_orders(self, symbol: str) -> str:
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

    def manage_open_trade(self, symbol: str) -> str:
        """Call on every poll while a position is open. The ENTIRE job of
        this method: has more than max_hold_seconds (60) elapsed since
        this position opened? If yes, close it at market immediately,
        regardless of profit or loss."""
        pos = self._get_position(symbol)
        if pos is None:
            return "No open trade"

        cfg = SYMBOL_CONFIG[symbol]
        open_time = datetime.datetime.fromtimestamp(pos.time, tz=datetime.timezone.utc)
        elapsed = (
            datetime.datetime.now(datetime.timezone.utc) - open_time
        ).total_seconds()

        if elapsed < cfg["max_hold_seconds"]:
            return f"Holding — {elapsed:.0f}s elapsed, force-close at {cfg['max_hold_seconds']:.0f}s"

        closed = self._close_position_at_market(symbol, pos)
        return (
            f"{cfg['max_hold_seconds']:.0f}s elapsed — force-closed at market"
            if closed
            else "Force-close FAILED — will retry next cycle"
        )

    def _close_position_at_market(self, symbol: str, position) -> bool:
        """deviation uses EXIT_DEVIATION (effectively uncapped as of
        2026-09-08), not a tight fixed value — see module docstring
        CHANGE LOG. Previously deviation=10 could reject this close
        outright during real volatility, silently turning the "hard
        60-second" exit into a retry loop that could drift well past
        60s while conditions stayed bad. This is a maximum tolerance,
        not a target — it doesn't change fill price on a calm exit,
        only whether a volatile one is allowed to complete."""
        tick = self._get_tick(symbol)
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
                "deviation": EXIT_DEVIATION,
                "magic": MAGIC,
                "comment": "news_spike_force_close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": self._filling_mode(symbol),
            }
        )
        if result is not None and result.retcode != mt5.TRADE_RETCODE_DONE:
            print(
                f"  Close {symbol} rejected — retcode={result.retcode} comment='{result.comment}'"
            )
        return result is not None and result.retcode == mt5.TRADE_RETCODE_DONE

    # ---------------------------------------------------------------- reporting

    def get_performance_summary(
        self, symbol: Optional[str] = None, lookback_days: int = 120
    ) -> Dict[str, Any]:
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
        return {
            "total_trades": len(all_closes),
            "win_rate": f"{wins/len(all_closes)*100:.1f}%",
        }

    def get_performance_by_symbol(self, lookback_days: int = 120) -> Dict[str, Any]:
        """Per-symbol breakdown."""
        return {
            sym: self.get_performance_summary(sym, lookback_days)
            for sym in self.traded_symbols
        }

    # ---------------------------------------------------------------- util

    def _no(self, reason: str) -> Dict[str, Any]:
        return {
            "signal": None,
            "buy_stop": None,
            "sell_stop": None,
            "lot_size": None,
            "reason": reason,
        }

    def __repr__(self) -> str:
        return (
            f"NewsSpikeStrategy(symbols={self.traded_symbols}, "
            f"events=[NFP,CPI,FOMC], risk_pct=2% (XAUUSDm only), "
            f"max_hold=60s, exit_deviation={EXIT_DEVIATION} (effectively uncapped), "
            f"filter=None, one_shot_per_event=True, "
            f"entry_window='pre-release only, {EARLY_ENTRY_SECONDS:.0f}s before real release, no post-release retry', "
            f"stateless=True, "
            f"validated=['XAUUSDm'])"
        )


if __name__ == "__main__":
    s = NewsSpikeStrategy()
    print(s)
    print()
    now = datetime.datetime.now(datetime.timezone.utc)
    for name, schedule in (
        ("NFP", NFP_SCHEDULE_UTC),
        ("CPI", CPI_SCHEDULE_UTC),
        ("FOMC", FOMC_SCHEDULE_UTC),
    ):
        future = [d for d in schedule if d >= now]
        print(f"{name} events scheduled: {len(schedule)} total, {len(future)} upcoming")
        print(
            f"  Next (stored, {EARLY_ENTRY_SECONDS:.0f}s early): {min(future) if future else 'NONE — add dates'}"
        )
    print()
    print("*** XAUUSDm backed by real, control-tested 1-min data + 1 real tick-level walkthrough ***")
    print("*** risk_pct=2% — final value of a same-day sequence (7% -> 3% -> 33.2143% -> 2%), not independently backtested at this level ***")
    print("*** XAGUSDm removed 2026-09-11, copper (XCUUSDm) removed 2026-09-08 — see CHANGE LOG ***")
    print(
        f"*** Entry window: pre-release only ({EARLY_ENTRY_SECONDS:.0f}s early -> real release), no retry after ***"
    )
    print("*** Still DEMO ONLY — do not run any of this on real money ***")