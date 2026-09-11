from datetime import datetime, timezone, timedelta
import MetaTrader5 as mt5
from decouple import config
import pandas as pd
import csv
import os
import threading
import queue


if not mt5.initialize(
    path=config("MT5_PATHWAY"),
    login=int(config("MT5_USERNAME")),
    password=config("MT5_PASSWORD"),
    server=config("MT5_SERVER"),
):
    print("MT5 init failed:", mt5.last_error())
    exit()

print("MT5 connected:", mt5.account_info().server)

# ── Config ────────────────────────────────────────────────────────────────────
SYMBOLS = ["XAUUSDm"]

MINUTES_BEFORE = 5   # window padding before release, so we see calm-before
MINUTES_AFTER = 5    # ...and settle-after, not just the release instant

GAP_THRESHOLD_MS = 200  # tick-to-tick gap wider than this = real liquidity gap

TICK_FETCH_TIMEOUT_SECONDS = 20  # if copy_ticks_range hasn't returned by
# this point, treat it as stuck on an uncached old date rather than let it
# block the rest of the batch. See _copy_ticks_range_with_timeout().

NFP_CALENDAR_CSV = "nfp_calendar_2022_2026.csv"  # source of truth if present
                                                   # next to this script
                                                   # (expects 'datetime_utc',
                                                   #  or 'date'+'time' columns)

# Fallback, used ONLY if NFP_CALENDAR_CSV isn't found. Reconstructed from
# public sources 2026-09-11 -- NOT read from your real calendar file. 2026
# had irregular dates (Jan delayed to Feb 11 by the shutdown, April moved to
# the second Friday May 8, June moved to Thursday July 2 ahead of July 4th).
# The Sept 4 2026 / 12:30:00 UTC entry here matches what was confirmed live,
# for what that's worth, but verify the rest against your actual CSV.
FALLBACK_NFP_DATES_UTC = [
    "2025-11-07 13:30:00",
    "2025-12-05 13:30:00",
    "2026-02-11 13:30:00",
    "2026-03-06 13:30:00",
    "2026-04-03 12:30:00",
    "2026-05-08 12:30:00",
    "2026-06-05 12:30:00",
    "2026-07-02 12:30:00",
    "2026-08-07 12:30:00",
    "2026-09-04 12:30:00",
]


