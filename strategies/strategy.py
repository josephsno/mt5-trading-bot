import pandas as pd
from typing import Dict, Any, Optional
import MetaTrader5 as mt5
import random, time, datetime, pytz


class MACDTrendStrategy:
    """
    MACD + EMA Trend-Following Strategy

    Rules:
    - Buy: Price > EMA and MACD crosses above signal
    - Sell: Price < EMA and MACD crosses below signal

    Risk:
    - SL at EMA
    - TP = risk * RR
    """

    def __init__(
        self,
        ema_period: int = 200,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        risk_reward_ratio: float = 3.0,
        allowed_weekdays: Optional[list[int]] = None,
    ):
        self.ema_period = ema_period
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.risk_reward_ratio = risk_reward_ratio

        self.allowed_weekdays = allowed_weekdays or list(range(7))
        self.blocked_dates: list[str] = []

        self.min_bars = max(ema_period, macd_slow + macd_signal)

    # ---------------- INDICATORS ---------------- #

    @staticmethod
    def _ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    def _macd(self, series: pd.Series) -> tuple[pd.Series, pd.Series]:
        fast = self._ema(series, self.macd_fast)
        slow = self._ema(series, self.macd_slow)
        macd = fast - slow
        signal = macd.ewm(span=self.macd_signal, adjust=False).mean()
        return macd, signal

    # ---------------- VALIDATION ---------------- #

    def _validate_data(self, df: pd.DataFrame) -> Optional[str]:
        if df is None or df.empty:
            return "Price data is empty"
        if "close" not in df.columns or "time" not in df.columns:
            return "Missing required columns"
        if len(df) < self.min_bars:
            return f"Insufficient bars ({len(df)} < {self.min_bars})"
        return None

    def _empty_signal(self, reason: str) -> Dict[str, Any]:
        return {
            "signal": None,
            "entry_price": None,
            "stop_loss": None,
            "take_profit": None,
            "trend": None,
            "reason": reason,
            "entry_date": None,
        }

    # ---------------- HELPERS ---------------- #

    @staticmethod
    def _macd_cross(mp, mn, sp, sn) -> Optional[str]:
        if mp < sp and mn > sn:
            return "bullish"
        if mp > sp and mn < sn:
            return "bearish"
        return None

    def _sl_tp(self, entry: float, ema: float, side: str) -> tuple[float, float]:
        risk = abs(entry - ema)
        reward = risk * self.risk_reward_ratio

        if side == "buy":
            return ema, entry + reward
        else:
            return ema, entry - reward

    def _is_allowed_date(self, timestamp) -> bool:
        dt = pd.to_datetime(timestamp)
        if dt.weekday() not in self.allowed_weekdays:
            return False
        if dt.strftime("%Y-%m-%d") in self.blocked_dates:
            return False
        return True

    # ---------------- MAIN LOGIC ---------------- #

    def generate_signal(self, price_data: pd.DataFrame) -> Dict[str, Any]:
        error = self._validate_data(price_data)
        if error:
            return self._empty_signal(error)

        entry_time = price_data["time"].iloc[-1]
        if not self._is_allowed_date(entry_time):
            return self._empty_signal("Trading not allowed on this date")

        try:
            closes = price_data["close"]
            price = closes.iloc[-1]

            ema = self._ema(closes, self.ema_period).iloc[-1]
            macd, signal = self._macd(closes)

            mp, mn = macd.iloc[-2], macd.iloc[-1]
            sp, sn = signal.iloc[-2], signal.iloc[-1]

            trend = "bullish" if price > ema else "bearish" if price < ema else None
            cross = self._macd_cross(mp, mn, sp, sn)

            result = {
                "signal": None,
                "entry_price": price,
                "stop_loss": None,
                "take_profit": None,
                "trend": trend,
                "macd_prev": float(mp),
                "macd_now": float(mn),
                "macd_signal_prev": float(sp),
                "macd_signal_now": float(sn),
                "ema": float(ema),
                "reason": None,
                "entry_date": entry_time,
            }

            if trend == "bullish" and cross == "bullish":
                sl, tp = self._sl_tp(price, ema, "buy")
                result.update(
                    {
                        "signal": "buy",
                        "stop_loss": sl,
                        "take_profit": tp,
                        "reason": "MACD bullish cross in uptrend",
                    }
                )

            elif trend == "bearish" and cross == "bearish":
                sl, tp = self._sl_tp(price, ema, "sell")
                result.update(
                    {
                        "signal": "sell",
                        "stop_loss": sl,
                        "take_profit": tp,
                        "reason": "MACD bearish cross in downtrend",
                    }
                )

            else:
                result["reason"] = "No valid MACD + trend alignment"

            return result

        except Exception as e:
            return self._empty_signal(f"Signal error: {e}")

    # ---------------- META ---------------- #

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "name": "MACD Trend Strategy",
            "ema_period": self.ema_period,
            "macd_fast": self.macd_fast,
            "macd_slow": self.macd_slow,
            "macd_signal": self.macd_signal,
            "risk_reward_ratio": self.risk_reward_ratio,
            "min_bars": self.min_bars,
        }

    def __repr__(self) -> str:
        return (
            f"MACDTrendStrategy("
            f"EMA={self.ema_period}, "
            f"MACD={self.macd_fast}/{self.macd_slow}/{self.macd_signal}, "
            f"RR={self.risk_reward_ratio})"
        )


