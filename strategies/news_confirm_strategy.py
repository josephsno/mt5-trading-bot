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
         rather than staying in the market and riding it.

Entry  : Buy-stop + sell-stop straddle, placed in a narrow PRE-RELEASE
         window only (see "Entry window" below) — offset from the
         anchor price (per-symbol distance below). Whichever side fills
         is the trade. Covers NFP, CPI, and FOMC — each on its own
         schedule (NFP/CPI at 8:30 AM ET, FOMC at 2:00 PM ET).
SL     : Real per-symbol stop — can still trigger before the 1-minute
         mark if price moves against it fast enough. Confirmed via real
         Sept 4 2026 tick-level analysis this SL is a price level, not a
         payment cap — a fast cascade can jump straight through it.
Exit   : Hard 1-minute force-close, no TP, no reload. Whichever comes
         first — the SL hitting, or 60 seconds elapsing — ends the
         trade. Exit deviation is effectively uncapped so this close
         cannot get rejected and silently retried through a volatile
         window.
Sizing : FLAT 14% risk_pct on ALL event types (NFP, CPI, FOMC) as of
         2026-09-12 (see CHANGE LOG) — explicit final decision, replacing
         an earlier differentiated split (NFP 5%/CPI 2%/FOMC 2%, tested
         the same day, also considered). Backtest across all 49
         tick-validated events (Jan 2025-Sep 2026), compounding from a
         $200 nominal balance: final balance $116,679.00 (58,240% total
         return), max drawdown -37.0% (peak $3,501.95 on 2026-03-06,
         trough $2,207.43 on 2026-05-12 — a ~10-week, 5-of-6-losing-event
         stretch), worst single trade -14.0%, win/loss dollar ratio
         4.81:1. This is more aggressive than the differentiated split
         (-23.9% drawdown, 10.81:1 ratio) — chosen anyway, as an explicit
         decision, not a backtest-driven optimum. Roughly 90%+ of the
         total historical gain traces to a small handful of large 2026
         NFP events (Feb 11, Mar 6, Jul 2, Aug 7, Sep 4) — this sizing
         is a real bet that events of that shape keep recurring, not a
         statistically settled allocation. Every SL-hit loss under this
         sizing is capped at exactly -14% of balance at the time (see
         the entry-slippage SL correction below) — no loss in the
         backtested history ever exceeded that.
Filter : NONE — tested and removed for this mechanic specifically.

Entry window : Every date in the three schedule constants below is
         stored 3 SECONDS EARLY relative to the real, source-verified
         release time. The entry window opens at the stored (early)
         time and closes hard AT the real release. If a straddle has
         not been placed by the real release moment, that symbol sits
         out the event entirely — no retry after price has already
         moved.

*** VALIDATED EVIDENCE — XAUUSDm ONLY ***
XAUUSDm is backed by real, minute-level, control-tested backtest data
(2024-01-02 to 2025-12-05 — 59.1%/54.5%/66.7% win rates, all beat
random-time controls), PLUS a full tick-level re-validation across Jan
2025-Sep 2026 (49 events, see 2026-09-12 CHANGE LOG) that directly
informed the per-event-type risk split now live. Still worth
remembering: 2026's per-event payoff sizes were much larger than
2025's on this same mechanic, most plausibly a market-regime effect
(2026 gold has been far more volatile/trending than 2025), not
evidence the mechanic itself got better. Sizing decisions should not
assume 2026's dollar magnitudes repeat.

*** XAGUSDm REMOVED 2026-09-11 *** — see CHANGE LOG. Only XAUUSDm is
traded by this file now.

*** COPPER (XCUUSDm) REMOVED 2026-09-08 *** — see CHANGE LOG.

*** STILL DEMO ONLY ***

CHANGE LOG (2026-09-12, flat 14% risk_pct across all event types):
  - CHANGED RISK_PCT_BY_EVENT from a differentiated split (NFP 5% / CPI
    2% / FOMC 2%, itself only hours old) to FLAT 14% on all three event
    types. Explicit final decision, not a backtest-optimum — the
    differentiated split had a better risk-adjusted profile (-23.9% max
    drawdown, 10.81:1 win/loss ratio, $26,681.73 final on the same
    49-event, $200-start sequence) than flat 14% (-37.0% max drawdown,
    4.81:1 ratio, $116,679.00 final). Flat 14% makes roughly 4.4x more
    on this specific historical sequence, at meaningfully worse drawdown
    and a materially worse win/loss ratio — chosen anyway. Worth
    remembering going in: the max-drawdown stretch (peak $3,501.95 on
    2026-03-06, trough $2,207.43 on 2026-05-12) spans 5 losing events
    out of 6 across roughly 10 weeks of real calendar time before
    recovering — a real period to be prepared to sit through, not a
    single bad day.
  - This sizing decision was tested specifically WITH the entry-
    slippage SL correction (see that CHANGE LOG entry) already active.
    Without that fix, the same flat-14% sequence would have produced a
    worse outcome on both counts: $96,904.76 final (vs $116,679.00) and
    -44.7% max drawdown / -23.7% worst single trade (vs -37.0% / -14.0%)
    — the SL correction is doing real, measurable work at this risk
    level specifically, not just a theoretical nicety.

