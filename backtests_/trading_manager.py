import pandas as pd
from datetime import datetime
from strategies.strategy import MACDTrendStrategy  # replace with actual import
import MetaTrader5

class TradeManager:
    def __init__(self, symbols, price_data_dict, lot_size=0.02, risk_reward_ratio=3.0):
        """
        Parameters:
        -----------
        symbols : list
            List of symbols to trade, e.g., ["EURUSD", "USDJPY"]
        price_data_dict : dict
            Dictionary of pandas DataFrames keyed by symbol
            Each DF must have 'close' and 'time' columns at minimum
        lot_size : float
            Default lot size for trades
        risk_reward_ratio : float
            Default risk-reward ratio to pass to strategy
        """
        self.symbols = symbols
        self.price_data_dict = price_data_dict
        self.lot_size = lot_size
        self.strategy = MACDTrendStrategy(risk_reward_ratio=risk_reward_ratio)
        self.executed_trades = []

    def run(self):
        """Loop over all symbols, generate signals, and execute trades"""
        for symbol in self.symbols:
            data = self.price_data_dict.get(symbol)
            if data is None or data.empty:
                print(f"⚠️ No price data for {symbol}")
                continue

            # Generate signal from strategy
            signal_data = self.strategy.generate_signal(data)

            if signal_data["signal"]:
                entry_price = signal_data["entry_price"]
                stop_loss = signal_data["stop_loss"]
                take_profit = signal_data["take_profit"]
                trend = signal_data["trend"]
                reason = signal_data["reason"]
                entry_date = pd.to_datetime(signal_data["entry_date"])
                date_str = entry_date.strftime("%Y-%m-%d %H:%M:%S")

                # Execute trade
                self.execute_trade(
                    symbol,
                    signal=signal_data["signal"],
                    lot_size=self.lot_size,
                    sl_pips=None,
                    tp_pips=None,
                    strategy="macd_trend",
                )

                # Store executed trade info
                self.executed_trades.append({
                    "symbol": symbol,
                    "signal": signal_data["signal"],
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "trend": trend,
                    "reason": reason,
                    "entry_date": date_str
                })

                # Print summary
                print(
                    f"📌 {symbol} | {signal_data['signal'].upper()} @ {entry_price:.5f} | "
                    f"SL: {stop_loss:.5f} | TP: {take_profit:.5f} | Date: {date_str} | {reason}"
                )
            else:
                print(f"⚙️ No trade signal for {symbol}: {signal_data['reason']}")

    def execute_trade(
        self,
        symbol,
        signal=None,
        lot_size=None,
        deviation=10,
        sl_pips=None,
        tp_pips=None,
        strategy="macd_trend",
    ):
        """Executes a buy or sell order in MetaTrader 5."""
        # Placeholder for your can_trade_symbol method
        can_trade = True
        if not can_trade:
            return

        if signal not in ["buy", "sell"]:
            print(f"⚙️ No trade action for {symbol}")
            return

        tick = MetaTrader5.symbol_info_tick(symbol)
        if tick is None:
            print(f"⚠️ Failed to get tick for {symbol}")
            return

        order_type = (
            MetaTrader5.ORDER_TYPE_BUY
            if signal == "buy"
            else MetaTrader5.ORDER_TYPE_SELL
        )
        price = tick.ask if signal == "buy" else tick.bid

        # Placeholder: calculate_trade_risk should return {'sl_price': sl, 'tp_price': tp}
        sl_price = sl_pips or price * 0.995 if signal=="buy" else price*1.005
        tp_price = tp_pips or price * 1.015 if signal=="buy" else price*0.985

        request = {
            "action": MetaTrader5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot_size,
            "type": order_type,
            "price": price,
            "deviation": deviation,
            "magic": 123456,
            "comment": f"{strategy} auto-bot",
            "type_time": MetaTrader5.ORDER_TIME_GTC,
            "type_filling": MetaTrader5.ORDER_FILLING_IOC,
            "tp": tp_price,
            "sl": sl_price,
        }

        result = MetaTrader5.order_send(request)
        if result.retcode != MetaTrader5.TRADE_RETCODE_DONE:
            print(f"❌ Trade failed for {symbol}: {result.comment}")
        else:
            print(f"✅ Trade executed for {symbol}: {signal.upper()} at {price}")