class RestrictiveVolumeStrategy:
    """
    Highly Restrictive MACD + EMA + Volume Strategy

    Rules:
    - Buy: Price > EMA AND MACD crosses above signal AND volume spike AND favorite time/day
    - Sell: Price < EMA AND MACD crosses below signal AND volume spike AND favorite time/day

    Risk:
    - Flexible SL: ATR-based or swing low/high
    - Flexible TP: Multiple methods (fixed RR, ATR-based, or trailing)
    """

    def __init__(
        self,
        # Trend
        ema_period: int = 200,
        # MACD
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        # Volume
        volume_ma_period: int = 20,
        volume_spike_multiplier: float = 2.0,  # Volume must be 1.5x average
        # Time filters
        favorite_hours: Optional[list[int]] = None,  # e.g., [8, 9, 10] for 8am-10am
        favorite_weekdays: Optional[list[int]] = None,  # 0=Mon, 4=Fri
        # Volatility
        atr_period: int = 14,
        # Risk management
        sl_method: str = "atr",  # "atr", "ema", "swing"
        sl_atr_multiplier: float = 2.0,
        tp_method: str = "atr",  # "fixed_rr", "atr", "swing"
        tp_atr_multiplier: float = 3.0,
        fixed_risk_reward: float = 3.0,
        # Additional filters
        min_distance_from_ema_pct: float = 0.1,  # Price must be >0.1% from EMA
        require_macd_histogram_momentum: bool = True,  # MACD must be accelerating
    ):
        self.ema_period = ema_period
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal

        self.volume_ma_period = volume_ma_period
        self.volume_spike_multiplier = volume_spike_multiplier

        self.favorite_hours = favorite_hours or list(range(24))
        self.favorite_weekdays = favorite_weekdays or list(range(7))

        self.atr_period = atr_period

        self.sl_method = sl_method
        self.sl_atr_multiplier = sl_atr_multiplier
        self.tp_method = tp_method
        self.tp_atr_multiplier = tp_atr_multiplier
        self.fixed_risk_reward = fixed_risk_reward

        self.min_distance_from_ema_pct = min_distance_from_ema_pct
        self.require_macd_histogram_momentum = require_macd_histogram_momentum

        self.blocked_dates: list[str] = []
        self.min_bars = (
            max(ema_period, macd_slow + macd_signal, volume_ma_period, atr_period) + 5
        )

    # ---------------- INDICATORS ---------------- #

    @staticmethod
    def _ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    def _macd(self, series: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
        fast = self._ema(series, self.macd_fast)
        slow = self._ema(series, self.macd_slow)
        macd = fast - slow
        signal = macd.ewm(span=self.macd_signal, adjust=False).mean()
        histogram = macd - signal
        return macd, signal, histogram

    def _atr(self, df: pd.DataFrame) -> pd.Series:
        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(window=self.atr_period).mean()

    def _volume_ma(self, volume: pd.Series) -> pd.Series:
        return volume.rolling(window=self.volume_ma_period).mean()

    def _find_swing_low(self, df: pd.DataFrame, lookback: int = 10) -> float:
        """Find recent swing low"""
        return df["low"].iloc[-lookback:].min()

    def _find_swing_high(self, df: pd.DataFrame, lookback: int = 10) -> float:
        """Find recent swing high"""
        return df["high"].iloc[-lookback:].max()

    # ---------------- VALIDATION ---------------- #

    def _validate_data(self, df: pd.DataFrame) -> Optional[str]:
        required_cols = ["close", "high", "low", "time", "tick_volume"]

        if df is None or df.empty:
            return "Price data is empty"

        missing = [col for col in required_cols if col not in df.columns]
        if missing:
            return f"Missing columns: {missing}"

        if len(df) < self.min_bars:
            return f"Insufficient bars ({len(df)} < {self.min_bars})"

        return None

    def _empty_signal(self, reason: str) -> Dict[str, Any]:
        return {
            "signal": None,
            "entry_price": None,
            "stop_loss": None,
            "take_profit": None,
            "trend": None,
            "reason": reason,
            "entry_date": None,
            "filters_passed": {},
        }

    # ---------------- FILTERS ---------------- #

    def _is_allowed_datetime(self, timestamp) -> tuple[bool, str]:
        dt = pd.to_datetime(timestamp)

        if dt.weekday() not in self.favorite_weekdays:
            return False, f"Weekday {dt.strftime('%A')} not in favorites"

        if dt.hour not in self.favorite_hours:
            return False, f"Hour {dt.hour} not in favorites"

        if dt.strftime("%Y-%m-%d") in self.blocked_dates:
            return False, "Date is blocked"

        return True, "Time filter passed"

    def _check_volume_spike(
        self, volume: pd.Series, volume_ma: pd.Series
    ) -> tuple[bool, str]:
        current_vol = volume.iloc[-1]
        avg_vol = volume_ma.iloc[-1]

        if pd.isna(avg_vol) or avg_vol == 0:
            return False, "Volume MA not available"

        ratio = current_vol / avg_vol

        if ratio >= self.volume_spike_multiplier:
            return True, f"Volume spike: {ratio:.2f}x average"

        return False, f"Volume too low: {ratio:.2f}x average"

    def _check_ema_distance(self, price: float, ema: float) -> tuple[bool, str]:
        distance_pct = abs(price - ema) / ema * 100

        if distance_pct >= self.min_distance_from_ema_pct:
            return True, f"Price {distance_pct:.2f}% from EMA"

        return False, f"Price too close to EMA: {distance_pct:.2f}%"

    def _check_macd_momentum(self, histogram: pd.Series) -> tuple[bool, str]:
        if not self.require_macd_histogram_momentum:
            return True, "Momentum check disabled"

        h_prev = histogram.iloc[-2]
        h_now = histogram.iloc[-1]

        # For bullish: histogram should be increasing (getting more positive)
        # For bearish: histogram should be decreasing (getting more negative)

        if h_now > h_prev and h_now > 0:
            return True, "Bullish momentum confirmed"

        if h_now < h_prev and h_now < 0:
            return True, "Bearish momentum confirmed"

        return False, "MACD momentum not confirmed"

    @staticmethod
    def _macd_cross(mp, mn, sp, sn) -> Optional[str]:
        if mp < sp and mn > sn:
            return "bullish"
        if mp > sp and mn < sn:
            return "bearish"
        return None

    # ---------------- STOP LOSS / TAKE PROFIT ---------------- #

    def _calculate_stop_loss(
        self, df: pd.DataFrame, entry: float, atr: float, side: str
    ) -> float:
        if self.sl_method == "atr":
            if side == "buy":
                return entry - (atr * self.sl_atr_multiplier)
            else:
                return entry + (atr * self.sl_atr_multiplier)

        elif self.sl_method == "ema":
            ema = self._ema(df["close"], self.ema_period).iloc[-1]
            return ema

        elif self.sl_method == "swing":
            if side == "buy":
                return self._find_swing_low(df)
            else:
                return self._find_swing_high(df)

        else:
            raise ValueError(f"Unknown SL method: {self.sl_method}")

    def _calculate_take_profit(
        self, df: pd.DataFrame, entry: float, stop_loss: float, atr: float, side: str
    ) -> float:
        if self.tp_method == "fixed_rr":
            risk = abs(entry - stop_loss)
            reward = risk * self.fixed_risk_reward

            if side == "buy":
                return entry + reward
            else:
                return entry - reward

        elif self.tp_method == "atr":
            if side == "buy":
                return entry + (atr * self.tp_atr_multiplier)
            else:
                return entry - (atr * self.tp_atr_multiplier)

        elif self.tp_method == "swing":
            # Take profit at previous swing high/low
            if side == "buy":
                return self._find_swing_high(df)
            else:
                return self._find_swing_low(df)

        else:
            raise ValueError(f"Unknown TP method: {self.tp_method}")

    # ---------------- MAIN LOGIC ---------------- #
    def generate_signal(self, price_data: pd.DataFrame) -> Dict[str, Any]:
        error = self._validate_data(price_data)
        if error:
            return self._empty_signal(error)

        df = price_data.copy()
        entry_time = df["time"].iloc[-1]

        filters = {}

        # ---------------- TIME FILTER ----------------
        time_ok, time_msg = self._is_allowed_datetime(entry_time)
        filters["time"] = time_msg

        closes = df["close"]
        volumes = df["tick_volume"]

        price = float(closes.iloc[-1])

        # ---------------- INDICATORS ----------------
        ema = float(self._ema(closes, self.ema_period).iloc[-1])
        macd, signal, histogram = self._macd(closes)
        atr = float(self._atr(df).iloc[-1])
        volume_ma = self._volume_ma(volumes)

        mp, mn = macd.iloc[-2], macd.iloc[-1]
        sp, sn = signal.iloc[-2], signal.iloc[-1]

        # ---------------- TREND ----------------
        trend = "bullish" if price > ema else "bearish" if price < ema else None
        filters["trend"] = f"Trend: {trend}"

        # ---------------- MACD CROSS ----------------
        cross = self._macd_cross(mp, mn, sp, sn)
        filters["macd_cross"] = f"MACD cross: {cross}"

        # ---------------- VOLUME ----------------
        vol_ok, vol_msg = self._check_volume_spike(volumes, volume_ma)
        filters["volume"] = vol_msg

        # ---------------- EMA DISTANCE ----------------
        dist_ok, dist_msg = self._check_ema_distance(price, ema)
        filters["ema_distance"] = dist_msg

        # ---------------- MOMENTUM ----------------
        mom_ok, mom_msg = self._check_macd_momentum(histogram)
        filters["momentum"] = mom_msg

        # ---------------- BASE RESULT (ALWAYS RETURNED) ----------------
        result = {
            "signal": None,
            "entry_price": price,
            "stop_loss": None,
            "take_profit": None,
            "trend": trend,
            "ema": ema,
            "atr": atr,
            "macd_histogram": float(histogram.iloc[-1]),
            "volume_ratio": float(volumes.iloc[-1] / volume_ma.iloc[-1]),
            "entry_date": entry_time,
            "filters_passed": filters,
            "reason": None,
            "position_size": 0.05,
        }

        # ---------------- DECISION LOGIC ----------------
        if not time_ok:
            result["reason"] = "Time filter blocked"
            return result

        if not trend:
            result["reason"] = "No clear trend"
            return result

        if not cross:
            result["reason"] = "No MACD cross"
            return result

        if not vol_ok:
            result["reason"] = "Volume filter failed"
            return result

        if not dist_ok:
            result["reason"] = "EMA distance filter failed"
            return result

        if not mom_ok:
            result["reason"] = "Momentum filter failed"
            return result

        # ---------------- SIGNAL GENERATION ----------------
        if trend == "bullish" and cross == "bullish":
            sl = self._calculate_stop_loss(df, price, atr, "buy")
            tp = self._calculate_take_profit(df, price, sl, atr, "buy")

            result.update(
                {
                    "signal": "buy",
                    "stop_loss": sl,
                    "take_profit": tp,
                    "risk_pips": abs(price - sl),
                    "reward_pips": abs(tp - price),
                    "risk_reward": abs(tp - price) / abs(price - sl),
                    "reason": "All bullish filters passed",
                }
            )

        elif trend == "bearish" and cross == "bearish":
            sl = self._calculate_stop_loss(df, price, atr, "sell")
            tp = self._calculate_take_profit(df, price, sl, atr, "sell")

            result.update(
                {
                    "signal": "sell",
                    "stop_loss": sl,
                    "take_profit": tp,
                    "risk_pips": abs(price - sl),
                    "reward_pips": abs(price - tp),
                    "risk_reward": abs(price - tp) / abs(price - sl),
                    "reason": "All bearish filters passed",
                }
            )

        else:
            result["reason"] = "Trend and MACD direction mismatch"

        return result

    # ---------------- META ---------------- #

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "name": "Restrictive Volume Strategy",
            "ema_period": self.ema_period,
            "macd": f"{self.macd_fast}/{self.macd_slow}/{self.macd_signal}",
            "volume_spike_multiplier": self.volume_spike_multiplier,
            "favorite_hours": self.favorite_hours,
            "favorite_weekdays": [
                ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d]
                for d in self.favorite_weekdays
            ],
            "atr_period": self.atr_period,
            "sl_method": self.sl_method,
            "tp_method": self.tp_method,
            "min_distance_from_ema_pct": self.min_distance_from_ema_pct,
            "require_macd_histogram_momentum": self.require_macd_histogram_momentum,
        }

    def __repr__(self) -> str:
        return (
            f"RestrictiveVolumeStrategy("
            f"EMA={self.ema_period}, "
            f"Vol={self.volume_spike_multiplier}x, "
            f"SL={self.sl_method}, "
            f"TP={self.tp_method})"
        )


