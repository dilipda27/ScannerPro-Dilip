import pandas as pd
import numpy as np
import datetime
import os
import yfinance as yf
import logging
from kiteconnect import KiteConnect
from requests.adapters import HTTPAdapter

# Global patch to increase requests connection pool size for multi-threading stability
_original_kite_init = KiteConnect.__init__
def _patched_kite_init(self, *args, **kwargs):
    _original_kite_init(self, *args, **kwargs)
    if hasattr(self, "reqsession"):
        adapter = HTTPAdapter(pool_connections=100, pool_maxsize=100)
        self.reqsession.mount("https://", adapter)
        self.reqsession.mount("http://", adapter)
KiteConnect.__init__ = _patched_kite_init

import config
import concurrent.futures
import paper_trader

BULLISH_CACHE_FILE = os.path.join("data", "cache", "fno_strength_cache.csv")

def calculate_vwap(df):
    """Calculate cumulative intraday VWAP resetting daily."""
    df_calc = df.copy()
    tp = (df_calc['high'] + df_calc['low'] + df_calc['close']) / 3
    tpv = tp * df_calc['volume']
    
    # Intraday VWAP (groups by date to reset calculations at the start of each session)
    dates = df_calc.index.date
    df_calc['tpv'] = tpv
    df_calc['cum_tpv'] = df_calc.groupby(dates)['tpv'].cumsum()
    df_calc['cum_vol'] = df_calc.groupby(dates)['volume'].cumsum()
    df_calc['vwap'] = df_calc['cum_tpv'] / df_calc['cum_vol']
    return df_calc['vwap']

def calculate_ema(series, span=9):
    """Calculate Exponential Moving Average."""
    return series.ewm(span=span, adjust=False).mean()

def calculate_rsi(series, period=14):
    """Calculate Relative Strength Index using Wilder's smoothing method."""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def detect_bullish_reversals(df):
    """Vectorized Bullish Candlestick Pattern detection."""
    high = df['high']
    low = df['low']
    close = df['close']
    open_p = df['open']
    
    body = (close - open_p).abs()
    candle_range = high - low
    upper_shadow = high - df[['open', 'close']].max(axis=1)
    lower_shadow = df[['open', 'close']].min(axis=1) - low
    
    # 1. Hammer: Lower shadow is at least 2x the body, small upper shadow, small body
    is_hammer = (
        (lower_shadow >= 2 * body) & 
        (body <= 0.3 * candle_range) & 
        (upper_shadow <= 0.25 * candle_range) & 
        (candle_range > 0)
    )
    
    # 2. Bullish Engulfing: Current green candle completely engulfs the body of the previous red candle
    prev_close = df['close'].shift(1)
    prev_open = df['open'].shift(1)
    is_green = close > open_p
    prev_red = prev_close < prev_open
    is_bullish_engulfing = (
        is_green & 
        prev_red & 
        (close >= prev_open) & 
        (open_p <= prev_close)
    )
    
    # 3. Bullish Pin Bar (Slightly softer rejection candle definition)
    is_bullish_pinbar = (
        (lower_shadow >= 0.6 * candle_range) & 
        (close > open_p - 0.1 * body) & 
        (candle_range > 0)
    )
    
    return is_hammer, is_bullish_engulfing, is_bullish_pinbar

