import pandas as pd
from typing import Dict, Any, Optional
import MetaTrader5 as mt5
import time, datetime


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
        tp_pips: float = None,
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
        min_ema_slope: float = 0.0005,
        set_wat_time: bool = True,  # whether to set WAT timezone for live balance checks
    ):
        self.sl_pips = sl_pips
        self.tp_pips = tp_pips if tp_pips is not None else sl_pips  # 1:1 R/R

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
        self.min_ema_slope = min_ema_slope
        # Define WAT (UTC+1)

        self.wat_tz = (
            datetime.timezone(datetime.timedelta(hours=1))
            if set_wat_time
            else datetime.timezone.utc
        )

        self.balance_cap: dict = {
            "EURUSDm": {"p": 0.2, "l": 0.2},
            "GBPJPYm": {"p": 0.2, "l": 0.2},
            "EURJPYm": {"p": 0.2, "l": 0.2},
            "XAUUSDm": {"p": 0.2, "l": 0.2},
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
        now = datetime.datetime.now(self.wat_tz)
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
            p, l = self.balance_cap.get(symbol, {"p": 0.6, "l": 0.4}).values()
            # Profit threshold (>=70% gain)
            if (bal > self.starting_balance) and (bal - self.starting_balance) >= (
                p * self.starting_balance
            ):
                return True, "profit_target"
            elif (self.starting_balance > bal) and (self.starting_balance - bal) >= (
                l * self.starting_balance
            ):
                return True, "loss_target"
            return False, "ok"
        else:
            bal = self.get_live_balance_from_trades(symbol=symbol)
            p, l = self.balance_cap.get(symbol, {"p": 0.6, "l": 0.4}).values()
            # Profit threshold (>=70% gain)
            if bal >= p * self.starting_balance:
                print(f"✅ reached profit target!")
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
            if not hasattr(self, "trade_log") or not self.trade_log:
                return False

            daily_pnl = sum(
                t["pnl"]
                for t in self.trade_log
                if day_start <= pd.to_datetime(t["exit_time"]) <= day_end
            )

        else:
            # Live mode — use real clock
            now = datetime.datetime.now(self.wat_tz)
            day_start = datetime.datetime(now.year, now.month, now.day)
            from_timestamp = int(day_start.timestamp())
            now_timestamp = int(time.time())

            deals = mt5.history_deals_get(from_timestamp, now_timestamp)
            if not deals:
                return False

            daily_pnl = sum(d.profit for d in deals)

        daily_limit = -0.1 * self.starting_balance

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
            d
            for d in deals
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
            print(
                f"{symbol} consecutive wins counted: {wins} | lot set to {self.current_lot}"
            )
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
        # if self._check_daily_loss(price_data=price_data):
        #     return self._empty_signal(
        #         "🛑 Global daily loss limit hit — no more trades today"
        #     )
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


class RSIFlexibleStrategy_MACDReversal:
    """
    Flexible RSI Strategy + MACD Reversal Filter
    - RSI trend with moderate validations
    - EMA slope optional confirmation
    - Volume check optional
    - Dynamic lot sizing using winning streak
    - MACD reversal filter: BUY only if prev MACD histogram was red (negative)
                            SELL only if prev MACD histogram was green (positive)
    """

    def __init__(
        self,
        sl_pips: float = 20.0,
        tp_pips: float = None,
        allowed_weekdays: Optional[list[int]] = None,
        allowed_hours: Optional[list[int]] = None,
        starting_lot: float = 0.01,
        rsi_period: int = 14,
        rsi_buy_level: float = 35,
        rsi_sell_level: float = 65,
        ema_trend: int = 50,
        ema_slope_lookback: int = 5,
        use_volume_filter: bool = True,
        backtest_mode: bool = False,
        initial_balance: float = 100.0,
        min_ema_slope: float = 0.0005,
        set_wat_time: bool = True,
        # MACD params
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
    ):
        self.sl_pips = sl_pips
        self.tp_pips = tp_pips if tp_pips is not None else sl_pips  # 1:1 R/R

        self.allowed_weekdays = allowed_weekdays or list(range(7))
        self.allowed_hours = allowed_hours or list(range(24))

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
        self.min_ema_slope = min_ema_slope

        # MACD
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal_period = macd_signal

        # Define WAT (UTC+1)
        self.wat_tz = (
            datetime.timezone(datetime.timedelta(hours=1))
            if set_wat_time
            else datetime.timezone.utc
        )

        self.balance_cap: dict = {
            "EURUSDm": {"p": 0.2, "l": 0.2},
            "GBPJPYm": {"p": 0.2, "l": 0.2},
            "EURJPYm": {"p": 0.2, "l": 0.2},
            "XAUUSDm": {"p": 0.2, "l": 0.2},
        }

        # Volume
        self.use_volume_filter = use_volume_filter
        self.volume_ma_period = 20

        self.starting_balance = initial_balance
        self.current_balance = initial_balance

    # ---------------- BALANCE MANAGEMENT ---------------- #
    def get_balance(self) -> float:
        if self.backtest_mode:
            return getattr(self, "current_balance", self.starting_balance)

    def get_live_balance_from_trades(self, symbol: str = None) -> float:
        now = datetime.datetime.now(self.wat_tz)
        year_start = datetime.datetime(now.year, 1, 1)

        now_timestamp = int(time.time())
        from_timestamp = int(year_start.timestamp())

        deals = mt5.history_deals_get(from_timestamp, now_timestamp)

        if symbol:
            deals = [d for d in deals if d.symbol == symbol]

        profit = sum(d.profit for d in deals)
        return profit

    def update_balance(self, new_balance: float):
        if not self.backtest_mode:
            raise RuntimeError("Cannot manually update balance in live mode")
        self.current_balance = new_balance

    def _check_balance_stop(self, symbol=None):
        if self.backtest_mode:
            bal = self.get_balance()
            p, l = self.balance_cap.get(symbol, {"p": 0.6, "l": 0.4}).values()
            if (bal > self.starting_balance) and (bal - self.starting_balance) >= (
                p * self.starting_balance
            ):
                return True, "profit_target"
            elif (self.starting_balance > bal) and (self.starting_balance - bal) >= (
                l * self.starting_balance
            ):
                return True, "loss_target"
            return False, "ok"
        else:
            bal = self.get_live_balance_from_trades(symbol=symbol)
            p, l = self.balance_cap.get(symbol, {"p": 0.6, "l": 0.4}).values()
            if bal >= p * self.starting_balance:
                print(f"✅ reached profit target!")
                return True, "profit_target"

            if bal <= -1 * (l * self.starting_balance):
                print(
                    f"⚠️ {symbol or 'Symbol'} hit loss limit! Stop trading. Current balance: {bal:.2f}"
                )
                return True, "loss_limit"

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
            current_time = pd.to_datetime(self._get_entry_time(price_data))
            day_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = current_time

            if not hasattr(self, "trade_log") or not self.trade_log:
                return False

            daily_pnl = sum(
                t["pnl"]
                for t in self.trade_log
                if day_start <= pd.to_datetime(t["exit_time"]) <= day_end
            )
        else:
            now = datetime.datetime.now(self.wat_tz)
            day_start = datetime.datetime(now.year, now.month, now.day)
            from_timestamp = int(day_start.timestamp())
            now_timestamp = int(time.time())

            deals = mt5.history_deals_get(from_timestamp, now_timestamp)
            if not deals:
                return False

            daily_pnl = sum(d.profit for d in deals)

        daily_limit = -0.1 * self.starting_balance

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
        if symbol.endswith("JPY"):
            return 0.01
        return 0.0001

    def _sl_tp(self, entry_price: float, side: str, symbol: str) -> tuple[float, float]:
        if symbol.startswith("XAU"):
            pip_value = 0.1
            digits = 3
        elif symbol.endswith("JPYm"):
            pip_value = 0.01
            digits = 3
        else:
            pip_value = 0.0001
            digits = 5

        sl_distance = self.sl_pips * pip_value
        tp_distance = self.tp_pips * pip_value

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

    def _calculate_macd(self, close: pd.Series):
        """
        Calculate MACD histogram (MACD line - Signal line).
        Histogram > 0 = bullish (green bar)
        Histogram < 0 = bearish (red bar)
        """
        ema_fast = close.ewm(span=self.macd_fast, adjust=False).mean()
        ema_slow = close.ewm(span=self.macd_slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=self.macd_signal_period, adjust=False).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    def _get_consecutive_wins(self, symbol: str, lookback_days=30) -> int:
        if not mt5.initialize():
            return 0

        now = int(time.time())
        from_time = now - 60 * 60 * 24 * lookback_days

        deals = mt5.history_deals_get(from_time, now)
        if not deals:
            return 0

        allowed_types = (
            mt5.DEAL_TYPE_BUY,
            mt5.DEAL_TYPE_SELL,
        )

        exit_deals = [
            d
            for d in deals
            if d.symbol == symbol and d.entry == 1 and d.type in allowed_types
        ]

        if not exit_deals:
            return 0

        exit_deals.sort(key=lambda d: d.time, reverse=True)

        wins = 0
        for d in exit_deals:
            if d.profit > 0:
                wins += 1
            else:
                break

        return wins

    # ---------------- LOT SIZING ---------------- #
    def _get_lot_size(self, symbol: str) -> float:
        if self.backtest_mode:
            if self.last_trade_won and self.winning_streak >= 2:
                self.current_lot += 0.01
            else:
                self.current_lot = self.starting_lot
            return round(self.current_lot, 2)
        else:
            wins = self._get_consecutive_wins(symbol)
            self.current_lot = (
                self.starting_lot if wins < 2 else self.starting_lot + 0.01 * (wins - 1)
            )
            print(
                f"{symbol} consecutive wins counted: {wins} | lot set to {self.current_lot}"
            )
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
            self.macd_slow
            + self.macd_signal_period
            + 2,  # enough bars for MACD to warm up
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
        _, _, macd_hist = self._calculate_macd(close)

        last_close = close.iloc[-1]
        last_open = open_.iloc[-1]
        last_ema = ema.iloc[-1]

        # ---------- TREND DIRECTION (PRICE vs EMA) ----------
        trend = "buy" if last_close >= last_ema else "sell"

        # ---------- EMA SLOPE (STEEPNESS) ----------
        ema_slope = ema.iloc[-1] - ema.iloc[-self.ema_slope_lookback]
        min_slope = self.min_ema_slope

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

        # ---------- MACD REVERSAL FILTER ----------
        # We use iloc[-2] = the fully closed previous bar's histogram value
        # BUY  → prev histogram must be red  (< 0): selling pressure just eased
        # SELL → prev histogram must be green (> 0): buying pressure just peaked
        prev_macd_hist = macd_hist.iloc[-2]

        if trend == "buy" and prev_macd_hist >= 0:
            return self._empty_signal(
                f"❌ BUY blocked | prev MACD hist={prev_macd_hist:.5f} was green — no reversal yet"
            )

        if trend == "sell" and prev_macd_hist <= 0:
            return self._empty_signal(
                f"❌ SELL blocked | prev MACD hist={prev_macd_hist:.5f} was red — no reversal yet"
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

        if self.backtest_mode:
            entry_price = last_close
        else:
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
                f"prev_MACD_hist={prev_macd_hist:.5f} ({'red 🔴' if prev_macd_hist < 0 else 'green 🟢'}), "
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
            "name": "RSI Flexible Strategy + MACD Reversal",
            "sl_pips": self.sl_pips,
            "tp_pips": self.tp_pips,
            "rsi_period": self.rsi_period,
            "rsi_buy_level": self.rsi_buy_level,
            "rsi_sell_level": self.rsi_sell_level,
            "ema_trend": self.ema_trend,
            "ema_slope_lookback": self.ema_slope_lookback,
            "volume_filter": self.use_volume_filter,
            "starting_lot": self.starting_lot,
            "macd_fast": self.macd_fast,
            "macd_slow": self.macd_slow,
            "macd_signal": self.macd_signal_period,
        }

    def __repr__(self) -> str:
        return (
            f"RSIFlexibleStrategy_MACDReversal(RSI{self.rsi_period}, EMA{self.ema_trend}, "
            f"MACD({self.macd_fast},{self.macd_slow},{self.macd_signal_period}), "
            f"SL={self.sl_pips}, TP={self.tp_pips})"
        )


class RSIFlexibleStrategy_MACDReversal_Trial1:
    """
    Flexible RSI Strategy + MACD Reversal Filter — Trial 1
    - RSI trend with moderate validations
    - EMA slope optional confirmation
    - Volume check optional
    - Dynamic lot sizing: tiered streak multiplier (1.5x / 2x / 3x)
      with equity curve drawdown guard (scales to 0.5x when below 20-bar MA)
    - MACD reversal filter: BUY only if prev MACD histogram was red (negative)
                            SELL only if prev MACD histogram was green (positive)
    """

    def __init__(
        self,
        sl_pips: float = 20.0,
        tp_pips: float = None,
        allowed_weekdays: Optional[list[int]] = None,
        allowed_hours: Optional[list[int]] = None,
        starting_lot: float = 0.01,
        rsi_period: int = 14,
        rsi_buy_level: float = 35,
        rsi_sell_level: float = 65,
        ema_trend: int = 50,
        ema_slope_lookback: int = 5,
        use_volume_filter: bool = True,
        backtest_mode: bool = False,
        initial_balance: float = 100.0,
        min_ema_slope: float = 0.0005,
        set_wat_time: bool = True,
        # MACD params
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
    ):
        self.sl_pips = sl_pips
        self.tp_pips = tp_pips if tp_pips is not None else sl_pips  # 1:1 R/R

        self.allowed_weekdays = allowed_weekdays or list(range(7))
        self.allowed_hours = allowed_hours or list(range(24))

        self.starting_lot = starting_lot
        self.current_lot = starting_lot
        self.backtest_mode = backtest_mode
        self.winning_streak = 0
        self.last_trade_won = False
        self._equity_history: list[float] = []

        # RSI
        self.rsi_period = rsi_period
        self.rsi_buy_level = rsi_buy_level
        self.rsi_sell_level = rsi_sell_level

        # EMA Slope
        self.ema_trend = ema_trend
        self.ema_slope_lookback = ema_slope_lookback
        self.min_ema_slope = min_ema_slope

        # MACD
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal_period = macd_signal

        # Define WAT (UTC+1)
        self.wat_tz = (
            datetime.timezone(datetime.timedelta(hours=1))
            if set_wat_time
            else datetime.timezone.utc
        )

        self.balance_cap: dict = {
            "EURUSDm": {"p": 0.2, "l": 0.2},
            "GBPJPYm": {"p": 0.2, "l": 0.2},
            "EURJPYm": {"p": 0.2, "l": 0.2},
            "XAUUSDm": {"p": 0.2, "l": 0.2},
        }

        # Volume
        self.use_volume_filter = use_volume_filter
        self.volume_ma_period = 20

        self.starting_balance = initial_balance
        self.current_balance = initial_balance

    # ---------------- BALANCE MANAGEMENT ---------------- #

    def get_balance(self) -> float:
        if self.backtest_mode:
            return getattr(self, "current_balance", self.starting_balance)

    def get_live_balance_from_trades(self, symbol: str = None) -> float:
        now = datetime.datetime.now(self.wat_tz)
        year_start = datetime.datetime(now.year, 1, 1)

        now_timestamp = int(time.time())
        from_timestamp = int(year_start.timestamp())

        deals = mt5.history_deals_get(from_timestamp, now_timestamp)

        if symbol:
            deals = [d for d in deals if d.symbol == symbol]

        profit = sum(d.profit for d in deals)
        return profit

    def update_balance(self, new_balance: float):
        if not self.backtest_mode:
            raise RuntimeError("Cannot manually update balance in live mode")
        self.current_balance = new_balance

    def _check_balance_stop(self, symbol=None):
        if self.backtest_mode:
            bal = self.get_balance()
            p, l = self.balance_cap.get(symbol, {"p": 0.6, "l": 0.4}).values()
            if (bal > self.starting_balance) and (bal - self.starting_balance) >= (
                p * self.starting_balance
            ):
                return True, "profit_target"
            elif (self.starting_balance > bal) and (self.starting_balance - bal) >= (
                l * self.starting_balance
            ):
                return True, "loss_target"
            return False, "ok"
        else:
            bal = self.get_live_balance_from_trades(symbol=symbol)
            p, l = self.balance_cap.get(symbol, {"p": 0.6, "l": 0.4}).values()
            if bal >= p * self.starting_balance:
                print(f"✅ reached profit target!")
                return True, "profit_target"

            if bal <= -1 * (l * self.starting_balance):
                print(
                    f"⚠️ {symbol or 'Symbol'} hit loss limit! Stop trading. Current balance: {bal:.2f}"
                )
                return True, "loss_limit"

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
            current_time = pd.to_datetime(self._get_entry_time(price_data))
            day_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = current_time

            if not hasattr(self, "trade_log") or not self.trade_log:
                return False

            daily_pnl = sum(
                t["pnl"]
                for t in self.trade_log
                if day_start <= pd.to_datetime(t["exit_time"]) <= day_end
            )
        else:
            now = datetime.datetime.now(self.wat_tz)
            day_start = datetime.datetime(now.year, now.month, now.day)
            from_timestamp = int(day_start.timestamp())
            now_timestamp = int(time.time())

            deals = mt5.history_deals_get(from_timestamp, now_timestamp)
            if not deals:
                return False

            daily_pnl = sum(d.profit for d in deals)

        daily_limit = -0.1 * self.starting_balance

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
        if symbol.endswith("JPY"):
            return 0.01
        return 0.0001

    def _sl_tp(self, entry_price: float, side: str, symbol: str) -> tuple[float, float]:
        if symbol.startswith("XAU"):
            pip_value = 0.1
            digits = 3
        elif symbol.endswith("JPYm"):
            pip_value = 0.01
            digits = 3
        else:
            pip_value = 0.0001
            digits = 5

        sl_distance = self.sl_pips * pip_value
        tp_distance = self.tp_pips * pip_value

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

    def _calculate_macd(self, close: pd.Series):
        """
        Calculate MACD histogram (MACD line - Signal line).
        Histogram > 0 = bullish (green bar)
        Histogram < 0 = bearish (red bar)
        """
        ema_fast = close.ewm(span=self.macd_fast, adjust=False).mean()
        ema_slow = close.ewm(span=self.macd_slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=self.macd_signal_period, adjust=False).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    def _get_consecutive_wins(self, symbol: str, lookback_days=30) -> int:
        if not mt5.initialize():
            return 0

        now = int(time.time())
        from_time = now - 60 * 60 * 24 * lookback_days

        deals = mt5.history_deals_get(from_time, now)
        if not deals:
            return 0

        allowed_types = (
            mt5.DEAL_TYPE_BUY,
            mt5.DEAL_TYPE_SELL,
        )

        exit_deals = [
            d
            for d in deals
            if d.symbol == symbol and d.entry == 1 and d.type in allowed_types
        ]

        if not exit_deals:
            return 0

        exit_deals.sort(key=lambda d: d.time, reverse=True)

        wins = 0
        for d in exit_deals:
            if d.profit > 0:
                wins += 1
            else:
                break

        return wins

    # ---------------- LOT SIZING (TRIAL 1) ---------------- #

    def _get_lot_size(self, symbol: str) -> float:
        """
        Tiered streak scaling + equity curve drawdown guard.

        Streak tiers:
          < 2  → 1.0x  (base)
          2–3  → 1.5x
          4–5  → 2.0x
          6+   → 3.0x

        Equity curve guard:
          If current_balance < 20-bar rolling avg → scale back to 0.5x base.
          Protects capital during losing/choppy phases.

        Hard cap: 1.0 lot (safety for small accounts).
        """
        if self.backtest_mode:
            streak = self.winning_streak

            # --- Equity curve guard (drawdown protection) ---
            if len(self._equity_history) >= 20:
                rolling_avg = sum(self._equity_history[-20:]) / 20
                if self.current_balance < rolling_avg:
                    lot = max(self.starting_lot * 0.5, 0.01)
                    self.current_lot = round(lot, 2)
                    return self.current_lot

            # --- Tiered streak multiplier ---
            if streak >= 6:
                multiplier = 3.0
            elif streak >= 4:
                multiplier = 2.0
            elif streak >= 2:
                multiplier = 1.5
            else:
                multiplier = 1.0

            lot = self.starting_lot * multiplier
            self.current_lot = round(min(lot, 1.0), 2)
            return self.current_lot

        else:
            wins = self._get_consecutive_wins(symbol)

            if wins >= 6:
                multiplier = 3.0
            elif wins >= 4:
                multiplier = 2.0
            elif wins >= 2:
                multiplier = 1.5
            else:
                multiplier = 1.0

            self.current_lot = round(min(self.starting_lot * multiplier, 1.0), 2)
            print(
                f"{symbol} streak={wins} | multiplier={multiplier}x "
                f"| lot={self.current_lot}"
            )
            return self.current_lot

    def update_trade_result(self, was_win: bool):
        """Track win streak and push balance snapshot into equity history."""
        if was_win:
            self.winning_streak += 1
        else:
            self.winning_streak = 0
        self.last_trade_won = was_win

        # Snapshot current balance for equity curve guard
        self._equity_history.append(self.current_balance)

    # ---------------- MAIN LOGIC ---------------- #

    def generate_signal(self, price_data: pd.DataFrame, symbol: str) -> Dict[str, Any]:

        # ---------- BASIC GUARDS ----------
        if price_data is None or price_data.empty:
            return self._empty_signal("❌ Price data is empty or None")

        stop, reason = self._check_balance_stop(symbol=symbol)
        if stop:
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
            self.macd_slow + self.macd_signal_period + 2,
        )

        if len(price_data) < min_bars:
            return self._empty_signal(
                f"❌ Not enough data | bars={len(price_data)} need≥{min_bars}"
            )

        # ---------- TIME FILTER ----------
        entry_time = self._get_entry_time(price_data)
        day_names = [
            "Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday",
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
        _, _, macd_hist = self._calculate_macd(close)

        last_close = close.iloc[-1]
        last_open = open_.iloc[-1]
        last_ema = ema.iloc[-1]

        # ---------- TREND DIRECTION (PRICE vs EMA) ----------
        trend = "buy" if last_close >= last_ema else "sell"

        # ---------- EMA SLOPE (STEEPNESS) ----------
        ema_slope = ema.iloc[-1] - ema.iloc[-self.ema_slope_lookback]
        min_slope = self.min_ema_slope

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

        # ---------- MACD REVERSAL FILTER ----------
        prev_macd_hist = macd_hist.iloc[-2]

        if trend == "buy" and prev_macd_hist >= 0:
            return self._empty_signal(
                f"❌ BUY blocked | prev MACD hist={prev_macd_hist:.5f} was green — no reversal yet"
            )

        if trend == "sell" and prev_macd_hist <= 0:
            return self._empty_signal(
                f"❌ SELL blocked | prev MACD hist={prev_macd_hist:.5f} was red — no reversal yet"
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

        if self.backtest_mode:
            entry_price = last_close
        else:
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
                f"prev_MACD_hist={prev_macd_hist:.5f} ({'red 🔴' if prev_macd_hist < 0 else 'green 🟢'}), "
                f"volume_ratio={volume_ratio if volume_ratio else 'OK'}, "
                f"streak={self.winning_streak}, "
                f"lot={lot}"
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
            "name": "RSI Flexible Strategy + MACD Reversal — Trial 1",
            "sl_pips": self.sl_pips,
            "tp_pips": self.tp_pips,
            "rsi_period": self.rsi_period,
            "rsi_buy_level": self.rsi_buy_level,
            "rsi_sell_level": self.rsi_sell_level,
            "ema_trend": self.ema_trend,
            "ema_slope_lookback": self.ema_slope_lookback,
            "volume_filter": self.use_volume_filter,
            "starting_lot": self.starting_lot,
            "macd_fast": self.macd_fast,
            "macd_slow": self.macd_slow,
            "macd_signal": self.macd_signal_period,
            # Trial 1 additions
            "lot_tiers": "1.0x / 1.5x / 2.0x / 3.0x",
            "equity_curve_lookback": 20,
            "drawdown_lot_multiplier": 0.5,
        }

    def __repr__(self) -> str:
        return (
            f"RSIFlexibleStrategy_MACDReversal_Trial1("
            f"RSI{self.rsi_period}, EMA{self.ema_trend}, "
            f"MACD({self.macd_fast},{self.macd_slow},{self.macd_signal_period}), "
            f"SL={self.sl_pips}, TP={self.tp_pips}, "
            f"streak={self.winning_streak}, lot={self.current_lot})"
        )