class MixedIndicatorsStrategy:
    """
    Mixed Indicators Strategy: EMA + RSI + Bollinger Bands

    Rules:
    - Buy: Price > EMA AND RSI < oversold AND price touches lower Bollinger Band AND favorite time/day
    - Sell: Price < EMA AND RSI > overbought AND price touches upper Bollinger Band AND favorite time/day

    Risk:
    - Flexible SL: ATR-based, EMA-based, or swing
    - Flexible TP: Fixed RR, ATR-based, or swing
    """

    def __init__(
        self,
        ema_period: int = 50,
        rsi_period: int = 14,
        rsi_overbought: float = 70,
        rsi_oversold: float = 30,
        bb_period: int = 20,
        bb_stddev: float = 2.0,
        atr_period: int = 14,
        sl_method: str = "atr",
        sl_atr_multiplier: float = 1.5,
        tp_method: str = "fixed_rr",
        tp_atr_multiplier: float = 2.0,
        fixed_risk_reward: float = 2.0,
        min_distance_from_ema_pct: float = 0.1,
        favorite_hours: Optional[list[int]] = None,  # e.g., [8, 9, 10] for 8am-10am
        favorite_weekdays: Optional[list[int]] = None,  # 0=Mon, 4=Fri
    ):
        self.ema_period = ema_period
        self.rsi_period = rsi_period
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold
        self.bb_period = bb_period
        self.bb_stddev = bb_stddev
        self.atr_period = atr_period

        self.sl_method = sl_method
        self.sl_atr_multiplier = sl_atr_multiplier
        self.tp_method = tp_method
        self.tp_atr_multiplier = tp_atr_multiplier
        self.fixed_risk_reward = fixed_risk_reward
        self.min_distance_from_ema_pct = min_distance_from_ema_pct

        self.favorite_hours = favorite_hours or list(range(24))
        self.favorite_weekdays = favorite_weekdays or list(range(7))

        self.min_bars = max(ema_period, rsi_period, bb_period, atr_period) + 5
        self.blocked_dates: list[str] = []

    # ---------------- INDICATORS ---------------- #

    @staticmethod
    def _ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def _rsi(series: pd.Series, period: int) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(period).mean()
        avg_loss = loss.rolling(period).mean()
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def _bollinger_bands(
        series: pd.Series, period: int, stddev: float
    ) -> tuple[pd.Series, pd.Series]:
        sma = series.rolling(period).mean()
        upper = sma + stddev * series.rolling(period).std()
        lower = sma - stddev * series.rolling(period).std()
        return upper, lower

    def _atr(self, df: pd.DataFrame) -> pd.Series:
        high, low, close = df["high"], df["low"], df["close"]
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(window=self.atr_period).mean()

    def _find_swing_low(self, df: pd.DataFrame, lookback: int = 10) -> float:
        return df["low"].iloc[-lookback:].min()

    def _find_swing_high(self, df: pd.DataFrame, lookback: int = 10) -> float:
        return df["high"].iloc[-lookback:].max()

    # ---------------- VALIDATION ---------------- #

    def _validate_data(self, df: pd.DataFrame) -> Optional[str]:
        required_cols = ["close", "high", "low", "time"]
        if df is None or df.empty:
            return "Price data is empty"
        missing = [col for col in required_cols if col not in df.columns]
        if missing:
            return f"Missing columns: {missing}"
        if len(df) < self.min_bars:
            return f"Insufficient bars ({len(df)} < {self.min_bars})"
        return None

    def _empty_signal(self, reason: str) -> Dict[str, Any]:
        return {
            "signal": None,
            "entry_price": None,
            "stop_loss": None,
            "take_profit": None,
            "trend": None,
            "reason": reason,
            "entry_date": None,
        }

    # ---------------- TIME FILTER ---------------- #

    def _is_allowed_datetime(self, timestamp) -> tuple[bool, str]:
        dt = pd.to_datetime(timestamp)

        if dt.weekday() not in self.favorite_weekdays:
            return False, f"Weekday {dt.strftime('%A')} not in favorites"
        if dt.hour not in self.favorite_hours:
            return False, f"Hour {dt.hour} not in favorites"
        if dt.strftime("%Y-%m-%d") in self.blocked_dates:
            return False, "Date is blocked"
        return True, "Time filter passed"

    # ---------------- STOP LOSS / TAKE PROFIT ---------------- #

    def _calculate_stop_loss(
        self, df: pd.DataFrame, entry: float, atr: float, side: str
    ) -> float:
        if self.sl_method == "atr":
            return (
                entry - atr * self.sl_atr_multiplier
                if side == "buy"
                else entry + atr * self.sl_atr_multiplier
            )
        elif self.sl_method == "ema":
            return self._ema(df["close"], self.ema_period).iloc[-1]
        elif self.sl_method == "swing":
            return (
                self._find_swing_low(df) if side == "buy" else self._find_swing_high(df)
            )
        else:
            raise ValueError(f"Unknown SL method: {self.sl_method}")

    def _calculate_take_profit(
        self, df: pd.DataFrame, entry: float, stop_loss: float, atr: float, side: str
    ) -> float:
        if self.tp_method == "fixed_rr":
            risk = abs(entry - stop_loss)
            reward = risk * self.fixed_risk_reward
            return entry + reward if side == "buy" else entry - reward
        elif self.tp_method == "atr":
            return (
                entry + atr * self.tp_atr_multiplier
                if side == "buy"
                else entry - atr * self.tp_atr_multiplier
            )
        elif self.tp_method == "swing":
            return (
                self._find_swing_high(df) if side == "buy" else self._find_swing_low(df)
            )
        else:
            raise ValueError(f"Unknown TP method: {self.tp_method}")

    # ---------------- MAIN LOGIC ---------------- #

    def generate_signal(self, price_data: pd.DataFrame) -> Dict[str, Any]:
        error = self._validate_data(price_data)
        if error:
            return self._empty_signal(error)

        df = price_data.copy()
        closes = df["close"]
        entry_time = df["time"].iloc[-1]
        price = closes.iloc[-1]

        # ---------------- TIME FILTER ----------------
        time_ok, time_msg = self._is_allowed_datetime(entry_time)
        if not time_ok:
            return self._empty_signal(time_msg)

        ema = float(self._ema(closes, self.ema_period).iloc[-1])
        rsi = float(self._rsi(closes, self.rsi_period).iloc[-1])
        upper_bb, lower_bb = self._bollinger_bands(
            closes, self.bb_period, self.bb_stddev
        )
        atr = float(self._atr(df).iloc[-1])

        # Trend
        trend = "bullish" if price > ema else "bearish"

        # Signal conditions
        signal = None
        if (
            trend == "bullish"
            and rsi < self.rsi_oversold
            and price <= lower_bb.iloc[-1]
        ):
            signal = "buy"
        elif (
            trend == "bearish"
            and rsi > self.rsi_overbought
            and price >= upper_bb.iloc[-1]
        ):
            signal = "sell"

        if not signal:
            return self._empty_signal("No signal conditions met")

        sl = self._calculate_stop_loss(df, price, atr, signal)
        tp = self._calculate_take_profit(df, price, sl, atr, signal)

        return {
            "signal": signal,
            "entry_price": price,
            "stop_loss": sl,
            "take_profit": tp,
            "trend": trend,
            "ema": ema,
            "rsi": rsi,
            "upper_bb": upper_bb.iloc[-1],
            "lower_bb": lower_bb.iloc[-1],
            "atr": atr,
            "entry_date": entry_time,
        }

    # ---------------- META ---------------- #

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "name": "Mixed Indicators Strategy",
            "ema_period": self.ema_period,
            "rsi_period": self.rsi_period,
            "rsi_overbought": self.rsi_overbought,
            "rsi_oversold": self.rsi_oversold,
            "bb_period": self.bb_period,
            "bb_stddev": self.bb_stddev,
            "atr_period": self.atr_period,
            "sl_method": self.sl_method,
            "sl_atr_multiplier": self.sl_atr_multiplier,
            "tp_method": self.tp_method,
            "tp_atr_multiplier": self.tp_atr_multiplier,
            "fixed_risk_reward": self.fixed_risk_reward,
            "min_distance_from_ema_pct": self.min_distance_from_ema_pct,
            "favorite_hours": self.favorite_hours,
            "favorite_weekdays": self.favorite_weekdays,
        }

    def __repr__(self) -> str:
        return (
            f"MixedIndicatorsStrategy("
            f"EMA={self.ema_period}, RSI={self.rsi_period}, BB={self.bb_period}, "
            f"SL={self.sl_method}, TP={self.tp_method})"
        )


