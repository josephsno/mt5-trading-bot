from datetime import datetime, timezone
import MetaTrader5 as mt5
from decouple import config
import pandas as pd
import os


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
SYMBOL    = "XAUUSDm"
TIMEFRAME = mt5.TIMEFRAME_M1

t = "1m"

# ── Select symbol ─────────────────────────────────────────────────────────────
if not mt5.symbol_select(SYMBOL, True):
    print(f"Failed to select {SYMBOL}:", mt5.last_error())
    mt5.shutdown()
    exit()

# ── Check symbol info ─────────────────────────────────────────────────────────
info = mt5.symbol_info(SYMBOL)
if info is None:
    print(f"Symbol {SYMBOL} not found on this broker")
    mt5.shutdown()
    exit()

print(f"Symbol OK: {SYMBOL}")
print(f"Digits: {info.digits}")
print("contract_size:", info.trade_contract_size)
print("tick_value:", info.trade_tick_value)
print("tick_size:", info.trade_tick_size)
print("point:", info.point)
print("digits:", info.digits)

# ── Check what history is available ──────────────────────────────────────────
# First try: just grab the last 50000 bars — simplest approach
print("\nFetching last 50000 bars...")
rates = mt5.copy_rates_from_pos(SYMBOL, TIMEFRAME, 0, 50000)

if rates is None or len(rates) == 0:
    print("copy_rates_from_pos failed:", mt5.last_error())

    # Second try: use copy_rates_range with smaller range
    print("\nTrying copy_rates_range with 2025 only...")
    START = datetime(2025, 1, 1, tzinfo=timezone.utc)
    END   = datetime(2026, 8, 25, tzinfo=timezone.utc)
    rates = mt5.copy_rates_range(SYMBOL, TIMEFRAME, START, END)

    if rates is None or len(rates) == 0:
        print("copy_rates_range also failed:", mt5.last_error())
        mt5.shutdown()
        exit()

df = pd.DataFrame(rates)
df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)

print(f"\nFetched {len(df)} bars")
print(f"Range: {df['time'].iloc[0]} → {df['time'].iloc[-1]}")
print(df.head())

# ── Gap check ─────────────────────────────────────────────────────────────────
# copy_rates_from_pos / copy_rates_range only return what the terminal's LOCAL
# history cache already has — they do NOT force a fresh download for anything
# missing, and a partial result is not None, so it fails silently otherwise.
# Flag any gap wider than expected for this timeframe so an incomplete
# terminal history cache doesn't quietly ship into a backtest.
GAP_THRESHOLD_HOURS = 50  # covers a normal Fri~22:00 -> Sun~22:00 UTC weekend
# close without flagging it as an error; anything wider is suspicious.

diffs = df["time"].diff()
gap_mask = diffs > pd.Timedelta(hours=GAP_THRESHOLD_HOURS)
gaps = df.loc[gap_mask, "time"]

if gaps.empty:
    print(f"\nGap check OK — no gaps wider than {GAP_THRESHOLD_HOURS}h found.")
else:
    print(f"\n*** WARNING: {len(gaps)} gap(s) wider than {GAP_THRESHOLD_HOURS}h found ***")
    for idx in gaps.index:
        gap_start = df["time"].iloc[idx - 1]
        gap_end = df["time"].iloc[idx]
        gap_len = gap_end - gap_start
        print(f"  Missing: {gap_start} -> {gap_end}  ({gap_len})")
    print(
        "Most of these are probably legitimate extended closures (holidays "
        "like Thanksgiving/Easter/July 4th, where gold closes early and "
        "reopens late around a normal weekend) — expected, not a bug. But "
        "any gap of several DAYS with no obvious holiday nearby likely means "
        "the terminal's local history cache is incomplete for that window, "
        "not that the market was closed. Check each one against a market "
        "calendar; for a real gap, open this symbol's chart in the MT5 "
        "terminal and manually scroll back through the missing window to "
        "force a backfill from the broker, then re-run this export."
    )

# ── Save ──────────────────────────────────────────────────────────────────────
save_dir = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(save_dir, exist_ok=True)

start_year = df['time'].iloc[0].year
end_year   = df['time'].iloc[-1].year
filename   = f"{SYMBOL}_{t}_{start_year}_{end_year}.csv"
save_path  = os.path.join(save_dir, filename)

df.to_csv(save_path, index=False)
print(f"\nSaved → {save_path}")

mt5.shutdown()