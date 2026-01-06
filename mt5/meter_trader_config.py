"""
MetaTrader 5 Configuration and Trade Management
Clean, modular implementation with comprehensive error handling.
"""

import os
import pandas as pd
import MetaTrader5 as mt5
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List


class MetaTraderConfig:
    """
    MetaTrader 5 connection, data fetching, and trade execution manager.

    Features:
    - MT5 initialization and connection management
    - Historical data fetching with validation
    - Trade execution with risk management
    - Trade history retrieval
    - Data export utilities
    """

    # Timeframe duration mapping (in seconds)
    TIMEFRAME_DURATIONS = {
        mt5.TIMEFRAME_M1: 60,
        mt5.TIMEFRAME_M2: 120,
        mt5.TIMEFRAME_M3: 180,
        mt5.TIMEFRAME_M4: 240,
        mt5.TIMEFRAME_M5: 300,
        mt5.TIMEFRAME_M15: 900,
        mt5.TIMEFRAME_M30: 1800,
        mt5.TIMEFRAME_H1: 3600,
        mt5.TIMEFRAME_H4: 14400,
        mt5.TIMEFRAME_D1: 86400,
    }

    def __init__(self):
        """Initialize MT5 configuration."""
        self.mt5_init = False
        self.username = None
        self.password = None
        self.server = None

    # ============================================================
    # CONNECTION & INITIALIZATION
    # ============================================================

    def start_mt5(self, project_settings: Dict[str, Any]) -> bool:
        """
        Initialize and connect to MetaTrader 5.

        Args:
            project_settings: Dictionary containing MT5 credentials:
                {
                    "mt5": {
                        "username": "12345678",
                        "password": "your_password",
                        "server": "Broker-Server",
                        "mt5_pathway": "C:/Program Files/MetaTrader 5/terminal64.exe"
                    }
                }

        Returns:
            True if successful, False otherwise
        """
        try:
            mt5_config = project_settings
            self.username = int(mt5_config["username"])
            self.password = mt5_config["password"]
            self.server = mt5_config["server"]
            mt5_pathway = mt5_config["mt5_pathway"]

            # Initialize MT5
            self.mt5_init = mt5.initialize(
                login=self.username,
                password=self.password,
                server=self.server,
                path=mt5_pathway,
            )

            if not self.mt5_init:
                error = mt5.last_error()
                print(f"❌ Failed to initialize MT5: {error}")
                return False

            # Attempt login
            if not self.login_mt5():
                return False

            print(f"✅ MT5 initialized successfully")

            return True

        except KeyError as e:
            print(f"❌ Missing configuration key: {e}")
            return False
        except Exception as e:
            print(f"❌ Error initializing MT5: {e}")
            return False

    def login_mt5(self) -> bool:
        """
        Login to MT5 account.

        Returns:
            True if login successful, False otherwise
        """
        if not self.mt5_init:
            print("❌ MT5 not initialized. Call start_mt5() first.")
            return False

        try:
            login_success = mt5.login(
                login=self.username, password=self.password, server=self.server
            )

            if not login_success:
                error = mt5.last_error()
                print(f"❌ MT5 login failed: {error}")
                return False

            print(f"✅ Logged in to MT5 account")
            return True

        except Exception as e:
            print(f"❌ Error during MT5 login: {e}")
            return False

    def check_connection(self) -> bool:
        """
        Verify MT5 connection status.

        Returns:
            True if connected, False otherwise
        """
        terminal_info = mt5.terminal_info()
        if terminal_info is None:
            print("❌ MT5 not initialized")
            return False

        if not terminal_info.connected:
            print("❌ MT5 not connected to broker")
            return False

        account_info = mt5.account_info()
        if account_info is None:
            print("❌ No account logged in")
            return False

        return True

    def get_timeframe_duration(self, timeframe: int) -> int:
        """
        Get duration in seconds for a given MT5 timeframe.

        Args:
            timeframe: MT5 timeframe constant (e.g., mt5.TIMEFRAME_M15)

        Returns:
            Duration in seconds (default: 60 if unknown)
        """
        return self.TIMEFRAME_DURATIONS.get(timeframe, 60)

    # ============================================================
    # DATA FETCHING
    # ============================================================

    def get_market_data_date_range(
        self,
        symbol: str,
        timeframe: int,
        start_date: datetime = None,
        end_date: datetime = None,
        no_of_candles: Optional[int] = None,
        download: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Fetch historical OHLCV data from MetaTrader 5.

        Args:
            symbol: Trading symbol (e.g., 'EURUSD', 'EURUSDm')
            timeframe: MT5 timeframe constant (e.g., mt5.TIMEFRAME_M15)
            start_date: Start datetime for historical data
            end_date: End datetime for historical data
            no_of_candles: Number of candles to fetch (overrides date range)
            download: Export format ('csv' or 'xlsx'), None to skip export

        Returns:
            DataFrame with columns: time, open, high, low, close, tick_volume
            Returns empty DataFrame if no data available
        """
        # Validate inputs
        if not symbol:
            print("❌ Symbol is required")
            return pd.DataFrame()

        if timeframe is None:
            print("❌ Timeframe is required")
            return pd.DataFrame()

        # Check connection
        if not self.check_connection():
            return pd.DataFrame()

        # Select symbol
        if not mt5.symbol_select(symbol, True):
            print(f"❌ Failed to select symbol: {symbol}")
            error = mt5.last_error()
            print(f"   MT5 Error: {error}")
            return pd.DataFrame()

        # Fetch data
        try:
            if no_of_candles is not None:
                # Fetch specific number of candles from start_date
                data = mt5.copy_rates_from(
                    symbol, timeframe, start_date, int(no_of_candles)
                )
            else:
                # Fetch data in date range
                data = mt5.copy_rates_range(symbol, timeframe, start_date, end_date)

        except Exception as e:
            print(f"❌ Error fetching data for {symbol}: {e}")
            return pd.DataFrame()

        # Validate response
        if data is None or len(data) == 0:
            error = mt5.last_error()
            print(f"⚠️ No data returned for {symbol}")
            print(f"   MT5 Error: {error}")
            return pd.DataFrame()

        # Convert to DataFrame
        try:
            df = pd.DataFrame.from_records(data)
        except Exception as e:
            print(f"❌ Error converting data to DataFrame: {e}")
            return pd.DataFrame()

        # Validate required columns
        required_columns = {"time", "open", "high", "low", "close", "tick_volume"}
        missing = required_columns - set(df.columns)
        if missing:
            print(f"❌ MT5 data missing columns: {missing}")
            print(f"   Available columns: {df.columns.tolist()}")
            return pd.DataFrame()

        # Convert time to datetime
        df["time"] = pd.to_datetime(df["time"], unit="s")

        print(f"✅ Fetched {len(df)} bars for {symbol}")

        # Optional export
        if download:
            self.export_data(
                df, filename=f"{symbol.lower()}_market_data", filetype=download
            )

        return df

    # ============================================================
    # TRADE HISTORY
    # ============================================================

    def get_trade_history(
        self,
        start_time: datetime,
        end_time: datetime,
        symbols: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        """
        Retrieve closed trades from MT5 in a given time range.

        Args:
            start_time: Start of history period
            end_time: End of history period
            symbols: List of symbols to filter (None for all)

        Returns:
            DataFrame with trade history
        """
        # Ensure timezone awareness
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)

        all_data = []

        # Get symbols to query
        if symbols is None:
            symbols = ["*"]  # All symbols

        for sym in symbols:
            history = mt5.history_deals_get(start_time, end_time, group=sym)
            if history is None:
                continue

            for deal in history:
                # Only include closed positions (with profit/loss)
                if deal.profit != 0:
                    all_data.append(
                        {
                            "ticket": deal.ticket,
                            "symbol": deal.symbol,
                            "time": datetime.fromtimestamp(deal.time, tz=timezone.utc),
                            "type": "buy" if deal.type == 0 else "sell",
                            "volume": deal.volume,
                            "price": deal.price,
                            "profit": deal.profit,
                            "comment": deal.comment,
                            "exit_reason": (
                                "hit_sl" if "sl" in deal.comment.lower() else "hit_tp"
                            ),
                        }
                    )

        if not all_data:
            print("⚠️ No closed trades found in this period")
            return pd.DataFrame()

        df = pd.DataFrame(all_data)
        print(f"✅ Retrieved {len(df)} closed trades")

        return df

    # ============================================================
    # TRADE EXECUTION
    # ============================================================
    def get_open_trades_count(self, symbol=None):
        positions = mt5.positions_get()
        if positions is None:
            return 0

        if symbol:
            return sum(1 for p in positions if p.symbol == symbol)

        return len(positions)

    def can_trade_symbol(self, symbol: str, cooldown_minutes: int = 0) -> bool:
        """
        Check if a symbol can be traded based on:
        1. No open positions for the symbol
        2. Cooldown period since last closed trade

        Args:
            symbol: Trading symbol
            cooldown_minutes: Minutes to wait after closing a trade (default: 0)

        Returns:
            True if symbol can be traded, False otherwise
        """
        # Check for open positions
        open_positions = mt5.positions_get(symbol=symbol)
        if open_positions and len(open_positions) > 10:
            print(f"⚠️ {symbol} already has an open position — skipping")
            return False

        # Check cooldown period
        if cooldown_minutes > 0:
            now = datetime.now(timezone.utc)
            from_time = now - timedelta(days=1)

            closed_trades = mt5.history_deals_get(from_time, now, group=symbol)
            if closed_trades:
                exit_deals = [
                    deal for deal in closed_trades if deal.entry == mt5.DEAL_ENTRY_OUT
                ]

                if exit_deals:
                    last_close_time = max(deal.time for deal in exit_deals)
                    last_close_dt = datetime.fromtimestamp(
                        last_close_time, tz=timezone.utc
                    )
                    # time_diff = now - last_close_dt

                    # if time_diff < timedelta(minutes=cooldown_minutes):
                    #     mins_elapsed = int(time_diff.total_seconds() // 60)
                    #     print(
                    #         f"⏳ {symbol} closed {mins_elapsed} min ago — cooling down"
                    #     )
                    #     return False

        print(f"✅ {symbol} is available for trading")
        return True

    def calculate_position_size(
        self,
        symbol: str,
        account_risk_percent: float = 1.0,
        stop_loss_pips: Optional[float] = None,
        entry_price: Optional[float] = None,
        stop_price: Optional[float] = None,
    ) -> float:
        """
        Calculate position size based on account risk.

        Args:
            symbol: Trading symbol
            account_risk_percent: Percentage of account to risk (default: 1.0%)
            stop_loss_pips: Stop loss in pips (optional)
            entry_price: Entry price (optional, uses current price if None)
            stop_price: Stop loss price (optional)

        Returns:
            Lot size (volume)
        """
        account_info = mt5.account_info()
        if account_info is None:
            print("❌ Cannot get account info")
            return 0.01  # Minimum lot size

        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            print(f"❌ Cannot get symbol info for {symbol}")
            return 0.01

        # Calculate risk amount
        risk_amount = account_info.balance * (account_risk_percent / 100)

        # Get pip value
        tick_value = symbol_info.trade_tick_value
        tick_size = symbol_info.trade_tick_size

        # Calculate stop loss in price terms
        if stop_price and entry_price:
            stop_distance = abs(entry_price - stop_price)
        elif stop_loss_pips:
            stop_distance = stop_loss_pips * symbol_info.point * 10
        else:
            print("⚠️ No stop loss provided, using default risk")
            return 0.01

        # Calculate lot size
        if stop_distance > 0:
            lot_size = risk_amount / (stop_distance / tick_size * tick_value)

            # Round to symbol's volume step
            volume_step = symbol_info.volume_step
            lot_size = round(lot_size / volume_step) * volume_step

            # Ensure within limits
            lot_size = max(
                symbol_info.volume_min, min(lot_size, symbol_info.volume_max)
            )

            return lot_size

        return 0.01

    def execute_trade(
        self,
        symbol: str,
        signal: str,
        entry_price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        lot_size: Optional[float] = None,
        deviation: int = 10,
        strategy_name: str = "auto_strategy",
        cooldown_minutes: int = 20,
    ) -> bool:
        """
        Execute a buy or sell trade in MetaTrader 5.

        Args:
            symbol: Trading symbol
            signal: 'buy' or 'sell'
            entry_price: Entry price (None = market price)
            stop_loss: Stop loss price
            take_profit: Take profit price
            lot_size: Position size (None = auto-calculate)
            deviation: Maximum price slippage in points
            strategy_name: Strategy identifier for comments
            cooldown_minutes: Minutes to wait before trading same symbol again

        Returns:
            True if trade executed successfully, False otherwise
        """
        # Validate signal
        if signal not in ["buy", "sell"]:
            print(f"⚠️ Invalid signal: {signal}")
            return False

        # Check if can trade
        if not self.can_trade_symbol(symbol, cooldown_minutes):
            return False

        # Get current price
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            print(f"❌ Failed to get tick for {symbol}")
            return False

        # Determine order type and price
        if signal == "buy":
            order_type = mt5.ORDER_TYPE_BUY
            price = entry_price if entry_price else tick.ask
        else:  # sell
            order_type = mt5.ORDER_TYPE_SELL
            price = entry_price if entry_price else tick.bid

        # Auto-calculate lot size if not provided
        if lot_size is None:
            if stop_loss:
                lot_size = self.calculate_position_size(
                    symbol,
                    account_risk_percent=1.0,
                    entry_price=price,
                    stop_price=stop_loss,
                )
            else:
                lot_size = 0.01

        # Prepare trade request
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot_size,
            "type": order_type,
            "price": price,
            "deviation": deviation,
            "magic": 123456,
            "comment": f"{strategy_name}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        # Add SL/TP if provided
        if stop_loss:
            request["sl"] = stop_loss
        if take_profit:
            request["tp"] = take_profit

        # Execute trade
        result = mt5.order_send(request)

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"❌ Trade failed for {symbol}")
            print(f"   Return code: {result.retcode}")
            print(f"   Comment: {result.comment}")
            return False

        print(
            f"✅ Trade executed: {signal.upper()} {lot_size} lots of {symbol} @ {price}"
        )
        if stop_loss:
            print(f"   SL: {stop_loss}")
        if take_profit:
            print(f"   TP: {take_profit}")

        return True

    # ============================================================
    # UTILITIES
    # ============================================================

    def export_data(
        self, data: pd.DataFrame, filename: str = "market_data", filetype: str = "csv"
    ) -> Optional[str]:
        """
        Export DataFrame to CSV or Excel.

        Args:
            data: DataFrame to export
            filename: File name without extension
            filetype: 'csv' or 'xlsx'

        Returns:
            Full path to saved file, or None if failed
        """
        if data is None or data.empty:
            print("⚠️ No data to export")
            return None

        # Create export directory
        export_dir = os.path.join(os.getcwd(), "exports")
        os.makedirs(export_dir, exist_ok=True)

        # Clean filename
        filename = filename.replace(" ", "_")

        # Determine file path
        if filetype.lower() == "xlsx":
            filepath = os.path.join(export_dir, f"{filename}.xlsx")
            data.to_excel(filepath, index=False)
        else:
            filepath = os.path.join(export_dir, f"{filename}.csv")
            data.to_csv(filepath, index=False)

        print(f"✅ Data exported: {filepath}")
        return filepath

    def shutdown(self):
        """Shutdown MT5 connection."""
        mt5.shutdown()
        print("✅ MT5 connection closed")