class BalancedMixedStrategy:
    """
    Balanced EMA + MACD + RSI + Volume Strategy

    Rules:
    - Buy: Price > EMA AND MACD bullish AND RSI not overbought AND mild volume spike AND favorite time/day
    - Sell: Price < EMA AND MACD bearish AND RSI not oversold AND mild volume spike AND favorite time/day

    Risk:
    - ATR-based SL
    - TP based on fixed risk-reward
    """

    def __init__(
        self,
        # Trend
        ema_period: int = 100,
        min_distance_from_ema_pct: float = 0.05,
        # MACD
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        require_macd_momentum: bool = True,
        # RSI
        rsi_period: int = 14,
        rsi_overbought: float = 70,
        rsi_oversold: float = 30,
        require_rsi_confirmation: bool = True,
        # Volume
        volume_ma_period: int = 20,
        volume_spike_multiplier: float = 1.2,
        # Time filters
        favorite_hours: Optional[list[int]] = None,
        favorite_weekdays: Optional[list[int]] = None,
        # ATR & risk
        atr_period: int = 14,
        sl_method: str = "atr",
        sl_atr_multiplier: float = 2.0,
        tp_method: str = "fixed_rr",
        fixed_risk_reward: float = 3.0,
    ):
        # Trend
        self.ema_period = ema_period
        self.min_distance_from_ema_pct = min_distance_from_ema_pct

        # MACD
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.require_macd_momentum = require_macd_momentum

        # RSI
        self.rsi_period = rsi_period
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold
        self.require_rsi_confirmation = require_rsi_confirmation

        # Volume
        self.volume_ma_period = volume_ma_period
        self.volume_spike_multiplier = volume_spike_multiplier

        # Time filters
        self.favorite_hours = favorite_hours or list(range(24))
        self.favorite_weekdays = favorite_weekdays or list(range(7))

        # Risk
        self.atr_period = atr_period
        self.sl_method = sl_method
        self.sl_atr_multiplier = sl_atr_multiplier
        self.tp_method = tp_method
        self.fixed_risk_reward = fixed_risk_reward

        self.min_bars = (
            max(
                ema_period,
                macd_slow + macd_signal,
                volume_ma_period,
                atr_period,
                rsi_period,
            )
            + 5
        )

        self.blocked_dates: list[str] = []

    # ---------------- INDICATORS ---------------- #

    @staticmethod
    def _ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    def _macd(self, series: pd.Series):
        fast = self._ema(series, self.macd_fast)
        slow = self._ema(series, self.macd_slow)
        macd = fast - slow
        signal = macd.ewm(span=self.macd_signal, adjust=False).mean()
        histogram = macd - signal
        return macd, signal, histogram

    def _rsi(self, series: pd.Series):
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(self.rsi_period).mean()
        avg_loss = loss.rolling(self.rsi_period).mean()
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _atr(self, df: pd.DataFrame) -> pd.Series:
        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(window=self.atr_period).mean()

    def _volume_ma(self, volume: pd.Series) -> pd.Series:
        return volume.rolling(window=self.volume_ma_period).mean()

    # ---------------- FILTERS ---------------- #

    def _is_allowed_datetime(self, timestamp) -> tuple[bool, str]:
        dt = pd.to_datetime(timestamp)

        if dt.weekday() not in self.favorite_weekdays:
            return False, f"Weekday {dt.strftime('%A')} not allowed"
        if dt.hour not in self.favorite_hours:
            return False, f"Hour {dt.hour} not allowed"
        if dt.strftime("%Y-%m-%d") in self.blocked_dates:
            return False, "Date blocked"
        return True, "Time filter passed"

    def _check_volume_spike(
        self, volume: pd.Series, volume_ma: pd.Series
    ) -> tuple[bool, str]:
        ratio = volume.iloc[-1] / volume_ma.iloc[-1] if volume_ma.iloc[-1] else 0
        if ratio >= self.volume_spike_multiplier:
            return True, f"Volume spike {ratio:.2f}x"
        return False, f"Volume too low {ratio:.2f}x"

    def _check_ema_distance(self, price: float, ema: float) -> tuple[bool, str]:
        dist_pct = abs(price - ema) / ema * 100
        if dist_pct >= self.min_distance_from_ema_pct:
            return True, f"Price {dist_pct:.2f}% from EMA"
        return False, f"Price too close to EMA: {dist_pct:.2f}%"

    def _check_macd_momentum(self, histogram: pd.Series) -> tuple[bool, str]:
        if not self.require_macd_momentum:
            return True, "Momentum check disabled"
        return (
            (histogram.iloc[-1] > histogram.iloc[-2], "Momentum increasing")
            if histogram.iloc[-1] > 0
            else (histogram.iloc[-1] < histogram.iloc[-2], "Momentum decreasing")
        )

    def _check_rsi(self, rsi: pd.Series, side: str) -> tuple[bool, str]:
        if not self.require_rsi_confirmation:
            return True, "RSI check disabled"
        val = rsi.iloc[-1]
        if side == "buy" and val < self.rsi_overbought:
            return True, f"RSI ok {val:.2f}"
        if side == "sell" and val > self.rsi_oversold:
            return True, f"RSI ok {val:.2f}"
        return False, f"RSI extreme {val:.2f}"

    @staticmethod
    def _macd_cross(mp, mn, sp, sn) -> Optional[str]:
        if mp < sp and mn > sn:
            return "bullish"
        if mp > sp and mn < sn:
            return "bearish"
        return None

    # ---------------- SL/TP ---------------- #

    def _calculate_stop_loss(self, entry: float, atr: float, side: str) -> float:
        return (
            entry - atr * self.sl_atr_multiplier
            if side == "buy"
            else entry + atr * self.sl_atr_multiplier
        )

    def _calculate_take_profit(
        self, entry: float, stop_loss: float, side: str
    ) -> float:
        risk = abs(entry - stop_loss)
        return (
            entry + risk * self.fixed_risk_reward
            if side == "buy"
            else entry - risk * self.fixed_risk_reward
        )

    # ---------------- SIGNAL GENERATION ---------------- #

    def generate_signal(self, price_data: pd.DataFrame) -> Dict[str, Any]:
        if price_data is None or len(price_data) < self.min_bars:
            return {"signal": None, "reason": "Insufficient data"}

        price = price_data["close"].iloc[-1]
        entry_time = price_data["time"].iloc[-1]
        ema = self._ema(price_data["close"], self.ema_period).iloc[-1]
        macd, signal, hist = self._macd(price_data["close"])
        rsi = self._rsi(price_data["close"])
        atr = self._atr(price_data).iloc[-1]
        volume_ma = self._volume_ma(price_data["tick_volume"])

        filters = {}
        time_ok, filters["time"] = self._is_allowed_datetime(entry_time)
        vol_ok, filters["volume"] = self._check_volume_spike(
            price_data["tick_volume"], volume_ma
        )
        dist_ok, filters["ema_distance"] = self._check_ema_distance(price, ema)

        mp, mn = macd.iloc[-2], macd.iloc[-1]
        sp, sn = signal.iloc[-2], signal.iloc[-1]
        cross = self._macd_cross(mp, mn, sp, sn)

        mom_ok, filters["momentum"] = self._check_macd_momentum(hist)

        if not time_ok or not vol_ok or not dist_ok or not mom_ok or not cross:
            return {
                "signal": None,
                "filters_passed": filters,
                "reason": "Filter failed",
            }

        side = "buy" if price > ema and cross == "bullish" else "sell"
        rsi_ok, filters["rsi"] = self._check_rsi(rsi, side)
        if not rsi_ok:
            return {
                "signal": None,
                "filters_passed": filters,
                "reason": "RSI filter failed",
            }

        sl = self._calculate_stop_loss(price, atr, side)
        tp = self._calculate_take_profit(price, sl, side)

        return {
            "signal": side,
            "entry_price": price,
            "stop_loss": sl,
            "take_profit": tp,
            "filters_passed": filters,
            "atr": atr,
            "ema": ema,
            "macd_histogram": hist.iloc[-1],
            "rsi": rsi.iloc[-1],
            "entry_date": entry_time,
            "reason": "All filters passed",
        }

    # ---------------- PARAMETERS ---------------- #

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "name": "BalancedMixedStrategy",
            "ema_period": self.ema_period,
            "macd": f"{self.macd_fast}/{self.macd_slow}/{self.macd_signal}",
            "rsi_period": self.rsi_period,
            "volume_spike_multiplier": self.volume_spike_multiplier,
            "favorite_hours": self.favorite_hours,
            "favorite_weekdays": self.favorite_weekdays,
            "atr_period": self.atr_period,
            "sl_method": self.sl_method,
            "tp_method": self.tp_method,
            "fixed_risk_reward": self.fixed_risk_reward,
        }

    def __repr__(self) -> str:
        return f"BalancedMixedStrategy(EMA={self.ema_period}, Vol={self.volume_spike_multiplier}x, SL={self.sl_method}, TP={self.tp_method})"


