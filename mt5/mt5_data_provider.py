import pandas as pd
import MetaTrader5

class MT5DataProvider:
    """
    Data provider for MetaTrader 5
    
    Handles all MT5 data fetching and formatting
    Completely separate from backtester and strategy
    """
    
    def __init__(self, mt5_config):
        """
        Parameters:
        -----------
        mt5_config : MetaTraderConfig
            Your existing MT5 config object
        """
        self.mt5_config = mt5_config
        self.cache = {}  # Optional: cache data to avoid repeated fetches
    
    def fetch_data(self, symbol, timeframe, start_date, end_date=None, bars=None):
        """
        Fetch OHLC data from MT5
        
        Parameters:
        -----------
        symbol : str
            Trading symbol (e.g., 'EURUSD')
        timeframe : int
            MT5 timeframe constant (e.g., MetaTrader5.TIMEFRAME_H1)
        start_date : datetime
            Start date for data
        end_date : datetime, optional
            End date for data (default: now)
        bars : int, optional
            Number of bars to fetch (if provided, overrides end_date)
            
        Returns:
        --------
        pd.DataFrame
            OHLC data with columns: time, open, high, low, close, tick_volume, spread, real_volume
        """
        # Check cache
        cache_key = f"{symbol}_{timeframe}_{start_date}_{end_date}_{bars}"
        if cache_key in self.cache:
            return self.cache[cache_key].copy()
        
        print(f"📥 Fetching {symbol} data from MT5...")
        
        # Fetch using your existing MT5 config
        data = self.mt5_config.get_market_data_date_range(
            symbol=symbol,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            no_of_candles=bars
        )
        
        if data is None or data.empty:
            print(f"⚠️ No data retrieved for {symbol}")
            return pd.DataFrame()
        
        # Ensure required columns
        required_cols = ['time', 'open', 'high', 'low', 'close']
        if not all(col in data.columns for col in required_cols):
            print(f"⚠️ Missing required columns in {symbol} data")
            return pd.DataFrame()
        
        # Sort by time
        data = data.sort_values('time').reset_index(drop=True)
        
        # Cache it
        self.cache[cache_key] = data.copy()
        
        print(f"✅ Fetched {len(data)} bars for {symbol}")
        return data
    
    def fetch_multiple_symbols(self, symbols, timeframe, start_date, end_date=None, bars=None):
        """
        Fetch data for multiple symbols
        
        Parameters:
        -----------
        symbols : list
            List of trading symbols
        timeframe : int
            MT5 timeframe constant
        start_date : datetime
        end_date : datetime, optional
        bars : int, optional
            
        Returns:
        --------
        dict
            Dictionary mapping symbol -> DataFrame
        """
        data_dict = {}
        
        for symbol in symbols:
            df = self.fetch_data(symbol, timeframe, start_date, end_date, bars)
            if not df.empty:
                data_dict[symbol] = df
        
        return data_dict
    
    def get_timeframe_name(self, timeframe):
        """
        Convert MT5 timeframe constant to readable name
        
        Parameters:
        -----------
        timeframe : int
            MT5 timeframe constant
            
        Returns:
        --------
        str
            Timeframe name (e.g., 'H1', 'M15')
        """
        mapping = {
            MetaTrader5.TIMEFRAME_M1: 'M1',
            MetaTrader5.TIMEFRAME_M5: 'M5',
            MetaTrader5.TIMEFRAME_M15: 'M15',
            MetaTrader5.TIMEFRAME_M30: 'M30',
            MetaTrader5.TIMEFRAME_H1: 'H1',
            MetaTrader5.TIMEFRAME_H4: 'H4',
            MetaTrader5.TIMEFRAME_D1: 'D1',
            MetaTrader5.TIMEFRAME_W1: 'W1',
            MetaTrader5.TIMEFRAME_MN1: 'MN1'
        }
        return mapping.get(timeframe, 'Unknown')
    
    def validate_data(self, df):
        """
        Validate OHLC data quality
        
        Parameters:
        -----------
        df : pd.DataFrame
            OHLC dataframe
            
        Returns:
        --------
        tuple
            (is_valid: bool, issues: list)
        """
        issues = []
        
        if df.empty:
            return False, ['DataFrame is empty']
        
        # Check required columns
        required = ['time', 'open', 'high', 'low', 'close']
        missing = [col for col in required if col not in df.columns]
        if missing:
            issues.append(f'Missing columns: {missing}')
        
        # Check for NaN values
        if df[required].isnull().any().any():
            issues.append('Contains NaN values')
        
        # Check OHLC logic
        if (df['high'] < df['low']).any():
            issues.append('High < Low in some bars')
        
        if (df['high'] < df['close']).any() or (df['high'] < df['open']).any():
            issues.append('High < Open/Close in some bars')
        
        if (df['low'] > df['close']).any() or (df['low'] > df['open']).any():
            issues.append('Low > Open/Close in some bars')
        
        # Check for duplicate timestamps
        if df['time'].duplicated().any():
            issues.append('Duplicate timestamps found')
        
        # Check for gaps (optional warning)
        time_diffs = df['time'].diff()
        if len(time_diffs) > 1:
            median_diff = time_diffs.median()
            large_gaps = (time_diffs > median_diff * 3).sum()
            if large_gaps > 0:
                issues.append(f'Found {large_gaps} large time gaps')
        
        is_valid = len(issues) == 0
        return is_valid, issues
    
    def export_data(self, df, symbol, timeframe, output_dir='./data'):
        """
        Export data to CSV
        
        Parameters:
        -----------
        df : pd.DataFrame
        symbol : str
        timeframe : int
        output_dir : str
            
        Returns:
        --------
        str
            Path to saved file
        """
        import os
        
        os.makedirs(output_dir, exist_ok=True)
        
        tf_name = self.get_timeframe_name(timeframe)
        filename = f"{symbol}_{tf_name}_{df['time'].min().date()}_{df['time'].max().date()}.csv"
        filepath = os.path.join(output_dir, filename)
        
        df.to_csv(filepath, index=False)
        print(f"✅ Data exported to: {filepath}")
        
        return filepath
    
    def clear_cache(self):
        """Clear cached data"""
        self.cache.clear()
        print("✅ Cache cleared")


