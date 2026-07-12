import pandas as pd
import numpy as np
import datetime
import plotly.graph_objects as go

def clean_dataframe(df, ticker=None):
    """
    Cleans the uploaded DataFrame by identifying and renaming columns 
    to standard names: open, high, low, close, volume, datetime.
    """
    df = df.copy()
    
    # Clean column names mapping to avoid duplicate naming conflicts
    cols = list(df.columns)
    col_mapping = {}
    ticker_col = None
    
    # 1. Identify Ticker/Symbol column
    for col in cols:
        col_lower = str(col).lower().strip()
        if col_lower in ['ticker', 'symbol', 'stock', 'name']:
            ticker_col = col
            break
            
    if ticker_col is not None:
        if ticker:
            df = df[df[ticker_col].astype(str).str.upper() == str(ticker).upper()]
        else:
            unique_tickers = df[ticker_col].dropna().unique()
            if len(unique_tickers) > 1:
                df = df[df[ticker_col] == unique_tickers[0]]

    # Helper function to find the best match
    def find_and_remove(candidates, cols_list):
        # Exact match check first
        for cand in candidates:
            for col in cols_list:
                if str(col).lower().strip() == cand:
                    cols_list.remove(col)
                    return col
        # Substring match check second
        for cand in candidates:
            for col in cols_list:
                if cand in str(col).lower().strip():
                    cols_list.remove(col)
                    return col
        return None

    # Mapped standard columns
    cols_to_map = cols.copy()
    if ticker_col in cols_to_map:
        cols_to_map.remove(ticker_col)
        
    dt_col = find_and_remove(['datetime', 'date', 'time', 'timestamp'], cols_to_map)
    if dt_col:
        col_mapping[dt_col] = 'datetime'
        
    open_col = find_and_remove(['open', 'op'], cols_to_map)
    if open_col:
        col_mapping[open_col] = 'open'
        
    high_col = find_and_remove(['high', 'hi'], cols_to_map)
    if high_col:
        col_mapping[high_col] = 'high'
        
    low_col = find_and_remove(['low', 'lo'], cols_to_map)
    if low_col:
        col_mapping[low_col] = 'low'
        
    close_col = find_and_remove(['close', 'cl'], cols_to_map)
    if close_col:
        col_mapping[close_col] = 'close'
        
    vol_col = find_and_remove(['volume', 'vol'], cols_to_map)
    if vol_col:
        col_mapping[vol_col] = 'volume'

    df.rename(columns=col_mapping, inplace=True)
    
    # If index is datetime, or we found a datetime column
    if 'datetime' in df.columns:
        df['datetime'] = pd.to_datetime(df['datetime'])
        df.set_index('datetime', inplace=True)
    else:
        df.index = pd.to_datetime(df.index)
        
    df = df[~df.index.duplicated(keep='first')]
    df.sort_index(inplace=True)
    
    # Drop rows with missing crucial data
    df.dropna(subset=['open', 'high', 'low', 'close'], inplace=True)
    
    # Ensure float types
    for col in ['open', 'high', 'low', 'close']:
        df[col] = df[col].astype(float)
        
    if 'volume' in df.columns:
        df['volume'] = df['volume'].astype(float)
    else:
        df['volume'] = 1.0 # fallback
        
    return df

def calculate_indicators(df):
    """Calculates EMA, RSI, ATR, and VWAP for backtesting."""
    df = df.copy()
    
    # 1. EMAs
    df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()
    
    # 2. RSI 14
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df['rsi_14'] = 100 - (100 / (1 + rs))
    df['rsi_14'] = df['rsi_14'].fillna(50)
    
    # 3. ATR 14
    high_low = df['high'] - df['low']
    high_prev_close = (df['high'] - df['close'].shift(1)).abs()
    low_prev_close = (df['low'] - df['close'].shift(1)).abs()
    tr = pd.concat([high_low, high_prev_close, low_prev_close], axis=1).max(axis=1)
    df['atr_14'] = tr.rolling(window=14).mean().fillna(high_low)
    
    # 4. Intraday VWAP resetting daily
    df['Date_only'] = df.index.date
    tp = (df['high'] + df['low'] + df['close']) / 3.0
    tpv = tp * df['volume']
    df['tpv'] = tpv
    df['cum_tpv'] = df.groupby('Date_only')['tpv'].cumsum()
    df['cum_vol'] = df.groupby('Date_only')['volume'].cumsum()
    df['vwap'] = df['cum_tpv'] / df['cum_vol']
    df['vwap'] = df['vwap'].fillna(df['close'])
    
    # 5. Vol MA20 (intraday)
    df['vol_ma20'] = df.groupby('Date_only')['volume'].transform(lambda x: x.rolling(20).mean()).fillna(df['volume'])
    
    # 6. ATR 5m (intraday)
    df['atr_5m'] = df.groupby('Date_only')['tpv'].transform(lambda x: tr.rolling(5).mean()).fillna(high_low)
    
    return df

def detect_candlestick_patterns(df):
    """Detects Hammmer, Shooting Star, and Engulfing reversal patterns."""
    df = df.copy()
    body = (df['close'] - df['open']).abs()
    candle_range = df['high'] - df['low']
    upper_shadow = df['high'] - df[['open', 'close']].max(axis=1)
    lower_shadow = df[['open', 'close']].min(axis=1) - df['low']
    
    # --- BULLISH PATTERNS ---
    # 1. Hammer
    is_hammer = (
        (lower_shadow >= 2 * body) & 
        (body <= 0.3 * candle_range) & 
        (upper_shadow <= 0.25 * candle_range) & 
        (candle_range > 0)
    )
    # 2. Bullish Engulfing
    prev_close = df['close'].shift(1)
    prev_open = df['open'].shift(1)
    is_green = df['close'] > df['open']
    prev_red = prev_close < prev_open
    is_bullish_engulfing = (
        is_green & 
        prev_red & 
        (df['close'] >= prev_open) & 
        (df['open'] <= prev_close)
    )
    # 3. Bullish Pin Bar
    is_bullish_pinbar = (
        (lower_shadow >= 0.6 * candle_range) & 
        (df['close'] > df['open'] - 0.1 * body) & 
        (candle_range > 0)
    )
    df['is_bullish_reversal'] = is_hammer | is_bullish_engulfing | is_bullish_pinbar
    
    # --- BEARISH PATTERNS ---
    # 1. Shooting Star
    is_shooting_star = (
        (upper_shadow >= 2 * body) & 
        (body <= 0.3 * candle_range) & 
        (lower_shadow <= 0.25 * candle_range) & 
        (candle_range > 0)
    )
    # 2. Bearish Engulfing
    is_red = df['close'] < df['open']
    prev_green = prev_close > prev_open
    is_bearish_engulfing = (
        is_red & 
        prev_green & 
        (df['close'] <= prev_open) & 
        (df['open'] >= prev_close)
    )
    # 3. Bearish Pin Bar
    is_bearish_pinbar = (
        (upper_shadow >= 0.6 * candle_range) & 
        (df['close'] < df['open'] + 0.1 * body) & 
        (candle_range > 0)
    )
    df['is_bearish_reversal'] = is_shooting_star | is_bearish_engulfing | is_bearish_pinbar
    
    return df