def generate_synthetic_bullish_setup(ticker, pdc):
    """Generates synthetic intraday data demonstrating a perfect Bullish VWAP Rejection setup."""
    base_time = datetime.datetime.now().replace(hour=9, minute=15, second=0, microsecond=0)
    timestamps = [base_time + datetime.timedelta(minutes=5 * i) for i in range(40)]
    
    price = pdc * 1.01
    prices = []
    volumes = []
    
    for idx in range(40):
        if idx < 10:
            price += np.random.uniform(0.5, 2.0)
            vol = np.random.randint(15000, 30000)
        elif idx == 10:
            price += 5.5
            vol = 45000  # High volume breakout
        elif idx > 10 and idx < 22:
            price -= np.random.uniform(0.2, 1.2)
            vol = np.random.randint(5000, 12000) # Low volume pullback
        elif idx == 22:
            price = pdc * 1.025
            vol = 8000  # Low volume pullback touch
        else:
            price += np.random.uniform(1.0, 3.5)
            vol = np.random.randint(15000, 25000)
            
        prices.append(price)
        volumes.append(vol)
        
    df = pd.DataFrame(index=timestamps)
    df.index.name = 'Date'
    
    opens = []
    highs = []
    lows = []
    closes = []
    
    for idx, close_p in enumerate(prices):
        if idx == 0:
            op = pdc * 1.01
        else:
            op = prices[idx-1]
            
        cl = close_p
        
        if idx == 10:
            hi = max(op, cl) + 1.2
            lo = min(op, cl) - 0.5
        elif idx == 22:
            op = pdc * 1.02
            cl = pdc * 1.022
            hi = pdc * 1.023
            lo = pdc * 1.015  # Rejection Low touches VWAP/EMA
        else:
            hi = max(op, cl) + np.random.uniform(0.1, 1.0)
            lo = min(op, cl) - np.random.uniform(0.1, 1.0)
            
        opens.append(op)
        highs.append(hi)
        lows.append(lo)
        closes.append(cl)
        
    df['open'] = opens
    df['high'] = highs
    df['low'] = lows
    df['close'] = closes
    df['volume'] = volumes
    
    return df

def fetch_stock_data(ticker, token, pdc, use_demo=False, kite=None):
    """Fetches 5-minute data with fallback order: Kite API -> YFinance -> Demo Mock."""
    if use_demo:
        return generate_synthetic_bullish_setup(ticker, pdc)
        
    if kite and token and not pd.isna(token):
        try:
            to_date = datetime.datetime.now()
            from_date = to_date - datetime.timedelta(days=4)
            import kite_scanner
            df = kite_scanner.fetch_kite_data(kite, int(token), from_date, to_date, "5minute")
            if not df.empty:
                df.columns = [c.lower() for c in df.columns]
                return df
        except Exception as e:
            logging.warning(f"Kite fetch failed for {ticker}: {e}")
            
    try:
        ticker_yf = ticker + ".NS"
        df = yf.download(ticker_yf, period="5d", interval="5m", progress=False)
        if not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.columns = [c.lower() for c in df.columns]
            return df
    except Exception as e:
        logging.error(f"YFinance fetch failed for {ticker}: {e}")
        
    return generate_synthetic_bullish_setup(ticker, pdc)