class MACDRandomnessStrategy:
    """
    MACD + EMA Trend-Following Strategy with static SL/TP in pips and dynamic lot sizing.

    Rules:
    - Buy: Price > EMA and MACD crosses above signal
    - Sell: Price < EMA and MACD crosses below signal

    Risk:
    - SL and TP are fixed in pips
    - TP defaults to 1:1 ratio with SL if not provided
    - Lot size adjusts based on previous trade (live) or simulated (backtesting)
    """

    def __init__(
        self,
        ema_period: int = 200,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        sl_pips: float = 50.0,  # SL in pips
        tp_pips: Optional[float] = None,  # TP in pips
        allowed_weekdays: Optional[list[int]] = None,
        allowed_hours: Optional[list[int]] = None,  # ✅ NEW
        starting_lot: float = 0.01,
        backtest_mode: bool = False,
        last_trade_won: bool = False,  # assume first trade is a win in backtest
    ):
        self.ema_period = ema_period
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.sl_pips = sl_pips
        self.tp_pips = tp_pips or sl_pips
        self.allowed_weekdays = allowed_weekdays or list(range(7))
        self.allowed_hours = allowed_hours or list(range(24))  # ✅ default = all hours
        self.blocked_dates: list[str] = []
        self.min_bars = max(ema_period, macd_slow + macd_signal)

        # Lot sizing
        self.starting_lot = starting_lot
        self.backtest_mode = backtest_mode
        self.last_trade_won = last_trade_won  # assume first trade is a win in backtest
        self.current_lot = starting_lot

    # ---------------- INDICATORS ---------------- #

    @staticmethod
    def _ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    def _macd(self, series: pd.Series):
        fast = self._ema(series, self.macd_fast)
        slow = self._ema(series, self.macd_slow)
        macd = fast - slow
        signal = macd.ewm(span=self.macd_signal, adjust=False).mean()
        return macd, signal

    # ---------------- VALIDATION ---------------- #

    def _validate_data(self, df: pd.DataFrame) -> Optional[str]:
        if df is None or df.empty:
            return "Price data is empty"
        if "close" not in df.columns or "time" not in df.columns:
            return "Missing required columns"
        if len(df) < self.min_bars:
            return f"Insufficient bars ({len(df)} < {self.min_bars})"
        return None

    def _empty_signal(self, reason: str) -> Dict[str, Any]:
        return {
            "signal": None,
            "entry_price": None,
            "stop_loss": None,
            "take_profit": None,
            "trend": None,
            "reason": reason,
            "entry_date": None,
            "lot_size": None,
        }

    # ---------------- HELPERS ---------------- #

    def update_trade_result(self, was_win: bool):
        """
        Update the lot size after a trade in backtesting.
        was_win = True if the trade was profitable, False if not.
        """
        self.last_trade_won = was_win

    @staticmethod
    def _macd_cross(mp, mn, sp, sn) -> Optional[str]:
        if mp < sp and mn > sn:
            return "bullish"
        if mp > sp and mn < sn:
            return "bearish"
        return None

    def _sl_tp(self, entry: float, side: str):
        pip_value = 0.0001
        if "JPY" in str(entry):
            pip_value = 0.01

        if side == "buy":
            sl_price = entry - self.sl_pips * pip_value
            tp_price = entry + self.tp_pips * pip_value
        else:
            sl_price = entry + self.sl_pips * pip_value
            tp_price = entry - self.tp_pips * pip_value

        return sl_price, tp_price

    def _is_allowed_date(self, timestamp) -> bool:
        dt = pd.to_datetime(timestamp)
        if dt.weekday() not in self.allowed_weekdays:
            return False
        if dt.strftime("%Y-%m-%d") in self.blocked_dates:
            return False
        return True

    def _is_allowed_hour(self, timestamp) -> bool:
        if timestamp is None:
            return False
        hour = pd.to_datetime(timestamp).hour
        return hour in self.allowed_hours

    # ---------------- LOT SIZING ---------------- #

    def _get_lot_size(self) -> float:
        """
        Determine lot size based on last trade in MT5 or backtest simulation.

        Returns:
            float: lot size
        """
        if self.backtest_mode:
            # Backtesting: simulate last trade result
            if self.last_trade_won:
                self.current_lot += 0.01
            else:
                self.current_lot = self.starting_lot
            return round(self.current_lot, 2)

        # Live trading mode
        lot = self.starting_lot

        # Ensure MT5 is initialized
        if not mt5.initialize():
            raise RuntimeError("MT5 initialization failed")

        # Get all historical deals
        from_time = 0  # beginning of time
        to_time = mt5.time_current()
        deals = mt5.history_deals_get(from_time, to_time)

        if deals and len(deals) > 0:
            last_trade = deals[-1]  # last trade regardless of symbol
            profit = last_trade.profit
            if profit > 0:
                lot += 0.01
            else:
                lot = self.starting_lot

        # Round lot to 2 decimal places to avoid floating-point issues
        return round(lot, 2)

    # ---------------- MAIN SIGNAL ---------------- #

    def generate_signal(self, price_data: pd.DataFrame) -> Dict[str, Any]:
        error = self._validate_data(price_data)
        if error:
            return self._empty_signal(error)

        entry_time = price_data["time"].iloc[-1]

        if not self._is_allowed_date(entry_time):
            return self._empty_signal("Weekday not allowed")

        if not self._is_allowed_hour(entry_time):
            return self._empty_signal("Hour not allowed")

        try:
            closes = price_data["close"]
            price = closes.iloc[-1]

            ema = self._ema(closes, self.ema_period).iloc[-1]
            macd, signal = self._macd(closes)

            mp, mn = macd.iloc[-2], macd.iloc[-1]
            sp, sn = signal.iloc[-2], signal.iloc[-1]

            trend = "bullish" if price > ema else "bearish" if price < ema else None
            cross = self._macd_cross(mp, mn, sp, sn)

            result = {
                "signal": None,
                "entry_price": price,
                "stop_loss": None,
                "take_profit": None,
                "trend": trend,
                "macd_prev": float(mp),
                "macd_now": float(mn),
                "macd_signal_prev": float(sp),
                "macd_signal_now": float(sn),
                "ema": float(ema),
                "reason": None,
                "entry_date": entry_time,
                "lot_size": None,
            }

            if trend == "bullish" and cross == "bullish":
                sl, tp = self._sl_tp(price, "buy")
                lot = self._get_lot_size()
                result.update(
                    {
                        "signal": "buy",
                        "stop_loss": sl,
                        "take_profit": tp,
                        "reason": "MACD bullish cross in uptrend",
                        "lot_size": lot,
                    }
                )

            elif trend == "bearish" and cross == "bearish":
                sl, tp = self._sl_tp(price, "sell")
                lot = self._get_lot_size()
                result.update(
                    {
                        "signal": "sell",
                        "stop_loss": sl,
                        "take_profit": tp,
                        "reason": "MACD bearish cross in downtrend",
                        "lot_size": lot,
                    }
                )

            else:
                result["reason"] = "No valid MACD + trend alignment"

            return result

        except Exception as e:
            return self._empty_signal(f"Signal error: {e}")


