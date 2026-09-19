"""
filter_exness_by_date_september2026.py

Filters the September 2026 Exness XAUUSDm tick history CSV down to
that month's three events. Reads in chunks so the whole file never has
to load into memory at once. Point this at the raw monthly export; it
writes out one small CSV per requested date.

Release times (September 2026 is inside DST, EDT):
    2026-09-04  NFP   8:30am ET = 12:30 UTC
    2026-09-11  CPI   8:30am ET = 12:30 UTC  (today's date in this project --
                already flagged in the handoff as possibly still live/unresolved
                at time of writing, confirm before assuming stale)
    2026-09-16  FOMC  2:00pm ET = 18:00 UTC  (decision day of the Sep 15-16 meeting)

Note: NFP Sep 4 already has a live-measured real result from actual MT5
trading (+$69.21/unit, one-shot, no dual-fill data available since it
wasn't pulled via tick history) -- filtering it here lets that number
get independently rechecked against the dual-fill mechanism the same
way every other 2026 event has been.

Usage:
    python filter_exness_by_date_september2026.py
"""

import pandas as pd
import os

# ── Config ────────────────────────────────────────────────────────────────
INPUT_PATH = r"C:\Users\AFRIPOINTDEV\Downloads\Exness_XAUUSDm_2026_09_16 (1)"
# ^ if this is a folder (extracted zip contents) rather than the CSV
# itself, the script below will look inside it for a .csv automatically.
OUTPUT_DIR = "filtered"

TARGET_DATES = ["2026-09-04", "2026-09-11", "2026-09-16"]  # NFP, CPI, FOMC

CHUNK_SIZE = 500_000  # rows per chunk -- lower this if memory is still tight


def resolve_input_file(path: str) -> str:
    """Handles INPUT_PATH being either the CSV itself, a CSV with a
    hidden extension (common on Windows), or a folder containing one
    (e.g. extracted zip contents) -- rather than assuming which."""
    if os.path.isfile(path):
        return path

    if os.path.isfile(path + ".csv"):
        return path + ".csv"

    if os.path.isdir(path):
        csvs = [f for f in os.listdir(path) if f.lower().endswith(".csv")]
        if len(csvs) == 1:
            return os.path.join(path, csvs[0])
        if len(csvs) > 1:
            raise ValueError(
                f"Found multiple CSVs in {path}: {csvs}. "
                f"Point INPUT_PATH at the exact one you want."
            )
        raise ValueError(f"No .csv files found inside folder: {path}")

    raise FileNotFoundError(
        f"Could not find {path} as a file, {path}.csv, or a folder "
        f"containing a .csv. Double-check the path."
    )


def find_timestamp_column(columns) -> str:
    for candidate in ("Timestamp", "timestamp", "Time", "time", "DateTime", "datetime"):
        if candidate in columns:
            return candidate
    raise ValueError(
        f"Could not find a timestamp-like column in {list(columns)}. "
        f"Check the real header name and add it to find_timestamp_column()."
    )


def main():
    try:
        input_file = resolve_input_file(INPUT_PATH)
    except (FileNotFoundError, ValueError) as e:
        print(str(e))
        return

    print(f"Using input file: {input_file}")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"Reading {input_file} in chunks of {CHUNK_SIZE:,} rows...")

    matched_frames = {date: [] for date in TARGET_DATES}
    ts_col = None
    total_rows = 0

    for chunk_num, chunk in enumerate(pd.read_csv(input_file, chunksize=CHUNK_SIZE)):
        total_rows += len(chunk)

        if ts_col is None:
            ts_col = find_timestamp_column(chunk.columns)
            print(f"Using timestamp column: '{ts_col}'")

        chunk[ts_col] = pd.to_datetime(chunk[ts_col], format="mixed", errors="coerce")
        chunk["_date_str"] = chunk[ts_col].dt.strftime("%Y-%m-%d")

        for date in TARGET_DATES:
            match = chunk[chunk["_date_str"] == date]
            if not match.empty:
                matched_frames[date].append(match.drop(columns=["_date_str"]))

        if chunk_num % 10 == 0:
            print(f"  ...processed {total_rows:,} rows so far")

    print(f"\nDone reading. Total rows processed: {total_rows:,}\n")

    for date, frames in matched_frames.items():
        if not frames:
            print(f"{date}: NO rows found -- check the date is actually in this file's range.")
            continue
        result = pd.concat(frames, ignore_index=True)
        result = result.sort_values(ts_col).reset_index(drop=True)
        out_path = os.path.join(OUTPUT_DIR, f"XAUUSDm_{date}.csv")
        result.to_csv(out_path, index=False)
        print(f"{date}: {len(result):,} rows -> {out_path}")


if __name__ == "__main__":
    main()