def run_rejection_scanner(df, pdc, yesterday_high=None, nifty_bearish=False):
    """Scans intraday data for Bullish VWAP Rejection signals with strict structural & safety filters."""
    if df.empty or len(df) < 21:
        return df, []
        
    # 1. Filter Today's/Latest session data
    df.index = pd.to_datetime(df.index)
    latest_date = df.index.max().date()
    df_session = df[df.index.date == latest_date].copy()
    
    if len(df_session) < 3:
        unique_dates = sorted(list(set(df.index.date)))
        if len(unique_dates) >= 2:
            latest_date = unique_dates[-1]
            df_session = df[df.index.date == latest_date].copy()
        else:
            return df, []
            
    # 2. Vectorized Indicators
    df_session['vwap'] = calculate_vwap(df_session)
    df_session['ema_9'] = calculate_ema(df_session['close'], span=9)
    df_session['vol_ma20'] = df_session['volume'].rolling(window=20).mean()
    df_session['rsi_5m'] = calculate_rsi(df_session['close'], period=14)
    
    # Calculate 5-minute ATR for dynamic rejection band
    high_low = df_session['high'] - df_session['low']
    high_prev_close = (df_session['high'] - df_session['close'].shift(1)).abs()
    low_prev_close = (df_session['low'] - df_session['close'].shift(1)).abs()
    tr = pd.concat([high_low, high_prev_close, low_prev_close], axis=1).max(axis=1)
    df_session['atr_5m'] = tr.rolling(window=5).mean().fillna(high_low)
    
    # 3. Reversal Candlestick Arrays
    hm, be, pb = detect_bullish_reversals(df_session)
    df_session['is_hammer'] = hm
    df_session['is_bullish_engulfing'] = be
    df_session['is_bullish_pinbar'] = pb
    df_session['has_reversal_pattern'] = hm | be | pb
    
    # 4. Strategy Rule: Trend Conditions
    df_session['vwap_sloping_up'] = (df_session['vwap'] > df_session['vwap'].shift(3)).fillna(True)
    
    df_session['trend_ok'] = (df_session['close'] > df_session['vwap']) & \
                             (df_session['vwap'] > pdc) & \
                             (df_session['vwap_sloping_up'])
    
    # 5. Resistance / VWAP Breakout checks
    or_high = df_session['high'].iloc[0:3].max() if len(df_session) >= 3 else df_session['high'].iloc[0]
    df_session['or_high'] = or_high
    
    df_session['resistance_break'] = df_session['close'] > or_high
    df_session['vwap_break'] = (df_session['close'].shift(1) < df_session['vwap'].shift(1)) & \
                               (df_session['close'] > df_session['vwap']) & \
                               (df_session['volume'] > df_session['vol_ma20'] * 1.2)
                               
    df_session['has_broken_out'] = (df_session['resistance_break'] | df_session['vwap_break']).cumsum() > 0
    
    # 6. Pullback Detection
    atr_buffer = 0.2 * df_session['atr_5m']
    v_pullback = (df_session['low'] <= df_session['vwap'] + atr_buffer) & \
                 (df_session['high'] >= df_session['vwap'] - atr_buffer)
                 
    e_pullback = (df_session['low'] <= df_session['ema_9'] + atr_buffer) & \
                 (df_session['high'] >= df_session['ema_9'] - atr_buffer)
                 
    df_session['pullback_touches'] = v_pullback | e_pullback
    df_session['volume_is_low'] = df_session['volume'] < df_session['vol_ma20']
    
    # 7. ADDED CONVICTION & SAFETY FILTERS
    if yesterday_high is not None:
        df_session['above_pdh'] = df_session['close'] > yesterday_high
    else:
        df_session['above_pdh'] = True
        
    df_session['rsi_ok'] = df_session['rsi_5m'] <= 70
    df_session['day_change_pct'] = (df_session['close'] - pdc) / pdc * 100
    df_session['not_extended'] = df_session['day_change_pct'] < 3.0
    
    df_session['vwap_dist_pct'] = ((df_session['close'] - df_session['vwap']) / df_session['vwap'] * 100).abs()
    df_session['ema_dist_pct'] = ((df_session['close'] - df_session['ema_9']) / df_session['ema_9'] * 100).abs()
    df_session['min_dist_pct'] = df_session[['vwap_dist_pct', 'ema_dist_pct']].min(axis=1)
    df_session['not_chasing'] = df_session['min_dist_pct'] <= 0.4
    
    candle_range = df_session['high'] - df_session['low']
    pct_range = 0.6 if nifty_bearish else 0.5
    df_session['candle_ok'] = (df_session['close'] > (df_session['low'] + pct_range * candle_range)) & (candle_range > 0)
    
    df_session['trigger_signal'] = (
        df_session['trend_ok'] & 
        df_session['has_broken_out'] & 
        df_session['pullback_touches'] & 
        df_session['volume_is_low'] & 
        df_session['has_reversal_pattern'] &
        df_session['above_pdh'] &
        df_session['rsi_ok'] &
        df_session['not_extended'] &
        df_session['not_chasing'] &
        df_session['candle_ok']
    )
    
    alerts = []
    triggered_rows = df_session[df_session['trigger_signal'] == True]
    
    for idx, row in triggered_rows.iterrows():
        pos = df_session.index.get_loc(idx)
        pullback_window = df_session.iloc[max(0, pos-2):pos+1]
        swing_low = pullback_window['low'].min()
        
        entry = row['close']
        sl = swing_low * 0.999
        risk = entry - sl
        
        if risk <= entry * 0.003:
            sl = entry * 0.995
            risk = entry - sl
            
        target_1 = entry + (1.5 * risk)
        target_2 = entry + (3.0 * risk)
        
        pattern_name = "Bullish Engulfing" if row['is_bullish_engulfing'] else \
                       "Hammer" if row['is_hammer'] else "Bullish Pin Bar"
                       
        rejection_zone = "VWAP" if (row['low'] <= row['vwap'] * 1.001) else "9 EMA"
        
        alerts.append({
            'Timestamp': idx,
            'Price': round(entry, 2),
            'Pattern': pattern_name,
            'Zone': rejection_zone,
            'SL': round(sl, 2),
            'Target_1': round(target_1, 2),
            'Target_2': round(target_2, 2),
            'Risk_Reward': '1:1.5 / 1:3',
            'Volume': int(row['volume']),
            'Avg_Volume': int(row['vol_ma20'])
        })
        
    return df_session, alerts