def run_backtest(df, strategy, capital=250000, risk_pct=1.0, slippage_pct=0.05, ticker=None, progress_callback=None):
    """
    Runs backtest for the specified strategy over cleaned & indicator-populated DataFrame.
    """
    df = clean_dataframe(df, ticker=ticker)
    ticker = ticker if ticker else 'STOCK'
    df = calculate_indicators(df)
    df = detect_candlestick_patterns(df)
    
    # 1. Resample to daily timeframe to compute pre-market caching rules
    is_intraday = True
    if len(df) > 1:
        time_diff = df.index[1] - df.index[0]
        if time_diff >= pd.Timedelta(days=1):
            is_intraday = False
            
    if is_intraday:
        daily_df = df.resample('D').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()
    else:
        daily_df = df.copy()
        
    # Calculate daily indicators for pre-market caching checks
    daily_df['ema_20'] = daily_df['close'].ewm(span=20, adjust=False).mean()
    daily_df['ema_50'] = daily_df['close'].ewm(span=50, adjust=False).mean()
    daily_df['ema_200'] = daily_df['close'].ewm(span=200, adjust=False).mean()
    daily_delta = daily_df['close'].diff()
    daily_gain = (daily_delta.where(daily_delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    daily_loss = (-daily_delta.where(daily_delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    daily_rs = daily_gain / daily_loss.replace(0, np.nan)
    daily_df['rsi_14'] = 100 - (100 / (1 + daily_rs))
    daily_df['rsi_14'] = daily_df['rsi_14'].fillna(50)
    
    # Calculate ATR 14 (Daily) and ATR % for volatility pre-filter
    daily_hl = daily_df['high'] - daily_df['low']
    daily_hpc = (daily_df['high'] - daily_df['close'].shift(1)).abs()
    daily_lpc = (daily_df['low'] - daily_df['close'].shift(1)).abs()
    daily_tr = pd.concat([daily_hl, daily_hpc, daily_lpc], axis=1).max(axis=1)
    daily_df['atr_14'] = daily_tr.rolling(window=14).mean().fillna(daily_hl)
    daily_df['atr_pct'] = (daily_df['atr_14'] / daily_df['close']) * 100
    
    def get_yesterday_indicators(curr_day):
        prior_bars = daily_df[daily_df.index.date < curr_day]
        if prior_bars.empty:
            return None
        return prior_bars.iloc[-1]
    
    trades = []
    days = sorted(list(set(df.index.date)))
    
    if strategy in ["Bullish Breakout (Intraday)", "15-Min ORB Breakout (Intraday)"]:
        for i in range(1, len(days)):
            prev_day = days[i-1]
            curr_day = days[i]
            
            # Show progress
            if progress_callback:
                progress_callback(i, len(days), curr_day)
            print(f"[{strategy}] Backtesting day {i}/{len(days)}: {curr_day}...")
            
            # Yesterday's indicators check (9:20 pre-market cache check)
            yesterday_data = get_yesterday_indicators(curr_day)
            if yesterday_data is None:
                continue
                
            # Filter for structural daily strength: Close > EMA 20 > EMA 50 > EMA 200 AND RSI > 55 AND ATR_Pct >= 2.0 (Phase 1 Morning Pre-Filter)
            is_strong = (yesterday_data['close'] > yesterday_data['ema_20'] > yesterday_data['ema_50'] > yesterday_data['ema_200']) and \
                        (yesterday_data['rsi_14'] > 55) and \
                        (yesterday_data['atr_pct'] >= 2.0)
            if not is_strong:
                continue
            
            # Yesterday's High
            prev_df = df[df.index.date == prev_day]
            if prev_df.empty:
                continue
            pdh = prev_df['high'].max()
            prev_close = prev_df['close'].iloc[-1]
            
            # Today's data
            curr_df = df[df.index.date == curr_day].copy()
            if len(curr_df) < 5:
                continue
                
            # Early opening momentum check (Gap limit check instead of green-only/PDH-closeness check)
            first_candle = curr_df.between_time('09:15', '09:20')
            if first_candle.empty:
                continue
            first_row = first_candle.iloc[0]
            gap_pct = ((first_row['open'] - prev_close) / prev_close) * 100 if prev_close > 0 else 0
            if abs(gap_pct) > 3.0 or gap_pct < -0.5:
                continue
            
            # Find Opening Range (first 3 candles, 9:15 - 9:30)
            or_df = curr_df.between_time('09:15', '09:30')
            if or_df.empty:
                continue
            or_high = or_df['high'].max()
            breakout_level = max(pdh, or_high)
            
            # Calculate volume rolling average before the trigger candle (20-period 5-min volume MA)
            curr_df['vol_ma20'] = curr_df['volume'].rolling(window=20).mean().shift(1)
            
            # Run simulation
            active_trade = None
            day_candles = curr_df.between_time('09:31', '15:20')
            
            for timestamp, row in day_candles.iterrows():
                if active_trade is None:
                    # 15m ORB conditions aligned with live scanner:
                    # 1. Close > breakout_level
                    # 2. No-Chase: close within 0.05% to 1.5% of breakout level
                    # 3. Volume > 1.5x average volume of previous 20 candles
                    # 4. Close > VWAP
                    # 5. Consolidation check (tight range of preceding 3 candles <= 1.2%)
                    # 6. Candle shape (body >= 50% of range)
                    vol_threshold = row['vol_ma20'] * 1.5 if not pd.isna(row['vol_ma20']) else 0
                    
                    # Consolidation check (1.2% threshold)
                    idx = curr_df.index.get_loc(timestamp)
                    if idx >= 3:
                        preceding_candles = curr_df.iloc[idx-3:idx]
                        preceding_low = preceding_candles['low'].min()
                        tight_range = (preceding_candles['high'].max() - preceding_low) / preceding_low * 100 if preceding_low > 0 else 99
                        is_consolidating = tight_range <= 1.20
                    else:
                        is_consolidating = True
                        
                    # Candle shape (Body >= 50% of range)
                    candle_range = row['high'] - row['low']
                    body_size = abs(row['close'] - row['open'])
                    strength_ok = (body_size >= 0.5 * candle_range) if candle_range > 0 else False
                    
                    # Distance pct check
                    dist_pct = (row['close'] - breakout_level) / breakout_level * 100 if breakout_level > 0 else 0
                    
                    if (row['close'] > breakout_level) and \
                       (0.05 <= dist_pct <= 1.5) and \
                       (row['volume'] > vol_threshold) and \
                       (row['close'] > row['vwap']) and \
                       is_consolidating and \
                       strength_ok:
                        
                        entry_price = row['close'] * (1 + slippage_pct/100)
                        vwap_sl = row['vwap'] * 0.998
                        sl_price = max(vwap_sl, entry_price * 0.975) # Max 2.5% SL
                        sl_price = min(sl_price, entry_price * 0.995) # Min 0.5% SL
                        
                        risk = entry_price - sl_price
                        target_price = entry_price + 2.0 * risk
                        qty = int((capital * (risk_pct/100)) / risk) if risk > 0 else 1
                        
                        active_trade = {
                            'Ticker': ticker,
                            'Type': 'BUY',
                            'EntryTime': timestamp,
                            'EntryPrice': entry_price,
                            'Qty': qty,
                            'SL': sl_price,
                            'Target': target_price,
                            'Status': 'Open',
                            'Trailed': False
                        }
                else:
                    # Trailing Stop Loss: Move SL to VWAP once price moves 1% in favor
                    if not active_trade['Trailed'] and row['high'] >= active_trade['EntryPrice'] * 1.01:
                        active_trade['SL'] = row['vwap']
                        active_trade['Trailed'] = True
                        
                    # Monitor active trade
                    if row['low'] <= active_trade['SL']:
                        active_trade['ExitPrice'] = active_trade['SL'] * (1 - slippage_pct/100)
                        active_trade['ExitTime'] = timestamp
                        active_trade['Status'] = 'SL HIT'
                        active_trade['PnL'] = (active_trade['ExitPrice'] - active_trade['EntryPrice']) * active_trade['Qty']
                        trades.append(active_trade)
                        active_trade = None
                    elif row['high'] >= active_trade['Target']:
                        active_trade['ExitPrice'] = active_trade['Target'] * (1 - slippage_pct/100)
                        active_trade['ExitTime'] = timestamp
                        active_trade['Status'] = 'TARGET HIT'
                        active_trade['PnL'] = (active_trade['ExitPrice'] - active_trade['EntryPrice']) * active_trade['Qty']
                        trades.append(active_trade)
                        active_trade = None
                    elif timestamp.time() >= datetime.time(15, 20):
                        active_trade['ExitPrice'] = row['close'] * (1 - slippage_pct/100)
                        active_trade['ExitTime'] = timestamp
                        active_trade['Status'] = 'EOD CLOSED'
                        active_trade['PnL'] = (active_trade['ExitPrice'] - active_trade['EntryPrice']) * active_trade['Qty']
                        trades.append(active_trade)
                        active_trade = None

    elif strategy == "Bearish Breakdown (Intraday)":
        for i in range(1, len(days)):
            prev_day = days[i-1]
            curr_day = days[i]
            
            # Show progress
            if progress_callback:
                progress_callback(i, len(days), curr_day)
            print(f"[{strategy}] Backtesting day {i}/{len(days)}: {curr_day}...")
            
            # Yesterday's indicators check (9:20 pre-market cache check)
            yesterday_data = get_yesterday_indicators(curr_day)
            if yesterday_data is None:
                continue
                
            # Filter for structural daily weakness: Close < EMA 20 < EMA 50 < EMA 200 AND RSI < 45 AND ATR_Pct >= 2.0 (Phase 1 Morning Pre-Filter)
            is_weak = (yesterday_data['close'] < yesterday_data['ema_20'] < yesterday_data['ema_50'] < yesterday_data['ema_200']) and \
                      (yesterday_data['rsi_14'] < 45) and \
                      (yesterday_data['atr_pct'] >= 2.0)
            if not is_weak:
                continue
            
            # Yesterday's Low
            prev_df = df[df.index.date == prev_day]
            if prev_df.empty:
                continue
            pdl = prev_df['low'].min()
            prev_close = prev_df['close'].iloc[-1]
            
            # Today's data
            curr_df = df[df.index.date == curr_day].copy()
            if len(curr_df) < 5:
                continue
                
            # Early opening momentum check (Gap limit check instead of green-only/PDH-closeness check)
            first_candle = curr_df.between_time('09:15', '09:20')
            if first_candle.empty:
                continue
            first_row = first_candle.iloc[0]
            gap_pct = ((first_row['open'] - prev_close) / prev_close) * 100 if prev_close > 0 else 0
            if abs(gap_pct) > 3.0 or gap_pct > 0.5:
                continue
            
            # Find Opening Range (first 3 candles, 9:15 - 9:30)
            or_df = curr_df.between_time('09:15', '09:30')
            if or_df.empty:
                continue
            or_low = or_df['low'].min()
            breakdown_level = min(pdl, or_low)
            
            # Calculate volume rolling average before the trigger candle
            curr_df['vol_ma20'] = curr_df['volume'].rolling(window=20).mean().shift(1)
            
            # Run simulation
            active_trade = None
            day_candles = curr_df.between_time('09:31', '15:20')
            
            for timestamp, row in day_candles.iterrows():
                if active_trade is None:
                    # Bearish ORB conditions:
                    # 1. Close < breakdown_level
                    # 2. No-Chase: close within 0.05% to 1.5% of breakdown level
                    # 3. Volume > 1.5x average volume of previous 20 candles
                    # 4. Close < VWAP
                    # 5. Consolidation check (tight range of preceding 3 candles <= 1.2%)
                    # 6. Candle shape (body >= 50% of range)
                    # 7. RSI oversold filter (RSI >= 30)
                    vol_threshold = row['vol_ma20'] * 1.5 if not pd.isna(row['vol_ma20']) else 0
                    
                    # Consolidation check (1.2% threshold)
                    idx = curr_df.index.get_loc(timestamp)
                    if idx >= 3:
                        preceding_candles = curr_df.iloc[idx-3:idx]
                        preceding_low = preceding_candles['low'].min()
                        tight_range = (preceding_candles['high'].max() - preceding_low) / preceding_low * 100 if preceding_low > 0 else 99
                        is_consolidating = tight_range <= 1.20
                    else:
                        is_consolidating = True
                        
                    # Candle shape
                    candle_range = row['high'] - row['low']
                    body_size = abs(row['close'] - row['open'])
                    strength_ok = (body_size >= 0.5 * candle_range) if candle_range > 0 else False
                    
                    # RSI oversold filter
                    is_oversold = row['rsi_14'] < 30
                    
                    # Distance pct check
                    dist_pct = (breakdown_level - row['close']) / breakdown_level * 100 if breakdown_level > 0 else 0
                    
                    if (row['close'] < breakdown_level) and \
                       (0.05 <= dist_pct <= 1.5) and \
                       (row['volume'] > vol_threshold) and \
                       (row['close'] < row['vwap']) and \
                       is_consolidating and \
                       strength_ok and \
                       not is_oversold:
                        
                        entry_price = row['close'] * (1 - slippage_pct/100)
                        vwap_sl = row['vwap'] * 1.002
                        sl_price = min(vwap_sl, entry_price * 1.025) # Max 2.5% SL
                        sl_price = max(sl_price, entry_price * 1.005) # Min 0.5% SL
                        
                        risk = sl_price - entry_price
                        target_price = entry_price - 2.0 * risk
                        qty = int((capital * (risk_pct/100)) / risk) if risk > 0 else 1
                        
                        active_trade = {
                            'Ticker': ticker,
                            'Type': 'SELL',
                            'EntryTime': timestamp,
                            'EntryPrice': entry_price,
                            'Qty': qty,
                            'SL': sl_price,
                            'Target': target_price,
                            'Status': 'Open',
                            'Trailed': False
                        }
                else:
                    # Trailing Stop Loss: Move SL to VWAP once price moves 1% in favor
                    if not active_trade['Trailed'] and row['low'] <= active_trade['EntryPrice'] * 0.99:
                        active_trade['SL'] = row['vwap']
                        active_trade['Trailed'] = True
                        
                    # Monitor active trade
                    if row['high'] >= active_trade['SL']:
                        active_trade['ExitPrice'] = active_trade['SL'] * (1 + slippage_pct/100)
                        active_trade['ExitTime'] = timestamp
                        active_trade['Status'] = 'SL HIT'
                        active_trade['PnL'] = (active_trade['EntryPrice'] - active_trade['ExitPrice']) * active_trade['Qty']
                        trades.append(active_trade)
                        active_trade = None
                    elif row['low'] <= active_trade['Target']:
                        active_trade['ExitPrice'] = active_trade['Target'] * (1 + slippage_pct/100)
                        active_trade['ExitTime'] = timestamp
                        active_trade['Status'] = 'TARGET HIT'
                        active_trade['PnL'] = (active_trade['EntryPrice'] - active_trade['ExitPrice']) * active_trade['Qty']
                        trades.append(active_trade)
                        active_trade = None
                    elif timestamp.time() >= datetime.time(15, 20):
                        active_trade['ExitPrice'] = row['close'] * (1 + slippage_pct/100)
                        active_trade['ExitTime'] = timestamp
                        active_trade['Status'] = 'EOD CLOSED'
                        active_trade['PnL'] = (active_trade['EntryPrice'] - active_trade['ExitPrice']) * active_trade['Qty']
                        trades.append(active_trade)
                        active_trade = None

    elif strategy == "Bullish VWAP Rejection (Intraday)":
        for i in range(1, len(days)):
            prev_day = days[i-1]
            curr_day = days[i]
            
            # Show progress
            if progress_callback:
                progress_callback(i, len(days), curr_day)
            print(f"[{strategy}] Backtesting day {i}/{len(days)}: {curr_day}...")
            
            # Yesterday's indicators check
            yesterday_data = get_yesterday_indicators(curr_day)
            if yesterday_data is None:
                continue
                
            # Filter for structural daily strength
            is_strong = yesterday_data['close'] > yesterday_data['ema_50'] and yesterday_data['rsi_14'] > 50
            if not is_strong:
                continue
            
            # Yesterday's Close & High
            prev_df = df[df.index.date == prev_day]
            if prev_df.empty:
                continue
            pdc = prev_df['close'].iloc[-1]
            pdh = prev_df['high'].max()
            
            # Today's data
            curr_df = df[df.index.date == curr_day].copy()
            if len(curr_df) < 5:
                continue
                
            # Early opening momentum check (9:20 AM pre-market filter check)
            first_candle = curr_df.between_time('09:15', '09:20')
            if first_candle.empty:
                continue
            first_row = first_candle.iloc[0]
            # Must open/trade above yesterday's close to be screened in (aligning with batch_pre_screen)
            if first_row['close'] <= pdc:
                continue
            
            # Compute vwap slope (sloping up check)
            curr_df['vwap_slope_ok'] = curr_df['vwap'] > curr_df['vwap'].shift(3)
            
            or_high = curr_df['high'].iloc[0:3].max() if len(curr_df) >= 3 else curr_df['high'].iloc[0]
            curr_df['resistance_break'] = curr_df['close'] > or_high
            curr_df['vwap_break'] = (curr_df['close'].shift(1) < curr_df['vwap'].shift(1)) & \
                                     (curr_df['close'] > curr_df['vwap']) & \
                                     (curr_df['volume'] > curr_df['vol_ma20'] * 1.2)
            curr_df['has_broken_out'] = (curr_df['resistance_break'] | curr_df['vwap_break']).cumsum() > 0
            
            active_trade = None
            trade_taken_today = False
            day_candles = curr_df.between_time('09:31', '15:20')
            
            for timestamp, row in day_candles.iterrows():
                # Resistance / VWAP breakout tracking
                has_broken_out = row['has_broken_out']
                
                if active_trade is None:
                    if trade_taken_today:
                        continue
                        
                    # Rejection touch bounds
                    atr_buffer = 0.2 * row['atr_5m']
                    touch_vwap = (row['low'] <= row['vwap'] + atr_buffer) & (row['high'] >= row['vwap'] - atr_buffer)
                    touch_ema = (row['low'] <= row['ema_9'] + atr_buffer) & (row['high'] >= row['ema_9'] - atr_buffer)
                    
                    # Candleshap confirmation: Close must be in the upper 60% of the candle range
                    candle_range = row['high'] - row['low']
                    candle_ok = row['close'] > (row['low'] + 0.6 * candle_range) if candle_range > 0 else False
                    
                    # Distance limit (No-chasing filter: within 0.4% of VWAP or 9 EMA)
                    vwap_dist = abs(row['close'] - row['vwap']) / row['vwap'] * 100
                    ema_dist = abs(row['close'] - row['ema_9']) / row['ema_9'] * 100
                    not_chasing = min(vwap_dist, ema_dist) <= 0.4
                    
                    # Daily extension limits
                    day_change_pct = (row['close'] - pdc) / pdc * 100
                    not_extended = day_change_pct < 3.0
                    
                    # Volume Filter: Rejection candle volume must confirm institutional activity
                    vol_ok = row['volume'] > row['vol_ma20'] * 0.8 if not pd.isna(row['vol_ma20']) else True
                    
                    if (row['close'] > row['vwap']) and \
                       (row['vwap'] > pdc) and \
                       (row['vwap_slope_ok']) and \
                       has_broken_out and \
                       (touch_vwap or touch_ema) and \
                       (row['is_bullish_reversal']) and \
                       (row['close'] > pdc) and \
                       (row['close'] > curr_df['open'].iloc[0]) and \
                       (row['rsi_14'] <= 70) and \
                       vol_ok and \
                       not_extended and \
                       not_chasing and \
                       candle_ok:
                        
                        entry_price = row['close'] * (1 + slippage_pct/100)
                        
                        # Swing SL is recent 5 candles low or VWAP with buffer
                        recent_low = curr_df.loc[:timestamp].tail(5)['low'].min()
                        vwap_sl = row['vwap'] - (1.5 * row['atr_5m'])
                        sl_price = min(recent_low, vwap_sl)
                        
                        risk = entry_price - sl_price
                        if risk <= entry_price * 0.004:
                            sl_price = entry_price * 0.996
                            risk = entry_price - sl_price
                        elif risk >= entry_price * 0.015:
                            sl_price = entry_price * 0.985
                            risk = entry_price - sl_price
                            
                        target_price = entry_price + 1.5 * risk
                        qty = int((capital * (risk_pct/100)) / risk) if risk > 0 else 1
                        
                        active_trade = {
                            'Ticker': ticker,
                            'Type': 'BUY',
                            'EntryTime': timestamp,
                            'EntryPrice': entry_price,
                            'Qty': qty,
                            'SL': sl_price,
                            'Target': target_price,
                            'Status': 'Open',
                            'Trailed': False
                        }
                        trade_taken_today = True
                else:
                    # Trailing Stop Loss: Move SL to VWAP once price moves 1% in favor
                    if not active_trade['Trailed'] and row['high'] >= active_trade['EntryPrice'] * 1.01:
                        active_trade['SL'] = row['vwap']
                        active_trade['Trailed'] = True
                        
                    # Monitor active trade
                    if row['low'] <= active_trade['SL']:
                        active_trade['ExitPrice'] = active_trade['SL'] * (1 - slippage_pct/100)
                        active_trade['ExitTime'] = timestamp
                        active_trade['Status'] = 'SL HIT'
                        active_trade['PnL'] = (active_trade['ExitPrice'] - active_trade['EntryPrice']) * active_trade['Qty']
                        trades.append(active_trade)
                        active_trade = None
                    elif row['high'] >= active_trade['Target']:
                        active_trade['ExitPrice'] = active_trade['Target'] * (1 - slippage_pct/100)
                        active_trade['ExitTime'] = timestamp
                        active_trade['Status'] = 'TARGET HIT'
                        active_trade['PnL'] = (active_trade['ExitPrice'] - active_trade['EntryPrice']) * active_trade['Qty']
                        trades.append(active_trade)
                        active_trade = None
                    elif timestamp.time() >= datetime.time(15, 20):
                        active_trade['ExitPrice'] = row['close'] * (1 - slippage_pct/100)
                        active_trade['ExitTime'] = timestamp
                        active_trade['Status'] = 'EOD CLOSED'
                        active_trade['PnL'] = (active_trade['ExitPrice'] - active_trade['EntryPrice']) * active_trade['Qty']
                        trades.append(active_trade)
                        active_trade = None

    elif strategy == "Bearish VWAP Rejection (Intraday)":
        for i in range(1, len(days)):
            prev_day = days[i-1]
            curr_day = days[i]
            
            # Show progress
            if progress_callback:
                progress_callback(i, len(days), curr_day)
            print(f"[{strategy}] Backtesting day {i}/{len(days)}: {curr_day}...")
            
            # Yesterday's indicators check
            yesterday_data = get_yesterday_indicators(curr_day)
            if yesterday_data is None:
                continue
                
            # Filter for structural daily weakness
            is_weak = yesterday_data['close'] < yesterday_data['ema_50'] or yesterday_data['rsi_14'] < 55
            if not is_weak:
                continue
            
            # Yesterday's Close & Low
            prev_df = df[df.index.date == prev_day]
            if prev_df.empty:
                continue
            pdc = prev_df['close'].iloc[-1]
            pdl = prev_df['low'].min()
            
            # Today's data
            curr_df = df[df.index.date == curr_day].copy()
            if len(curr_df) < 5:
                continue
                
            # Early opening momentum check (9:20 AM pre-market filter check)
            first_candle = curr_df.between_time('09:15', '09:20')
            if first_candle.empty:
                continue
            first_row = first_candle.iloc[0]
            # Must open/trade below yesterday's close to be screened in (aligning with batch_pre_screen)
            if first_row['close'] >= pdc:
                continue
            
            # Compute vwap slope (sloping down check)
            curr_df['vwap_slope_ok'] = curr_df['vwap'] < curr_df['vwap'].shift(3)
            
            or_low = curr_df['low'].iloc[0:3].min() if len(curr_df) >= 3 else curr_df['low'].iloc[0]
            curr_df['resistance_break'] = curr_df['close'] < or_low
            curr_df['vwap_break'] = (curr_df['close'].shift(1) > curr_df['vwap'].shift(1)) & \
                                     (curr_df['close'] < curr_df['vwap']) & \
                                     (curr_df['volume'] > curr_df['vol_ma20'] * 1.2)
            curr_df['has_broken_out'] = (curr_df['resistance_break'] | curr_df['vwap_break']).cumsum() > 0
            
            active_trade = None
            trade_taken_today = False
            day_candles = curr_df.between_time('09:31', '15:20')
            
            for timestamp, row in day_candles.iterrows():
                # Resistance / VWAP breakdown tracking
                has_broken_out = row['has_broken_out']
                
                if active_trade is None:
                    if trade_taken_today:
                        continue
                        
                    # Rejection touch bounds
                    atr_buffer = 0.2 * row['atr_5m']
                    touch_vwap = (row['high'] >= row['vwap'] - atr_buffer) & (row['low'] <= row['vwap'] + atr_buffer)
                    touch_ema = (row['high'] >= row['ema_9'] - atr_buffer) & (row['low'] <= row['ema_9'] + atr_buffer)
                    
                    # Candleshap confirmation: Close must be in the lower 40% of the candle range
                    candle_range = row['high'] - row['low']
                    candle_ok = row['close'] < (row['low'] + 0.4 * candle_range) if candle_range > 0 else False
                    
                    # Distance limit (No-chasing filter: within 0.4% of VWAP or 9 EMA)
                    vwap_dist = abs(row['close'] - row['vwap']) / row['vwap'] * 100
                    ema_dist = abs(row['close'] - row['ema_9']) / row['ema_9'] * 100
                    not_chasing = min(vwap_dist, ema_dist) <= 0.4
                    
                    # Daily extension limits
                    day_change_pct = (row['close'] - pdc) / pdc * 100
                    not_extended = day_change_pct > -3.0
                    
                    # Volume Filter: Rejection candle volume must confirm institutional activity
                    vol_ok = row['volume'] > row['vol_ma20'] * 0.8 if not pd.isna(row['vol_ma20']) else True
                    
                    if (row['close'] < row['vwap']) and \
                       (row['vwap'] < pdc) and \
                       (row['vwap_slope_ok']) and \
                       has_broken_out and \
                       (touch_vwap or touch_ema) and \
                       (row['is_bearish_reversal']) and \
                       (row['close'] < pdc) and \
                       (row['close'] < curr_df['open'].iloc[0]) and \
                       (row['rsi_14'] >= 30) and \
                       vol_ok and \
                       not_extended and \
                       not_chasing and \
                       candle_ok:
                        
                        entry_price = row['close'] * (1 - slippage_pct/100)
                        
                        # Swing SL is recent 5 candles high or VWAP with buffer
                        recent_high = curr_df.loc[:timestamp].tail(5)['high'].max()
                        vwap_sl = row['vwap'] + (1.5 * row['atr_5m'])
                        sl_price = max(recent_high, vwap_sl)
                        
                        risk = sl_price - entry_price
                        if risk <= entry_price * 0.004:
                            sl_price = entry_price * 1.006
                            risk = sl_price - entry_price
                        elif risk >= entry_price * 0.015:
                            sl_price = entry_price * 1.015
                            risk = sl_price - entry_price
                            
                        target_price = entry_price - 2.0 * risk
                        qty = int((capital * (risk_pct/100)) / risk) if risk > 0 else 1
                        
                        active_trade = {
                            'Ticker': ticker,
                            'Type': 'SELL',
                            'EntryTime': timestamp,
                            'EntryPrice': entry_price,
                            'Qty': qty,
                            'SL': sl_price,
                            'Target': target_price,
                            'Status': 'Open',
                            'Trailed': False
                        }
                        trade_taken_today = True
                else:
                    # Trailing Stop Loss: Move SL to VWAP once price moves 1% in favor
                    if not active_trade['Trailed'] and row['low'] <= active_trade['EntryPrice'] * 0.99:
                        active_trade['SL'] = row['vwap']
                        active_trade['Trailed'] = True
                        
                    # Monitor active trade
                    if row['high'] >= active_trade['SL']:
                        active_trade['ExitPrice'] = active_trade['SL'] * (1 + slippage_pct/100)
                        active_trade['ExitTime'] = timestamp
                        active_trade['Status'] = 'SL HIT'
                        active_trade['PnL'] = (active_trade['EntryPrice'] - active_trade['ExitPrice']) * active_trade['Qty']
                        trades.append(active_trade)
                        active_trade = None
                    elif row['low'] <= active_trade['Target']:
                        active_trade['ExitPrice'] = active_trade['Target'] * (1 + slippage_pct/100)
                        active_trade['ExitTime'] = timestamp
                        active_trade['Status'] = 'TARGET HIT'
                        active_trade['PnL'] = (active_trade['EntryPrice'] - active_trade['ExitPrice']) * active_trade['Qty']
                        trades.append(active_trade)
                        active_trade = None
                    elif timestamp.time() >= datetime.time(15, 20):
                        active_trade['ExitPrice'] = row['close'] * (1 + slippage_pct/100)
                        active_trade['ExitTime'] = timestamp
                        active_trade['Status'] = 'EOD CLOSED'
                        active_trade['PnL'] = (active_trade['EntryPrice'] - active_trade['ExitPrice']) * active_trade['Qty']
                        trades.append(active_trade)
                        active_trade = None

    elif strategy == "52-Week High Breakout":
        # Multi-day swing strategy. Runs on daily or daily-resampled data
        daily_df = df.copy()
        
        # Calculate 52W High (rolling max high over 250 daily bars)
        daily_df['52W_High'] = daily_df['high'].shift(1).rolling(window=250).max()
        
        # Check alignment price > 20 ema > 50 ema > 200 ema
        daily_df['trending'] = (
            (daily_df['close'] > daily_df['ema_20']) & 
            (daily_df['ema_20'] > daily_df['ema_50']) & 
            (daily_df['ema_50'] > daily_df['ema_200'])
        )
        
        # Calculate 20-day average volume for RVOL check
        daily_df['vol_ma20_daily'] = daily_df['volume'].rolling(window=20).mean().shift(1)
        
        total_rows = len(daily_df)
        active_trade = None
        for idx, (timestamp, row) in enumerate(daily_df.iterrows()):
            if pd.isna(row['52W_High']) or pd.isna(row['vol_ma20_daily']) or pd.isna(row['atr_14']):
                continue
                
            # Show progress
            if progress_callback:
                progress_callback(idx, total_rows, timestamp.date())
            print(f"[{strategy}] Backtesting day {idx}/{total_rows}: {timestamp.date()}...")
            
            # Filter: Check if the stock is trending and close to the 52W High (within 3%) prior to the breakout day
            dist_from_high = (row['52W_High'] - row['close']) / row['close'] * 100
            
            if active_trade is None:
                # 52W High Breakout Exact Conditions:
                # 1. Close > 52W High
                # 2. Trending stack (close > EMA 20 > EMA 50 > EMA 200)
                # 3. RVOL > 2.5 (Volume > 2.5x 20-day daily average volume)
                # 4. ATR_14 % of close >= 1.5% (to ensure juice to move)
                atr_pct = (row['atr_14'] / row['close']) * 100
                rvol = row['volume'] / row['vol_ma20_daily']
                
                if (row['close'] > row['52W_High']) and \
                   row['trending'] and \
                   (rvol > 2.5) and \
                   (atr_pct >= 1.5) and \
                   (dist_from_high <= 3.0):
                    
                    entry_price = row['close'] * (1 + slippage_pct/100)
                    risk = max(1.5 * row['atr_14'], entry_price * 0.05)
                    sl_price = entry_price - risk
                    target_price = entry_price + 2.0 * risk
                    qty = int((capital * (risk_pct/100)) / risk) if risk > 0 else 1
                    
                    active_trade = {
                        'Ticker': ticker,
                        'Type': 'BUY',
                        'EntryTime': timestamp,
                        'EntryPrice': entry_price,
                        'Qty': qty,
                        'SL': sl_price,
                        'Target': target_price,
                        'Status': 'Open'
                    }
            else:
                # Monitor swing trade
                if row['low'] <= active_trade['SL']:
                    active_trade['ExitPrice'] = active_trade['SL'] * (1 - slippage_pct/100)
                    active_trade['ExitTime'] = timestamp
                    active_trade['Status'] = 'SL HIT'
                    active_trade['PnL'] = (active_trade['ExitPrice'] - active_trade['EntryPrice']) * active_trade['Qty']
                    trades.append(active_trade)
                    active_trade = None
                elif row['high'] >= active_trade['Target']:
                    active_trade['ExitPrice'] = active_trade['Target'] * (1 - slippage_pct/100)
                    active_trade['ExitTime'] = timestamp
                    active_trade['Status'] = 'TARGET HIT'
                    active_trade['PnL'] = (active_trade['ExitPrice'] - active_trade['EntryPrice']) * active_trade['Qty']
                    trades.append(active_trade)
                    active_trade = None
                    
        # If still open at end of data
        if active_trade:
            latest_row = daily_df.iloc[-1]
            active_trade['ExitPrice'] = latest_row['close']
            active_trade['ExitTime'] = daily_df.index[-1]
            active_trade['Status'] = 'OPEN POSITION'
            active_trade['PnL'] = (active_trade['ExitPrice'] - active_trade['EntryPrice']) * active_trade['Qty']
            trades.append(active_trade)
            
    elif strategy == "15-Min ORB Breakout (Intraday)":
        for i in range(1, len(days)):
            prev_day = days[i-1]
            curr_day = days[i]
            
            # Show progress
            if progress_callback:
                progress_callback(i, len(days), curr_day)
            print(f"[{strategy}] Backtesting day {i}/{len(days)}: {curr_day}...")
            
            yesterday_data = get_yesterday_indicators(curr_day)
            if yesterday_data is None:
                continue
                
            prev_close = yesterday_data['close']
            prev_day_high = yesterday_data['high']
            prev_day_low = yesterday_data['low']
            daily_ema_20 = yesterday_data['ema_20']
            daily_rsi = yesterday_data['rsi_14']
            
            # Today's data
            curr_df = df[df.index.date == curr_day].copy()
            if len(curr_df) < 5:
                continue
                
            # Early gap and opening checks
            first_candle = curr_df.between_time('09:15', '09:20')
            if first_candle.empty:
                continue
            first_row = first_candle.iloc[0]
            gap_pct = ((first_row['open'] - prev_close) / prev_close) * 100 if prev_close > 0 else 0
            if abs(gap_pct) > 3.0:
                continue
                
            # Calculate 20-period average volume for 5-min candles
            curr_df['Vol_Avg_5'] = curr_df['volume'].rolling(window=20).mean().fillna(curr_df['volume'])
            
            # Find Opening Range (first 3 candles, 9:15 - 9:30)
            orb_df = curr_df.between_time('09:15', '09:30')
            if len(orb_df) < 3:
                continue
            orb_high = orb_df['high'].max()
            orb_low = orb_df['low'].min()
            
            active_trade = None
            has_broken_high = False
            has_broken_low = False
            
            day_candles = curr_df.between_time('09:31', '15:20')
            
            for idx_val, (timestamp, row) in enumerate(day_candles.iterrows()):
                full_idx = curr_df.index.get_loc(timestamp)
                prev_row = curr_df.iloc[full_idx - 1]
                
                # Check cleanliness filter: no prior close outside ORB
                if row['close'] > orb_high:
                    has_broken_high = True
                if row['close'] < orb_low:
                    has_broken_low = True
                
                if active_trade is None:
                    # Common Filters
                    vol_ok = row['volume'] > (row['Vol_Avg_5'] * 1.5)
                    candle_range = row['high'] - row['low']
                    body_size = abs(row['close'] - row['open'])
                    strength_ok = (body_size >= 0.5 * candle_range) if candle_range > 0 else False
                    
                    if not (vol_ok and strength_ok):
                        continue
                        
                    # Consolidation Check (preceding 3 candles range <= 1.2%)
                    if full_idx >= 3:
                        preceding_candles = curr_df.iloc[full_idx-3:full_idx]
                        preceding_low = preceding_candles['low'].min()
                        tight_range = (preceding_candles['high'].max() - preceding_low) / preceding_low * 100 if preceding_low > 0 else 99
                        is_consolidating = tight_range <= 1.20
                    else:
                        is_consolidating = True
                        
                    # Price Filter
                    if not (100 <= row['close'] <= 5000):
                        continue
                        
                    # BULLISH BREAKOUT
                    if row['close'] > orb_high and not has_broken_high:
                        dist_pct = (row['close'] - orb_high) / orb_high * 100
                        if orb_high > prev_day_high and row['close'] > daily_ema_20 and daily_rsi > 55 and row['close'] > row['vwap']:
                            if gap_pct >= -0.5 and 0.05 <= dist_pct <= 1.5 and is_consolidating:
                                entry_price = orb_high if row['low'] <= orb_high else row['close']
                                sl_price = prev_row['low']
                                
                                risk = entry_price - sl_price
                                if risk <= entry_price * 0.002:
                                    sl_price = entry_price * 0.995
                                    risk = entry_price - sl_price
                                    
                                target_price = entry_price + 2.0 * risk
                                qty = int(250000 / entry_price)
                                
                                active_trade = {
                                    'Ticker': ticker,
                                    'Type': 'BUY',
                                    'EntryTime': timestamp,
                                    'EntryPrice': entry_price,
                                    'Qty': qty,
                                    'SL': sl_price,
                                    'InitialSL': sl_price,
                                    'Target': target_price,
                                    'Status': 'Open',
                                    'Trailed': False
                                }
                                has_broken_high = True
                                
                    # BEARISH BREAKOUT
                    elif row['close'] < orb_low and not has_broken_low:
                        dist_pct = (orb_low - row['close']) / orb_low * 100
                        if orb_low < prev_close and row['close'] < daily_ema_20 and daily_rsi < 50 and row['close'] < row['vwap']:
                            if gap_pct <= 0.5 and 0.05 <= dist_pct <= 1.5 and is_consolidating:
                                entry_price = orb_low if row['high'] >= orb_low else row['close']
                                sl_price = prev_row['high']
                                
                                risk = sl_price - entry_price
                                if risk <= entry_price * 0.002:
                                    sl_price = entry_price * 1.005
                                    risk = sl_price - entry_price
                                    
                                target_price = entry_price - 2.0 * risk
                                qty = int(250000 / entry_price)
                                
                                active_trade = {
                                    'Ticker': ticker,
                                    'Type': 'SELL',
                                    'EntryTime': timestamp,
                                    'EntryPrice': entry_price,
                                    'Qty': qty,
                                    'SL': sl_price,
                                    'InitialSL': sl_price,
                                    'Target': target_price,
                                    'Status': 'Open',
                                    'Trailed': False
                                }
                                has_broken_low = True
                else:
                    # Monitor active trade with multi-stage trailing SL
                    initial_sl = active_trade['InitialSL']
                    entry_price = active_trade['EntryPrice']
                    current_sl = active_trade['SL']
                    
                    if active_trade['Type'] == 'BUY':
                        initial_risk = entry_price - initial_sl
                        
                        # Apply multi-stage trailing stop-loss
                        if row['high'] >= entry_price + (2.0 * initial_risk):
                            new_sl = entry_price + (1.0 * initial_risk)
                            if new_sl > current_sl:
                                active_trade['SL'] = new_sl
                        elif row['high'] >= entry_price + (1.5 * initial_risk):
                            new_sl = entry_price + (0.5 * initial_risk)
                            if new_sl > current_sl:
                                active_trade['SL'] = new_sl
                        elif row['high'] >= entry_price + (1.0 * initial_risk):
                            if entry_price > current_sl:
                                active_trade['SL'] = entry_price
                                
                        # Check exit triggers
                        if row['low'] <= active_trade['SL']:
                            active_trade['ExitPrice'] = active_trade['SL'] * (1 - slippage_pct/100)
                            active_trade['ExitTime'] = timestamp
                            active_trade['Status'] = 'SL HIT'
                            active_trade['PnL'] = (active_trade['ExitPrice'] - active_trade['EntryPrice']) * active_trade['Qty']
                            trades.append(active_trade)
                            active_trade = None
                        elif row['high'] >= active_trade['Target']:
                            active_trade['ExitPrice'] = active_trade['Target'] * (1 - slippage_pct/100)
                            active_trade['ExitTime'] = timestamp
                            active_trade['Status'] = 'TARGET HIT'
                            active_trade['PnL'] = (active_trade['ExitPrice'] - active_trade['EntryPrice']) * active_trade['Qty']
                            trades.append(active_trade)
                            active_trade = None
                        elif timestamp.time() >= datetime.time(15, 20):
                            active_trade['ExitPrice'] = row['close'] * (1 - slippage_pct/100)
                            active_trade['ExitTime'] = timestamp
                            active_trade['Status'] = 'EOD CLOSED'
                            active_trade['PnL'] = (active_trade['ExitPrice'] - active_trade['EntryPrice']) * active_trade['Qty']
                            trades.append(active_trade)
                            active_trade = None
                            
                    else: # Bearish / SELL trade
                        initial_risk = initial_sl - entry_price
                        
                        # Apply multi-stage trailing stop-loss
                        if row['low'] <= entry_price - (2.0 * initial_risk):
                            new_sl = entry_price - (1.0 * initial_risk)
                            if new_sl < current_sl:
                                active_trade['SL'] = new_sl
                        elif row['low'] <= entry_price - (1.5 * initial_risk):
                            new_sl = entry_price - (0.5 * initial_risk)
                            if new_sl < current_sl:
                                active_trade['SL'] = new_sl
                        elif row['low'] <= entry_price - (1.0 * initial_risk):
                            if entry_price < current_sl:
                                active_trade['SL'] = entry_price
                                
                        # Check exit triggers
                        if row['high'] >= active_trade['SL']:
                            active_trade['ExitPrice'] = active_trade['SL'] * (1 + slippage_pct/100)
                            active_trade['ExitTime'] = timestamp
                            active_trade['Status'] = 'SL HIT'
                            active_trade['PnL'] = (active_trade['EntryPrice'] - active_trade['ExitPrice']) * active_trade['Qty']
                            trades.append(active_trade)
                            active_trade = None
                        elif row['low'] <= active_trade['Target']:
                            active_trade['ExitPrice'] = active_trade['Target'] * (1 + slippage_pct/100)
                            active_trade['ExitTime'] = timestamp
                            active_trade['Status'] = 'TARGET HIT'
                            active_trade['PnL'] = (active_trade['EntryPrice'] - active_trade['ExitPrice']) * active_trade['Qty']
                            trades.append(active_trade)
                            active_trade = None
                        elif timestamp.time() >= datetime.time(15, 20):
                            active_trade['ExitPrice'] = row['close'] * (1 + slippage_pct/100)
                            active_trade['ExitTime'] = timestamp
                            active_trade['Status'] = 'EOD CLOSED'
                            active_trade['PnL'] = (active_trade['EntryPrice'] - active_trade['ExitPrice']) * active_trade['Qty']
                            trades.append(active_trade)
                            active_trade = None
                            
        # If still open at end of data
        if active_trade:
            latest_row = daily_df.iloc[-1]
            active_trade['ExitPrice'] = latest_row['close']
            active_trade['ExitTime'] = daily_df.index[-1]
            active_trade['Status'] = 'OPEN POSITION'
            active_trade['PnL'] = (active_trade['ExitPrice'] - active_trade['EntryPrice']) * active_trade['Qty']
            trades.append(active_trade)
            
    # Calculate daily equity curve
    trades_df = pd.DataFrame(trades)
    
    # Generate stats
    stats = {}
    if not trades_df.empty:
        stats['Total Trades'] = len(trades_df)
        wins = trades_df[trades_df['PnL'] > 0]
        losses = trades_df[trades_df['PnL'] <= 0]
        stats['Wins'] = len(wins)
        stats['Losses'] = len(losses)
        stats['Win Rate'] = (len(wins) / len(trades_df)) * 100
        stats['Total Profit'] = trades_df['PnL'].sum()
        stats['Profit Factor'] = abs(wins['PnL'].sum() / losses['PnL'].sum()) if not losses.empty and losses['PnL'].sum() != 0 else float('inf')
        
        # Equity Curve calculation
        trades_df['ExitDateOnly'] = pd.to_datetime(trades_df['ExitTime']).dt.date
        daily_pnl = trades_df.groupby('ExitDateOnly')['PnL'].sum().reset_index()
        daily_pnl.sort_values('ExitDateOnly', inplace=True)
        daily_pnl['Cum_PnL'] = daily_pnl['PnL'].cumsum()
        daily_pnl['Equity'] = capital + daily_pnl['Cum_PnL']
        
        # Max Drawdown
        equity_series = daily_pnl['Equity'].tolist()
        peak = capital
        max_dd = 0.0
        for eq in equity_series:
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_dd:
                max_dd = dd
        stats['Max Drawdown'] = max_dd
        stats['Max Drawdown %'] = (max_dd / capital) * 100
        
        # Sharpe Ratio (daily basis)
        if len(daily_pnl) > 1:
            daily_returns = daily_pnl['PnL'] / capital
            mean_ret = daily_returns.mean()
            std_ret = daily_returns.std()
            stats['Sharpe Ratio'] = (mean_ret / std_ret) * np.sqrt(252) if std_ret > 0 else 0.0
        else:
            stats['Sharpe Ratio'] = 0.0
            
        equity_df = daily_pnl
    else:
        stats = {
            'Total Trades': 0, 'Wins': 0, 'Losses': 0, 'Win Rate': 0,
            'Total Profit': 0.0, 'Profit Factor': 0.0, 'Max Drawdown': 0.0,
            'Max Drawdown %': 0.0, 'Sharpe Ratio': 0.0
        }
        equity_df = pd.DataFrame(columns=['ExitDateOnly', 'PnL', 'Cum_PnL', 'Equity'])
        
    return trades_df, equity_df, stats