class RandomTradeStrategy:
    """
    Random Buy/Sell Strategy with static SL/TP in pips and dynamic lot sizing.

    Rules:
    - Randomly generates "buy" or "sell" signals.
    - SL/TP and lot sizing work exactly like a regular strategy.
    """

    def __init__(
        self,
        sl_pips: float = 20.0,
        tp_pips: Optional[float] = None,
        allowed_weekdays: Optional[list[int]] = None,
        starting_lot: float = 0.01,
        backtest_mode: bool = False,
        last_trade_won: bool = False,
    ):
        self.sl_pips = sl_pips
        self.tp_pips = tp_pips or sl_pips
        self.allowed_weekdays = allowed_weekdays or list(range(7))
        self.blocked_dates: list[str] = []

        # Lot sizing
        self.starting_lot = starting_lot
        self.current_lot = starting_lot
        self.backtest_mode = backtest_mode
        self.last_trade_won = last_trade_won

    # ---------------- HELPERS ---------------- #
    def update_trade_result(self, was_win: bool):
        """Update lot size after a trade in backtesting."""
        self.last_trade_won = was_win

    def _sl_tp(self, entry: float, side: str):
        pip_value = 0.0001
        if "JPY" in str(entry):
            pip_value = 0.01

        if side == "buy":
            sl_price = entry - self.sl_pips * pip_value
            tp_price = entry + self.tp_pips * pip_value
        else:
            sl_price = entry + self.sl_pips * pip_value
            tp_price = entry - self.tp_pips * pip_value

        return sl_price, tp_price

    def _is_allowed_date(self, timestamp) -> bool:
        dt = pd.to_datetime(timestamp)
        if dt.weekday() not in self.allowed_weekdays:
            return False
        if dt.strftime("%Y-%m-%d") in self.blocked_dates:
            return False
        return True

    # ---------------- LOT SIZING ---------------- #
    def _get_lot_size(self) -> float:
        return self.starting_lot
        # if self.backtest_mode:
        #     if self.last_trade_won and self.current_lot <= 0.02:
        #         self.current_lot += 0.01
        #     else:
        #         self.current_lot = self.starting_lot
        #     return round(self.current_lot, 2)

        # lot = self.starting_lot

        # if not mt5.initialize():
        #     raise RuntimeError("MT5 initialization failed")

        # deals = mt5.history_deals_get(0, mt5.time_current())
        # if deals and len(deals) > 0:
        #     last_trade = deals[-1]
        #     if last_trade.profit > 0:
        #         lot += 0.01
        #     else:
        #         lot = self.starting_lot

        # return round(lot, 2)

    # ---------------- MAIN SIGNAL ---------------- #
    def generate_signal(self, price_data: pd.DataFrame) -> Dict[str, Any]:
        if price_data is None or price_data.empty:
            return self._empty_signal("Price data empty")

        entry_time = price_data["time"].iloc[-1]
        if not self._is_allowed_date(entry_time):
            return self._empty_signal("Trading not allowed on this date")

        price = price_data["close"].iloc[-1]

        # Random signal
        signal = random.choice(["buy", "sell"])
        sl, tp = self._sl_tp(price, signal)
        lot = self._get_lot_size()

        return {
            "signal": signal,
            "entry_price": price,
            "stop_loss": sl,
            "take_profit": tp,
            "entry_date": entry_time,
            "reason": "Random choice",
            "lot_size": lot,
        }

    def _empty_signal(self, reason: str) -> Dict[str, Any]:
        return {
            "signal": None,
            "entry_price": None,
            "stop_loss": None,
            "take_profit": None,
            "reason": reason,
            "entry_date": None,
            "lot_size": None,
        }

    # ---------------- PARAMETERS ---------------- #
    def get_parameters(self) -> Dict[str, Any]:
        return {
            "name": "Random Trade Strategy",
            "sl_pips": self.sl_pips,
            "tp_pips": self.tp_pips,
            "allowed_weekdays": self.allowed_weekdays,
            "starting_lot": self.starting_lot,
            "backtest_mode": self.backtest_mode,
            "current_lot": self.current_lot,
            "last_trade_won": self.last_trade_won,
        }

    def __repr__(self) -> str:
        return (
            f"RandomTradeStrategy(SL={self.sl_pips}, TP={self.tp_pips}, "
            f"lot={self.current_lot}, backtest={self.backtest_mode})"
        )