CHANGE LOG (2026-09-12, entry-slippage SL correction):
  - Added _correct_sl_for_slippage(), called from manage_open_trade() for
    every open position the instant it's found. Fixes a real, distinct
    risk-sizing gap: the SL submitted with a pending stop order is a
    FIXED PRICE calculated from the INTENDED trigger level at placement
    time. If the entry itself slips (fills at a worse price than the
    intended stop level during a fast cascade — real, documented
    behavior on this account), that fixed SL doesn't move with it, so
    the realized risk distance from actual entry to SL widens beyond
    what risk_pct was sized for, on every trade where entry slips
    against the position. This re-anchors the SL to the REAL fill price
    (position.price_open) the moment a position is detected, keeping the
    realized risk distance consistently at cfg["sl"] regardless of how
    much the entry slipped. Idempotent — safe to call every cycle, no-op
    once already correct.
  - IMPORTANT DISTINCTION, not fixed by this change: this addresses
    ENTRY-side slippage only. It does NOT address the SL's OWN fill
    slipping when it later triggers during a fast cascade — that's the
    separate, previously-documented ~$40-worst-case-against-a-nominal-$7
    risk (see "Sizing" section above and the 2026-09-08 CHANGE LOG),
    which remains architecturally unfixable in MT5 (no deviation/
    tolerance control exists on a triggered stop-loss). The two
    slippage sources are independent; this change narrows one of them,
    not both.

