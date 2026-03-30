from datetime import datetime
import MetaTrader5

# Import your modules
from mt5.meter_trader_config import MetaTraderConfig
from backtests_.reset import TradingSystem
from strategies.strategy import MACDTrendStrategy

from mt5.mt5_data_provider import MT5DataProvider
from backtests_.backtester import Backtester
from main import project_settings
from datetime import datetime
import pandas as pd


# 1. Setup MT5
mt5_config = MetaTraderConfig()
mt5_config.start_mt5(project_settings)


symbols = [
    "USDJPYm",  # USD/JPY micro
    "EURUSDm",  # EUR/USD micro
    "GBPUSDm",  # GBP/USD micro
    "GBPJPYm",  # GBP/JPY micro
]
data_provider = MT5DataProvider(mt5_config)

# MT5 timeframe mapping
MT5_TIMEFRAME_MAP = {
    MetaTrader5.TIMEFRAME_M1: "1 Min",
    MetaTrader5.TIMEFRAME_M5: "5 Min",
    MetaTrader5.TIMEFRAME_M10: "10 Min",
    MetaTrader5.TIMEFRAME_M15: "15 Min",
    MetaTrader5.TIMEFRAME_M30: "30 Min",
    MetaTrader5.TIMEFRAME_H1: "1 Hour",
    MetaTrader5.TIMEFRAME_H4: "4 Hour",
    MetaTrader5.TIMEFRAME_D1: "1 Day",
    MetaTrader5.TIMEFRAME_W1: "1 Week",
    MetaTrader5.TIMEFRAME_MN1: "1 Month",
}

timeframe = MetaTrader5.TIMEFRAME_M15


def backtest_by_month_df(strategies_df, symbols, year, initial_balance=1000):
    """
    Run backtests month-by-month for a single year.

    Parameters
    ----------
    strategies_df : pd.DataFrame
        Columns: ["strategy_name", "strategy_instance"]
    symbols : list
        Symbols to backtest
    year : int
        Year to backtest (e.g. 2024)
    initial_balance : float
        Starting balance per month

    Returns
    -------
    pd.DataFrame
        Monthly backtest results
    """
    results_list = []

    for month in range(1, 13):
        month_start = datetime(year, month, 1)
        if month == 12:
            month_end = datetime(year, 12, 31, 23, 59, 59)
        else:
            month_end = datetime(year, month + 1, 1) - pd.Timedelta(seconds=1)

        print(f"\n{'='*50}\n📅 BACKTESTING: {year}-{month:02d}\n{'='*50}\n")

        # Fetch monthly data
        df_dict = data_provider.fetch_multiple_symbols(
            symbols=symbols,
            timeframe=timeframe,
            start_date=month_start,
            end_date=month_end,
        )

        strategy_name = "RANDOMNESS"

        trading_system = TradingSystem(
            balance=initial_balance,
            risk_per_trade=0.02,
            max_positions=5,
            max_daily_loss=0.05,
            max_drawdown=0.15,
            correlation_groups={
                "EURUSD": "USD",
                "GBPUSD": "USD",
                "USDJPY": "USD",
            },
        )

        backtester = Backtester(
            trading_system=trading_system,
            timeframe="1H",
        )

        # Remove symbols without data
        filtered_df_dict = {
            sym: df
            for sym, df in df_dict.items()
            if df is not None and not df.empty
        }

        if not filtered_df_dict:
            print(f"⚠️ No data for {year}-{month:02d}, skipping {strategy_name}")
            continue

        results = backtester.run(
            symbols=list(filtered_df_dict.keys()),
            df_dict=filtered_df_dict,
            start_date=month_start,
            end_date=month_end,
        )

        if not results:
            continue

        equity_df = results["equity_curve_df"]
        lowest_balance = equity_df["balance"].min()

        results_list.append(
            {
                "year": year,
                "month": month,
                "month_name": month_start.strftime("%B"),
                "strategy": strategy_name,
                "total_trades": results["total_trades"],
                "winning_trades": results["winning_trades"],
                "losing_trades": results["losing_trades"],
                "win_rate": results["win_rate"],
                "net_pnl": results["net_pnl"],
                "return_pct": results["return_pct"],
                "lowest_balance": lowest_balance,
                "lowest_balance_pct": (
                    (lowest_balance - initial_balance) / initial_balance
                )
                * 100,
                "max_drawdown_pct": results["max_drawdown_pct"],
                "final_balance": results["final_balance"],
            }
        )

    return pd.DataFrame(results_list)


# ------------------------
# Example usage
# ------------------------

symbols = [
    "USDJPYm",  # USD/JPY micro
    "EURUSDm",  # EUR/USD micro
    "GBPUSDm",  # GBP/USD micro
    "GBPJPYm",  # GBP/JPY micro
]
data_provider = MT5DataProvider(mt5_config)

# MT5 timeframe mapping
MT5_TIMEFRAME_MAP = {
    MetaTrader5.TIMEFRAME_M1: "1 Min",
    MetaTrader5.TIMEFRAME_M5: "5 Min",
    MetaTrader5.TIMEFRAME_M10: "10 Min",
    MetaTrader5.TIMEFRAME_M15: "15 Min",
    MetaTrader5.TIMEFRAME_M30: "30 Min",
    MetaTrader5.TIMEFRAME_H1: "1 Hour",
    MetaTrader5.TIMEFRAME_H4: "4 Hour",
    MetaTrader5.TIMEFRAME_D1: "1 Day",
    MetaTrader5.TIMEFRAME_W1: "1 Week",
    MetaTrader5.TIMEFRAME_MN1: "1 Month",
}

timeframe = MetaTrader5.TIMEFRAME_M15

year = 2024
yearly_results = backtest_by_month_df(symbols, year=year)

# Export results
yearly_results.to_csv(f"monthly_strategy_comparison_{year}_adaptive.csv", index=False)
print(yearly_results)