class EMACrossStrategy:
    """
    EMA Crossover Strategy with static SL/TP in pips and dynamic lot sizing.

    Rules:
    - Buy: Short EMA crosses above Long EMA
    - Sell: Short EMA crosses below Long EMA

    Risk:
    - SL and TP are fixed in pips
    - Lot size adjusts based on previous trade (live) or simulated (backtesting)
    """

    def __init__(
        self,
        short_ema: int = 20,
        long_ema: int = 50,
        sl_pips: float = 20.0,
        tp_pips: Optional[float] = None,
        allowed_weekdays: Optional[list[int]] = None,
        starting_lot: float = 0.01,
        backtest_mode: bool = False,
        last_trade_won: bool = False,
    ):
        self.short_ema = short_ema
        self.long_ema = long_ema
        self.sl_pips = sl_pips
        self.tp_pips = tp_pips or sl_pips
        self.allowed_weekdays = allowed_weekdays or list(range(7))
        self.blocked_dates: list[str] = []

        self.min_bars = max(short_ema, long_ema)

        # Lot sizing
        self.starting_lot = starting_lot
        self.current_lot = starting_lot
        self.backtest_mode = backtest_mode
        self.last_trade_won = last_trade_won
        self.winning_streak = 0

    # ---------------- INDICATORS ---------------- #
    @staticmethod
    def _ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    # ---------------- VALIDATION ---------------- #
    def _validate_data(self, df: pd.DataFrame) -> Optional[str]:
        if df is None or df.empty:
            return "Price data is empty"
        if "close" not in df.columns or "time" not in df.columns:
            return "Missing required columns"
        if len(df) < self.min_bars:
            return f"Insufficient bars ({len(df)} < {self.min_bars})"
        return None

    def _empty_signal(self, reason: str) -> Dict[str, Any]:
        return {
            "signal": None,
            "entry_price": None,
            "stop_loss": None,
            "take_profit": None,
            "reason": reason,
            "entry_date": None,
            "lot_size": None,
        }

    # ---------------- HELPERS ---------------- #
    def update_trade_result(self, was_win: bool):
        """
        Update lot size after a trade in backtesting.
        """
        if was_win:
            self.winning_streak += 1
        else:
            self.winning_streak = 0
        self.last_trade_won = was_win

    def _sl_tp(self, entry: float, side: str):
        pip_value = 0.0001
        if str(entry) > 10:
            # if "JPY" in str(entry):
            pip_value = 0.01

        if side == "buy":
            sl_price = entry - self.sl_pips * pip_value
            tp_price = entry + self.tp_pips * pip_value
        else:
            sl_price = entry + self.sl_pips * pip_value
            tp_price = entry - self.tp_pips * pip_value

        return sl_price, tp_price

    def _is_allowed_date(self, timestamp) -> bool:
        dt = pd.to_datetime(timestamp)
        if dt.weekday() not in self.allowed_weekdays:
            return False
        if dt.strftime("%Y-%m-%d") in self.blocked_dates:
            return False
        return True

    # ---------------- LOT SIZING ---------------- #
    def _get_lot_size(self) -> float:
        """
        Determine lot size based on last trade in MT5 or backtest simulation.
        """
        if self.backtest_mode:
            if self.last_trade_won and self.winning_streak >= 2:
                self.current_lot += 0.01
            else:
                self.current_lot = self.starting_lot
            return round(self.current_lot, 2)

        lot = self.starting_lot

        if not mt5.initialize():
            raise RuntimeError("MT5 initialization failed")

        deals = mt5.history_deals_get(0, mt5.time_current())
        if deals and len(deals) > 0:
            last_trade = deals[-1]
            last_two_trades = deals[-2]
            if last_trade.profit > 0 and last_two_trades.profit > 0:
                lot += 0.01
            else:
                lot = self.starting_lot

        return round(lot, 2)

    # ---------------- MAIN SIGNAL ---------------- #
    def generate_signal(self, price_data: pd.DataFrame) -> Dict[str, Any]:
        error = self._validate_data(price_data)
        if error:
            return self._empty_signal(error)

        entry_time = price_data["time"].iloc[-1]
        if not self._is_allowed_date(entry_time):
            return self._empty_signal("Trading not allowed on this date")

        try:
            closes = price_data["close"]
            price = closes.iloc[-1]

            short_ema_val = self._ema(closes, self.short_ema).iloc[-1]
            long_ema_val = self._ema(closes, self.long_ema).iloc[-1]
            prev_short_ema = self._ema(closes, self.short_ema).iloc[-2]
            prev_long_ema = self._ema(closes, self.long_ema).iloc[-2]

            result = {
                "signal": None,
                "entry_price": price,
                "stop_loss": None,
                "take_profit": None,
                "reason": None,
                "entry_date": entry_time,
                "lot_size": None,
                "short_ema": float(short_ema_val),
                "long_ema": float(long_ema_val),
            }

            # EMA crossover logic
            if prev_short_ema <= prev_long_ema and short_ema_val > long_ema_val:
                sl, tp = self._sl_tp(price, "buy")
                lot = self._get_lot_size()
                result.update(
                    {
                        "signal": "buy",
                        "stop_loss": sl,
                        "take_profit": tp,
                        "lot_size": lot,
                        "reason": "Short EMA crossed above Long EMA",
                    }
                )

            elif prev_short_ema >= prev_long_ema and short_ema_val < long_ema_val:
                sl, tp = self._sl_tp(price, "sell")
                lot = self._get_lot_size()
                result.update(
                    {
                        "signal": "sell",
                        "stop_loss": sl,
                        "take_profit": tp,
                        "lot_size": lot,
                        "reason": "Short EMA crossed below Long EMA",
                    }
                )
            else:
                result["reason"] = "No EMA crossover"

            return result

        except Exception as e:
            return self._empty_signal(f"Signal error: {e}")

    # ---------------- PARAMETERS ---------------- #
    def get_parameters(self) -> Dict[str, Any]:
        return {
            "name": "EMA Crossover Strategy",
            "short_ema": self.short_ema,
            "long_ema": self.long_ema,
            "sl_pips": self.sl_pips,
            "tp_pips": self.tp_pips,
            "min_bars": self.min_bars,
            "allowed_weekdays": self.allowed_weekdays,
            "starting_lot": self.starting_lot,
            "backtest_mode": self.backtest_mode,
            "current_lot": self.current_lot,
            "last_trade_won": self.last_trade_won,
        }

    # ---------------- STRING REPRESENTATION ---------------- #
    def __repr__(self) -> str:
        return (
            f"EMACrossStrategy(short_ema={self.short_ema}, long_ema={self.long_ema}, "
            f"SL={self.sl_pips} pips, TP={self.tp_pips} pips, "
            f"lot={self.current_lot}, backtest={self.backtest_mode})"
        )