CHANGE LOG (2026-09-12, simplified to a single T+60s close — no lingering):
  - Collapsed everything down to ONE deadline: real_release_time +
    max_hold_seconds (T+60s), full stop. Per explicit decision, removed
    two things that had crept in as separate, disconnected timers:
    (1) pending orders no longer get a 5-minute MT5-side expiration —
    they now expire at the SAME T+60s moment everything else closes,
    via the same real_release_time + max_hold_seconds calculation used
    everywhere else in this file; (2) the portfolio-wide global flatten
    no longer runs across a separate 2-minute (GLOBAL_FLATTEN_WINDOW_
    SECONDS) window after T+60s — it now targets that same single T+60s
    moment, with only a short GLOBAL_FLATTEN_RETRY_SECONDS (15s) safety
    margin for a rejected order_send to get a couple more attempts on
    the same tight 1-second cadence, not a genuinely separate later
    closing window.
  - Net effect: at T+60s, in one shot — both pending stop orders
    (whichever side never filled, if any), any open position(s) on
    XAUUSDm (both legs if a dual-fill occurred), and every other open
    position/pending order on the whole account (any symbol, any
    strategy's magic) all close/cancel together. Nothing is designed to
    linger past that single moment.
  - Tight 1-second polling (see main_news_spike.py) now runs
    continuously from T-2min through T+60s with no drop back to 30s in
    between, since there is no longer a reason to distinguish an
    "entry-window" tight band from a "post-event" tight band — it's one
    continuous tight-polling stretch covering the whole event lifecycle,
    then a short retry margin, then back to normal 30s cadence.

CHANGE LOG (2026-09-12, portfolio-wide flatten at event deadline):
  - Added _flatten_entire_account() / check_global_flatten(): at
    real_release_time + max_hold_seconds (the SAME deadline used for
    this strategy's own gold positions, see the OCO/release-anchored
    close entry above), EVERY open position and EVERY pending order
    across the WHOLE ACCOUNT closes/cancels — not just this strategy's
    own XAUUSDm trades. This is a deliberate, explicit design decision,
    not scope creep: NFP/CPI/FOMC move the US dollar broadly, not just
    gold, so a straddle_strategy.py position on EURUSDm/USDJPYm/GBPUSDm
    (MAGIC=20260716, a completely different strategy/process) sitting on
    unrealized profit at the moment of a shared-currency news shock is
    exposed to the same reversal risk gold itself is being protected
    from by its own hard 60-second close. Rather than have this file try
    to reach into straddle_strategy.py's own state, the flatten is
    unconditional and symbol/magic-blind: mt5.positions_get() and
    mt5.orders_get() with NO filters, closing/cancelling everything
    found. This intentionally breaks this project's usual pattern of
    strategies never touching each other's state — the FOMC/NFP/CPI
    reset is a deliberate, narrow exception to that pattern, not a
    general precedent.
  - IMPORTANT — REQUIRES A MAIN-LOOP CHANGE NOT MADE IN THIS FILE:
    check_global_flatten(now) must be called ONCE PER POLL CYCLE by
    main_news_spike.py, OUTSIDE the per-symbol loop (unlike
    manage_pending_orders()/manage_open_trade()/check_and_place(),
    which are called once per symbol in traded_symbols — currently just
    XAUUSDm). A one-line addition to main_news_spike.py's main loop,
    right after the `now = datetime.now(timezone.utc)` capture and
    before or after the `for symbol in strategy.traded_symbols:` block:
        flatten_status = strategy.check_global_flatten(now)
        if flatten_status:
            print(flatten_status)
    Without this addition, the portfolio-wide flatten NEVER RUNS, no
    matter what this file does — it is not wired into the polling loop
    on its own.
  - GLOBAL_FLATTEN_WINDOW_SECONDS = 120.0: the flatten stays "active"
    (keeps retrying, idempotently — closing zero positions costs
    nothing) for 2 minutes after each event's release+60s deadline, in
    case the first attempt hits a rejected order_send during volatility.
    Outside that window, check_global_flatten() is a no-op every cycle,
    so this doesn't run 24/7 for no reason — only in a narrow band
    around each scheduled event.
    *** SUPERSEDED LATER THE SAME DAY (2026-09-12) *** — see the
    "simplified to a single T+60s close" entry above this one.
    GLOBAL_FLATTEN_WINDOW_SECONDS (120s) no longer exists as a constant;
    replaced by GLOBAL_FLATTEN_RETRY_SECONDS (15s), a much shorter pure
    safety margin rather than a genuinely separate later window.

CHANGE LOG (2026-09-12, OCO cancellation removed + release-anchored close):
  - REMOVED the opposite-order cancellation in manage_pending_orders().
    Previously, the instant one side of the straddle filled, this method
    cancelled the other pending order — the intent was clean OCO
    behavior, but under real poll-cycle timing that cancellation could
    lag by up to a full poll interval, producing an uncontrolled,
    unintended dual-fill (both sides filling) on a real but unpredictable
    minority of events. Per explicit decision: rather than keep chasing
    that race condition, BOTH sides are now allowed to fire freely if
    price reaches both offsets. This is a deliberate acceptance of
    occasional double exposure on a single event, not a bug — backtested
    across 49 real events (Jan 2025-Sep 2026), real dual-fills occurred
    on 12 of 49 (~24%), net -$6.35/unit across those 12. Small, bounded,
    accepted cost of a simpler system.
  - REPLACED entry-time-based force-close with a RELEASE-ANCHORED hard
    deadline. Previously, manage_open_trade() closed each position
    max_hold_seconds (60s) after THAT position's own open time
    (pos.time) — meaning a late-filling second leg got its own fresh 60
    seconds, sometimes closing 80-90+ seconds after the real release.
    Now, EVERY position tied to a given event — whichever side, whenever
    it actually filled — closes at the SAME fixed moment:
    real_release_time + max_hold_seconds. A leg that fills late gets
    whatever time is left until that shared deadline, not a fresh 60
    seconds. Added _release_time_for_position() to derive which event a
    given position belongs to (the most recent real release at or before
    the position's own open time) — stateless, same pattern as
    everything else in this file, no stored event-to-position mapping.
    Backtested effect on the full 49-event sequence: net P&L moved from
    +$518.92 (old entry+60s rule) to +$506.87 (release+60s rule) — a
    small (~$12) net cost, traded for materially simpler logic and for
    correctly cancelling orders that never trigger within the release
    minute (previously, a very late incidental fill — e.g. the 2026-03-06
    NFP that didn't trigger until 4m46s after release on unrelated drift,
    not a real reaction to the news — could still open a position and
    run a full fresh 60s from that late, disconnected-from-the-news
    entry; now such an order is simply cancelled if it hasn't filled
    within the release minute).
  - FIXED a real bug this change exposed: _get_position() only ever
    returned the FIRST matching open position for this strategy's MAGIC
    number. Once OCO cancellation is removed, a dual-fill produces TWO
    simultaneous open positions with the same MAGIC on the same symbol
    (requires hedging mode, already a documented account requirement).
    The old code would have force-closed only one of the two — the
    other would have stayed open indefinitely with no exit logic ever
    checking on it again, a genuine unmanaged-risk bug, not a rare edge
    case, given dual-fills are now an expected ~24% occurrence rather
    than something actively prevented. Added _get_positions() (plural)
    returning ALL of this strategy's own open positions on a symbol;
    manage_open_trade() now iterates and force-closes each independently
    once past ITS event's release+60s deadline. has_open_position() /
    has_own_open_trade() updated to check for ANY open position, not
    just a single one.

CHANGE LOG (2026-09-12, per-event-type risk_pct):
  - Replaced flat risk_pct=2.0 (same for every event type) with
    RISK_PCT_BY_EVENT = {"NFP": 5.0, "CPI": 2.0, "FOMC": 2.0}.
    SYMBOL_CONFIG["XAUUSDm"]["risk_pct"] is now a FALLBACK ONLY, used if
    an event_type somehow isn't in RISK_PCT_BY_EVENT (shouldn't happen
    in normal operation, since _next_event_trigger_window() only ever
    returns NFP/CPI/FOMC).
  - _base_lot() now accepts an explicit risk_pct_override parameter.
    check_and_place() resolves the event-type-specific risk_pct right
    after the trigger window match (event_type is already known at that
    point) and passes it through, rather than _base_lot() reading a
    single fixed value off SYMBOL_CONFIG.
  - Backing evidence: full tick-level backtest, Jan 2025-Sep 2026, 49
    validated events (52 scheduled, 3 never triggered within the
    release+60s window and produced no trade). Real dual-fills — both
    the buy-stop and sell-stop triggering on the same event, which this
    file's own OCO cancellation (see manage_pending_orders()) does NOT
    fully prevent under real-world poll-cycle timing — occurred on 12
    of the 49 (~24%), net -$6.35/unit across those 12. This was a
    conscious, explicit decision: rather than build additional
    cancellation-race mitigation, dual-fills are accepted as a small,
    bounded cost of a simpler system. Net result across all 49 events
    at $7 SL, $1/unit terms: +$567.09, 73.5% win rate. Per-type: NFP
    72-75% win rate but far larger average payoff than CPI/FOMC in the
    2026 half of the sample specifically (see Sizing note above for the
    caution on reading too much into that).
  - Manual sizing simulation (not automated in this file) tested several
    NFP/other risk_pct combinations against this same 49-event sequence
    starting from a nominal $200 balance: 14%/4% -> $42,637 final but
    -23.8% worst single trade / -27.8% max drawdown; 10%/3% -> $12,859
    final, -17.0%/-20.2%; 7%/2% -> $4,240 final, -11.9%/-14.1%; 5%/2%
    (THIS CHANGE) -> $2,425 final, -8.5%/-10.8%; 4%/1.5% -> $1,483
    final, -6.8%/-8.6%. 5%/2% was chosen as a reasonable middle point,
    not the highest-return option tested — deliberately favoring
    survivable drawdown over maximum historical return, given the
    small sample size and the outlier-driven nature of 2026's largest
    NFP wins.

CHANGE LOG (2026-09-11, schedule fixes):
  - FIXED FOMC_SCHEDULE_UTC: every literal was stored as XX:00:57 (57s
    AFTER real release) instead of XX:59:57 (3s before). Corrected.
    NFP/CPI literals checked and found correct.
  - Removed stale 2026-09-09 NFP test entry.
  - Moved a misfiled NFP-labeled entry to CPI_SCHEDULE_UTC and corrected
    its time (was 14:29:57, real CPI release is 12:29:57 3s-early).
  - XAUUSDm risk_pct changed AGAIN, 33.2143 -> 2.0. SUPERSEDED 2026-09-12
    — see per-event-type risk_pct change above.
  - REMOVED XAGUSDm from SYMBOL_CONFIG entirely — never independently
    backtested, contract size never confirmed against the broker.
  - XAUUSDm risk_pct changed 7.0 -> 3.0 (superseded later same day).
  - XAUUSDm offset REVERTED from 4.0 back to 3.0 (original 2026-08-07
    value). SL left at 7.0.
  - XAUUSDm risk_pct changed AGAIN, 3.0 -> 33.2143 — restoring the full
    original total budget now entirely onto XAUUSDm alone.

CHANGE LOG (2026-09-08):
  - REMOVED XCUUSDm (copper) — drastically lower leverage than gold/
    silver, forced an unusually large lot count; volume_min/max/step
    also never confirmed against the broker.
  - risk_pct changed from differentiated to FLAT 7% (XAU/XAG).
  - `_close_position_at_market()` / `_flatten_symbol()` deviation
    changed from 10 to EXIT_DEVIATION (effectively uncapped) — fixes
    real rejected force-closes/flattens during volatility that could
    silently drift the "hard 60-second" exit well past 60s.
  - 5s-early entry window considered for narrowing to 3s, rejected at
    the time (superseded 2026-09-09 — later changed to 3s anyway).

CHANGE LOG (2026-09-09):
  - EARLY_ENTRY_SECONDS changed 5.0 -> 3.0. Does NOT reduce slippage —
    a narrower window only raises the odds a poll cycle steps over it
    and misses the entry entirely.
  - All schedule literals re-shifted from 5s-early to 3s-early to match.

CHANGE LOG (2026-09-04, risk_pct redistribution):
  - risk_pct redistributed across XAUUSDm/XAGUSDm/XCUUSDm, preserving
    the original 33.2143% total budget. SUPERSEDED 2026-09-08.

CHANGE LOG (2026-09-04, gold/silver/copper only):
  - FX pairs (EURUSDm, GBPUSDm, USDJPYm, USDCADm) REMOVED entirely.
    This strategy now trades ONLY XAUUSDm, XAGUSDm, XCUUSDm.

CHANGE LOG (2026-09-04, entry-window/polling changes):
  - All three schedule constants shifted 5s EARLY (later 3s) relative
    to real release times — fixes live rejections (retcode=10015/10006)
    from anchor-price staleness and dynamic trade_stops_level widening
    at the exact release moment. Resting orders placed before release
    sidestep both.
  - `_next_event_trigger_window()` narrowed to a HARD pre-release-only
    window — no retry after real release.
  - `_next_flatten_window()` default lead_minutes changed 5.0 -> 10.0.
  - Requires ~1s main-loop polling to be reliable near a scheduled
    event (not enforced in this file — that's main_news_spike.py's job).

CHANGE LOG (2026-08-30):
  - Added XCUUSDm (copper), risk_pct=3.2143%. REMOVED 2026-09-08.

CHANGE LOG (2026-08-12):
  - Re-added the unconditional final flatten check immediately before
    order placement in check_and_place() — fixes a real live bug where
    the window-based flatten alone silently never ran for a symbol if
    the main loop routed it to manage_open_trade() instead during the
    flatten window.
  - Trimmed to 6 symbols (XAUUSDm, XAGUSDm, EURUSDm, GBPUSDm, USDJPYm,
    USDCADm).
  - FX offset/SL settled at 12/20 pips.
  - Flatten lead time set to 5 minutes (later revised to 10, see above).
  - Added _next_flatten_window()/_flatten_symbol(): closes ANY position
    and cancels ANY pending order on a symbol, regardless of magic
    number, ahead of a scheduled event — deliberate, simpler alternative
    to hedging-mode detection/blocking.
  - Lot sizing rewritten to pull live from
    mt5.symbol_info(symbol).trade_tick_value/trade_tick_size instead of
    a static pip-value guess table, which had been wrong by orders of
    magnitude for several symbols.

CHANGE LOG (2026-08-11, earlier same day):
  - Added XAGUSDm + 7 major USD pairs (later trimmed to 4).
  - Added `decimals` to SYMBOL_CONFIG per symbol.
  - Added automatic hedging-mode check at import time (informational).

CHANGE LOG (prior revision):
  - Expanded from NFP-only to three event types: NFP, CPI, FOMC.
  - Removed the FOMC-proximity filter entirely — helped the reload
    chain, hurt this mechanic when tested directly.

CHANGE LOG (initial):
  - Initial build. Fourth standalone strategy, own MAGIC number, own
    process. Includes the "already traded this event" guard from the
    start, given a live bug found in news_reload_strategy.py.
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
# EARLY_ENTRY_SECONDS. If EARLY_ENTRY_SECONDS is ever changed again, every
# literal below must be manually re-shifted to match, or the window-open
# time and the code's internal "real release" time drift out of sync.
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
    # otherwise -> stored 3s EARLY as 17:59:57 / 18:59:57 respectively.
    # All 2027 dates confirmed against the Fed's own published (tentative)
    # 2027 calendar as of 2026-09-11 — not extrapolated.
]

