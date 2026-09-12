"""
process_exness_tick_history.py

Processes tick history files downloaded MANUALLY from Exness's public
Tick History page (https://www.exness.com/tick-history/) -- no MT5
connection needed, no retention-wall problem, since this is Exness's
own archived data rather than your terminal's local cache.

Workflow:
    1. Go to https://www.exness.com/tick-history/
    2. Pick instrument (e.g. XAUUSDm or XAUUSD -- check which suffix
       matches your account type), year, month, day. Click "Get ticks".
    3. Extract the downloaded .zip -- it'll contain one .csv (sometimes
       one per day, depending on what you selected).
    4. Drop the extracted CSV(s) into the same folder as this script,
       or point INPUT_DIR below at wherever you put them.
    5. Run this script.

Exness's own column format isn't independently confirmed here -- the
parser below is written defensively to auto-detect common column name
variants (Time/Timestamp, Bid, Ask) rather than assuming one exact
layout. If it fails to find columns, it prints what it actually found
so you can tell it the real header names.
"""

import pandas as pd
import os
import glob

# ── Config ────────────────────────────────────────────────────────────────
INPUT_DIR = "."  # folder containing extracted Exness tick history CSVs
OUTPUT_DIR = "exness_tick_processed"
GAP_THRESHOLD_MS = 200  # same threshold as the MT5-based fetcher, for
# consistency when comparing results across sources

# If you know the exact release moment you're checking this file against
# (e.g. an NFP release), set it here to get excursion/SL-style analysis.
# Leave as None to just do the gap check with no release-time analysis.
RELEASE_TIME_UTC = None  # e.g. "2026-05-08 12:30:00"


def find_candidate_files():
    patterns = ["*.csv", "*.CSV"]
    files = []
    for p in patterns:
        files.extend(glob.glob(os.path.join(INPUT_DIR, p)))
    # exclude anything this script itself might have already written out
    files = [f for f in files if OUTPUT_DIR not in f]
    return sorted(files)


def detect_columns(df: pd.DataFrame):
    """Exness's exact export header isn't independently confirmed here --
    detect common variants defensively rather than assume one layout."""
    cols_lower = {c.lower().strip(): c for c in df.columns}

    time_col = None
    for candidate in ("timestamp", "time", "datetime", "date"):
        if candidate in cols_lower:
            time_col = cols_lower[candidate]
            break

    bid_col = cols_lower.get("bid")
    ask_col = cols_lower.get("ask")

    return time_col, bid_col, ask_col


def process_file(filepath: str):
    print(f"\n--- {os.path.basename(filepath)} ---")

    # Exness exports have been reported as potentially very large and
    # sometimes problematic to open in Excel/Numbers -- read directly
    # with pandas rather than opening in a spreadsheet app first.
    try:
        df = pd.read_csv(filepath)
    except Exception as e:
        print(f"  Failed to read as CSV: {e}")
        print(f"  If this looks like it might be semicolon or "
              f"tab-delimited instead of comma, re-run with sep=';' or "
              f"sep='\\t' adjusted in this function.")
        return

    print(f"  Columns found: {list(df.columns)}")
    print(f"  Rows: {len(df)}")

    time_col, bid_col, ask_col = detect_columns(df)

    if time_col is None or bid_col is None or ask_col is None:
        print(f"  Could not confidently detect time/bid/ask columns "
              f"(found time={time_col}, bid={bid_col}, ask={ask_col}). "
              f"Check the actual column names above and adjust "
              f"detect_columns() if Exness uses different labels.")
        return

    df[time_col] = pd.to_datetime(df[time_col], format="mixed", utc=True, errors="coerce")
    bad_times = df[time_col].isna().sum()
    if bad_times > 0:
        print(f"  WARNING: {bad_times} rows had unparseable timestamps "
              f"and were dropped.")
        df = df.dropna(subset=[time_col])

    df = df.sort_values(time_col).reset_index(drop=True)

    print(f"  Range: {df[time_col].iloc[0]} -> {df[time_col].iloc[-1]}")

    # ── Gap check — same logic/threshold as the MT5-based fetcher, so
    # results are directly comparable across sources.
    diffs_ms = df[time_col].diff().dt.total_seconds() * 1000
    gap_mask = diffs_ms > GAP_THRESHOLD_MS
    gaps = df.loc[gap_mask]

    if gaps.empty:
        print(f"  No tick gaps wider than {GAP_THRESHOLD_MS}ms.")
    else:
        print(f"  *** {len(gaps)} tick gap(s) wider than {GAP_THRESHOLD_MS}ms ***")
        for idx in gaps.index:
            prev_bid = df[bid_col].iloc[idx - 1]
            prev_ask = df[ask_col].iloc[idx - 1]
            this_bid = df[bid_col].iloc[idx]
            this_ask = df[ask_col].iloc[idx]
            gap_start = df[time_col].iloc[idx - 1]
            gap_end = df[time_col].iloc[idx]
            print(
                f"    {gap_start} -> {gap_end}  "
                f"({(gap_end - gap_start).total_seconds() * 1000:.0f}ms)  "
                f"bid {prev_bid}->{this_bid}  ask {prev_ask}->{this_ask}"
            )

    # ── Optional release-time excursion check, same idea as the
    # simulation run earlier against the Sept 4 tick file.
    if RELEASE_TIME_UTC is not None:
        release_time = pd.Timestamp(RELEASE_TIME_UTC, tz="UTC")
        window = df[
            (df[time_col] >= release_time - pd.Timedelta(minutes=1))
            & (df[time_col] <= release_time + pd.Timedelta(minutes=1))
        ]
        if window.empty:
            print(f"  No ticks found within 1 minute of {release_time} -- "
                  f"this file may not cover that date/time.")
        else:
            pre = window[window[time_col] <= release_time]
            post = window[window[time_col] > release_time]
            if not pre.empty and not post.empty:
                anchor = (pre[bid_col].iloc[-1] + pre[ask_col].iloc[-1]) / 2.0
                max_move = (post[bid_col] - anchor).abs().max()
                print(f"  Anchor price near release: {anchor:.3f}")
                print(f"  Max move in the minute after release: ${max_move:.3f}")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_name = os.path.splitext(os.path.basename(filepath))[0] + "_processed.csv"
    out_path = os.path.join(OUTPUT_DIR, out_name)
    df.to_csv(out_path, index=False)
    print(f"  Saved cleaned copy -> {out_path}")


def main():
    files = find_candidate_files()
    if not files:
        print(f"No CSV files found in {os.path.abspath(INPUT_DIR)}. "
              f"Extract the Exness tick history .zip download(s) here "
              f"first, or update INPUT_DIR.")
        return

    print(f"Found {len(files)} file(s) to process.")
    for f in files:
        process_file(f)


if __name__ == "__main__":
    main()