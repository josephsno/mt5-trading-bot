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
SYMBOL    = "USDCADm"
TIMEFRAME = mt5.TIMEFRAME_MN1  

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

# ── Check what history is available ──────────────────────────────────────────
# First try: just grab the last 50000 bars — simplest approach
print("\nFetching last 50000 bars...")
rates = mt5.copy_rates_from_pos(SYMBOL, TIMEFRAME, 0, 50000)

if rates is None or len(rates) == 0:
    print("copy_rates_from_pos failed:", mt5.last_error())

    # Second try: use copy_rates_range with smaller range
    print("\nTrying copy_rates_range with 2025 only...")
    START = datetime(2025, 1, 1, tzinfo=timezone.utc)
    END   = datetime(2026, 6, 11, tzinfo=timezone.utc)
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

# ── Save ──────────────────────────────────────────────────────────────────────
save_dir = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(save_dir, exist_ok=True)

start_year = df['time'].iloc[0].year
end_year   = df['time'].iloc[-1].year
filename   = f"{SYMBOL}_M15_{start_year}_{end_year}.csv"
save_path  = os.path.join(save_dir, filename)

df.to_csv(save_path, index=False)
print(f"\nSaved → {save_path}")

mt5.shutdown()