def batch_pre_screen(cache_df):
    """Filters the stock universe to select stocks currently trading in active uptrends."""
    tickers = cache_df['Ticker'].tolist()
    if not tickers:
        return cache_df
        
    try:
        logging.info(f"⚡ Bulk Pre-Screen: Downloading current daily prices for {len(tickers)} tickers...")
        ticker_symbols = [t + ".NS" for t in tickers]
        batch_df = yf.download(ticker_symbols, period="1d", progress=False)
        
        if batch_df.empty:
            return cache_df
            
        if isinstance(batch_df.columns, pd.MultiIndex):
            close_prices = batch_df['Close'].iloc[-1]
        else:
            close_prices = batch_df['Close']
            
        active_candidates = []
        for _, row in cache_df.iterrows():
            ticker = row['Ticker']
            symbol = ticker + ".NS"
            ltp = close_prices.get(symbol)
            if ltp is None or pd.isna(ltp):
                ltp = close_prices.get(ticker, row['Prev_Close'])
                
            pdc = row['Prev_Close']
            if ltp > pdc:
                active_candidates.append(row)
                
        logging.info(f"⚡ Pre-screen complete. Reduced universe to {len(active_candidates)} active uptrend candidates.")
        return pd.DataFrame(active_candidates) if active_candidates else pd.DataFrame()
        
    except Exception as e:
        logging.warning(f"Batch pre-screen failed: {e}. Falling back to full list.")
        return cache_df

