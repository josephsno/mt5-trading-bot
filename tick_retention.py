"""
check_tick_retention.py

Finds out how far back Exness actually retains history for a symbol --
both tick-level and M1 bar-level -- instead of guessing from a single
copy_ticks_range() failure. Brokers commonly cap tick retention well
short of bar retention (bars are cheap to store, raw ticks are not), so
this checks both separately.

Usage:
    python check_tick_retention.py
"""

from datetime import datetime, timezone, timedelta
import MetaTrader5 as mt5
from decouple import config

SYMBOL = "XAUUSDm"

# How far back to probe, and in what steps. Starts recent, walks backward
# until data stops coming back -- rather than guessing one date and
# concluding too much from a single failure.
PROBE_START = datetime(2026, 9, 1, tzinfo=timezone.utc)
PROBE_STEP_DAYS = 30
PROBE_MAX_STEPS = 24  # 24 * 30 days =~ 2 years back, generous ceiling


if not mt5.initialize(
    path=config("MT5_PATHWAY"),
    login=int(config("MT5_USERNAME")),
    password=config("MT5_PASSWORD"),
    server=config("MT5_SERVER"),
):
    print("MT5 init failed:", mt5.last_error())
    exit()

print("MT5 connected:", mt5.account_info().server)

if not mt5.symbol_select(SYMBOL, True):
    print(f"Failed to select {SYMBOL}:", mt5.last_error())
    mt5.shutdown()
    exit()


def probe_ticks(day: datetime) -> bool:
    """True if any ticks come back for a 1-hour window on this day."""
    start = day
    end = day + timedelta(hours=1)
    ticks = mt5.copy_ticks_range(SYMBOL, start, end, mt5.COPY_TICKS_ALL)
    return ticks is not None and len(ticks) > 0


def probe_bars(day: datetime) -> bool:
    """True if any M1 bars come back for a 1-hour window on this day."""
    start = day
    end = day + timedelta(hours=1)
    bars = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M1, start, end)
    return bars is not None and len(bars) > 0


print(f"\nProbing {SYMBOL} backward from {PROBE_START.date()} in "
      f"{PROBE_STEP_DAYS}-day steps...\n")

tick_cutoff = None
bar_cutoff = None

for step in range(PROBE_MAX_STEPS):
    probe_day = PROBE_START - timedelta(days=PROBE_STEP_DAYS * step)

    has_ticks = probe_ticks(probe_day)
    has_bars = probe_bars(probe_day)

    print(f"{probe_day.date()}  ticks={'yes' if has_ticks else 'NO'}   "
          f"M1 bars={'yes' if has_bars else 'NO'}")

    if not has_ticks and tick_cutoff is None:
        tick_cutoff = probe_day
    if not has_bars and bar_cutoff is None:
        bar_cutoff = probe_day

    # stop early once both have failed -- no need to keep probing further
    # back once we've found where each one runs out
    if tick_cutoff is not None and bar_cutoff is not None:
        break

print("\n--- Result ---")
if tick_cutoff is not None:
    print(f"Tick history appears to run out somewhere around {tick_cutoff.date()} "
          f"(last successful probe before this: "
          f"{(tick_cutoff + timedelta(days=PROBE_STEP_DAYS)).date()})")
    print(f"This is a real range -- narrow it further by re-probing in smaller "
          f"steps (e.g. 5 days) between those two dates if you need the exact cutoff.")
else:
    print(f"Ticks were available for the entire {PROBE_MAX_STEPS * PROBE_STEP_DAYS} "
          f"day probe window -- retention limit (if any) is further back than tested.")

if bar_cutoff is not None:
    print(f"M1 bar history appears to run out somewhere around {bar_cutoff.date()}")
else:
    print(f"M1 bars were available for the entire probe window.")

if tick_cutoff is not None and bar_cutoff is not None:
    if tick_cutoff > bar_cutoff:
        print(f"\nAs expected: tick retention ({tick_cutoff.date()}) is shorter "
              f"than bar retention ({bar_cutoff.date()}). This is normal broker "
              f"behavior -- ticks are far more expensive to store than bars.")

mt5.shutdown()