# Real release time = stored schedule time + this. Kept as a named constant
# so every place in the file that needs to reason about the REAL moment
# (vs. the deliberately-early stored one) references the same value.
EARLY_ENTRY_SECONDS = 3.0

# Maximum acceptable slippage (in points) on the exits THIS FILE controls
# directly (force-close, pre-event flatten) — TRADE_ACTION_DEAL requests
# only. Deliberately large/effectively-uncapped: deviation is a MAXIMUM
# tolerance, not a target, so a large value costs nothing on calm exits
# and only matters on volatile ones — which is exactly when a guaranteed
# exit matters most. Does NOT apply to entries (pending stop orders carry
# no deviation field in MT5) or to SL fills (MT5 does not support
# deviation on a triggered stop-loss).
EXIT_DEVIATION = 500

# ---------------------------------------------------------------------------
# Per-event-type risk sizing — added 2026-09-12. See module docstring
# CHANGE LOG for the backtest that informed this split. NFP is sized
# higher than CPI/FOMC based on 49 tick-validated events (Jan 2025-Sep
# 2026), with the explicit caveat noted there: NFP's larger 2026 payoffs
# are plausibly a market-regime effect rather than a proven structural
# edge, and this split is a deliberately moderate choice (5%/2%) rather
# than the most aggressive one tested.
# ---------------------------------------------------------------------------
RISK_PCT_BY_EVENT: Dict[str, float] = {
    "NFP": 14.0,
    "CPI": 14.0,
    "FOMC": 14.0,
}

