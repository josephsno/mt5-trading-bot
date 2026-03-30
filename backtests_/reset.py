# 1. Account risk check
# 2. Market regime check
# 3. Risk definition (SL first)
# 4. Position sizing
# 5. Entry confirmation
# 6. Execution

import numpy as np
from datetime import datetime

class TradingSystem:
    def __init__(
        self,
        balance=10000,
        risk_per_trade=0.02,
        symbols=None,
        max_positions=5,
        max_daily_loss=0.05,
        max_drawdown=0.15,
        correlation_groups=None,
    ):
        self.initial_balance = balance
        self.balance = balance
        self.risk_per_trade = risk_per_trade
        self.symbols = symbols or []
        self.positions = []
        self.max_positions = max_positions
        self.max_daily_loss = max_daily_loss
        self.max_drawdown = max_drawdown
        self.correlation_groups = correlation_groups or {}

        # Performance tracking
        self.peak_balance = balance
        self.daily_start_balance = balance
        self.daily_pnl = 0
        self.current_drawdown = 0
        self.trade_history = []
        self.daily_locked = False
        self.current_day = None  # tracks the day of the current bar

    # --------------------------
    # DAILY RESET BASED ON CANDLE
    # --------------------------
    def reset_daily_tracking(self, bar_time):
        """
        Reset daily P&L and daily lock if the candle is on a new day
        """
        bar_day = bar_time.date()
        if self.current_day != bar_day:
            print(f"\n📅 New trading day: {bar_day}")
            print(f"   Yesterday's P&L: ${self.daily_pnl:.2f}")
            self.daily_start_balance = self.balance
            self.daily_pnl = 0
            self.daily_locked = False
            self.current_day = bar_day

    # --------------------------
    # ACCOUNT RISK CHECK
    # --------------------------
    def check_account_risk(self):
        if self.balance <= 0:
            print("⚠️ Account balance too low to trade.")
            return False

        daily_loss_pct = abs(self.daily_pnl / self.daily_start_balance)
        # if self.daily_locked or (self.daily_pnl < 0 and daily_loss_pct >= self.max_daily_loss):
        #     self.daily_locked = True
        #     print(f"🛑 Daily loss limit reached ({daily_loss_pct*100:.1f}%). No more trades today.")
        #     return False

        # # Drawdown
        # self.current_drawdown = (self.peak_balance - self.balance) / self.peak_balance
        # if self.current_drawdown >= self.max_drawdown:
        #     print(f"🛑 Max drawdown reached ({self.current_drawdown*100:.1f}%). Reducing risk or stopping.")
        #     return False

        # Max positions
        if len(self.positions) >= self.max_positions:
            print(f"⚠️ Max positions ({self.max_positions}) already open.")
            return False

        return True

    # --------------------------
    # CORRELATION RISK CHECK
    # --------------------------
    def check_correlation_risk(self, symbol):
        if not self.correlation_groups or symbol not in self.correlation_groups:
            return True

        target_group = self.correlation_groups[symbol]
        group_exposure = sum(
            1 for pos in self.positions
            if self.correlation_groups.get(pos["symbol"]) == target_group
        )

        if group_exposure >= 2:
            print(f"⚠️ Already have {group_exposure} positions in {target_group} group. Skipping {symbol}.")
            return False

        return True

    # --------------------------
    # MARKET REGIME CHECK
    # --------------------------
    def check_market_regime(self, symbol):
        # Placeholder for strategy-specific checks
        return True

    # --------------------------
    # RISK DEFINITION
    # --------------------------
    def define_risk(self, entry_price, stop_loss_price):
        if entry_price == stop_loss_price:
            raise ValueError("Stop-loss must be different from entry price.")

        base_risk = self.balance * self.risk_per_trade
        if self.current_drawdown > 0.10:
            base_risk *= 0.5
            print(f"⚠️ In drawdown ({self.current_drawdown*100:.1f}%). Reducing risk by 50%.")

        trade_risk = abs(entry_price - stop_loss_price)
        return base_risk, trade_risk

    # --------------------------
    # POSITION SIZING
    # --------------------------
    def calculate_position_size(self, entry_price, stop_loss_price):
        risk_amount, trade_risk = self.define_risk(entry_price, stop_loss_price)
        return risk_amount / trade_risk

    # --------------------------
    # ENTRY SIGNAL
    # --------------------------
    def confirm_entry(self, symbol):
        signal = np.random.choice(["buy", "sell", None], p=[0.4, 0.4, 0.2])
        entry_price = 100 + np.random.uniform(-5, 5)

        if signal == "buy":
            stop_loss_price = entry_price - 2
        elif signal == "sell":
            stop_loss_price = entry_price + 2
        else:
            stop_loss_price = None

        return signal, entry_price, stop_loss_price

    # --------------------------
    # EXECUTION
    # --------------------------
    def execute_trade(self, symbol, signal, entry_price, stop_loss_price):
        position_size = self.calculate_position_size(entry_price, stop_loss_price)
        print(f"📌 Executing {signal.upper()} on {symbol} | Entry: {entry_price:.2f}, SL: {stop_loss_price:.2f}, Size: {position_size:.2f}")

        self.positions.append({
            "symbol": symbol,
            "signal": signal,
            "entry": entry_price,
            "sl": stop_loss_price,
            "size": position_size,
            "open_time": datetime.now(),
        })

    # --------------------------
    # CLOSE POSITION
    # --------------------------
    def close_position(self, position_idx, exit_price):
        pos = self.positions[position_idx]
        pnl = (exit_price - pos["entry"]) * pos["size"] if pos["signal"] == "buy" else (pos["entry"] - exit_price) * pos["size"]

        self.balance += pnl
        self.daily_pnl += pnl
        self.trade_history.append({**pos, "exit_price": exit_price, "pnl": pnl, "close_time": datetime.now()})
        self.positions.pop(position_idx)
        self.update_peak_balance()

        print(f"✅ Closed {pos['symbol']} | P&L: ${pnl:.2f} | Balance: ${self.balance:.2f}")

    # --------------------------
    # PEAK BALANCE UPDATE
    # --------------------------
    def update_peak_balance(self):
        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
            self.current_drawdown = 0
            print(f"🎉 New peak balance: ${self.peak_balance:.2f}")

    # --------------------------
    # MAIN RUN LOOP
    # --------------------------
    def run(self, symbols_with_times):
        """
        symbols_with_times: list of tuples [(symbol, bar_time), ...]
        bar_time must be datetime of the candle
        """
        for symbol, bar_time in symbols_with_times:
            # Reset daily tracking using candle time
            self.reset_daily_tracking(bar_time)

            if not self.check_account_risk():
                continue

            if not self.check_correlation_risk(symbol):
                continue

            if not self.check_market_regime(symbol):
                print(f"⛔ {symbol} not suitable for trading right now.")
                continue

            signal, entry_price, stop_loss_price = self.confirm_entry(symbol)
            if signal:
                self.execute_trade(symbol, signal, entry_price, stop_loss_price)
            else:
                print(f"❌ No trade signal for {symbol} at this time.")

        # Print summary
        print(f"\n📊 Summary:")
        print(f"   Balance: ${self.balance:.2f}")
        print(f"   Peak: ${self.peak_balance:.2f}")
        print(f"   Drawdown: {self.current_drawdown*100:.1f}%")
        print(f"   Daily P&L: ${self.daily_pnl:.2f}")
        print(f"   Open Positions: {len(self.positions)}/{self.max_positions}")


# Example usage
if __name__ == "__main__":
    # Define correlation groups
    correlations = {
        "EURUSD": "USD",
        "GBPUSD": "USD",
        "USDJPY": "USD",
        "AUDUSD": "USD",
        "EURJPY": "JPY",
        "GBPJPY": "JPY",
    }

    system = TradingSystem(
        balance=10000,
        risk_per_trade=0.02,
        symbols=["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"],
        max_positions=5,
        max_daily_loss=0.05,
        max_drawdown=0.15,
        correlation_groups=correlations,
    )

    system.run()