class CSVDataProvider:
    """
    Alternative data provider for CSV files
    Useful for testing without MT5 connection
    """
    
    def __init__(self, data_dir='./data'):
        """
        Parameters:
        -----------
        data_dir : str
            Directory containing CSV files
        """
        self.data_dir = data_dir
    
    def fetch_data(self, symbol, timeframe=None, start_date=None, end_date=None):
        """
        Load data from CSV file
        
        Parameters:
        -----------
        symbol : str
            Symbol name (used to find CSV file)
        timeframe : str, optional
            Timeframe string (e.g., 'H1')
        start_date : datetime, optional
        end_date : datetime, optional
            
        Returns:
        --------
        pd.DataFrame
            OHLC data
        """
        import os
        import glob
        
        # Find CSV file matching symbol
        pattern = os.path.join(self.data_dir, f"{symbol}*.csv")
        files = glob.glob(pattern)
        
        if not files:
            print(f"⚠️ No CSV file found for {symbol}")
            return pd.DataFrame()
        
        # Use first matching file
        filepath = files[0]
        print(f"📥 Loading {symbol} from {filepath}")
        
        df = pd.read_csv(filepath)
        
        # Convert time column to datetime
        if 'time' in df.columns:
            df['time'] = pd.to_datetime(df['time'])
        elif 'timestamp' in df.columns:
            df['time'] = pd.to_datetime(df['timestamp'])
        elif 'date' in df.columns:
            df['time'] = pd.to_datetime(df['date'])
        
        # Filter by date range
        if start_date and 'time' in df.columns:
            df = df[df['time'] >= start_date]
        if end_date and 'time' in df.columns:
            df = df[df['time'] <= end_date]
        
        print(f"✅ Loaded {len(df)} bars for {symbol}")
        return df
    
    def fetch_multiple_symbols(self, symbols, **kwargs):
        """Fetch data for multiple symbols"""
        data_dict = {}
        
        for symbol in symbols:
            df = self.fetch_data(symbol, **kwargs)
            if not df.empty:
                data_dict[symbol] = df
        
        return data_dict