from datetime import datetime
import MetaTrader5

# Import your modules
from mt5.meter_trader_config import MetaTraderConfig
from backtests_.reset import TradingSystem
from mt5.mt5_data_provider import MT5DataProvider
from backtests_.backtester import Backtester
from main import project_settings
from datetime import datetime
import pandas as pd

# 1. Setup MT5
mt5_config = MetaTraderConfig()
mt5_config.start_mt5(project_settings)

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
# higher_tf = MetaTrader5.TIMEFRAME_M30


def backtest_by_year_df(symbols, start_year, end_year, initial_balance=100):
    """
    Run backtests for multiple strategies year by year using a DataFrame to hold strategies.

    Parameters
    ----------
    strategies_df : pd.DataFrame
        DataFrame with columns ["strategy_name", "strategy_instance"]
    symbols : list
        List of symbols to trade
    start_year : int
        Start year (inclusive)
    end_year : int
        End year (inclusive)
    initial_balance : float
        Starting balance for each year

    Returns
    -------
    pd.DataFrame
        Aggregated yearly results for all strategies
    """
    results_list = []

    for year in range(start_year, end_year + 1):
        year_start = datetime(year, 1, 1)
        year_end = datetime(year, 12, 31, 23, 59, 59)

        # Fetch data for the year
        df_dict = data_provider.fetch_multiple_symbols(
            symbols=symbols,
            timeframe=timeframe,
            start_date=year_start,
            end_date=year_end,
        )

        print(f"\n{'='*50}\n📅 BACKTESTING YEAR: {year}\n{'='*50}\n")

        strategy_name = "RANDOMNESS"

        # Reset trading system for each strategy/year
        trading_system = TradingSystem(
            balance=initial_balance,
            risk_per_trade=0.02,
            max_positions=5,
            max_daily_loss=0.05,
            max_drawdown=0.15,
            correlation_groups={"EURUSD": "USD", "GBPUSD": "USD", "USDJPY": "USD"},
        )

        backtester = Backtester(
            trading_system=trading_system,
            timeframe=timeframe,  # adjust as needed
        )

        # Filter out symbols with no data
        filtered_df_dict = {
            sym: df for sym, df in df_dict.items() if df is not None and not df.empty
        }
        if not filtered_df_dict:
            print(
                f"⚠️ No data available for any symbol in {year}, skipping strategy {strategy_name}"
            )
            continue

        # Run backtest
        results = backtester.run(
            symbols=list(filtered_df_dict.keys()),
            df_dict=filtered_df_dict,
            start_date=year_start,
            end_date=year_end,
        )

        if results:

            lowest_balance = results["lowest_balance"]
            lowest_balance_pct = (
                (lowest_balance - initial_balance) / initial_balance
            ) * 100
            highest_balance = results["highest_balance"]
            highest_balance_pct = (
                (highest_balance - initial_balance) / initial_balance
            ) * 100

            results_list.append(
                {
                    "year": year,
                    "strategy": strategy_name,
                    "total_trades": results["total_trades"],
                    "winning_trades": results["winning_trades"],
                    "losing_trades": results["losing_trades"],
                    "win_rate": results["win_rate"],
                    "net_pnl": results["net_pnl"],
                    "return_pct": results["return_pct"],
                    "lowest_balance_pct": lowest_balance_pct,
                    "lowest_balance": lowest_balance,
                    "highest_balance_pct": highest_balance_pct,
                    "highest_balance": highest_balance,
                    "max_drawdown_pct": results["max_drawdown_pct"],
                    "final_balance": results["final_balance"],
                    "max_drawdown_pct": results["max_drawdown_pct"],
                }
            )

    return pd.DataFrame(results_list)


symbols = [
    "EURUSDm",  # 0.65p 0.4l
    # "GBPJPYm",  # 0.6p 0.4l
    # "EURJPYm",  # 0.8p 0.4l
    # "USDJPYm",  # 0.8p 0.4l
    # "CADJPYm",  # 0.8p 0.4l
    # "AUDJPYm",  # 0.8p 0.4l
    # "SGDJPYm",  # 0.8p 0.4l
    # "XAUUSDm"
]

data_provider = MT5DataProvider(mt5_config)


yearly_results = backtest_by_year_df(symbols, start_year=2021, end_year=2026)

# Export results
yearly_results.to_csv(f"yearly_strategy_comparison.csv", index=False)
print(yearly_results)
