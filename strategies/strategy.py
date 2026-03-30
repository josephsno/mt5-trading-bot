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


class EMAPullbackStrategy:
    """
    EMA Pullback Trend Rider Strategy
    -----------------------------------
    - 3 EMA stack confirms trend direction (EMA20 > EMA50 > EMA200 for buys)
    - ADX filter blocks consolidation/ranging markets
    - Waits for price to pull back to EMA50 before entering
    - RSI confirms pullback is ending and momentum is resuming
    - Entry triggered by a strong momentum candle
    - Dynamic SL based on EMA50 distance + buffer
    - Dynamic lot sizing based on % risk of account balance
    - 1:2 Risk/Reward ratio
    """

    def __init__(
        self,
        allowed_weekdays: Optional[list] = None,
        allowed_hours: Optional[list] = None,
        risk_percent: float = 2.0,          # % of balance to risk per trade
        min_lot: float = 0.01,
        max_lot: float = 5.0,
        ema_fast: int = 20,
        ema_mid: int = 50,
        ema_slow: int = 200,
        adx_period: int = 14,
        adx_min_threshold: float = 25.0,    # min ADX to allow trading
        adx_max_threshold: float = 60.0,    # max ADX to avoid blow-off moves
        rsi_period: int = 14,
        rsi_buy_zone_low: float = 35.0,     # RSI must be in this zone on pullback
        rsi_buy_zone_high: float = 55.0,
        rsi_sell_zone_low: float = 45.0,
        rsi_sell_zone_high: float = 65.0,
        sl_buffer_pips: float = 5.0,        # buffer pips beyond EMA50 for SL
        pullback_tolerance_pips: float = 15.0,  # how close price must be to EMA50
        rr_ratio: float = 2.0,              # risk/reward ratio
        backtest_mode: bool = False,
        initial_balance: float = 100.0,
    ):
        # Time filters
        self.allowed_weekdays = allowed_weekdays or list(range(7))
        self.allowed_hours = allowed_hours or list(range(24))

        # Risk management
        self.risk_percent = risk_percent
        self.min_lot = min_lot
        self.max_lot = max_lot
        self.rr_ratio = rr_ratio

        # EMA periods
        self.ema_fast = ema_fast
        self.ema_mid = ema_mid
        self.ema_slow = ema_slow

        # ADX settings
        self.adx_period = adx_period
        self.adx_min_threshold = adx_min_threshold
        self.adx_max_threshold = adx_max_threshold

        # RSI settings
        self.rsi_period = rsi_period
        self.rsi_buy_zone_low = rsi_buy_zone_low
        self.rsi_buy_zone_high = rsi_buy_zone_high
        self.rsi_sell_zone_low = rsi_sell_zone_low
        self.rsi_sell_zone_high = rsi_sell_zone_high

        # Pullback settings
        self.sl_buffer_pips = sl_buffer_pips
        self.pullback_tolerance_pips = pullback_tolerance_pips

        # Balance
        self.backtest_mode = backtest_mode
        self.starting_balance = initial_balance
        self.current_balance = initial_balance

        # Trade tracking (for streak/result awareness)
        self.winning_streak = 0
        self.last_trade_won = False

        # Balance protection thresholds
        self.profit_target_pct = 0.60   # stop at +60%
        self.loss_limit_pct = 0.60      # stop at -60%

        # Expose these so backtester can read them (same as RSIFlexibleStrategy)
        self.ema_period = self.ema_slow   # used in backtester min_bars_needed
        self.ltf_rsi_period = self.rsi_period
        self.htf_rsi_period = self.rsi_period

    # ─────────────────────────────────────────────
    # BALANCE MANAGEMENT  (mirrors RSIFlexibleStrategy)
    # ─────────────────────────────────────────────

    def get_balance(self) -> float:
        if self.backtest_mode:
            return self.current_balance
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

    def _check_balance_stop(self, symbol=None):
        bal = self.get_balance()
        gain = bal - self.starting_balance
        if gain >= self.profit_target_pct * self.starting_balance:
            return True, "profit_target"
        if gain <= -(self.loss_limit_pct * self.starting_balance):
            return True, "loss_target"
        return False, "ok"

    def update_trade_result(self, was_win: bool):
        """Called by backtester after each trade closes — mirrors RSIFlexibleStrategy"""
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
        if "JPY" in symbol:
            return 0.01
        if symbol.startswith("XAU"):
            return 0.1
        return 0.0001

    def _pip_value_per_lot(self, symbol: str) -> float:
        """USD value of 1 pip for 1 standard lot"""
        if "JPY" in symbol:
            return 9.0      # approx — varies with rate
        if symbol.startswith("XAU"):
            return 10.0
        return 10.0         # standard USD quote pairs

    # ─────────────────────────────────────────────
    # INDICATORS
    # ─────────────────────────────────────────────

    def _calculate_rsi(self, close: pd.Series) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=self.rsi_period - 1, min_periods=self.rsi_period).mean()
        avg_loss = loss.ewm(com=self.rsi_period - 1, min_periods=self.rsi_period).mean()
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _calculate_adx(self, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
        """
        Wilder's ADX — measures trend strength regardless of direction.
        Returns ADX series.
        """
        period = self.adx_period

        # True Range
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ], axis=1).max(axis=1)

        # Directional moves
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low

        plus_dm = pd.Series(0.0, index=high.index)
        minus_dm = pd.Series(0.0, index=high.index)

        plus_dm[((up_move > down_move) & (up_move > 0))] = up_move
        minus_dm[((down_move > up_move) & (down_move > 0))] = down_move

        # Wilder smoothing
        atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        plus_di = 100 * (plus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr)
        minus_di = 100 * (minus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr)

        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1))
        adx = dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

        return adx

    # ─────────────────────────────────────────────
    # LOT SIZING
    # ─────────────────────────────────────────────

    def _calculate_lot_size(self, symbol: str, sl_pips: float) -> float:
        """
        Dynamic lot size based on % risk of current balance.

        lot = risk_amount / (sl_pips * pip_value_per_lot)
        """
        balance = self.get_balance()
        risk_amount = balance * (self.risk_percent / 100)
        pip_val = self._pip_value_per_lot(symbol)

        if sl_pips <= 0 or pip_val <= 0:
            return self.min_lot

        lot = risk_amount / (sl_pips * pip_val)
        lot = max(self.min_lot, min(round(lot, 2), self.max_lot))
        return lot

    # ─────────────────────────────────────────────
    # SL / TP
    # ─────────────────────────────────────────────

    def _sl_tp(self, entry_price: float, side: str, ema50: float, symbol: str):
        """
        Dynamic SL/TP:
        - SL is placed beyond EMA50 + buffer pips
        - TP is SL distance * rr_ratio
        """
        pip = self._pip_size(symbol)
        buffer = self.sl_buffer_pips * pip

        if side == "buy":
            sl = ema50 - buffer
            sl_distance = entry_price - sl
        else:
            sl = ema50 + buffer
            sl_distance = sl - entry_price

        # Safety — sl_distance must be positive
        sl_distance = max(sl_distance, pip * 5)
        tp_distance = sl_distance * self.rr_ratio

        if side == "buy":
            tp = entry_price + tp_distance
        else:
            tp = entry_price - tp_distance

        # Round to appropriate digits
        digits = 3 if ("JPY" in symbol or symbol.startswith("XAU")) else 5
        return round(sl, digits), round(tp, digits)

    # ─────────────────────────────────────────────
    # MAIN SIGNAL GENERATION
    # ─────────────────────────────────────────────

    def generate_signal(self, price_data: pd.DataFrame, symbol: str) -> Dict[str, Any]:

        # ── GUARD: empty data ──
        if price_data is None or price_data.empty:
            return self._empty_signal("❌ Price data empty")

        # ── GUARD: balance protection ──
        stop, reason = self._check_balance_stop(symbol=symbol)
        if stop:
            msg = "✅ Profit target reached" if reason == "profit_target" else "⚠️ Loss limit reached"
            return self._empty_signal(msg)

        # ── GUARD: enough bars ──
        min_bars = self.ema_slow + self.adx_period + 10
        if len(price_data) < min_bars:
            return self._empty_signal(f"❌ Not enough bars | {len(price_data)} < {min_bars}")

        # ── TIME FILTERS ──
        entry_time = self._get_entry_time(price_data)
        if not self._is_allowed_date(entry_time):
            return self._empty_signal(f"📅 Day filtered | weekday={pd.to_datetime(entry_time).weekday()}")
        if not self._is_allowed_hour(entry_time):
            return self._empty_signal(f"⏰ Hour filtered | hour={pd.to_datetime(entry_time).hour}")

        # ── PRICE SERIES ──
        close = price_data["close"]
        high  = price_data["high"]
        low   = price_data["low"]
        open_ = price_data["open"]

        # ── INDICATORS ──
        ema20  = close.ewm(span=self.ema_fast,  adjust=False).mean()
        ema50  = close.ewm(span=self.ema_mid,   adjust=False).mean()
        ema200 = close.ewm(span=self.ema_slow,  adjust=False).mean()
        rsi    = self._calculate_rsi(close)
        adx    = self._calculate_adx(high, low, close)

        # Current values
        last_close  = close.iloc[-1]
        last_open   = open_.iloc[-1]
        last_ema20  = ema20.iloc[-1]
        last_ema50  = ema50.iloc[-1]
        last_ema200 = ema200.iloc[-1]
        last_rsi    = rsi.iloc[-1]
        last_adx    = adx.iloc[-1]

        pip = self._pip_size(symbol)

        # ── FILTER 1: ADX — must be trending ──
        if last_adx < self.adx_min_threshold:
            return self._empty_signal(f"🚫 Consolidation | ADX={last_adx:.1f} < {self.adx_min_threshold}")
        if last_adx > self.adx_max_threshold:
            return self._empty_signal(f"🚫 Blow-off move | ADX={last_adx:.1f} > {self.adx_max_threshold}")

        # ── FILTER 2: EMA Stack — all 3 must be aligned ──
        bullish_stack = last_ema20 > last_ema50 > last_ema200
        bearish_stack = last_ema20 < last_ema50 < last_ema200

        if not bullish_stack and not bearish_stack:
            return self._empty_signal(
                f"❌ EMA stack not aligned | "
                f"EMA20={last_ema20:.5f} EMA50={last_ema50:.5f} EMA200={last_ema200:.5f}"
            )

        trend = "buy" if bullish_stack else "sell"

        # ── FILTER 3: Pullback to EMA50 ──
        # Price must be within pullback_tolerance_pips of EMA50
        distance_to_ema50 = abs(last_close - last_ema50) / pip
        tolerance = self.pullback_tolerance_pips

        if distance_to_ema50 > tolerance:
            return self._empty_signal(
                f"⏳ Waiting for pullback | "
                f"Distance to EMA50={distance_to_ema50:.1f} pips > {tolerance} pips"
            )

        # Price must still be on the correct side of EMA200 (not broken through)
        if trend == "buy" and last_close < last_ema200:
            return self._empty_signal("❌ BUY pullback broke below EMA200")
        if trend == "sell" and last_close > last_ema200:
            return self._empty_signal("❌ SELL pullback broke above EMA200")

        # ── FILTER 4: RSI in pullback zone ──
        if trend == "buy":
            if not (self.rsi_buy_zone_low <= last_rsi <= self.rsi_buy_zone_high):
                return self._empty_signal(
                    f"❌ RSI not in BUY pullback zone | RSI={last_rsi:.1f} "
                    f"(need {self.rsi_buy_zone_low}-{self.rsi_buy_zone_high})"
                )
        else:
            if not (self.rsi_sell_zone_low <= last_rsi <= self.rsi_sell_zone_high):
                return self._empty_signal(
                    f"❌ RSI not in SELL pullback zone | RSI={last_rsi:.1f} "
                    f"(need {self.rsi_sell_zone_low}-{self.rsi_sell_zone_high})"
                )

        # ── FILTER 5: Momentum candle ──
        # Current candle must confirm resumption of trend
        if trend == "buy" and last_close <= last_open:
            return self._empty_signal(
                f"❌ BUY momentum candle fail | close={last_close:.5f} ≤ open={last_open:.5f}"
            )
        if trend == "sell" and last_close >= last_open:
            return self._empty_signal(
                f"❌ SELL momentum candle fail | close={last_close:.5f} ≥ open={last_open:.5f}"
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
        sl, tp = self._sl_tp(entry_price, trend, last_ema50, symbol)

        # ── LOT SIZE ──
        sl_pips = abs(entry_price - sl) / pip
        lot = self._calculate_lot_size(symbol, sl_pips)

        # ── FINAL SIGNAL ──
        return {
            "signal": trend,
            "entry_price": entry_price,
            "stop_loss": sl,
            "take_profit": tp,
            "entry_date": entry_time,
            "lot_size": lot,
            "adx": last_adx,
            "rsi": last_rsi,
            "ema50": last_ema50,
            "sl_pips": round(sl_pips, 1),
            "distance_to_ema50": round(distance_to_ema50, 1),
            "reason": (
                f"✅ {trend.upper()} PULLBACK | "
                f"ADX={last_adx:.1f} | "
                f"RSI={last_rsi:.1f} | "
                f"EMA50={last_ema50:.5f} | "
                f"SL={round(sl_pips, 1)} pips | "
                f"Lot={lot}"
            ),
        }

    # ─────────────────────────────────────────────
    # EMPTY SIGNAL  (mirrors RSIFlexibleStrategy)
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
    # PARAMETERS / REPR  (mirrors RSIFlexibleStrategy)
    # ─────────────────────────────────────────────

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "name": "EMA Pullback Trend Rider",
            "ema_fast": self.ema_fast,
            "ema_mid": self.ema_mid,
            "ema_slow": self.ema_slow,
            "adx_period": self.adx_period,
            "adx_min": self.adx_min_threshold,
            "adx_max": self.adx_max_threshold,
            "rsi_period": self.rsi_period,
            "rsi_buy_zone": f"{self.rsi_buy_zone_low}-{self.rsi_buy_zone_high}",
            "rsi_sell_zone": f"{self.rsi_sell_zone_low}-{self.rsi_sell_zone_high}",
            "sl_buffer_pips": self.sl_buffer_pips,
            "pullback_tolerance_pips": self.pullback_tolerance_pips,
            "risk_percent": self.risk_percent,
            "rr_ratio": self.rr_ratio,
        }

    def __repr__(self) -> str:
        return (
            f"EMAPullbackStrategy("
            f"EMA{self.ema_fast}/{self.ema_mid}/{self.ema_slow}, "
            f"ADX>{self.adx_min_threshold}, "
            f"RR=1:{self.rr_ratio}, "
            f"Risk={self.risk_percent}%)"
        )