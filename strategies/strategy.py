import pandas as pd
from typing import Dict, Any, Optional
import MetaTrader5 as mt5
import time, datetime

class RSIFlexibleStrategy1:
    """
    Flexible RSI Strategy
    - RSI trend with moderate validations
    - EMA slope optional confirmation
    - Volume check optional
    - Dynamic lot sizing using winning streak
    """

    def __init__(
        self,
        sl_pips: float = 20.0,
        allowed_weekdays: Optional[list[int]] = None,
        allowed_hours: Optional[list[int]] = None,  # ✅ NEW
        starting_lot: float = 0.01,
        rsi_period: int = 14,
        rsi_buy_level: float = 35,
        rsi_sell_level: float = 65,
        ema_trend: int = 50,
        ema_slope_lookback: int = 5,
        use_volume_filter: bool = True,
        backtest_mode: bool = False,
        initial_balance: float = 100.0,  # initial capital for risk management
    ):
        self.sl_pips = sl_pips
        self.tp_pips = sl_pips  # 1:1 R/R

        self.allowed_weekdays = allowed_weekdays or list(range(7))
        self.allowed_hours = allowed_hours or list(range(24))  # ✅ default = all hours

        self.starting_lot = starting_lot
        self.current_lot = starting_lot
        self.backtest_mode = backtest_mode
        self.winning_streak = 0
        self.last_trade_won = False

        # RSI
        self.rsi_period = rsi_period
        self.rsi_buy_level = rsi_buy_level
        self.rsi_sell_level = rsi_sell_level

        # EMA Slope
        self.ema_trend = ema_trend
        self.ema_slope_lookback = ema_slope_lookback
        self.min_ema_slope = 0.00005

        self.balance_cap: dict = {
            "EURUSDm": {"p": 0.5, "l": 0.4},
            "GBPJPYm": {"p": 0.5, "l": 0.4},
            "EURJPYm": {"p": 0.5, "l": 0.4},
            "USDJPYm": {"p": 0.5, "l": 0.4},
            "CADJPYm": {"p": 0.5, "l": 0.4},
            "AUDJPYm": {"p": 0.5, "l": 0.4},
            "SGDJPYm": {"p": 0.5, "l": 0.4},
        }

        # Volume
        self.use_volume_filter = use_volume_filter
        self.volume_ma_period = 20

        self.starting_balance = initial_balance
        self.current_balance = initial_balance

    # ---------------- BALANCE MANAGEMENT ---------------- #
    def get_balance(self) -> float:
        """
        Return current balance depending on mode.
        - Backtest: use self.current_balance
        - Live: fetch from MT5
        """
        if self.backtest_mode:
            return getattr(self, "current_balance", self.starting_balance)

    def get_live_balance_from_trades(self, symbol: str = None) -> float:
        """
        Calculate total profit from all closed trades since beginning of current year.
        If symbol is provided, only include trades for that symbol.
        """
        

        # Calculate lookback from now to beginning of current year
        now = datetime.datetime.now()
        year_start = datetime.datetime(now.year, 1, 1)

        # Use the same timestamp method as your wins function
        now_timestamp = int(time.time())
        from_timestamp = int(year_start.timestamp())

        # Get deals from beginning of year to now
        deals = mt5.history_deals_get(from_timestamp, now_timestamp)
       
        # Filter by symbol if provided
        if symbol:
            deals = [d for d in deals if d.symbol == symbol]

        # Just give me the sum of the profits
        profit = sum(d.profit for d in deals)

        return profit

    def update_balance(self, new_balance: float):
        """
        Manually update balance (only for backtest mode)
        """
        if not self.backtest_mode:
            raise RuntimeError("Cannot manually update balance in live mode")
        self.current_balance = new_balance

    def _check_balance_stop(self, symbol=None):
        """
        Checks balance thresholds and decides whether to stop trading.

        - Profit threshold: 70% gain over starting balance
        - Loss threshold: 40% loss of starting balance (i.e., 60% remaining)

        Returns:
            (stop_trading: bool, reason: str)
        """
        # Get current balance
        if self.backtest_mode:
            bal = self.get_balance()
            # Profit threshold (>=70% gain)
            if (bal > self.starting_balance) and (bal - self.starting_balance) >= (0.6 * self.starting_balance):
                return True, "profit_target"
            elif (self.starting_balance > bal) and (self.starting_balance-bal) >= (0.6 * self.starting_balance):
                return True , "loss_target"
            return False, "ok"
        else:
            bal = self.get_live_balance_from_trades(symbol=symbol)
            p, l = self.balance_cap.get(symbol, {"p": 0.6, "l": 0.4}).values()
            # Profit threshold (>=70% gain)
            if bal >= p * self.starting_balance:
                print(
                    f"✅ reached profit target!"
                )
                return True, "profit_target"

            # Loss threshold (<=60% of starting balance)
            if bal <= -1 * (l * self.starting_balance):
                print(
                    f"⚠️ {symbol or 'Symbol'} hit loss limit! Stop trading. Current balance: {bal:.2f}"
                )
                return True, "loss_limit"

            # No stop condition met
            return False, "ok"

    # ---------------- HELPERS ---------------- #
    def _get_entry_time(self, price_data: pd.DataFrame):
        if "time" in price_data.columns:
            return price_data["time"].iloc[-1]
        if isinstance(price_data.index, pd.DatetimeIndex):
            return price_data.index[-1]
        return None

    def _is_allowed_date(self, timestamp) -> bool:
        if timestamp is None:
            return False
        return pd.to_datetime(timestamp).weekday() in self.allowed_weekdays

    def _is_allowed_hour(self, timestamp) -> bool:
        if timestamp is None:
            return False
        hour = pd.to_datetime(timestamp).hour
        return hour in self.allowed_hours

    def _pip_value(self, symbol: str) -> float:
        """
        Return pip value per symbol.
        Assumes standard Forex conventions.
        """
        if symbol.endswith("JPY"):
            return 0.01
        return 0.0001

    def _sl_tp(self, entry_price: float, side: str, symbol: str) -> tuple[float, float]:
        """
        Simple SL/TP calculation for 1:1 RR.
        - entry_price: price to enter
        - side: 'buy' or 'sell'
        - symbol: used to determine pip value
        """
        # Determine pip value
        if symbol.startswith("XAU"):
            pip_value = 0.1  # Gold pip = 0.1
            digits = 3  # Gold quotes usually 3 decimal digits
        elif symbol.endswith("JPYm"):
            pip_value = 0.01  # JPY pairs pip
            digits = 3  # JPY pairs usually 3 digits in MT5
        else:
            pip_value = 0.0001  # Standard FX pip
            digits = 5  # EURUSD, GBPUSD etc.

        sl_distance = self.sl_pips * pip_value
        tp_distance = sl_distance  # mirror for 1:1 RR

        if side.lower() == "buy":
            sl = entry_price - sl_distance
            tp = entry_price + tp_distance
        else:
            sl = entry_price + sl_distance
            tp = entry_price - tp_distance

        return round(sl, digits), round(tp, digits)

    def _calculate_rsi(self, close: pd.Series):
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(self.rsi_period).mean()
        avg_loss = loss.rolling(self.rsi_period).mean()
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _get_consecutive_wins(self, symbol: str, lookback_days=80) -> int:
        if not mt5.initialize():
            return 0

        now = int(time.time())
        from_time = now - 60 * 60 * 24 * lookback_days

        deals = mt5.history_deals_get(from_time, now)
        if not deals:
            return 0

        exit_deals = [
            d
            for d in deals
            if d.symbol == symbol
            and d.entry == 1
            and d.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL)
        ]

        if not exit_deals:
            return 0

        exit_deals.sort(key=lambda d: d.time, reverse=True)

        wins = 0
        for d in exit_deals:
            if d.profit > 0:
                wins += 1
            else:
                break  # 🔥 loss resets immediately

        return wins

    # ---------------- LOT SIZING ---------------- #
    def _get_lot_size(self, symbol: str) -> float:
        if self.backtest_mode:
            if self.last_trade_won and self.winning_streak >= 2:
                self.current_lot += 0.01
            else:
                self.current_lot = self.starting_lot
            return round(self.current_lot, 2)
        # ---------- LIVE (MT5) ----------

        else:
            wins = self._get_consecutive_wins(symbol)
            self.current_lot = (
                self.starting_lot if wins < 2 else self.starting_lot + 0.01 * (wins - 1)
            )
            print(f"{wins} consecutive wins for {symbol}")
            return round(self.current_lot, 2)

    def update_trade_result(self, was_win: bool):
        if was_win:
            self.winning_streak += 1
        else:
            self.winning_streak = 0
        self.last_trade_won = was_win

    # ---------------- MAIN LOGIC ---------------- #
    def generate_signal(self, price_data: pd.DataFrame, symbol: str) -> Dict[str, Any]:

        # ---------- BASIC GUARDS ----------
        if price_data is None or price_data.empty:
            return self._empty_signal("❌ Price data is empty or None")

        stop, reason = self._check_balance_stop(symbol=symbol)
        if stop:
            # You can customize the message based on reason
            if reason == "profit_target":
                return self._empty_signal("✅ Price has reached profit cap")
            elif reason == "loss_limit":
                return self._empty_signal("⚠️ Price has reached loss limit")
            else:
                return self._empty_signal("❌ Trading stopped")

        min_bars = max(
            self.rsi_period,
            self.ema_trend,
            self.ema_slope_lookback,
            self.volume_ma_period + 2,
        )

        if len(price_data) < min_bars:
            return self._empty_signal(
                f"❌ Not enough data | bars={len(price_data)} need≥{min_bars}"
            )

        # ---------- TIME FILTER ----------
        entry_time = self._get_entry_time(price_data)
        day_names = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]
        weekday_num = int(pd.to_datetime(entry_time).weekday())
        day_name = day_names[weekday_num]

        if not self._is_allowed_date(entry_time):
            return self._empty_signal(f"📅 Day filtered out | weekday = {day_name}")

        if not self._is_allowed_hour(entry_time):
            return self._empty_signal(
                f"⏰ Hour filtered out | hour={pd.to_datetime(entry_time).hour}"
            )

        # ---------- PRICE SERIES ----------
        close = price_data["close"]
        open_ = price_data["open"]

        # ---------- VOLUME SERIES ----------
        volume_col = next(
            (
                c
                for c in ["volume", "tick_volume", "vol", "real_volume"]
                if c in price_data.columns
            ),
            None,
        )
        volume = price_data[volume_col] if volume_col else None

        # ---------- INDICATORS ----------
        ema = close.ewm(span=self.ema_trend).mean()
        rsi = self._calculate_rsi(close)

        last_close = close.iloc[-1]
        last_open = open_.iloc[-1]
        last_ema = ema.iloc[-1]

        # ---------- TREND DIRECTION (PRICE vs EMA) ----------
        trend = "buy" if last_close >= last_ema else "sell"

        # ---------- EMA SLOPE (STEEPNESS) ----------
        ema_slope = ema.iloc[-1] - ema.iloc[-self.ema_slope_lookback]
        min_slope = self.min_ema_slope  # symbol-tuned or ATR-based

        if trend == "buy" and ema_slope < min_slope:
            return self._empty_signal(
                f"❌ Weak BUY trend | EMA slope={ema_slope:.6f} < {min_slope}"
            )

        if trend == "sell" and ema_slope > -min_slope:
            return self._empty_signal(
                f"❌ Weak SELL trend | EMA slope={ema_slope:.6f} > -{min_slope}"
            )

        # ---------- RSI CONFIRMATION ----------
        if trend == "buy" and rsi.iloc[-1] < self.rsi_buy_level:
            return self._empty_signal(
                f"❌ BUY rejected | RSI={rsi.iloc[-1]:.2f} < {self.rsi_buy_level}"
            )

        if trend == "sell" and rsi.iloc[-1] > self.rsi_sell_level:
            return self._empty_signal(
                f"❌ SELL rejected | RSI={rsi.iloc[-1]:.2f} > {self.rsi_sell_level}"
            )

        # ---------- MOMENTUM CANDLE ----------
        if trend == "buy" and last_close <= last_open:
            return self._empty_signal(
                f"❌ BUY momentum fail | close={last_close:.5f} ≤ open={last_open:.5f}"
            )

        if trend == "sell" and last_close >= last_open:
            return self._empty_signal(
                f"❌ SELL momentum fail | close={last_close:.5f} ≥ open={last_open:.5f}"
            )

        # ---------- VOLUME FILTER (CLOSED BAR) ----------
        volume_ratio = None
        if self.use_volume_filter and volume is not None:
            last_closed_vol = volume.iloc[-2]
            vol_ma = volume.iloc[-(self.volume_ma_period + 2) : -2].mean()
            volume_ratio = last_closed_vol / vol_ma if vol_ma > 0 else 0

            if volume_ratio < 0.6:
                return self._empty_signal(
                    f"❌ Volume too low | ratio={volume_ratio:.2f} < 0.6"
                )

        # ---------- MT5 EXECUTION DATA ----------
        if not symbol:
            return self._empty_signal("❌ Symbol not provided")

        # Check if we're in backtest mode
        if self.backtest_mode:
            # Use historical data
            entry_price = last_close
            # Or add spread simulation:
            # spread_pips = 1.2  # typical spread
            # pip_size = 0.0001
            # entry_price = last_close + (spread_pips * pip_size) if trend == "buy" else last_close - (spread_pips * pip_size)
        else:
            # Live trading - use MT5 tick
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                return self._empty_signal("❌ MT5 tick unavailable")
            entry_price = tick.ask if trend == "buy" else tick.bid

        # ---------- SL / TP ----------
        sl, tp = self._sl_tp(
            entry_price=entry_price,
            side=trend,
            symbol=symbol,
        )

        # ---------- LOT SIZE ----------
        lot = self._get_lot_size(symbol=symbol)

        # ---------- FINAL SIGNAL ----------
        return {
            "signal": trend,
            "entry_price": entry_price,
            "stop_loss": sl,
            "take_profit": tp,
            "entry_date": entry_time,
            "lot_size": lot,
            "slope": ema_slope,
            "reason": (
                f"✅ {trend.upper()} | "
                f"price={'above' if trend=='buy' else 'below'} EMA({self.ema_trend}), "
                f"slope={ema_slope:.6f}, "
                f"RSI={rsi.iloc[-1]:.2f}, "
                f"volume_ratio={volume_ratio if volume_ratio else 'OK'}"
            ),
        }

    # ---------------- EMPTY ---------------- #
    def _empty_signal(self, reason: str) -> Dict[str, Any]:
        return {
            "signal": None,
            "entry_price": None,
            "stop_loss": None,
            "take_profit": None,
            "entry_date": None,
            "lot_size": None,
            "reason": reason,
        }

    # ---------------- PARAMETERS ---------------- #
    def get_parameters(self) -> Dict[str, Any]:
        return {
            "name": "RSI Flexible Strategy",
            "sl_pips": self.sl_pips,
            "tp_pips": self.tp_pips,
            "rsi_period": self.rsi_period,
            "rsi_buy_level": self.rsi_buy_level,
            "rsi_sell_level": self.rsi_sell_level,
            "ema_trend": self.ema_trend,
            "ema_slope_lookback": self.ema_slope_lookback,
            "volume_filter": self.use_volume_filter,
            "starting_lot": self.starting_lot,
        }

    def __repr__(self) -> str:
        return (
            f"RSIFlexibleStrategy(RSI{self.rsi_period}, EMA{self.ema_trend}, "
            f"SL={self.sl_pips}, TP={self.tp_pips})"
        )

