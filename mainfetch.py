from datetime import datetime
import MetaTrader5 as mt5
from mt5.mt5_data_provider import MT5DataProvider
from decouple import config
import os


class DummyMT5Config:
    def __init__(self, config_dict):
        self.__dict__.update(config_dict)

    def get_market_data_date_range(
        self, symbol, timeframe, start_date, end_date=None, no_of_candles=None
    ):
        import pandas as pd

        rates = mt5.copy_rates_range(symbol, timeframe, start_date, end_date)

        if rates is None:
            return pd.DataFrame()

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        return df


if not mt5.initialize(
    path=config("MT5_PATHWAY"),
    login=int(config("MT5_USERNAME")),
    password=config("MT5_PASSWORD"),
    server=config("MT5_SERVER"),
):
    print("MT5 init failed:", mt5.last_error())
    exit()

print("MT5 connected:", mt5.account_info().server)

symbol = "EURUSDm"
all_symbols = [s.name for s in mt5.symbols_get() if "EUR" in s.name]

if symbol not in all_symbols:
    print(f"'{symbol}' not found. Available EUR symbols: {all_symbols}")
    mt5.shutdown()
    exit()

mt5.symbol_select(symbol, True)

mt5_config = DummyMT5Config(
    {
        "username": config("MT5_USERNAME"),
        "password": config("MT5_PASSWORD"),
        "server": config("MT5_SERVER"),
        "mt5_pathway": config("MT5_PATHWAY"),
    }
)

provider = MT5DataProvider(mt5_config)

start_date = datetime(2025, 1, 1)
end_date   = datetime(2026, 6, 11)

eurusd_data = provider.fetch_data(
    symbol=symbol,
    timeframe=mt5.TIMEFRAME_M15,
    start_date=start_date,
    end_date=end_date,
)

print(f"Fetched {len(eurusd_data)} bars")
print(eurusd_data.head())

# ── Save ──────────────────────────────────────────────────────────────────────
save_dir = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(save_dir, exist_ok=True)

filename = f"{symbol}_M15_2025_2026.csv"
save_path = os.path.join(save_dir, filename)

eurusd_data.to_csv(save_path, index=False)
print(f"Saved → {save_path}")

mt5.shutdown()