# ── NFP date loading ────────────────────────────────────────────────────────
def load_nfp_dates():
    """Last 10 NFP release datetimes (UTC, tz-aware), earliest first.
    Prefers the project's own calendar CSV if it's next to this script."""
    if os.path.exists(NFP_CALENDAR_CSV):
        rows = []
        with open(NFP_CALENDAR_CSV, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if "datetime_utc" in row:
                    dt = datetime.strptime(row["datetime_utc"], "%Y-%m-%d %H:%M:%S")
                else:
                    dt = datetime.strptime(f"{row['date']} {row['time']}", "%Y-%m-%d %H:%M:%S")
                rows.append(dt.replace(tzinfo=timezone.utc))
        rows.sort()
        now = datetime.now(timezone.utc)
        past = [d for d in rows if d <= now]
        print(f"Loaded {len(past)} past NFP dates from {NFP_CALENDAR_CSV}")
        return past[-10:]
    else:
        print(
            f"WARNING: {NFP_CALENDAR_CSV} not found next to this script -- "
            f"using reconstructed fallback dates. Verify these against your "
            f"real calendar file before trusting them."
        )
        return [
            datetime.strptime(d, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            for d in FALLBACK_NFP_DATES_UTC
        ]


# ── Timeout wrapper ──────────────────────────────────────────────────────
def _copy_ticks_range_with_timeout(symbol, start, end):
    """Runs mt5.copy_ticks_range() in a background thread and waits up to
    TICK_FETCH_TIMEOUT_SECONDS for it to return. The MT5 Python API is a
    synchronous ctypes/DLL call with no built-in cancel or timeout, so a
    hung call can't actually be killed here -- the background thread will
    keep running in the background even after we give up waiting on it.
    This is a workaround to stop ONE stuck date from blocking the rest of
    the batch, not a true cancellation. Returns (status, result) where
    status is 'ok', 'timeout', or 'error'."""
    result_queue: "queue.Queue" = queue.Queue()

    def _worker():
        try:
            ticks = mt5.copy_ticks_range(symbol, start, end, mt5.COPY_TICKS_ALL)
            result_queue.put(("ok", ticks))
        except Exception as e:
            result_queue.put(("error", str(e)))

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=TICK_FETCH_TIMEOUT_SECONDS)

    if thread.is_alive():
        return "timeout", None

    return result_queue.get()


# ── Per symbol/event fetch ──────────────────────────────────────────────────
def fetch_event_ticks(symbol, release_time, save_dir):
    start = release_time - timedelta(minutes=MINUTES_BEFORE)
    end = release_time + timedelta(minutes=MINUTES_AFTER)

    if not mt5.symbol_select(symbol, True):
        print(f"  Failed to select {symbol}:", mt5.last_error())
        return None

    info = mt5.symbol_info(symbol)
    if info is None:
        print(f"  Symbol {symbol} not found on this broker")
        return None

    # copy_ticks_range can either return None/empty FAST (a real "no data"
    # answer) or HANG for a long time on a date range the terminal hasn't
    # cached locally yet, rather than failing fast. The timeout wrapper
    # below catches the hang case specifically so one stubborn old date
    # can't block the rest of the batch.
    status, ticks = _copy_ticks_range_with_timeout(symbol, start, end)

    if status == "timeout":
        print(
            f"  TIMED OUT after {TICK_FETCH_TIMEOUT_SECONDS}s waiting on "
            f"copy_ticks_range -- the terminal's local tick cache almost "
            f"certainly doesn't reach back to {release_time.strftime('%Y-%m-%d')} "
            f"yet. Fix: open {symbol}'s chart in the terminal, scroll back "
            f"through that date manually to force the broker to backfill "
            f"tick history, then re-run this script for this date."
        )
        return None

    if status == "error":
        print(f"  copy_ticks_range raised an exception: {ticks}")
        return None

    if ticks is None or len(ticks) == 0:
        print(f"  copy_ticks_range returned nothing:", mt5.last_error())
        print(
            f"  If empty, MT5's local tick cache likely doesn't reach back "
            f"to {release_time.strftime('%Y-%m-%d')} yet. Fix: open "
            f"{symbol}'s chart in the terminal, scroll back through that "
            f"date manually to force the broker to backfill tick history, "
            f"then re-run this script."
        )
        return None

    df = pd.DataFrame(ticks)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df["time_msc"] = pd.to_datetime(df["time_msc"], unit="ms", utc=True)

    print(f"  Fetched {len(df)} ticks")
    print(f"  Range: {df['time_msc'].iloc[0]} -> {df['time_msc'].iloc[-1]}")

    # ── Gap check — a gap of even a few hundred ms during the release second
    # is the actual thing we're looking for (no liquidity at that price),
    # so this just flags it loudly rather than needing a holiday-aware
    # exception list like the M15 gap check does.
    diffs_ms = df["time_msc"].diff().dt.total_seconds() * 1000
    gap_mask = diffs_ms > GAP_THRESHOLD_MS
    gaps = df.loc[gap_mask]

    if gaps.empty:
        print(f"  No tick gaps wider than {GAP_THRESHOLD_MS}ms in this window.")
    else:
        print(f"  *** {len(gaps)} tick gap(s) wider than {GAP_THRESHOLD_MS}ms ***")
        for idx in gaps.index:
            prev_bid = df["bid"].iloc[idx - 1]
            prev_ask = df["ask"].iloc[idx - 1]
            this_bid = df["bid"].iloc[idx]
            this_ask = df["ask"].iloc[idx]
            gap_start = df["time_msc"].iloc[idx - 1]
            gap_end = df["time_msc"].iloc[idx]
            print(
                f"    {gap_start} -> {gap_end}  "
                f"({(gap_end - gap_start).total_seconds() * 1000:.0f}ms)  "
                f"bid {prev_bid}->{this_bid}  ask {prev_ask}->{this_ask}"
            )

    filename = (
        f"{symbol}_NFP_{release_time.strftime('%Y%m%d')}_"
        f"{start.strftime('%H%M')}_{end.strftime('%H%M')}.csv"
    )
    save_path = os.path.join(save_dir, filename)
    df.to_csv(save_path, index=False)
    print(f"  Saved -> {save_path}")

    return len(df)


# ── Main ──────────────────────────────────────────────────────────────────
def main():
    save_dir = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(save_dir, exist_ok=True)

    nfp_dates = load_nfp_dates()
    print(
        f"\nFetching ticks for {len(nfp_dates)} NFP events, symbols={SYMBOLS}, "
        f"window=-{MINUTES_BEFORE}m/+{MINUTES_AFTER}m\n"
    )

    # Newest-first: recent events are far more likely to already be cached
    # locally, so they succeed immediately. Older ones that time out (see
    # TICK_FETCH_TIMEOUT_SECONDS) get flagged for manual chart backfill
    # rather than blocking everything behind them.
    ordered_dates = sorted(nfp_dates, reverse=True)

    summary = []
    for release_time in ordered_dates:
        print(f"NFP {release_time.strftime('%Y-%m-%d %H:%M UTC')}:")
        for symbol in SYMBOLS:
            print(f" {symbol}")
            count = fetch_event_ticks(symbol, release_time, save_dir)
            summary.append((release_time, symbol, count))
        print()

    print("--- Summary ---")
    for release_time, symbol, count in summary:
        if count is None:
            print(f"{release_time.strftime('%Y-%m-%d')}  {symbol:10s}  FAILED  <-- CHECK THIS")
        else:
            print(f"{release_time.strftime('%Y-%m-%d')}  {symbol:10s}  {count:6d} ticks")

    mt5.shutdown()


if __name__ == "__main__":
    main()