# CURRENTLY BEST RECOMMENDED NB FOR XAUUSD IT DOES WELL FOR 10MINS TF
class RSIFlexibleStrategy:
    """
    Flexible RSI Strategy
    - RSI trend with moderate validations
    - EMA slope optional confirmation
    - Volume check optional
    - Dynamic lot sizing using winning streak
    """

    def __init__(
        self,
        sl_pips: float = 20.0,
        allowed_weekdays: Optional[list[int]] = None,
        allowed_hours: Optional[list[int]] = None,  # ✅ NEW
        starting_lot: float = 0.01,
        rsi_period: int = 14,
        rsi_buy_level: float = 35,
        rsi_sell_level: float = 65,
        ema_trend: int = 50,
        ema_slope_lookback: int = 5,
        use_volume_filter: bool = True,
        backtest_mode: bool = False,
        initial_balance: float = 100.0,  # initial capital for risk management
    ):
        self.sl_pips = sl_pips
        self.tp_pips = sl_pips  # 1:1 R/R

        self.allowed_weekdays = allowed_weekdays or list(range(7))
        self.allowed_hours = allowed_hours or list(range(24))  # ✅ default = all hours

        self.starting_lot = starting_lot
        self.current_lot = starting_lot
        self.backtest_mode = backtest_mode
        self.winning_streak = 0
        self.last_trade_won = False

        # RSI
        self.rsi_period = rsi_period
        self.rsi_buy_level = rsi_buy_level
        self.rsi_sell_level = rsi_sell_level

        # EMA Slope
        self.ema_trend = ema_trend
        self.ema_slope_lookback = ema_slope_lookback
        self.min_ema_slope = 0.00005

        self.balance_cap: dict = {
            "EURUSDm": {"p": 0.5, "l": 0.4},
            "GBPJPYm": {"p": 0.5, "l": 0.4},
            "EURJPYm": {"p": 0.5, "l": 0.4},
            "USDJPYm": {"p": 0.5, "l": 0.4},
            "CADJPYm": {"p": 0.5, "l": 0.4},
            "AUDJPYm": {"p": 0.5, "l": 0.4},
            "SGDJPYm": {"p": 0.5, "l": 0.4},
        }

        # Volume
        self.use_volume_filter = use_volume_filter
        self.volume_ma_period = 20

        self.starting_balance = initial_balance
        self.current_balance = initial_balance

    # ---------------- BALANCE MANAGEMENT ---------------- #
    def get_balance(self) -> float:
        """
        Return current balance depending on mode.
        - Backtest: use self.current_balance
        - Live: fetch from MT5
        """
        if self.backtest_mode:
            return getattr(self, "current_balance", self.starting_balance)

    def get_live_balance_from_trades(self, symbol: str = None) -> float:
        """
        Calculate total profit from all closed trades since beginning of current year.
        If symbol is provided, only include trades for that symbol.
        """
        

        # Calculate lookback from now to beginning of current year
        now = datetime.datetime.now()
        year_start = datetime.datetime(now.year, 1, 1)

        # Use the same timestamp method as your wins function
        now_timestamp = int(time.time())
        from_timestamp = int(year_start.timestamp())

        # Get deals from beginning of year to now
        deals = mt5.history_deals_get(from_timestamp, now_timestamp)
       
        # Filter by symbol if provided
        if symbol:
            deals = [d for d in deals if d.symbol == symbol]

        # Just give me the sum of the profits
        profit = sum(d.profit for d in deals)

        return profit

    def update_balance(self, new_balance: float):
        """
        Manually update balance (only for backtest mode)
        """
        if not self.backtest_mode:
            raise RuntimeError("Cannot manually update balance in live mode")
        self.current_balance = new_balance

    def _check_balance_stop(self, symbol=None):
        """
        Checks balance thresholds and decides whether to stop trading.

        - Profit threshold: 70% gain over starting balance
        - Loss threshold: 40% loss of starting balance (i.e., 60% remaining)

        Returns:
            (stop_trading: bool, reason: str)
        """
        # Get current balance
        if self.backtest_mode:
            bal = self.get_balance()
            # Profit threshold (>=70% gain)
            if (bal > self.starting_balance) and (bal - self.starting_balance) >= (0.6 * self.starting_balance):
                return True, "profit_target"
            elif (self.starting_balance > bal) and (self.starting_balance-bal) >= (0.4 * self.starting_balance):
                return True , "loss_target"
            return False, "ok"
        else:
            bal = self.get_live_balance_from_trades(symbol=symbol)
            p, l = self.balance_cap.get(symbol, {"p": 0.6, "l": 0.4}).values()
            # Profit threshold (>=70% gain)
            if bal >= p * self.starting_balance:
                print(
                    f"✅ reached profit target!"
                )
                return True, "profit_target"

            # Loss threshold (<=60% of starting balance)
            if bal <= -1 * (l * self.starting_balance):
                print(
                    f"⚠️ {symbol or 'Symbol'} hit loss limit! Stop trading. Current balance: {bal:.2f}"
                )
                return True, "loss_limit"

            # No stop condition met
            return False, "ok"

    # ---------------- HELPERS ---------------- #
    def _get_entry_time(self, price_data: pd.DataFrame):
        if "time" in price_data.columns:
            return price_data["time"].iloc[-1]
        if isinstance(price_data.index, pd.DatetimeIndex):
            return price_data.index[-1]
        return None

    def _is_allowed_date(self, timestamp) -> bool:
        if timestamp is None:
            return False
        return pd.to_datetime(timestamp).weekday() in self.allowed_weekdays
    

    def _check_daily_loss(self, price_data: pd.DataFrame = None) -> bool:
    
        if self.backtest_mode:
            # Use time from price data
            current_time = pd.to_datetime(self._get_entry_time(price_data))
            day_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = current_time

            # Filter backtest trade log by today's date
            if not hasattr(self, 'trade_log') or not self.trade_log:
                return False

            daily_pnl = sum(
                t['pnl'] for t in self.trade_log
                if day_start <= pd.to_datetime(t['exit_time']) <= day_end
            )

        else:
            # Live mode — use real clock
            now = datetime.datetime.now()
            day_start = datetime.datetime(now.year, now.month, now.day)
            from_timestamp = int(day_start.timestamp())
            now_timestamp = int(time.time())

            deals = mt5.history_deals_get(from_timestamp, now_timestamp)
            if not deals:
                return False

            daily_pnl = sum(d.profit for d in deals)

        daily_limit = -0.1* self.starting_balance

        if daily_pnl <= daily_limit:
            print(f"🛑 Daily loss limit hit | PnL today: {daily_pnl:.2f}")
            return True

        return False

    def _is_allowed_hour(self, timestamp) -> bool:
        if timestamp is None:
            return False
        hour = pd.to_datetime(timestamp).hour
        return hour in self.allowed_hours

    def _pip_value(self, symbol: str) -> float:
        """
        Return pip value per symbol.
        Assumes standard Forex conventions.
        """
        if symbol.endswith("JPY"):
            return 0.01
        return 0.0001

    def _sl_tp(self, entry_price: float, side: str, symbol: str) -> tuple[float, float]:
        """
        Simple SL/TP calculation for 1:1 RR.
        - entry_price: price to enter
        - side: 'buy' or 'sell'
        - symbol: used to determine pip value
        """
        # Determine pip value
        if symbol.startswith("XAU"):
            pip_value = 0.1  # Gold pip = 0.1
            digits = 3  # Gold quotes usually 3 decimal digits
        elif symbol.endswith("JPYm"):
            pip_value = 0.01  # JPY pairs pip
            digits = 3  # JPY pairs usually 3 digits in MT5
        else:
            pip_value = 0.0001  # Standard FX pip
            digits = 5  # EURUSD, GBPUSD etc.

        sl_distance = self.sl_pips * pip_value
        tp_distance = sl_distance  # mirror for 1:1 RR

        if side.lower() == "buy":
            sl = entry_price - sl_distance
            tp = entry_price + tp_distance
        else:
            sl = entry_price + sl_distance
            tp = entry_price - tp_distance

        return round(sl, digits), round(tp, digits)

    def _calculate_rsi(self, close: pd.Series):
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(self.rsi_period).mean()
        avg_loss = loss.rolling(self.rsi_period).mean()
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _get_consecutive_wins(self, symbol: str, lookback_days=30) -> int:
        if not mt5.initialize():
            return 0

        now = int(time.time())
        from_time = now - 60 * 60 * 24 * lookback_days

        deals = mt5.history_deals_get(from_time, now)
        if not deals:
            return 0

        # Include all executed trade types
        allowed_types = (
            mt5.DEAL_TYPE_BUY,
            mt5.DEAL_TYPE_SELL,

        )

        exit_deals = [
            d for d in deals
            if d.symbol == symbol and d.entry == 1 and d.type in allowed_types
        ]

        if not exit_deals:
            return 0

        # Sort most recent first
        exit_deals.sort(key=lambda d: d.time, reverse=True)

        # Count consecutive wins
        wins = 0
        for d in exit_deals:
            if d.profit > 0:
                wins += 1
            else:
                break  # loss resets streak

        return wins
    # ---------------- LOT SIZING ---------------- #
    def _get_lot_size(self, symbol: str) -> float:
        if self.backtest_mode:
            if self.last_trade_won and self.winning_streak >= 2:
                self.current_lot += 0.01
            else:
                self.current_lot = self.starting_lot
            return round(self.current_lot, 2)

        # ---------- LIVE (MT5) ----------
        else:
            wins = self._get_consecutive_wins(symbol)
            # Lot increases after 2 consecutive wins
            self.current_lot = (
                self.starting_lot if wins < 2 else self.starting_lot + 0.01 * (wins - 1)
            )
            print(f"{symbol} consecutive wins counted: {wins} | lot set to {self.current_lot}")
            return round(self.current_lot, 2)

    def update_trade_result(self, was_win: bool):
        if was_win:
            self.winning_streak += 1
        else:
            self.winning_streak = 0
        self.last_trade_won = was_win

    # ---------------- MAIN LOGIC ---------------- #
    def generate_signal(self, price_data: pd.DataFrame, symbol: str) -> Dict[str, Any]:

        # ---------- BASIC GUARDS ----------
        if price_data is None or price_data.empty:
            return self._empty_signal("❌ Price data is empty or None")

        stop, reason = self._check_balance_stop(symbol=symbol)
        if self._check_daily_loss(price_data=price_data):
            return self._empty_signal("🛑 Global daily loss limit hit — no more trades today")
        if stop:
            # You can customize the message based on reason
            if reason == "profit_target":
                return self._empty_signal("✅ Price has reached profit cap")
            elif reason == "loss_limit":
                return self._empty_signal("⚠️ Price has reached loss limit")
            else:
                return self._empty_signal("❌ Trading stopped")

        min_bars = max(
            self.rsi_period,
            self.ema_trend,
            self.ema_slope_lookback,
            self.volume_ma_period + 2,
        )

        if len(price_data) < min_bars:
            return self._empty_signal(
                f"❌ Not enough data | bars={len(price_data)} need≥{min_bars}"
            )

        # ---------- TIME FILTER ----------
        entry_time = self._get_entry_time(price_data)
        day_names = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]
        weekday_num = int(pd.to_datetime(entry_time).weekday())
        day_name = day_names[weekday_num]

        if not self._is_allowed_date(entry_time):
            return self._empty_signal(f"📅 Day filtered out | weekday = {day_name}")

        if not self._is_allowed_hour(entry_time):
            return self._empty_signal(
                f"⏰ Hour filtered out | hour={pd.to_datetime(entry_time).hour}"
            )

        # ---------- PRICE SERIES ----------
        close = price_data["close"]
        open_ = price_data["open"]

        # ---------- VOLUME SERIES ----------
        volume_col = next(
            (
                c
                for c in ["volume", "tick_volume", "vol", "real_volume"]
                if c in price_data.columns
            ),
            None,
        )
        volume = price_data[volume_col] if volume_col else None

        # ---------- INDICATORS ----------
        ema = close.ewm(span=self.ema_trend).mean()
        rsi = self._calculate_rsi(close)

        last_close = close.iloc[-1]
        last_open = open_.iloc[-1]
        last_ema = ema.iloc[-1]

        # ---------- TREND DIRECTION (PRICE vs EMA) ----------
        trend = "buy" if last_close >= last_ema else "sell"

        # ---------- EMA SLOPE (STEEPNESS) ----------
        ema_slope = ema.iloc[-1] - ema.iloc[-self.ema_slope_lookback]
        min_slope = self.min_ema_slope  # symbol-tuned or ATR-based

        if trend == "buy" and ema_slope < min_slope:
            return self._empty_signal(
                f"❌ Weak BUY trend | EMA slope={ema_slope:.6f} < {min_slope}"
            )

        if trend == "sell" and ema_slope > -min_slope:
            return self._empty_signal(
                f"❌ Weak SELL trend | EMA slope={ema_slope:.6f} > -{min_slope}"
            )

        # ---------- RSI CONFIRMATION ----------
        if trend == "buy" and rsi.iloc[-1] < self.rsi_buy_level:
            return self._empty_signal(
                f"❌ BUY rejected | RSI={rsi.iloc[-1]:.2f} < {self.rsi_buy_level}"
            )

        if trend == "sell" and rsi.iloc[-1] > self.rsi_sell_level:
            return self._empty_signal(
                f"❌ SELL rejected | RSI={rsi.iloc[-1]:.2f} > {self.rsi_sell_level}"
            )

        # ---------- MOMENTUM CANDLE ----------
        if trend == "buy" and last_close <= last_open:
            return self._empty_signal(
                f"❌ BUY momentum fail | close={last_close:.5f} ≤ open={last_open:.5f}"
            )

        if trend == "sell" and last_close >= last_open:
            return self._empty_signal(
                f"❌ SELL momentum fail | close={last_close:.5f} ≥ open={last_open:.5f}"
            )

        # ---------- VOLUME FILTER (CLOSED BAR) ----------
        volume_ratio = None
        if self.use_volume_filter and volume is not None:
            last_closed_vol = volume.iloc[-2]
            vol_ma = volume.iloc[-(self.volume_ma_period + 2) : -2].mean()
            volume_ratio = last_closed_vol / vol_ma if vol_ma > 0 else 0

            if volume_ratio < 0.6:
                return self._empty_signal(
                    f"❌ Volume too low | ratio={volume_ratio:.2f} < 0.6"
                )

        # ---------- MT5 EXECUTION DATA ----------
        if not symbol:
            return self._empty_signal("❌ Symbol not provided")

        # Check if we're in backtest mode
        if self.backtest_mode:
            # Use historical data
            entry_price = last_close
            # Or add spread simulation:
            # spread_pips = 1.2  # typical spread
            # pip_size = 0.0001
            # entry_price = last_close + (spread_pips * pip_size) if trend == "buy" else last_close - (spread_pips * pip_size)
        else:
            # Live trading - use MT5 tick
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                return self._empty_signal("❌ MT5 tick unavailable")
            entry_price = tick.ask if trend == "buy" else tick.bid

        # ---------- SL / TP ----------
        sl, tp = self._sl_tp(
            entry_price=entry_price,
            side=trend,
            symbol=symbol,
        )

        # ---------- LOT SIZE ----------
        lot = self._get_lot_size(symbol=symbol)

        # ---------- FINAL SIGNAL ----------
        return {
            "signal": trend,
            "entry_price": entry_price,
            "stop_loss": sl,
            "take_profit": tp,
            "entry_date": entry_time,
            "lot_size": lot,
            "slope": ema_slope,
            "reason": (
                f"✅ {trend.upper()} | "
                f"price={'above' if trend=='buy' else 'below'} EMA({self.ema_trend}), "
                f"slope={ema_slope:.6f}, "
                f"RSI={rsi.iloc[-1]:.2f}, "
                f"volume_ratio={volume_ratio if volume_ratio else 'OK'}"
            ),
        }

    # ---------------- EMPTY ---------------- #
    def _empty_signal(self, reason: str) -> Dict[str, Any]:
        return {
            "signal": None,
            "entry_price": None,
            "stop_loss": None,
            "take_profit": None,
            "entry_date": None,
            "lot_size": None,
            "reason": reason,
        }

    # ---------------- PARAMETERS ---------------- #
    def get_parameters(self) -> Dict[str, Any]:
        return {
            "name": "RSI Flexible Strategy",
            "sl_pips": self.sl_pips,
            "tp_pips": self.tp_pips,
            "rsi_period": self.rsi_period,
            "rsi_buy_level": self.rsi_buy_level,
            "rsi_sell_level": self.rsi_sell_level,
            "ema_trend": self.ema_trend,
            "ema_slope_lookback": self.ema_slope_lookback,
            "volume_filter": self.use_volume_filter,
            "starting_lot": self.starting_lot,
        }

    def __repr__(self) -> str:
        return (
            f"RSIFlexibleStrategy(RSI{self.rsi_period}, EMA{self.ema_trend}, "
            f"SL={self.sl_pips}, TP={self.tp_pips})"
        )