def scan_all_tickers_parallel(active_df, progress_callback=None, use_demo=False, kite=None, nifty_bearish=False):
    """Runs technical scans in parallel using ThreadPoolExecutor."""
    triggered_setups = []
    monitored_setups = []
    
    total = len(active_df)
    if total == 0:
        return pd.DataFrame(), pd.DataFrame()
        
    def scan_single(row):
        ticker = row['Ticker']
        token = row.get('Token', None)
        pdc = row['Prev_Close']
        yesterday_high = row.get('Yesterday_High', None)
        
        try:
            df_raw = fetch_stock_data(ticker, token, pdc, use_demo=use_demo, kite=kite)
            if df_raw.empty:
                return None
                
            df_analyzed, alerts = run_rejection_scanner(df_raw, pdc, yesterday_high=yesterday_high, nifty_bearish=nifty_bearish)
            
            latest_price = df_analyzed['close'].iloc[-1] if not df_analyzed.empty else pdc
            vwap = df_analyzed['vwap'].iloc[-1] if not df_analyzed.empty else pdc
            ema_9 = df_analyzed['ema_9'].iloc[-1] if not df_analyzed.empty else pdc
            
            if alerts:
                alert = alerts[-1]
                alert_time = alert['Timestamp']
                latest_time = df_analyzed.index[-1]
                
                if (latest_time - alert_time) <= pd.Timedelta(minutes=15):
                    alert['Ticker'] = ticker
                    alert['Token'] = token
                    alert['LTP'] = latest_price
                    alert['VWAP'] = vwap
                    alert['EMA_9'] = ema_9
                    return ('TRIGGERED', alert)
            
            return ('MONITORING', {
                'Ticker': ticker,
                'Token': token,
                'LTP': round(latest_price, 2),
                'VWAP': round(vwap, 2),
                'EMA_9': round(ema_9, 2),
                'Prev_Close': pdc,
                'Yesterday_High': row['Yesterday_High']
            })
        except Exception as e:
            logging.error(f"Error scanning {ticker} in thread: {e}")
            return None

    processed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(scan_single, row): row['Ticker'] for _, row in active_df.iterrows()}
        
        for future in concurrent.futures.as_completed(futures):
            processed += 1
            if progress_callback:
                progress_callback(processed, total, futures[future])
                
            result = future.result()
            if result:
                status, data = result
                if status == 'TRIGGERED':
                    triggered_setups.append(data)
                elif status == 'MONITORING':
                    monitored_setups.append(data)
                    
    return pd.DataFrame(triggered_setups), pd.DataFrame(monitored_setups)

def scan_bullish_vwap_rejections(kite, use_demo=False):
    """Programmatic entry point to run the bullish VWAP rejection scan."""
    nifty_bearish = False
    try:
        if not use_demo and kite:
            import kite_scanner
            nifty_token_map = kite_scanner.get_kite_instruments(kite, ["NIFTY 50"])
            if nifty_token_map and "NIFTY 50" in nifty_token_map:
                nifty_token = nifty_token_map["NIFTY 50"]
                to_date = datetime.datetime.now()
                nifty_from = to_date.replace(hour=9, minute=15, second=0, microsecond=0)
                if nifty_from > to_date:
                    nifty_from = nifty_from - datetime.timedelta(days=1)
                nifty_df = kite_scanner.fetch_kite_data(kite, nifty_token, nifty_from, to_date, "5minute")
                if not nifty_df.empty:
                    nifty_open = nifty_df.iloc[0]['open']
                    nifty_ltp = nifty_df.iloc[-1]['close']
                    nifty_bearish = nifty_ltp < nifty_open
                    logging.info(f"Broad Market Check -> Nifty Open: {nifty_open:.2f}, LTP: {nifty_ltp:.2f} | Bearish? {nifty_bearish}")
    except Exception as ne:
        logging.warning(f"Failed to fetch Nifty 50 trend: {ne}")

    universe_df = load_universe_data()
    active_candidates = batch_pre_screen(universe_df)
    if active_candidates.empty:
        return pd.DataFrame(), pd.DataFrame()
        
    triggered_df, monitored_df = scan_all_tickers_parallel(
        active_candidates, progress_callback=None, use_demo=use_demo, kite=kite, nifty_bearish=nifty_bearish
    )
    return triggered_df, monitored_df

def load_universe_data():
    if os.path.exists(BULLISH_CACHE_FILE):
        try:
            return pd.read_csv(BULLISH_CACHE_FILE)
        except Exception as e:
            logging.error(f"Error reading cache file: {e}")
    return pd.DataFrame({
        "Ticker": ["ASHOKLEY", "INFY", "RELIANCE", "SBIN", "VEDL", "WIPRO", "HCLTECH", "TATAPOWER"],
        "Token": [54273, 408065, 738561, 779521, 784129, 969473, 1850625, 877057],
        "Prev_Close": [153.13, 1119.0, 1336.4, 963.2, 331.05, 190.0, 1132.6, 407.0],
        "Yesterday_High": [154.5, 1125.0, 1345.0, 970.0, 335.0, 192.0, 1145.0, 410.0]
    })
