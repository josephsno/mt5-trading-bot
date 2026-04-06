import pandas as pd
from strategies.strategy import RSIFlexibleStrategyV1, RSIFlexibleStrategy
import pytz


class Backtester:
    """
    Generic backtester that works with any strategy and data source

    Completely modular - plug in any:
    - Strategy (must have generate_signal() method)
    - Data source (MT5, CSV, API, etc.)
    - Trading system (for risk management)
    """

    def __init__(self, trading_system=None, timeframe=None):
        """
        Parameters:
        -----------
        strategy : object
            Strategy object with generate_signal(price_data) method
        trading_system : object, optional
            Trading system with risk management (if None, basic position sizing used)
        """
        self.trading_system = trading_system

        # Results tracking
        self.trades = []
        self.equity_curve = []
        self.open_positions = {}

        # Performance metrics
        self.initial_balance = None
        self.current_balance = None
        self.timeframe = timeframe
        
        self.strategy = RSIFlexibleStrategy(
            allowed_weekdays=[1,2, 3],  # Monday-Friday trading
            # allowed_hours=[8, 9, 10, 11, 12, 13, 14, 15, 16,17,18,19],  # London + early NY
            backtest_mode=True,
            initial_balance=100,
            sl_pips=30,
            use_volume_filter=False

        )

    def format_date_range(self, start_date, end_date):
        delta_days = (end_date - start_date).days

        if delta_days < 31:
            return f"{delta_days} days"
        elif delta_days < 365:
            months = delta_days // 30
            return f"{months} months"
        else:
            years = delta_days // 365
            return f"{years} years"

    def prepare_price_windows(self, df, lookback_bars=250):
        """
        Yield rolling pandas DataFrame windows
        """
        for i in range(lookback_bars, len(df)):
            window_df = df.iloc[i - lookback_bars : i + 1].copy()
            yield window_df, i

    def calculate_position_size(
        self, entry_price, stop_loss, risk_percent=2, max_lots=5.0
    ):

        return 0.01

    def create_trade(self, symbol, signal_data, current_time, bar_index, position_size):
        """
        Create a trade object from signal

        Parameters:
        -----------
        symbol : str
        signal_data : dict
            Output from strategy.generate_signal()
        current_time : datetime
        bar_index : int

        Returns:
        --------
        dict or None
            Trade object or None if invalid signal
        """
        if not signal_data["signal"]:
            return None

        trade = {
            "symbol": symbol,
            "signal": signal_data["signal"],
            "entry_time": current_time,
            "entry_price": signal_data["entry_price"],
            "stop_loss": signal_data["stop_loss"],
            "take_profit": signal_data["take_profit"],
            "position_size": position_size,
            "entry_bar_index": bar_index,
            "entry_rsi": signal_data.get("rsi"),
            "entry_ma": signal_data.get("ma"),
            "exit_time": None,
            "exit_price": None,
            "exit_bar_index": None,
            "exit_reason": None,
            "pnl": 0,
            "pnl_r": 0,  # P&L in R multiples
            "bars_held": 0,
            "mae": 0,  # Maximum Adverse Excursion
            "mfe": 0,  # Maximum Favorable Excursion
        }

        return trade

    def check_exit(self, trade, high, low, close, bar_index):
        if trade["signal"] == "buy":
            # SL must be below entry to be valid
            if trade["stop_loss"] < trade["entry_price"] and low <= trade["stop_loss"]:
                return True, trade["stop_loss"], "stop_loss"
            if high >= trade["take_profit"]:
                return True, trade["take_profit"], "take_profit"

        elif trade["signal"] == "sell":
            # SL must be above entry to be valid
            if trade["stop_loss"] > trade["entry_price"] and high >= trade["stop_loss"]:
                return True, trade["stop_loss"], "stop_loss"
            if low <= trade["take_profit"]:
                return True, trade["take_profit"], "take_profit"

        return False, None, None

    def calculate_pnl(self, trade, exit_price):
        symbol = trade["symbol"]
        entry_price = trade["entry_price"]
        lot_size = trade["position_size"]

        # ✅ Fix: Check XAU first and separately
        is_jpy = "JPY" in symbol
        is_xau = symbol.startswith("XAU")  # ✅ Only startswith, not endswith USDm
        is_usd = not is_jpy and not is_xau  # everything else

        # Pip size
        pip_size = 0.01 if is_jpy else (0.1 if is_xau else 0.0001)

        # Pip movement
        if trade["signal"] == "buy":
            pips = (exit_price - entry_price) / pip_size
        else:
            pips = (entry_price - exit_price) / pip_size

        # Pip value per 0.01 lot
        if is_jpy:
            pip_value_001 = 1.91 / 30
        elif is_xau:
            pip_value_001 = 1.0 / 10   # XAU: $1 per 10 cent move @ 0.01
        else:
            pip_value_001 = 3.00 / 30  # ✅ EURUSDm correctly hits here = $0.10/pip

        # Scale by lot size
        pip_value = pip_value_001 * (lot_size / 0.01)

        pnl = pips * pip_value

        # R-multiple
        stop_pips = abs(entry_price - trade["stop_loss"]) / pip_size
        risk_amount = stop_pips * pip_value
        pnl_r = pnl / risk_amount if risk_amount > 0 else 0

        trade["pnl"] = round(pnl, 2)
        trade["pnl_r"] = round(pnl_r, 2)

        return trade


    def update_balance(self, pnl):
        """Update account balance"""
        self.current_balance += pnl

        if self.trading_system:
            self.trading_system.balance += pnl
            self.trading_system.daily_pnl += pnl
            self.trading_system.update_peak_balance()

    def check_risk_limits(self, symbol):
        """
        Check if new trade is allowed based on risk management

        Parameters:
        -----------
        symbol : str

        Returns:
        --------
        bool
            True if trade allowed, False otherwise
        """
        if not self.trading_system:
            # Basic check - max 5 open positions
            return len(self.open_positions) < 5

        # Use trading system's checks
        if not self.trading_system.check_account_risk():
            return False

        if not self.trading_system.check_correlation_risk(symbol):
            return False

        return True

    def calculate_lot_size(account_balance, risk_percent, entry_price, sl_price):
        """
        Determine trade size based on risk percentage and stop loss distance.
        """
        risk_amount = account_balance * (risk_percent / 100)
        pip_value = 0.0001  # For Forex, adjust for symbol
        stop_distance = abs(entry_price - sl_price)

        # Lots = risk / (stop distance in price units * pip value)
        lot_size = risk_amount / (stop_distance / pip_value)
        return lot_size

    def run(
        self,
        symbols,
        df_dict,
        low_df_dict=None,
        high_df_dict=None,
        start_date=None,
        end_date=None,
    ):

        print(f"\n{'='*70}")
        print(f"🚀 STARTING BACKTEST")
        print(f"{'='*70}")
        print(f"Strategy: {self.strategy}")
        print(f"Symbols: {symbols}")

        # Initialize balance
        self.initial_balance = (
            self.trading_system.initial_balance if self.trading_system else 10000
        )
        self.current_balance = (
            self.trading_system.balance if self.trading_system else 10000
        )
        print(f"Initial Balance: ${self.initial_balance:,.2f}")
        print(f"{'='*70}\n")

        for symbol in symbols:
            # Determine which data to use
            if low_df_dict and high_df_dict:
                ltf_df_full = low_df_dict.get(symbol)
                htf_df_full = high_df_dict.get(symbol)
                if ltf_df_full is None or htf_df_full is None:
                    print(f"⚠️ Missing LTF or HTF data for {symbol}, skipping...")
                    continue
                df_to_use = ltf_df_full
            else:
                df_to_use = df_dict.get(symbol)
                ltf_df_full = None
                htf_df_full = None
                if df_to_use is None:
                    print(f"⚠️ No data for {symbol}, skipping...")
                    continue

            # Filter by date
            if start_date:
                df_to_use = df_to_use[df_to_use["time"] >= start_date]
            if end_date:
                df_to_use = df_to_use[df_to_use["time"] <= end_date]

            if df_to_use.empty:
                print(f"⚠️ No data after filtering dates for {symbol}, skipping...")
                continue

            self.backtest_start = df_to_use["time"].iloc[0]
            self.backtest_end = df_to_use["time"].iloc[-1]

            print(f"\n📊 Processing {symbol} ({len(df_to_use)} bars)...")
            self.highest_balance = 0
            self.lowest_balance = 1000000000

            # ✅ Simple flag: can we look for new signals?
            can_trade = True

            # Iterate over bars
            for window, bar_idx in self.prepare_price_windows(df_to_use):
                current_time = window["time"].iloc[-1]
                high = window["high"].iloc[-1]
                low = window["low"].iloc[-1]
                close = window["close"].iloc[-1]

                # Determine minimum bars needed for all indicators
                min_bars_needed = max(
                    getattr(self.strategy, "ema_period", 200),
                    getattr(self.strategy, "ltf_rsi_period", 14),
                    getattr(self.strategy, "htf_rsi_period", 14),
                    50,  # for MACD etc.
                )

                # Slice LTF and HTF up to current bar
                if ltf_df_full is not None and htf_df_full is not None:
                    ltf_window = ltf_df_full.iloc[
                        max(0, bar_idx - min_bars_needed + 1) : bar_idx + 1
                    ]

                else:
                    ltf_window = window

                # Skip bars with insufficient data
                if len(ltf_window) < min_bars_needed:
                    continue

                # ✅ STEP 1: Check if we have an open trade - manage it first
                if symbol in self.open_positions:
                    trade = self.open_positions[symbol]
                    should_exit, exit_price, exit_reason = self.check_exit(
                        trade, high, low, close, bar_idx
                    )

                    if should_exit:
                        # Close the trade
                        trade["exit_time"] = current_time
                        trade["exit_price"] = exit_price
                        trade["exit_reason"] = exit_reason
                        trade["exit_bar_index"] = bar_idx
                        trade["bars_held"] = bar_idx - trade["entry_bar_index"]
                        trade = self.calculate_pnl(trade, exit_price)
                        self.update_balance(trade["pnl"])
                        self.trades.append(trade)
                        del self.open_positions[symbol]

                        emoji = "✅" if trade["pnl"] > 0 else "❌"
                        print(
                            f"{emoji} {symbol} | {exit_reason.upper()} | "
                            f"P&L: ${trade['pnl']:.2f} ({trade['pnl_r']:.2f}R) | "
                            f"Balance: ${self.current_balance:,.2f} | position_size:{trade['position_size']}"
                        )

                        was_win = trade["pnl"] > 0
                        self.strategy.update_trade_result(was_win=was_win)
                        self.strategy.update_balance(new_balance=self.current_balance)

                        # ✅ Trade closed - NOW we can look for new signals again
                        can_trade = True
                    else:
                        # Trade still open - don't look for new signals
                        can_trade = False

                # Update balance tracking
                if self.current_balance > self.highest_balance:
                    self.highest_balance = self.current_balance
                if self.current_balance < self.lowest_balance:
                    self.lowest_balance = self.current_balance

                # ✅ STEP 2: Only look for new signals if we're allowed to trade
                if can_trade and symbol not in self.open_positions:
                    if not self.check_risk_limits(symbol):
                        continue

                    # Generate signal
                    signal_data = self.strategy.generate_signal(
                        price_data=window, symbol=symbol
                    )
                    position_size = signal_data.get("lot_size")

                    if not signal_data or signal_data.get("entry_date") is None:
                        continue

                    if signal_data.get("signal"):
                        trade_dt = pd.to_datetime(signal_data["entry_date"])
                        nigerian_tz = pytz.timezone("Africa/Lagos")
                        trade_dt_nigeria = trade_dt.tz_localize(pytz.UTC).astimezone(
                            nigerian_tz
                        )
                        date_str = trade_dt_nigeria.strftime("%Y-%m-%d %H:%M:%S")

                        trade = self.create_trade(
                            symbol, signal_data, current_time, bar_idx, position_size
                        )
                        if trade:
                            self.open_positions[symbol] = trade

                            # ✅ Signal taken - stop looking for new signals until this trade closes
                            can_trade = False

                            print(
                                f"📌 {symbol} | {trade['signal'].upper()} @ {trade['entry_price']:.5f} | "
                                f"SL: {trade['stop_loss']:.5f} | TP: {trade['take_profit']:.5f} | Date: {date_str}"
                            )

                # Track equity
                self.equity_curve.append(
                    {
                        "time": current_time,
                        "balance": self.current_balance,
                        "open_positions": len(self.open_positions),
                    }
                )

        # Close remaining positions
        for symbol, trade in list(self.open_positions.items()):
            df_final = (
                df_dict.get(symbol) if df_dict.get(symbol) is not None else ltf_df_full
            )
            final_price = df_final["close"].iloc[-1]
            trade["exit_time"] = df_final["time"].iloc[-1]
            trade["exit_price"] = final_price
            trade["exit_reason"] = "backtest_end"
            trade = self.calculate_pnl(trade, final_price)
            self.update_balance(trade["pnl"])
            self.trades.append(trade)

        self.open_positions.clear()
        return self.generate_results()

    def generate_results(self):
        """
        Calculate and return backtest metrics

        Returns:
        --------
        dict
            Complete results with metrics and dataframes
        """
        if not self.trades:
            print("\n⚠️ No trades executed")
            return None

        trades_df = pd.DataFrame(self.trades)

        # Calculate metrics
        total_trades = len(trades_df)
        winners = trades_df[trades_df["pnl"] > 0]
        losers = trades_df[trades_df["pnl"] < 0]

        winning_trades = len(winners)
        losing_trades = len(losers)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

        total_profit = winners["pnl"].sum() if len(winners) > 0 else 0
        total_loss = abs(losers["pnl"].sum()) if len(losers) > 0 else 0
        profit_factor = (total_profit / total_loss) if total_loss > 0 else float("inf")

        net_pnl = self.current_balance - self.initial_balance
        return_pct = (net_pnl / self.initial_balance) * 100

        avg_win = winners["pnl"].mean() if len(winners) > 0 else 0
        avg_loss = losers["pnl"].mean() if len(losers) > 0 else 0
        avg_win_r = winners["pnl_r"].mean() if len(winners) > 0 else 0
        avg_loss_r = losers["pnl_r"].mean() if len(losers) > 0 else 0

        largest_win = winners["pnl"].max() if len(winners) > 0 else 0
        largest_loss = losers["pnl"].min() if len(losers) > 0 else 0

        avg_bars_held = trades_df["bars_held"].mean()

        period_length = self.format_date_range(self.backtest_start, self.backtest_end)

        # Calculate max drawdown from equity curve
        equity_df = pd.DataFrame(self.equity_curve)
        equity_df["peak"] = equity_df["balance"].cummax()
        equity_df["drawdown"] = (equity_df["balance"] - equity_df["peak"]) / equity_df[
            "peak"
        ]
        max_drawdown = abs(equity_df["drawdown"].min()) * 100

        results = {
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "net_pnl": net_pnl,
            "return_pct": return_pct,
            "initial_balance": self.initial_balance,
            "final_balance": self.current_balance,
            "highest_balance": self.highest_balance,
            "lowest_balance": self.lowest_balance,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "avg_win_r": avg_win_r,
            "avg_loss_r": avg_loss_r,
            "largest_win": largest_win,
            "largest_loss": largest_loss,
            "avg_bars_held": avg_bars_held,
            "max_drawdown_pct": max_drawdown,
            "trades_df": trades_df,
            "equity_curve_df": equity_df,
            "backtest_start": self.backtest_start,
            "backtest_end": self.backtest_end,
            "backtest_period": period_length,
            "timeframe": self.timeframe,  # <-- add your timeframe here
        }

        self.print_results(results)
        return results

    def print_results(self, results):
        """Print formatted results"""
        print(f"\n{'='*70}")
        print(f"📊 BACKTEST RESULTS")
        print(f"{'='*70}")

        print(f"\n💰 PERFORMANCE:")
        print(f"  Initial Balance:    ${results['initial_balance']:>12,.2f}")
        print(f"  Final Balance:      ${results['final_balance']:>12,.2f}")
        print(f"  Net P&L:            ${results['net_pnl']:>12,.2f}")
        print(f"  Return:             {results['return_pct']:>12.2f}%")
        print(f"  Max Drawdown:       {results['max_drawdown_pct']:>12.2f}%")

        print(f"\n📈 TRADE STATISTICS:")
        print(f"  Total Trades:       {results['total_trades']:>12}")
        print(f"  Winning Trades:     {results['winning_trades']:>12}")
        print(f"  Losing Trades:      {results['losing_trades']:>12}")
        print(f"  Win Rate:           {results['win_rate']:>12.1f}%")
        print(f"  Profit Factor:      {results['profit_factor']:>12.2f}")

        print(f"\n💵 AVERAGE TRADES:")
        print(
            f"  Avg Win:            ${results['avg_win']:>12.2f} ({results['avg_win_r']:.2f}R)"
        )
        print(
            f"  Avg Loss:           ${results['avg_loss']:>12.2f} ({results['avg_loss_r']:.2f}R)"
        )
        print(f"  Largest Win:        ${results['largest_win']:>12.2f}")
        print(f"  Largest Loss:       ${results['largest_loss']:>12.2f}")
        print(f"  Avg Bars Held:      {results['avg_bars_held']:>12.1f}")

        print(f"\n📅 BACKTEST PERIOD:")
        print(
            f"  From: {results['backtest_start'].strftime('%d %b %Y')} "
            f"→ To: {results['backtest_end'].strftime('%d %b %Y')} "
            f"({results['backtest_period']})"
        )
        print(f"⏱ Timeframe: {results['timeframe']}")  # <-- added line

        print(f"{'='*70}\n")