class RSIFlexibleStrategyV1:
    """
    Flexible RSI Strategy V1
    -------------------------
    Same core as RSIFlexibleStrategy — RSI + EMA slope + Volume + Momentum candle.
    
    What's new in V1 (quality filter improvements):
    - True Wilder RSI (ewm instead of rolling mean) — more accurate signals
    - RSI must be MOVING in the right direction (momentum confirmation)
    - EMA slope must be ACCELERATING not just positive — trend gaining strength
    - Volume filter tightened — needs stronger participation on signal candle
    - Added Higher Timeframe RSI bias — only trade when HTF RSI agrees
    - 1:1.5 RR instead of 1:1 — winners now pay more than losers cost
    - Risk % based lot sizing — scales with account balance
    - Daily loss limit is now a parameter
    - Fixed live mode get_balance() returning None
    """

    def __init__(
        self,
        sl_pips: float = 20.0,
        allowed_weekdays: Optional[list] = None,
        allowed_hours: Optional[list] = None,
        risk_percent: float = 1.0,          # % of balance to risk per trade
        min_lot: float = 0.01,
        max_lot: float = 5.0,
        rsi_period: int = 14,
        rsi_buy_level: float = 35,
        rsi_sell_level: float = 65,
        ema_trend: int = 50,
        ema_slope_lookback: int = 5,

        # V1: RSI momentum — RSI must be moving in the right direction
        rsi_momentum_lookback: int = 3,     # RSI must have risen/fallen over last N bars

        # V1: EMA slope acceleration — slope now vs slope N bars ago
        slope_accel_lookback: int = 3,      # slope must be steeper than N bars ago

        # V1: HTF RSI bias — simple proxy using a longer RSI period
        htf_rsi_period: int = 50,           # longer RSI acts as higher TF bias
        use_htf_rsi: bool = True,

        # V1: Volume
        use_volume_filter: bool = True,
        volume_ratio_min: float = 0.8,      # tightened from 0.6

        # V1: RR ratio now a parameter
        rr_ratio: float = 1.5,

        # V1: Daily loss limit now a parameter
        daily_loss_pct: float = 0.02,       # 2% of starting balance

        # Balance protection
        profit_target_pct: float = 0.60,
        loss_limit_pct: float = 0.40,

        backtest_mode: bool = False,
        initial_balance: float = 100.0,
    ):
        self.sl_pips = sl_pips
        self.allowed_weekdays = allowed_weekdays or list(range(7))
        self.allowed_hours = allowed_hours or list(range(24))

        self.risk_percent = risk_percent
        self.min_lot = min_lot
        self.max_lot = max_lot

        self.rsi_period = rsi_period
        self.rsi_buy_level = rsi_buy_level
        self.rsi_sell_level = rsi_sell_level

        self.ema_trend = ema_trend
        self.ema_slope_lookback = ema_slope_lookback
        self.min_ema_slope = 0.00005

        self.rsi_momentum_lookback = rsi_momentum_lookback
        self.slope_accel_lookback = slope_accel_lookback

        self.htf_rsi_period = htf_rsi_period
        self.use_htf_rsi = use_htf_rsi

        self.use_volume_filter = use_volume_filter
        self.volume_ratio_min = volume_ratio_min
        self.volume_ma_period = 20

        self.rr_ratio = rr_ratio
        self.daily_loss_pct = daily_loss_pct
        self.profit_target_pct = profit_target_pct
        self.loss_limit_pct = loss_limit_pct

        self.backtest_mode = backtest_mode
        self.starting_balance = initial_balance
        self.current_balance = initial_balance

        self.winning_streak = 0
        self.last_trade_won = False

        # Used by backtester
        self.ema_period = self.ema_trend
        self.ltf_rsi_period = self.rsi_period
        self.htf_rsi_period = self.htf_rsi_period

        self.balance_cap: dict = {
            "EURUSDm": {"p": 0.5, "l": 0.4},
            "GBPJPYm": {"p": 0.5, "l": 0.4},
            "EURJPYm": {"p": 0.5, "l": 0.4},
            "USDJPYm": {"p": 0.5, "l": 0.4},
            "CADJPYm": {"p": 0.5, "l": 0.4},
            "AUDJPYm": {"p": 0.5, "l": 0.4},
            "SGDJPYm": {"p": 0.5, "l": 0.4},
        }

        # Backtest trade log for daily loss check
        self.trade_log = []

    # ─────────────────────────────────────────────
    # BALANCE MANAGEMENT
    # ─────────────────────────────────────────────

    def get_balance(self) -> float:
        if self.backtest_mode:
            return self.current_balance
        # V1 FIX: live mode no longer returns None
        try:
            import MetaTrader5 as mt5
            info = mt5.account_info()
            return info.balance if info else self.current_balance
        except Exception:
            return self.current_balance

    def update_balance(self, new_balance: float):
        if not self.backtest_mode:
            raise RuntimeError("Cannot manually update balance in live mode")
        self.current_balance = new_balance

    def get_live_balance_from_trades(self, symbol: str = None) -> float:
        now = datetime.datetime.now()
        year_start = datetime.datetime(now.year, 1, 1)
        now_timestamp = int(time.time())
        from_timestamp = int(year_start.timestamp())
        try:
            import MetaTrader5 as mt5
            deals = mt5.history_deals_get(from_timestamp, now_timestamp)
            if symbol:
                deals = [d for d in deals if d.symbol == symbol]
            return sum(d.profit for d in deals)
        except Exception:
            return 0.0

    def _check_balance_stop(self, symbol=None):
        if self.backtest_mode:
            bal = self.get_balance()
            gain = bal - self.starting_balance
            if gain >= self.profit_target_pct * self.starting_balance:
                return True, "profit_target"
            if gain <= -(self.loss_limit_pct * self.starting_balance):
                return True, "loss_target"
            return False, "ok"
        else:
            bal = self.get_live_balance_from_trades(symbol=symbol)
            p, l = self.balance_cap.get(symbol, {"p": 0.6, "l": 0.4}).values()
            if bal >= p * self.starting_balance:
                return True, "profit_target"
            if bal <= -(l * self.starting_balance):
                return True, "loss_limit"
            return False, "ok"

    def _check_daily_loss(self, price_data: pd.DataFrame = None) -> bool:
        if self.backtest_mode:
            current_time = pd.to_datetime(self._get_entry_time(price_data))
            day_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)

            if not self.trade_log:
                return False

            daily_pnl = sum(
                t['pnl'] for t in self.trade_log
                if day_start <= pd.to_datetime(t['exit_time']) <= current_time
            )
        else:
            try:
                import MetaTrader5 as mt5
                now = datetime.datetime.now()
                day_start = datetime.datetime(now.year, now.month, now.day)
                deals = mt5.history_deals_get(int(day_start.timestamp()), int(time.time()))
                daily_pnl = sum(d.profit for d in deals) if deals else 0
            except Exception:
                return False

        # V1: daily loss limit is now a parameter
        daily_limit = -(self.daily_loss_pct * self.starting_balance)
        if daily_pnl <= daily_limit:
            return True
        return False

    def update_trade_result(self, was_win: bool):
        if was_win:
            self.winning_streak += 1
        else:
            self.winning_streak = 0
        self.last_trade_won = was_win

    # ─────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────

    def _get_entry_time(self, price_data: pd.DataFrame):
        if "time" in price_data.columns:
            return price_data["time"].iloc[-1]
        if isinstance(price_data.index, pd.DatetimeIndex):
            return price_data.index[-1]
        return None

    def _is_allowed_date(self, timestamp) -> bool:
        if timestamp is None:
            return False
        return pd.to_datetime(timestamp).weekday() in self.allowed_weekdays

    def _is_allowed_hour(self, timestamp) -> bool:
        if timestamp is None:
            return False
        return pd.to_datetime(timestamp).hour in self.allowed_hours

    def _pip_size(self, symbol: str) -> float:
        if symbol.startswith("XAU"):
            return 0.1
        if "JPY" in symbol:
            return 0.01
        return 0.0001

    def _pip_value_per_lot(self, symbol: str) -> float:
        if "JPY" in symbol:
            return 9.0
        if symbol.startswith("XAU"):
            return 10.0
        return 10.0

    # ─────────────────────────────────────────────
    # INDICATORS
    # ─────────────────────────────────────────────

    def _calculate_rsi(self, close: pd.Series, period: int) -> pd.Series:
        """V1: True Wilder RSI using ewm instead of rolling mean"""
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    # ─────────────────────────────────────────────
    # LOT SIZING
    # ─────────────────────────────────────────────

    def _calculate_lot_size(self, symbol: str) -> float:
        """V1: Risk % based lot sizing — scales with account balance"""
        balance = self.get_balance()
        risk_amount = balance * (self.risk_percent / 100)
        pip_val = self._pip_value_per_lot(symbol)

        if self.sl_pips <= 0 or pip_val <= 0:
            return self.min_lot

        lot = risk_amount / (self.sl_pips * pip_val)
        lot = max(self.min_lot, min(round(lot, 2), self.max_lot))
        return lot

    # ─────────────────────────────────────────────
    # SL / TP
    # ─────────────────────────────────────────────

    def _sl_tp(self, entry_price: float, side: str, symbol: str):
        """V1: RR ratio is now a parameter — default 1:1.5"""
        pip = self._pip_size(symbol)
        digits = 3 if ("JPY" in symbol or symbol.startswith("XAU")) else 5

        sl_distance = self.sl_pips * pip
        tp_distance = sl_distance * self.rr_ratio

        if side == "buy":
            sl = round(entry_price - sl_distance, digits)
            tp = round(entry_price + tp_distance, digits)
        else:
            sl = round(entry_price + sl_distance, digits)
            tp = round(entry_price - tp_distance, digits)

        return sl, tp

    # ─────────────────────────────────────────────
    # MAIN SIGNAL GENERATION
    # ─────────────────────────────────────────────

    def generate_signal(self, price_data: pd.DataFrame, symbol: str) -> Dict[str, Any]:

        # ── GUARD: empty data ──
        if price_data is None or price_data.empty:
            return self._empty_signal("❌ Price data is empty or None")

        # ── GUARD: balance protection ──
        stop, reason = self._check_balance_stop(symbol=symbol)
        if stop:
            msg = "✅ Profit target reached" if reason == "profit_target" else "⚠️ Loss limit reached"
            return self._empty_signal(msg)

        # ── GUARD: daily loss ──
        if self._check_daily_loss(price_data=price_data):
            return self._empty_signal("🛑 Daily loss limit hit — no more trades today")

        # ── GUARD: enough bars ──
        min_bars = max(
            self.htf_rsi_period,
            self.ema_trend,
            self.volume_ma_period + 2,
        ) + self.slope_accel_lookback + 5

        if len(price_data) < min_bars:
            return self._empty_signal(f"❌ Not enough bars | {len(price_data)} < {min_bars}")

        # ── TIME FILTERS ──
        entry_time = self._get_entry_time(price_data)
        if not self._is_allowed_date(entry_time):
            return self._empty_signal(f"📅 Day filtered | weekday={pd.to_datetime(entry_time).weekday()}")
        if not self._is_allowed_hour(entry_time):
            return self._empty_signal(f"⏰ Hour filtered | hour={pd.to_datetime(entry_time).hour}")

        # ── PRICE SERIES ──
        close  = price_data["close"]
        open_  = price_data["open"]
        high   = price_data["high"]
        low    = price_data["low"]

        # ── INDICATORS ──
        ema   = close.ewm(span=self.ema_trend, adjust=False).mean()
        rsi   = self._calculate_rsi(close, self.rsi_period)
        htf_rsi = self._calculate_rsi(close, self.htf_rsi_period)

        last_close  = close.iloc[-1]
        last_open   = open_.iloc[-1]
        last_ema    = ema.iloc[-1]
        last_rsi    = rsi.iloc[-1]
        last_htf_rsi = htf_rsi.iloc[-1]

        # ── FILTER 1: Trend direction (price vs EMA) ──
        trend = "buy" if last_close >= last_ema else "sell"

        # ── FILTER 2: EMA slope must be active ──
        ema_slope = ema.iloc[-1] - ema.iloc[-self.ema_slope_lookback]

        if trend == "buy" and ema_slope < self.min_ema_slope:
            return self._empty_signal(f"❌ Weak BUY slope | slope={ema_slope:.6f}")
        if trend == "sell" and ema_slope > -self.min_ema_slope:
            return self._empty_signal(f"❌ Weak SELL slope | slope={ema_slope:.6f}")

        # ── V1 FILTER 3: EMA slope must be ACCELERATING ──
        # Current slope vs slope N bars ago — trend must be gaining strength
        prev_slope = ema.iloc[-1 - self.slope_accel_lookback] - ema.iloc[-1 - self.slope_accel_lookback - self.ema_slope_lookback]

        if trend == "buy" and ema_slope < prev_slope:
            return self._empty_signal(
                f"❌ BUY slope decelerating | now={ema_slope:.6f} prev={prev_slope:.6f}"
            )
        if trend == "sell" and ema_slope > prev_slope:
            return self._empty_signal(
                f"❌ SELL slope decelerating | now={ema_slope:.6f} prev={prev_slope:.6f}"
            )

        # ── FILTER 4: RSI level confirmation ──
        if trend == "buy" and last_rsi < self.rsi_buy_level:
            return self._empty_signal(f"❌ BUY rejected | RSI={last_rsi:.2f} < {self.rsi_buy_level}")
        if trend == "sell" and last_rsi > self.rsi_sell_level:
            return self._empty_signal(f"❌ SELL rejected | RSI={last_rsi:.2f} > {self.rsi_sell_level}")

        # ── V1 FILTER 5: RSI must be MOVING in the right direction ──
        # RSI now vs RSI N bars ago — momentum must be picking up
        prev_rsi = rsi.iloc[-1 - self.rsi_momentum_lookback]

        if trend == "buy" and last_rsi <= prev_rsi:
            return self._empty_signal(
                f"❌ RSI not rising for BUY | now={last_rsi:.1f} prev={prev_rsi:.1f}"
            )
        if trend == "sell" and last_rsi >= prev_rsi:
            return self._empty_signal(
                f"❌ RSI not falling for SELL | now={last_rsi:.1f} prev={prev_rsi:.1f}"
            )

        # ── V1 FILTER 6: HTF RSI bias ──
        # Longer period RSI acts as higher timeframe bias
        # Only take trades when HTF RSI agrees with direction
        if self.use_htf_rsi:
            if trend == "buy" and last_htf_rsi < 50:
                return self._empty_signal(
                    f"❌ HTF RSI bearish for BUY | HTF RSI={last_htf_rsi:.1f} < 50"
                )
            if trend == "sell" and last_htf_rsi > 50:
                return self._empty_signal(
                    f"❌ HTF RSI bullish for SELL | HTF RSI={last_htf_rsi:.1f} > 50"
                )

        # ── FILTER 7: Momentum candle ──
        if trend == "buy" and last_close <= last_open:
            return self._empty_signal(f"❌ BUY momentum fail | close≤open")
        if trend == "sell" and last_close >= last_open:
            return self._empty_signal(f"❌ SELL momentum fail | close≥open")

        # ── V1 FILTER 8: Volume — tightened threshold ──
        volume_ratio = None
        if self.use_volume_filter:
            vol_col = next(
                (c for c in ["volume", "tick_volume", "vol", "real_volume"] if c in price_data.columns),
                None,
            )
            if vol_col:
                volume = price_data[vol_col]
                last_vol = volume.iloc[-2]  # closed bar volume
                vol_ma = volume.iloc[-(self.volume_ma_period + 2):-2].mean()
                volume_ratio = last_vol / vol_ma if vol_ma > 0 else 0

                if volume_ratio < self.volume_ratio_min:
                    return self._empty_signal(
                        f"❌ Volume too low | ratio={volume_ratio:.2f} < {self.volume_ratio_min}"
                    )

        # ── ENTRY PRICE ──
        if self.backtest_mode:
            entry_price = last_close
        else:
            try:
                import MetaTrader5 as mt5
                tick = mt5.symbol_info_tick(symbol)
                if tick is None:
                    return self._empty_signal("❌ MT5 tick unavailable")
                entry_price = tick.ask if trend == "buy" else tick.bid
            except Exception:
                entry_price = last_close

        # ── SL / TP ──
        sl, tp = self._sl_tp(entry_price, trend, symbol)

        # ── LOT SIZE ──
        lot = self._calculate_lot_size(symbol)

        # ── FINAL SIGNAL ──
        return {
            "signal": trend,
            "entry_price": entry_price,
            "stop_loss": sl,
            "take_profit": tp,
            "entry_date": entry_time,
            "lot_size": lot,
            "rsi": round(last_rsi, 2),
            "htf_rsi": round(last_htf_rsi, 2),
            "slope": round(ema_slope, 6),
            "volume_ratio": round(volume_ratio, 2) if volume_ratio else "N/A",
            "reason": (
                f"✅ {trend.upper()} | "
                f"EMA{self.ema_trend} slope={ema_slope:.6f} accelerating | "
                f"RSI={last_rsi:.1f} moving {'up' if trend == 'buy' else 'down'} | "
                f"HTF RSI={last_htf_rsi:.1f} | "
                f"Vol ratio={volume_ratio if volume_ratio else 'N/A'} | "
                f"Lot={lot}"
            ),
        }

    # ─────────────────────────────────────────────
    # EMPTY SIGNAL
    # ─────────────────────────────────────────────

    def _empty_signal(self, reason: str) -> Dict[str, Any]:
        return {
            "signal": None,
            "entry_price": None,
            "stop_loss": None,
            "take_profit": None,
            "entry_date": None,
            "lot_size": None,
            "reason": reason,
        }

    # ─────────────────────────────────────────────
    # PARAMETERS / REPR
    # ─────────────────────────────────────────────

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "name": "RSI Flexible Strategy V1",
            "sl_pips": self.sl_pips,
            "rr_ratio": self.rr_ratio,
            "rsi_period": self.rsi_period,
            "rsi_buy_level": self.rsi_buy_level,
            "rsi_sell_level": self.rsi_sell_level,
            "htf_rsi_period": self.htf_rsi_period,
            "use_htf_rsi": self.use_htf_rsi,
            "ema_trend": self.ema_trend,
            "ema_slope_lookback": self.ema_slope_lookback,
            "slope_accel_lookback": self.slope_accel_lookback,
            "rsi_momentum_lookback": self.rsi_momentum_lookback,
            "volume_filter": self.use_volume_filter,
            "volume_ratio_min": self.volume_ratio_min,
            "risk_percent": self.risk_percent,
            "daily_loss_pct": self.daily_loss_pct,
        }

    def __repr__(self) -> str:
        p = self.get_parameters()
        return (
            f"RSIFlexibleStrategyV1("
            f"RSI{p['rsi_period']} HTF{p['htf_rsi_period']} | "
            f"EMA{p['ema_trend']} | "
            f"SL={p['sl_pips']} pips | "
            f"RR=1:{p['rr_ratio']} | "
            f"Risk={p['risk_percent']}%)"
        )