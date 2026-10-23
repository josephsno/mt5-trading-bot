"""
Export daily (D1) bars 2018 -> today and the swap / contract specs for each symbol.

Run on the VPS:   python export_daily_and_swaps.py
Output (same folder):
    D1_<SYMBOL>.csv       time, open, high, low, close, tick_volume, spread, real_volume
    symbol_specs.csv      swap_long, swap_short, swap_mode, swap_rollover3days, contract size, tick value, ...
Uses the same .env keys as the bots: MT5_PATHWAY, MT5_USERNAME, MT5_PASSWORD, MT5_SERVER.

If a CSV starts later than 2018: in MT5 go to Tools > Options > Charts, set "Max bars in chart" to Unlimited,
restart MT5, open a D1 chart of that symbol and scroll back to load history, then run this again.
"""
import datetime
import csv

import MetaTrader5 as mt5
from decouple import config

SYMBOLS = [
    # FX majors + carry pairs
    "EURUSDm", "GBPUSDm", "USDJPYm", "AUDUSDm", "NZDUSDm", "USDCADm", "USDCHFm",
    "AUDJPYm", "NZDJPYm", "EURJPYm", "GBPJPYm", "CADJPYm", "CHFJPYm",
    # metals / indices / crypto
    "XAUUSDm", "XAGUSDm", "US30m", "USTECm", "US500m", "BTCUSDm",
]
DATE_FROM = datetime.datetime(2018, 1, 1, tzinfo=datetime.timezone.utc)
DATE_TO = datetime.datetime.now(datetime.timezone.utc)

SPEC_FIELDS = ["swap_long", "swap_short", "swap_mode", "swap_rollover3days", "trade_contract_size",
               "trade_tick_value", "trade_tick_size", "point", "digits", "volume_min", "volume_step",
               "currency_base", "currency_profit", "spread"]


def main() -> None:
    ok = mt5.initialize(
        path=config("MT5_PATHWAY"),
        login=int(config("MT5_USERNAME")),
        password=config("MT5_PASSWORD"),
        server=config("MT5_SERVER"),
    )
    if not ok:
        print("initialize() failed:", mt5.last_error())
        return

    specs = []
    for sym in SYMBOLS:
        if not mt5.symbol_select(sym, True):
            print(f"{sym}: not available on this account - skipped")
            continue
        info = mt5.symbol_info(sym)
        if info is None:
            print(f"{sym}: symbol_info returned None - skipped")
            continue
        specs.append([sym] + [getattr(info, f) for f in SPEC_FIELDS])

        rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_D1, DATE_FROM, DATE_TO)
        if rates is None or len(rates) == 0:
            print(f"{sym}: no D1 bars returned - {mt5.last_error()}")
            continue
        with open(f"D1_{sym}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"])
            for r in rates:
                t = datetime.datetime.fromtimestamp(int(r["time"]), tz=datetime.timezone.utc).strftime("%Y-%m-%d")
                w.writerow([t, r["open"], r["high"], r["low"], r["close"], int(r["tick_volume"]), int(r["spread"]), int(r["real_volume"])])
        first = datetime.datetime.fromtimestamp(int(rates[0]["time"]), tz=datetime.timezone.utc).date()
        print(f"{sym}: {len(rates)} D1 bars from {first}")

    with open("symbol_specs.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol"] + SPEC_FIELDS)
        w.writerows(specs)
    print(f"symbol_specs.csv: {len(specs)} symbols")
    mt5.shutdown()


if __name__ == "__main__":
    main()