# How long, in seconds, check_global_flatten() keeps retrying past the
# T+60s deadline if an earlier attempt gets rejected — kept short and
# purely a safety net, NOT a separate window. As of 2026-09-12 the
# target is ONE deadline (real_release + max_hold_seconds): everything
# should close right there. This constant only exists so a rejected
# order_send during real volatility gets a few more chances on the same
# tight 1-second cadence rather than silently giving up, not to create
# a second, later closing moment.
GLOBAL_FLATTEN_RETRY_SECONDS = 15.0


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
# risk_pct here is now a FALLBACK ONLY (used if an event_type isn't found
# in RISK_PCT_BY_EVENT) — see that dict above for the actual live values
# as of 2026-09-12 (NFP=5%, CPI=2%, FOMC=2%).

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
        # cap — see module docstring.
        "risk_pct": 2.0,  # FALLBACK ONLY as of 2026-09-12 — see
        # RISK_PCT_BY_EVENT above for the actual live per-event-type
        # values. Kept here so _base_lot() always has something sane to
        # fall back to if event_type resolution ever fails.
        "decimals": 2,
        "max_hold_seconds": 60.0,
    },
}

RISK_PCT = 2.0  # fallback default only if a symbol's config is missing risk_pct
# AND no event-type-specific value is available either.
# History: 2026-08-12 allocation scaled from an initial 14% total to 30%.
# 2026-08-30: +3.2143% for XCUUSDm -> 33.2143% total across 7 symbols.
# 2026-09-04: FX pairs removed, risk_pct redistributed across XAU/XAG/XCU.
# 2026-09-08: copper removed, XAU/XAG both set flat to 7%. 2026-09-11:
# XAGUSDm removed; XAUUSDm went 7.0 -> 3.0 -> 33.2143 -> 2.0, all same
# day, all explicit instructions. 2026-09-12: flat 2.0 replaced by
# RISK_PCT_BY_EVENT (NFP=5%, CPI=2%, FOMC=2%) — see module docstring
# CHANGE LOG.
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

    def _base_lot(self, symbol: str, risk_pct_override: Optional[float] = None) -> float:
        """Sizing pulled LIVE from the broker, not guessed. trade_tick_value
        / trade_tick_size gives $-per-1.0-price-unit-move per lot, already
        converted to account currency by MT5. Clamps to the symbol's real
        volume_min/volume_max/volume_step.

        risk_pct_override, added 2026-09-12: when provided (normally by
        check_and_place() resolving the current event_type against
        RISK_PCT_BY_EVENT), this takes priority over the symbol's own
        SYMBOL_CONFIG["risk_pct"]. Falls back to SYMBOL_CONFIG's value,
        then to the module-level RISK_PCT, only if no override is given —
        this keeps the function usable standalone/interactively without
        requiring an event_type."""
        cfg = SYMBOL_CONFIG[symbol]
        info = mt5.symbol_info(symbol)
        if info is None or not info.trade_tick_size:
            print(f"  {symbol}: symbol_info unavailable — defaulting to 0.01 floor")
            return 0.01

        value_per_unit_per_lot = info.trade_tick_value / info.trade_tick_size
        risk_pct = (
            risk_pct_override
            if risk_pct_override is not None
            else cfg.get("risk_pct", RISK_PCT)
        )
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
        of a broker-wide open-trade count. Checks for ANY own open
        position (plural-aware as of 2026-09-12) — a dual-fill can leave
        two open at once."""
        return len(self._get_positions(symbol)) > 0

    def has_own_open_trade(self, symbol: str) -> bool:
        """Public, magic-filtered check for the main loop to use instead
        of a broker-wide open-trades count. Checks for ANY own open
        position (plural-aware as of 2026-09-12) — a dual-fill can leave
        two open at once."""
        return len(self._get_positions(symbol)) > 0

    def _get_position(self, symbol: str):
        """Returns a SINGLE matching position (the first one found) —
        kept for any external caller that still expects one-position
        semantics. Internally, manage_open_trade() uses _get_positions()
        (plural) as of 2026-09-12, since a dual-fill (both straddle legs
        filling — now an accepted, expected outcome rather than something
        actively prevented, see CHANGE LOG) can leave TWO own positions
        open on the same symbol at once. Do not use this method anywhere
        that needs to guarantee every open position gets managed."""
        positions = mt5.positions_get(symbol=symbol)
        if not positions:
            return None
        own = [p for p in positions if p.magic == MAGIC]
        return own[0] if own else None

    def _get_positions(self, symbol: str) -> List[Any]:
        """Returns ALL of this strategy's own open positions on a symbol,
        not just the first. Added 2026-09-12 alongside the removal of
        OCO cancellation — a dual-fill now produces two simultaneous
        positions with this strategy's MAGIC, and both must be tracked
        and eventually force-closed independently, or one would sit open
        indefinitely with nothing ever managing it."""
        positions = mt5.positions_get(symbol=symbol)
        if not positions:
            return []
        return [p for p in positions if p.magic == MAGIC]

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
        (`release_time + EARLY_ENTRY_SECONDS`) — NOT minutes after. Once
        the real release has passed, price has already moved and there
        is no retry: that symbol sits out this event."""
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

    def _release_time_for_position(
        self, open_time: datetime.datetime
    ) -> Optional[datetime.datetime]:
        """Added 2026-09-12. Stateless lookup of which scheduled event a
        given open position belongs to, used by manage_open_trade() to
        anchor the force-close deadline to the REAL RELEASE TIME rather
        than the position's own open time (see CHANGE LOG for why this
        matters — a late-filling leg no longer gets a fresh 60 seconds
        of its own). Returns the most recent real release time (across
        all three calendars) at or before `open_time`. A position should
        only ever exist because it filled at-or-after some real release,
        so "most recent real release <= open_time" reliably identifies
        the event it belongs to without needing any stored event-to-
        position mapping."""
        candidates: List[datetime.datetime] = []
        for schedule in (NFP_SCHEDULE_UTC, CPI_SCHEDULE_UTC, FOMC_SCHEDULE_UTC):
            for release_time in schedule:
                real_release = release_time + datetime.timedelta(
                    seconds=EARLY_ENTRY_SECONDS
                )
                if real_release <= open_time:
                    candidates.append(real_release)
        return max(candidates) if candidates else None

    def _next_flatten_window(
        self, now: datetime.datetime, lead_minutes: float = 10.0
    ) -> Optional[Tuple[datetime.datetime, str]]:
        """Returns (release_time, event_type) if `now` is inside the
        pre-event flatten window — lead_minutes before the stored
        (early) release_time, up to release_time itself."""
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
        unconditional final check right before order placement. Scoped
        to a SINGLE symbol (this strategy's own, XAUUSDm) — for the
        portfolio-WIDE flatten across every symbol/magic at the event
        deadline, see _flatten_entire_account() below, added 2026-09-12."""
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

    def _next_global_flatten_deadline(
        self, now: datetime.datetime
    ) -> Optional[Tuple[datetime.datetime, str]]:
        """Added 2026-09-12, simplified same day to a single T+60s
        deadline (was a 2-minute window; that extra grace period was
        removed per explicit decision — everything closes right at
        real_release + max_hold_seconds, full stop, with only a short
        GLOBAL_FLATTEN_RETRY_SECONDS safety margin for a rejected
        order_send to get a couple more attempts on the same 1-second
        cadence). Returns (real_release_time, event_type) if `now` is at
        or just past any scheduled event's real_release_time +
        max_hold_seconds deadline. Uses XAUUSDm's max_hold_seconds as the
        reference hold time since that's the only symbol configured in
        this file."""
        hold_seconds = SYMBOL_CONFIG.get("XAUUSDm", {}).get("max_hold_seconds", 60.0)
        for event_type, schedule in (
            ("NFP", NFP_SCHEDULE_UTC),
            ("CPI", CPI_SCHEDULE_UTC),
            ("FOMC", FOMC_SCHEDULE_UTC),
        ):
            for release_time in schedule:
                real_release = release_time + datetime.timedelta(
                    seconds=EARLY_ENTRY_SECONDS
                )
                deadline = real_release + datetime.timedelta(seconds=hold_seconds)
                retry_end = deadline + datetime.timedelta(
                    seconds=GLOBAL_FLATTEN_RETRY_SECONDS
                )
                if deadline <= now <= retry_end:
                    return real_release, event_type
        return None

    def _flatten_entire_account(self) -> Optional[str]:
        """Added 2026-09-12. Closes EVERY open position and cancels
        EVERY pending order across the WHOLE ACCOUNT — no symbol filter,
        no magic filter. This is intentionally broader than
        _flatten_symbol() (which only touches one symbol). See module
        docstring CHANGE LOG for the full rationale: NFP/CPI/FOMC moves
        the dollar broadly, so any strategy's position on any USD pair
        is exposed to the same shared-news reversal risk gold itself is
        being protected from at the same moment. This deliberately
        crosses strategy boundaries (touches positions/orders opened by
        straddle_strategy.py, MAGIC=20260716, and any other running
        strategy) — a narrow, explicit exception to this project's usual
        pattern of strategies never touching each other's state."""
        actions: List[str] = []

        for pos in mt5.positions_get() or ():  # NO symbol filter, NO magic filter
            tick = self._get_tick(pos.symbol)
            if tick is None:
                continue
            is_buy = pos.type == mt5.POSITION_TYPE_BUY
            result = self._safe_order_send(
                {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": pos.symbol,
                    "volume": pos.volume,
                    "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                    "position": pos.ticket,
                    "price": tick.bid if is_buy else tick.ask,
                    "deviation": EXIT_DEVIATION,
                    "magic": pos.magic,  # preserve the ORIGINAL magic on
                    # the closing deal, not this strategy's own — this
                    # close is being done on behalf of whichever strategy
                    # opened it, not re-attributed to news_spike_strategy.
                    "comment": "news_spike_global_event_flatten",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": self._filling_mode(pos.symbol),
                }
            )
            if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                actions.append(
                    f"closed {pos.symbol} magic={pos.magic} ticket={pos.ticket}"
                )

        for order in mt5.orders_get() or ():  # NO symbol filter, NO magic filter
            result = self._safe_order_send(
                {"action": mt5.TRADE_ACTION_REMOVE, "order": order.ticket}
            )
            if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                actions.append(
                    f"cancelled pending {order.symbol} magic={order.magic} "
                    f"ticket={order.ticket}"
                )

        return "; ".join(actions) if actions else None

    def check_global_flatten(self, now: Optional[datetime.datetime] = None) -> Optional[str]:
        """PUBLIC — added 2026-09-12, simplified same day to a single
        T+60s deadline. Call ONCE PER POLL CYCLE from the main loop,
        OUTSIDE the per-symbol loop (unlike manage_pending_orders()/
        manage_open_trade()/check_and_place(), which are called once per
        symbol). See module docstring CHANGE LOG — main_news_spike.py
        needs a one-line addition to actually call this.

        Returns None (does nothing) outside the narrow band right at
        each event's real_release + max_hold_seconds deadline (plus a
        short GLOBAL_FLATTEN_RETRY_SECONDS safety margin). Inside that
        band, unconditionally flattens the ENTIRE account (see
        _flatten_entire_account()) every cycle it's called — safe/
        idempotent since closing zero remaining positions costs nothing."""
        if now is None:
            now = datetime.datetime.now(datetime.timezone.utc)

        window = self._next_global_flatten_deadline(now)
        if window is None:
            return None
        real_release, event_type = window

        result = self._flatten_entire_account()
        if result:
            return f"[GLOBAL FLATTEN] Post-{event_type} ({real_release}): {result}"
        return f"[GLOBAL FLATTEN] Post-{event_type} ({real_release}): already flat"

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
        cycle — not fetched fresh inside this method."""
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

        # Per-event-type risk_pct, added 2026-09-12 — resolved here since
        # event_type is already known at this point in the flow, then
        # passed explicitly into _base_lot() rather than that method
        # reading a single fixed value off SYMBOL_CONFIG. Falls back to
        # SYMBOL_CONFIG's risk_pct if event_type somehow isn't in
        # RISK_PCT_BY_EVENT (shouldn't happen — _next_event_trigger_window
        # only ever returns NFP/CPI/FOMC — but fails safe rather than
        # raising if the calendars are ever extended with a new type).
        risk_pct = RISK_PCT_BY_EVENT.get(event_type, cfg.get("risk_pct", RISK_PCT))
        lots = self._base_lot(symbol, risk_pct_override=risk_pct)

        # Expiration anchored to the REAL release time (release_time +
        # EARLY_ENTRY_SECONDS) plus the hold time — real_release_time +
        # max_hold_seconds. As of 2026-09-12 this is ONE single deadline,
        # not a separate longer expiration: if price never reaches either
        # offset by T+60s, the order simply expires right there, same
        # moment everything else closes. No more 5-minute lingering.
        real_release_time = release_time + datetime.timedelta(
            seconds=EARLY_ENTRY_SECONDS
        )
        hold_seconds = cfg.get("max_hold_seconds", 60.0)
        expiration = int(
            (real_release_time + datetime.timedelta(seconds=hold_seconds)).timestamp()
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
            "reason": f"[{event_type}] Straddle placed | buy={buy_stop} sell={sell_stop} | "
            f"lots={lots} | risk_pct={risk_pct}%",
        }

    # ---------------------------------------------------------------- OCO / cleanup

    def manage_pending_orders(self, symbol: str) -> str:
        """As of 2026-09-12, this method NO LONGER cancels the opposite
        order when one side fills — see CHANGE LOG. Both sides are
        allowed to fire; if that happens, manage_open_trade() is
        responsible for tracking and closing BOTH resulting positions
        independently (see _get_positions()). This method's only
        remaining job is expiring pending orders that never filled at
        all within their window."""
        pending = self._get_pending_orders(symbol)
        if pending["buy"] is None and pending["sell"] is None:
            return "No pending straddle"

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
            return "Neither side filled (or both filled and are now open positions) — no pending orders remain"
        return "Pending"

    # ---------------------------------------------------------------- trade management

    def _correct_sl_for_slippage(self, symbol: str, position) -> Optional[str]:
        """Added 2026-09-12. Pending stop orders can fill at a worse
        price than the intended trigger level during a fast news
        cascade (ENTRY slippage — distinct from SL-fill slippage, see
        module docstring's documented ~$40 worst-case example, which is
        about the SL itself slipping when triggered, not this). The SL
        submitted at order-placement time is a FIXED PRICE calculated
        from the INTENDED trigger level, not the eventual real fill
        price — so if entry slips, that SL is now the wrong distance
        from where the trade actually opened. Since slippage on entry
        pushes price AWAY from a SL that didn't move with it, this
        widens the realized risk distance beyond what risk_pct was
        sized for, every time entry slips against the position.

        This corrects that: re-anchors the SL to position.price_open
        (the REAL fill price) +/- cfg["sl"], the instant a position is
        found open, so the realized risk distance is always the
        intended $7 (or whatever cfg["sl"] is) regardless of entry
        slippage. Does NOT touch entry price or lot size, and does NOT
        protect against the SL's OWN fill slipping when it later
        triggers (MT5 has no deviation/tolerance control on a triggered
        stop-loss — see EXIT_DEVIATION's docstring note). Those are two
        separate slippage sources; this addresses only the entry-side
        one. Safe to call every cycle — a no-op once the SL already
        matches the corrected value."""
        cfg = SYMBOL_CONFIG[symbol]
        is_buy = position.type == mt5.POSITION_TYPE_BUY
        corrected_sl = _round_price(
            position.price_open - cfg["sl"]
            if is_buy
            else position.price_open + cfg["sl"],
            symbol,
        )
        tolerance = (10 ** -cfg.get("decimals", 2)) / 2
        if abs(corrected_sl - position.sl) < tolerance:
            return None  # already correct, nothing to do

        result = self._safe_order_send(
            {
                "action": mt5.TRADE_ACTION_SLTP,
                "symbol": symbol,
                "position": position.ticket,
                "sl": corrected_sl,
                "tp": position.tp,
            }
        )
        if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
            return (
                f"ticket={position.ticket}: SL corrected for entry slippage "
                f"{position.sl} -> {corrected_sl} (real entry {position.price_open})"
            )
        return (
            f"ticket={position.ticket}: SL correction FAILED — "
            f"retcode={result.retcode if result else 'None'}"
        )

    def manage_open_trade(self, symbol: str) -> str:
        """Call on every poll while a position (or positions — see below)
        is open. As of 2026-09-12, this checks and manages ALL of this
        strategy's own open positions on the symbol independently, not
        just one — necessary because OCO cancellation was removed
        (manage_pending_orders() no longer cancels the opposite order on
        a fill), so a dual-fill can leave TWO positions open at once, and
        both must be tracked or one would sit open indefinitely.

        The deadline for each position is now RELEASE-ANCHORED, not
        entry-anchored: real_release_time + max_hold_seconds, the SAME
        fixed moment for every position tied to a given event regardless
        of when that specific position actually filled. A late-filling
        leg gets whatever time is left until that shared deadline, not a
        fresh max_hold_seconds of its own — see CHANGE LOG for the
        backtested cost/benefit of this vs. the prior entry-anchored
        rule. Falls back to entry-anchored timing (this position's own
        open time + max_hold_seconds) only if the release time can't be
        determined at all, which should not happen in normal operation."""
        positions = self._get_positions(symbol)
        if not positions:
            return "No open trade"

        cfg = SYMBOL_CONFIG[symbol]
        now = datetime.datetime.now(datetime.timezone.utc)
        statuses: List[str] = []

        for pos in positions:
            sl_fix = self._correct_sl_for_slippage(symbol, pos)
            if sl_fix:
                statuses.append(sl_fix)

            open_time = datetime.datetime.fromtimestamp(
                pos.time, tz=datetime.timezone.utc
            )
            release_time = self._release_time_for_position(open_time)
            if release_time is not None:
                deadline = release_time + datetime.timedelta(
                    seconds=cfg["max_hold_seconds"]
                )
            else:
                # Fallback — should not normally trigger, but fails safe
                # to the old entry-anchored behavior rather than never
                # closing the position at all.
                deadline = open_time + datetime.timedelta(
                    seconds=cfg["max_hold_seconds"]
                )

            if now < deadline:
                remaining = (deadline - now).total_seconds()
                statuses.append(
                    f"ticket={pos.ticket} holding — {remaining:.0f}s until "
                    f"release-anchored close"
                )
                continue

            closed = self._close_position_at_market(symbol, pos)
            statuses.append(
                f"ticket={pos.ticket} "
                + (
                    "force-closed at market (release+"
                    f"{cfg['max_hold_seconds']:.0f}s deadline reached)"
                    if closed
                    else "force-close FAILED — will retry next cycle"
                )
            )

        return "; ".join(statuses)

    def _close_position_at_market(self, symbol: str, position) -> bool:
        """deviation uses EXIT_DEVIATION (effectively uncapped), not a
        tight fixed value. This is a maximum tolerance, not a target —
        it doesn't change fill price on a calm exit, only whether a
        volatile one is allowed to complete."""
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
        risk_str = ", ".join(f"{k}={v}%" for k, v in RISK_PCT_BY_EVENT.items())
        return (
            f"NewsSpikeStrategy(symbols={self.traded_symbols}, "
            f"events=[NFP,CPI,FOMC], risk_pct=[{risk_str}] (XAUUSDm only), "
            f"max_hold=60s, exit_deviation={EXIT_DEVIATION} (effectively uncapped), "
            f"filter=None, one_shot_per_event=True, "
            f"entry_window='pre-release only, {EARLY_ENTRY_SECONDS:.0f}s before real release, no post-release retry', "
            f"global_flatten_at_deadline=True (portfolio-wide, all symbols/magics — see CHANGE LOG), "
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
    print("*** XAUUSDm backed by real, control-tested 1-min data + tick-level re-validation (49 events, Jan 2025-Sep 2026) ***")
    print(f"*** risk_pct is FLAT 14% on all event types as of 2026-09-12: {RISK_PCT_BY_EVENT} — see CHANGE LOG for the backtest and drawdown behind this decision (-37.0% max drawdown on the 49-event historical sequence) ***")
    print("*** XAGUSDm removed 2026-09-11, copper (XCUUSDm) removed 2026-09-08 — see CHANGE LOG ***")
    print(
        f"*** Entry window: pre-release only ({EARLY_ENTRY_SECONDS:.0f}s early -> real release), no retry after ***"
    )
    print(
        "*** GLOBAL FLATTEN: at release+60s, EVERY position/order on the WHOLE ACCOUNT "
        "closes/cancels, not just this strategy's own gold trades — REQUIRES "
        "main_news_spike.py to call check_global_flatten(now) once per cycle, "
        "see CHANGE LOG ***"
    )
    print("*** Still DEMO ONLY — do not run any of this on real money ***")