# CURRENTLY BEST RECOMMENDED
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
            "EURUSDm": {"p": 0.65, "l": 0.4},
            "GBPJPYm": {"p": 0.6, "l": 0.4},
            "EURJPYm": {"p": 0.8, "l": 0.4},
            "USDJPYm": {"p": 0.8, "l": 0.4},
            "CADJPYm": {"p": 0.8, "l": 0.4},
            "AUDJPYm": {"p": 0.8, "l": 0.4},
            "SGDJPYm": {"p": 0.8, "l": 0.4},
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
        if not mt5.initialize():
            return 0.0

        # Calculate lookback from now to beginning of current year
        now = datetime.datetime.now()
        year_start = datetime.datetime(now.year, 1, 1)

        # Use the same timestamp method as your wins function
        now_timestamp = int(time.time())
        from_timestamp = int(year_start.timestamp())

        # Get deals from beginning of year to now
        deals = mt5.history_deals_get(from_timestamp, now_timestamp)
        if not deals:
            return 0.0

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
            if (bal - self.starting_balance) >= (1 * self.starting_balance):
                # print(
                #     f"✅ {symbol or 'Symbol'} reached profit target! Current balance: {bal:.2f}"
                # )
                return True, "profit_target"
            return False, "ok"
        else:
            bal = self.get_live_balance_from_trades(symbol=symbol)
            p, l = self.balance_cap.get(symbol, {"p": 0.7, "l": 0.4}).values()
            print(f"⚠️ {symbol or 'Symbol'}  Current balance: {bal:.2f}")
            # Profit threshold (>=70% gain)
            if bal >= p * self.starting_balance:
                print(
                    f"✅ {symbol or 'Symbol'} reached profit target! Current balance: {bal:.2f}"
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
        pip_value = 0.01 if symbol.endswith("JPYm") else 0.0001
        sl_distance = self.sl_pips * pip_value
        tp_distance = sl_distance  # mirror for 1:1

        if side == "buy":
            sl = entry_price - sl_distance
            tp = entry_price + tp_distance
        else:
            sl = entry_price + sl_distance
            tp = entry_price - tp_distance

        return round(sl, 5), round(tp, 5)

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
