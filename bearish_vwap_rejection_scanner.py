import streamlit as st
import pandas as pd
import numpy as np
import datetime
import os
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
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

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BEARISH_CACHE_FILE = "bearish_breakdown_cache.csv"


# --- TECHNICAL CALCULATIONS (VECTORIZED PANDAS MATH) ---
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


def detect_bearish_reversals(df):
    """Vectorized Bearish Candlestick Pattern detection."""
    high = df['high']
    low = df['low']
    close = df['close']
    open_p = df['open']
    
    body = (close - open_p).abs()
    candle_range = high - low
    upper_shadow = high - df[['open', 'close']].max(axis=1)
    lower_shadow = df[['open', 'close']].min(axis=1) - low
    
    # 1. Shooting Star: Upper shadow is at least 2x the body, small lower shadow, small body
    is_shooting_star = (
        (upper_shadow >= 2 * body) & 
        (body <= 0.3 * candle_range) & 
        (lower_shadow <= 0.25 * candle_range) & 
        (candle_range > 0)
    )
    
    # 2. Bearish Engulfing: Current red candle completely engulfs the body of the previous green candle
    prev_close = df['close'].shift(1)
    prev_open = df['open'].shift(1)
    is_red = close < open_p
    prev_green = prev_close > prev_open
    is_bearish_engulfing = (
        is_red & 
        prev_green & 
        (close <= prev_open) & 
        (open_p >= prev_close)
    )
    
    # 3. Bearish Pin Bar (Slightly softer rejection candle definition)
    is_bearish_pinbar = (
        (upper_shadow >= 0.6 * candle_range) & 
        (close < open_p + 0.1 * body) & 
        (candle_range > 0)
    )
    
    return is_shooting_star, is_bearish_engulfing, is_bearish_pinbar

# --- INTENTIONAL SIGNAL SYNTHESIZER (DEMO MODE) ---
def generate_synthetic_bearish_setup(ticker, pdc):
    """Generates synthetic intraday data demonstrating a perfect Bearish VWAP Rejection setup."""
    base_time = datetime.datetime.now().replace(hour=9, minute=15, second=0, microsecond=0)
    timestamps = [base_time + datetime.timedelta(minutes=5 * i) for i in range(40)]
    
    price = pdc * 0.99
    prices = []
    volumes = []
    
    for idx in range(40):
        if idx < 10:
            price -= np.random.uniform(0.5, 2.0)
            vol = np.random.randint(15000, 30000)
        elif idx == 10:
            price -= 5.5
            vol = 45000  # High volume breakdown
        elif idx > 10 and idx < 22:
            price += np.random.uniform(0.2, 1.2)
            vol = np.random.randint(5000, 12000) # Low volume pullback
        elif idx == 22:
            price = 981.5
            vol = 8000  # Low volume pullback touch
        else:
            price -= np.random.uniform(1.0, 3.5)
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
            op = pdc * 0.99
        else:
            op = prices[idx-1]
            
        cl = close_p
        
        if idx == 10:
            hi = max(op, cl) + 0.5
            lo = min(op, cl) - 1.2
        elif idx == 22:
            op = pdc * 0.98
            cl = pdc * 0.978
            hi = pdc * 0.985  # Rejection High touches VWAP/EMA
            lo = pdc * 0.977
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

# --- REAL/FALLBACK DATA FETCHING ---
def get_kite_client():
    """Initializes and returns Kite client if active session is available."""
    try:
        import streamlit as st
        # Safely access session_state in Streamlit context
        if st.runtime.exists() and 'kite_access_token' in st.session_state:
            token = st.session_state.kite_access_token
            if token:
                api_key = getattr(config, 'KITE_API_KEY', '')
                kite = KiteConnect(api_key=api_key)
                kite.set_access_token(token)
                return kite
    except Exception as e:
        logging.error(f"Error initializing Kite client: {e}")
    return None

def fetch_stock_data(ticker, token, pdc, use_demo=False, kite=None):
    """Fetches 5-minute data with fallback order: Kite API -> YFinance -> Demo Mock."""
    if use_demo:
        return generate_synthetic_bearish_setup(ticker, pdc)
        
    # Order 1: Try Kite
    active_kite = kite if kite is not None else get_kite_client()
    if kite and token and not pd.isna(token):
        try:
            to_date = datetime.datetime.now()
            from_date = to_date - datetime.timedelta(days=4)
            import kite_scanner
            df = kite_scanner.fetch_kite_data(active_kite, int(token), from_date, to_date, "5minute")
            if not df.empty:
                df.columns = [c.lower() for c in df.columns]
                return df
        except Exception as e:
            logging.warning(f"Kite fetch failed for {ticker}: {e}")
            
    # Order 2: Try YFinance
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
        
    # Order 3: Mock Fallback if completely offline / failing
    return generate_synthetic_bearish_setup(ticker, pdc)

# --- CORE SCANNING ENGINE ---
def run_rejection_scanner(df, pdc, pullback_threshold=0.0015, yesterday_low=None, nifty_bullish=False):
    """
    Scans intraday data for Bearish VWAP Rejection signals with strict structural & safety filters.
    Returns: df_analyzed, list of alerts
    """
    if df.empty or len(df) < 21:
        return df, []
        
    # 1. Filter Today's/Latest session data
    df.index = pd.to_datetime(df.index)
    latest_date = df.index.max().date()
    df_session = df[df.index.date == latest_date].copy()
    
    if len(df_session) < 3:
        # Fallback to the last two days of data to ensure we see intraday structures
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
    ss, be, pb = detect_bearish_reversals(df_session)
    df_session['is_shooting_star'] = ss
    df_session['is_bearish_engulfing'] = be
    df_session['is_bearish_pinbar'] = pb
    df_session['has_reversal_pattern'] = ss | be | pb
    
    # 4. Strategy Rule: Trend Conditions
    # Ensure VWAP is sloping downwards (strictly less than its value 3 candles / 15 mins ago)
    # Default to True for the first 3 candles of the day so early morning setups aren't blocked.
    df_session['vwap_sloping_down'] = (df_session['vwap'] < df_session['vwap'].shift(3)).fillna(True)
    
    df_session['trend_ok'] = (df_session['close'] < df_session['vwap']) & \
                             (df_session['vwap'] < pdc) & \
                             (df_session['vwap_sloping_down'])
    
    # 5. Support / VWAP Breakdown checks
    # Intraday support: low of first 3 candles (9:15-9:30 range)
    or_low = df_session['low'].iloc[0:3].min() if len(df_session) >= 3 else df_session['low'].iloc[0]
    df_session['or_low'] = or_low
    
    # Support breakdown: candle close below OR Low
    df_session['support_break'] = df_session['close'] < or_low
    
    # Clean VWAP breakdown: Close crosses below VWAP on above-average volume
    df_session['vwap_break'] = (df_session['close'].shift(1) > df_session['vwap'].shift(1)) & \
                               (df_session['close'] < df_session['vwap']) & \
                               (df_session['volume'] > df_session['vol_ma20'] * 1.2)
                               
    # Confirmed breakdown occurred in session before this candle
    df_session['has_broken_down'] = (df_session['support_break'] | df_session['vwap_break']).cumsum() > 0
    
    # 6. Pullback Detection: touched or came within an ATR-based dynamic band of VWAP/EMA
    atr_buffer = 0.2 * df_session['atr_5m']
    v_pullback = (df_session['high'] >= df_session['vwap'] - atr_buffer) & \
                 (df_session['low'] <= df_session['vwap'] + atr_buffer)
                 
    e_pullback = (df_session['high'] >= df_session['ema_9'] - atr_buffer) & \
                 (df_session['low'] <= df_session['ema_9'] + atr_buffer)
                 
    df_session['pullback_touches'] = v_pullback | e_pullback
    
    # Pullback volume must be LOWER than the 20-period moving average
    df_session['volume_is_low'] = df_session['volume'] < df_session['vol_ma20']
    
    # 7. ADDED CONVICTION & SAFETY FILTERS
    # Strict PDL check
    if yesterday_low is not None:
        df_session['below_pdl'] = df_session['close'] < yesterday_low
    else:
        df_session['below_pdl'] = True
        
    # RSI Intraday Oversold Filter
    df_session['rsi_ok'] = df_session['rsi_5m'] >= 30
    
    # Daily Extension Filter (not down > 3.0% already from PDC)
    df_session['day_change_pct'] = (df_session['close'] - pdc) / pdc * 100
    df_session['not_extended'] = df_session['day_change_pct'] > -3.0
    
    # Slippage / No-Chase Filter (within 0.4% of VWAP/EMA rejection zone)
    df_session['vwap_dist_pct'] = ((df_session['vwap'] - df_session['close']) / df_session['vwap'] * 100).abs()
    df_session['ema_dist_pct'] = ((df_session['ema_9'] - df_session['close']) / df_session['ema_9'] * 100).abs()
    df_session['min_dist_pct'] = df_session[['vwap_dist_pct', 'ema_dist_pct']].min(axis=1)
    df_session['not_chasing'] = df_session['min_dist_pct'] <= 0.4
    
    # Candle shape filter (Close in lower 50%, or lower 40% if Nifty is bullish)
    candle_range = df_session['high'] - df_session['low']
    pct_range = 0.6 if nifty_bullish else 0.5
    df_session['candle_ok'] = (df_session['close'] < (df_session['high'] - pct_range * candle_range)) & (candle_range > 0)
    
    # --- SCANNER TRIGGER SIGNAL ---
    # Trigger on the first candle meeting all strategy requirements
    df_session['trigger_signal'] = (
        df_session['trend_ok'] & 
        df_session['has_broken_down'] & 
        df_session['pullback_touches'] & 
        df_session['volume_is_low'] & 
        df_session['has_reversal_pattern'] &
        df_session['below_pdl'] &
        df_session['rsi_ok'] &
        df_session['not_extended'] &
        df_session['not_chasing'] &
        df_session['candle_ok']
    )
    
    # Compile Alerts
    alerts = []
    triggered_rows = df_session[df_session['trigger_signal'] == True]
    
    for idx, row in triggered_rows.iterrows():
        # Get swing high of pullback (max high of last 3 candles)
        pos = df_session.index.get_loc(idx)
        pullback_window = df_session.iloc[max(0, pos-2):pos+1]
        swing_high = pullback_window['high'].max()
        
        # Risk management parameters
        entry = row['close']
        sl = swing_high * 1.001  # 0.1% buffer
        risk = sl - entry
        
        # If risk is extremely tiny or negative due to spike close, set minimum risk of 0.3%
        if risk <= entry * 0.003:
            sl = entry * 1.005
            risk = sl - entry
            
        target_1 = entry - (1.5 * risk)
        target_2 = entry - (3.0 * risk)
        
        pattern_name = "Bearish Engulfing" if row['is_bearish_engulfing'] else \
                       "Shooting Star" if row['is_shooting_star'] else "Bearish Pin Bar"
                       
        rejection_zone = "VWAP" if (row['high'] >= row['vwap'] * 0.999) else "9 EMA"
        
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

# --- BULK SPEED OPTIMIZATIONS (BATCH + MULTI-THREADING) ---

def batch_pre_screen(cache_df):
    """
    Speeds up the scanner significantly by filtering the stock universe in one bulk call.
    Selects stocks currently trading in active downtrends (below Yesterday's Close).
    """
    tickers = cache_df['Ticker'].tolist()
    if not tickers:
        return cache_df
        
    try:
        logging.info(f"⚡ Bulk Pre-Screen: Downloading current daily prices for {len(tickers)} tickers...")
        ticker_symbols = [t + ".NS" for t in tickers]
        # Download 1-day daily close for all tickers in a single batch request
        batch_df = yf.download(ticker_symbols, period="1d", progress=False)
        
        if batch_df.empty:
            return cache_df
            
        # Get latest closed daily price
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
                ltp = close_prices.get(ticker, row['Prev_Close']) # Fallback
                
            # Trend Check: Stock must be trading below Yesterday's Close (PDC)
            pdc = row['Prev_Close']
            if ltp < pdc:
                active_candidates.append(row)
                
        logging.info(f"⚡ Pre-screen complete. Reduced F&O universe from {len(tickers)} to {len(active_candidates)} active downtrend candidates.")
        return pd.DataFrame(active_candidates) if active_candidates else pd.DataFrame()
        
    except Exception as e:
        logging.warning(f"Batch pre-screen failed: {e}. Falling back to full list.")
        return cache_df

def scan_all_tickers_parallel(active_df, pullback_threshold, progress_bar=None, use_demo=False, kite=None, nifty_bullish=False):
    """
    Runs technical scans in parallel using ThreadPoolExecutor for lightning speed.
    """
    triggered_setups = []
    monitored_setups = []
    
    total = len(active_df)
    if total == 0:
        return pd.DataFrame(), pd.DataFrame()
        
    def scan_single(row):
        ticker = row['Ticker']
        token = row.get('Token', None)
        pdc = row['Prev_Close']
        yesterday_low = row.get('Yesterday_Low', None)
        
        try:
            df_raw = fetch_stock_data(ticker, token, pdc, use_demo=use_demo, kite=kite)
            if df_raw.empty:
                return None
                
            df_analyzed, alerts = run_rejection_scanner(df_raw, pdc, pullback_threshold, yesterday_low=yesterday_low, nifty_bullish=nifty_bullish)
            
            latest_price = df_analyzed['close'].iloc[-1] if not df_analyzed.empty else pdc
            vwap = df_analyzed['vwap'].iloc[-1] if not df_analyzed.empty else pdc
            ema_9 = df_analyzed['ema_9'].iloc[-1] if not df_analyzed.empty else pdc
            
            if alerts:
                alert = alerts[-1]
                alert_time = alert['Timestamp']
                latest_time = df_analyzed.index[-1]
                
                # Only consider it a LIVE trigger if it happened within the last 15 minutes
                # (Allows triggering on the current open candle or the immediately previous closed candle)
                if (latest_time - alert_time) <= pd.Timedelta(minutes=15):
                    alert['Ticker'] = ticker
                    alert['Token'] = token
                    alert['LTP'] = latest_price
                    alert['VWAP'] = vwap
                    alert['EMA_9'] = ema_9
                    return ('TRIGGERED', alert)
            else:
                return ('MONITORING', {
                    'Ticker': ticker,
                    'Token': token,
                    'LTP': round(latest_price, 2),
                    'VWAP': round(vwap, 2),
                    'EMA_9': round(ema_9, 2),
                    'Prev_Close': pdc,
                    'Yesterday_Low': row['Yesterday_Low']
                })
        except Exception as e:
            logging.error(f"Error scanning {ticker} in thread: {e}")
            return None

    processed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(scan_single, row): row['Ticker'] for _, row in active_df.iterrows()}
        
        for future in concurrent.futures.as_completed(futures):
            processed += 1
            if progress_bar:
                progress_bar.progress(processed / total)
                
            result = future.result()
            if result:
                status, data = result
                if status == 'TRIGGERED':
                    triggered_setups.append(data)
                elif status == 'MONITORING':
                    monitored_setups.append(data)
                    
    return pd.DataFrame(triggered_setups), pd.DataFrame(monitored_setups)




def scan_bearish_vwap_rejections(kite, pullback_threshold=0.0015, use_demo=False):
    """
    Programmatic entry point to run the bearish VWAP rejection scan.
    Returns: (triggered_df, monitored_df)
    """
    # 1. Broad Market Trend Check (Nifty 50)
    nifty_bullish = False
    try:
        if not use_demo and kite:
            import kite_scanner
            nifty_token_map = kite_scanner.get_kite_instruments(kite, ["NIFTY 50"])
            if nifty_token_map and "NIFTY 50" in nifty_token_map:
                nifty_token = nifty_token_map["NIFTY 50"]
                to_date = datetime.datetime.now()
                nifty_from = to_date.replace(hour=9, minute=15, second=0, microsecond=0)
                nifty_df = kite_scanner.fetch_kite_data(kite, nifty_token, nifty_from, to_date, "5minute")
                if not nifty_df.empty:
                    nifty_open = nifty_df.iloc[0]['open']
                    nifty_ltp = nifty_df.iloc[-1]['close']
                    nifty_bullish = nifty_ltp > nifty_open
                    logging.info(f"Broad Market Check -> Nifty Open: {nifty_open:.2f}, LTP: {nifty_ltp:.2f} | Bullish? {nifty_bullish}")
    except Exception as ne:
        logging.warning(f"Failed to fetch Nifty 50 trend: {ne}")

    # 2. Load universe
    universe_df = None
    if os.path.exists(BEARISH_CACHE_FILE):
        try:
            universe_df = pd.read_csv(BEARISH_CACHE_FILE)
        except Exception as e:
            logging.error(f"Error reading cache file: {e}")
            
    if universe_df is None or universe_df.empty:
        universe_df = pd.DataFrame({
            "Ticker": ["ASHOKLEY", "INFY", "RELIANCE", "SBIN", "VEDL", "WIPRO", "HCLTECH", "TATAPOWER"],
            "Token": [54273, 408065, 738561, 779521, 784129, 969473, 1850625, 877057],
            "Prev_Close": [153.13, 1119.0, 1336.4, 963.2, 331.05, 190.0, 1132.6, 407.0],
            "Yesterday_Low": [152.12, 1101.6, 1329.2, 957.0, 325.0, 188.8, 1121.8, 405.0]
        })
        
    # 3. Pre-screen
    active_candidates = batch_pre_screen(universe_df)
    if active_candidates.empty:
        return pd.DataFrame(), pd.DataFrame()
        
    # 4. Concurrently fetch and scan active candidates
    triggered_df, monitored_df = scan_all_tickers_parallel(
        active_candidates, pullback_threshold, progress_bar=None, use_demo=use_demo, kite=kite, nifty_bullish=nifty_bullish
    )
    return triggered_df, monitored_df

def main():
    # --- PROFESSIONAL UI STYLING & DIRECT CUSTOMIZATIONS ---
    st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
        
        html, body, [class*="css"] {
            font-family: 'Inter', sans-serif;
            background-color: #0f172a;
            color: #cbd5e1;
        }
        
        .main {
            background-color: #0f172a;
        }
        
        /* Metrics Styling */
        div[data-testid="stMetric"] {
            background-color: #1e293b !important;
            padding: 18px 22px !important;
            border-radius: 12px !important;
            border: 1px solid #334155 !important;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06) !important;
        }
        div[data-testid="stMetric"] label {
            color: #94a3b8 !important;
            font-weight: 600 !important;
            font-size: 0.8rem !important;
            text-transform: uppercase !important;
            letter-spacing: 0.5px;
        }
        div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
            color: #f8fafc !important;
            font-size: 1.6rem !important;
            font-weight: 700 !important;
        }
        
        /* Header Bar */
        .header-bar {
            background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
            padding: 20px 25px;
            border-radius: 16px;
            border: 1px solid #334155;
            margin-bottom: 25px;
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3);
        }
        
        /* Dynamic Signal Cards */
        .trigger-card {
            background: linear-gradient(135deg, #7f1d1d 0%, #450a0a 100%);
            border: 1px solid #ef4444;
            border-left: 8px solid #ef4444;
            padding: 24px;
            border-radius: 14px;
            color: #fecaca;
            margin-bottom: 25px;
            box-shadow: 0 10px 20px -5px rgba(239, 68, 68, 0.3);
        }
        .monitoring-card {
            background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
            border: 1px solid #334155;
            border-left: 8px solid #3b82f6;
            padding: 24px;
            border-radius: 14px;
            color: #94a3b8;
            margin-bottom: 25px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }
        
        /* Sidebar Details */
        .sidebar .sidebar-content {
            background-color: #1e293b;
        }
        
        /* Clean tables */
        .stDataFrame {
            border-radius: 12px;
            border: 1px solid #334155;
            background-color: #1e293b;
        }
        </style>
    """, unsafe_allow_html=True)

    # --- MAIN STREAMLIT APPLICATION ---

    # Header Bar
    st.markdown("""
        <div class="header-bar">
            <h2 style='margin:0; font-weight:700; color: #ef4444; display: flex; align-items: center;'>
                📉 Bearish VWAP Rejection Scanner
                <span style='margin-left:15px; font-weight:400; font-size:0.95rem; color: #94a3b8; background: rgba(239, 68, 68, 0.1); padding: 4px 12px; border-radius: 20px;'>
                    Intraday Short-Selling Setup Engine
                </span>
            </h2>
        </div>
    """, unsafe_allow_html=True)

    # Load Ticker Universe from Cache CSV
    @st.cache_data(ttl=300)
    def load_universe():
        if os.path.exists(BEARISH_CACHE_FILE):
            try:
                return pd.read_csv(BEARISH_CACHE_FILE)
            except Exception as e:
                logging.error(f"Error reading cache file: {e}")
        # Return standard default tickers if file missing
        return pd.DataFrame({
            "Ticker": ["ASHOKLEY", "INFY", "RELIANCE", "SBIN", "VEDL", "WIPRO", "HCLTECH", "TATAPOWER"],
            "Token": [54273, 408065, 738561, 779521, 784129, 969473, 1850625, 877057],
            "Prev_Close": [153.13, 1119.0, 1336.4, 963.2, 331.05, 190.0, 1132.6, 407.0],
            "Yesterday_Low": [152.12, 1101.6, 1329.2, 957.0, 325.0, 188.8, 1121.8, 405.0]
        })

    universe_df = load_universe()

    # --- SIDEBAR INTERACTION PANEL ---
    st.sidebar.markdown("### ⚙️ Scanner Control Panel")

    # Operation Mode Selector
    op_mode = st.sidebar.radio("Data Input Mode", ["⚡ Live Market / YFinance", "🎬 Interactive Simulator (Demo)"])
    use_demo = (op_mode == "🎬 Interactive Simulator (Demo)")

    # Pullback Tolerance Selector
    pullback_pct = st.sidebar.slider("Rejection Resistance Buffer (%)", 0.02, 0.30, 0.15, 0.01, 
                                   help="Buffer zone percentage around VWAP or 9 EMA for valid rejection touches.")
    pullback_threshold = pullback_pct / 100.0

    # Capital Deployed per Paper Trade
    capital = st.sidebar.number_input("Risk Capital Per Paper Trade (₹)", value=250000, step=10000)

    # Auto Paper Trade Toggle
    auto_trade = st.sidebar.toggle("🤖 Enable Auto-Paper Trading", value=False,
                                 help="Automatically open paper trades when the scanner triggers bearish setups.")

    # Load tickers list
    tickers = universe_df['Ticker'].tolist()

    # Refresh button
    if st.sidebar.button("🔄 Invalidate Cache & Refresh", type="primary", use_container_width=True):
        st.cache_data.clear()
        st.toast("Refreshed data feeds!", icon="⚡")
        st.rerun()

    # Layout Tabs: Deep-Dive Charting vs Global scanner
    tab1, tab2 = st.tabs(["🎯 Single Ticker Deep Dive", "📡 Real-Time Global Scanner"])

    # --- TAB 1: SINGLE TICKER DEEP DIVE ---
    with tab1:
        selected_ticker = st.selectbox("🎯 Target Ticker Analysis", tickers)

        # Fetch ticker info from cache
        row_info = universe_df[universe_df['Ticker'] == selected_ticker].iloc[0]
        pdc = row_info['Prev_Close']
        token = row_info.get('Token', None)
        yesterday_low = row_info.get('Yesterday_Low', None)

        # Selected stock reference stats
        stat_col1, stat_col2, stat_col3 = st.columns(3)
        stat_col1.markdown(f"**Yesterday's Close (PDC)**: ₹{pdc:,.2f}")
        stat_col2.markdown(f"**Yesterday's Low**: ₹{yesterday_low:,.2f}" if yesterday_low else "**Yesterday's Low**: N/A")
        if 'RSI' in row_info:
            stat_col3.markdown(f"**Daily RSI (Pre-Filtered)**: `{row_info['RSI']}`")

        st.markdown("---")

        # Acquisition & Nifty 50 Trend Check
        nifty_bullish = False
        try:
            if not use_demo:
                kite = get_kite_client()
                if kite:
                    import kite_scanner
                    nifty_token_map = kite_scanner.get_kite_instruments(kite, ["NIFTY 50"])
                    if nifty_token_map and "NIFTY 50" in nifty_token_map:
                        nifty_token = nifty_token_map["NIFTY 50"]
                        to_date = datetime.datetime.now()
                        nifty_from = to_date.replace(hour=9, minute=15, second=0, microsecond=0)
                        nifty_df = kite_scanner.fetch_kite_data(kite, nifty_token, nifty_from, to_date, "5minute")
                        if not nifty_df.empty:
                            nifty_open = nifty_df.iloc[0]['open']
                            nifty_ltp = nifty_df.iloc[-1]['close']
                            nifty_bullish = nifty_ltp > nifty_open
        except Exception as e:
            logging.warning(f"Failed to check Nifty trend in single ticker scan: {e}")

        with st.spinner(f"Acquiring 5-minute ticks for {selected_ticker}..."):
            df_raw = fetch_stock_data(selected_ticker, token, pdc, use_demo=use_demo)

        if df_raw.empty:
            st.error(f"Failed to fetch market data for {selected_ticker}. Please retry.")
        else:
            df_analyzed, alerts = run_rejection_scanner(df_raw, pdc, pullback_threshold, yesterday_low=yesterday_low, nifty_bullish=nifty_bullish)

            # Real-time metrics
            if not df_analyzed.empty:
                latest = df_analyzed.iloc[-1]
                m_col1, m_col2, m_col3, m_col4 = st.columns(4)
                with m_col1:
                    st.metric("Last Traded Price", f"₹{latest['close']:,.2f}", 
                              delta=f"{((latest['close'] - pdc)/pdc * 100):.2f}% vs PDC")
                with m_col2:
                    st.metric("Session VWAP", f"₹{latest['vwap']:,.2f}")
                with m_col3:
                    st.metric("9 Period EMA", f"₹{latest['ema_9']:,.2f}")
                with m_col4:
                    avg_vol = latest['vol_ma20']
                    vol_ratio = latest['volume'] / avg_vol if avg_vol > 0 else 1.0
                    st.metric("Intraday Volume", f"{int(latest['volume']):,}", 
                              delta=f"{vol_ratio:.1f}x of Avg", delta_color="inverse" if vol_ratio < 1.0 else "normal")

            st.markdown("<br>", unsafe_allow_html=True)

            # Trigger Signal Confirmation Card
            if alerts:
                latest_alert = alerts[-1]
                with st.container():
                    st.markdown(f"""
                        <div class="trigger-card">
                            <h3 style='margin-top:0; color:#fecaca;'>🔴 BEARISH PULLBACK SETUP CONFIRMED</h3>
                            <p style='font-size:1.05rem; margin-bottom:15px;'>
                                A high-probability short-selling trigger occurred at <b>{latest_alert['Timestamp'].strftime('%H:%M')}</b>! 
                                A bearish <b>{latest_alert['Pattern']}</b> candlestick formed directly at the <b>{latest_alert['Zone']} Resistance Zone</b> 
                                on lower volume, signaling institutional rejection.
                            </p>
                            <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; background: rgba(0,0,0,0.2); padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                                <div>
                                    <span style="font-size:0.8rem; color:#f87171; text-transform:uppercase; font-weight:600;">ENTRY SHORT</span><br>
                                    <span style="font-size:1.3rem; font-weight:700;">₹{latest_alert['Price']:.2f}</span>
                                </div>
                                <div>
                                    <span style="font-size:0.8rem; color:#f87171; text-transform:uppercase; font-weight:600;">STOP LOSS (SL)</span><br>
                                    <span style="font-size:1.3rem; font-weight:700;">₹{latest_alert['SL']:.2f}</span>
                                </div>
                                <div>
                                    <span style="font-size:0.8rem; color:#34d399; text-transform:uppercase; font-weight:600;">TARGET 1 (1.5R)</span><br>
                                    <span style="font-size:1.3rem; font-weight:700;">₹{latest_alert['Target_1']:.2f}</span>
                                </div>
                                <div>
                                    <span style="font-size:0.8rem; color:#34d399; text-transform:uppercase; font-weight:600;">TARGET 2 (3.0R)</span><br>
                                    <span style="font-size:1.3rem; font-weight:700;">₹{latest_alert['Target_2']:.2f}</span>
                                </div>
                            </div>
                        </div>
                    """, unsafe_allow_html=True)

                    # --- PAPER TRADING INTEGRATION PANEL ---
                    qty = int(capital / latest_alert['Price'])
                    portfolio = paper_trader.get_portfolio()
                    is_active = False
                    if not portfolio.empty:
                        is_active = selected_ticker in portfolio[portfolio['Status'] == 'Active']['Ticker'].values

                    if is_active:
                        st.info(f"⏳ An active short trade is already running for {selected_ticker}!")
                    else:
                        if st.button("⚡ Execute Short Paper Trade", type="primary"):
                            success = paper_trader.execute_paper_trade(
                                ticker=selected_ticker,
                                trade_type="Bearish Pullback",
                                entry_price=latest_alert['Price'],
                                sl=latest_alert['SL'],
                                qty=qty,
                                token=token,
                                strategy="Bearish VWAP Rejection"
                            )
                            if success:
                                st.success(f"🚀 Short trade executed for {selected_ticker}! Quantity: {qty}")
                                st.toast(f"Executed paper trade: {selected_ticker}", icon="🚀")
                                st.rerun()
                            else:
                                st.error("Failed to execute paper trade.")
            else:
                with st.container():
                    st.markdown(f"""
                        <div class="monitoring-card">
                            <h3 style='margin-top:0; color:#93c5fd;'>📡 SCANNING & MONITORING</h3>
                            <p style='margin:0;'>
                                No bearish VWAP or 9 EMA rejection signals triggered yet today for <b>{selected_ticker}</b>. 
                                The trend alignment is being verified, and the scanner is actively waiting for a low-volume pullback touching 
                                the resistance zone.
                            </p>
                        </div>
                    """, unsafe_allow_html=True)

            # Plotly Candlestick Graph
            if not df_analyzed.empty:
                fig = make_subplots(rows=2, cols=1, shared_xaxes=True, 
                                    vertical_spacing=0.08, row_heights=[0.75, 0.25])

                # Candlestick
                fig.add_trace(go.Candlestick(
                    x=df_analyzed.index, open=df_analyzed['open'], high=df_analyzed['high'],
                    low=df_analyzed['low'], close=df_analyzed['close'], name='Candles',
                    increasing_line_color='#10b981', decreasing_line_color='#ef4444',
                    increasing_fillcolor='#10b981', decreasing_fillcolor='#ef4444'
                ), row=1, col=1)

                # VWAP
                fig.add_trace(go.Scatter(
                    x=df_analyzed.index, y=df_analyzed['vwap'],
                    line=dict(color='#eab308', width=2.5), name='VWAP'
                ), row=1, col=1)

                # 9 EMA
                fig.add_trace(go.Scatter(
                    x=df_analyzed.index, y=df_analyzed['ema_9'],
                    line=dict(color='#06b6d4', width=1.5, dash='dash'), name='9 EMA'
                ), row=1, col=1)

                # OR Low
                if 'or_low' in df_analyzed.columns:
                    fig.add_trace(go.Scatter(
                        x=df_analyzed.index, y=df_analyzed['or_low'],
                        line=dict(color='#64748b', width=1.2, dash='dot'), name='OR Support'
                    ), row=1, col=1)

                # Alerts Markers
                if alerts:
                    trigger_times = [a['Timestamp'] for a in alerts]
                    trigger_prices = [a['Price'] for a in alerts]
                    fig.add_trace(go.Scatter(
                        x=trigger_times, y=trigger_prices, mode='markers+text',
                        marker=dict(symbol='triangle-down', size=16, color='#f43f5e', line=dict(width=2, color='white')),
                        text=["  ALERT SHORT" for _ in range(len(alerts))],
                        textposition="top right", textfont=dict(color='#f43f5e', size=11), name='Short Entry'
                    ), row=1, col=1)

                # Volume
                v_colors = ['#10b981' if cl >= op else '#ef4444' for op, cl in zip(df_analyzed['open'], df_analyzed['close'])]
                fig.add_trace(go.Bar(
                    x=df_analyzed.index, y=df_analyzed['volume'], marker_color=v_colors, alpha=0.6, name='Volume'
                ), row=2, col=1)

                # Vol SMA
                fig.add_trace(go.Scatter(
                    x=df_analyzed.index, y=df_analyzed['vol_ma20'], line=dict(color='#f8fafc', width=1.2), name='Vol 20 SMA'
                ), row=2, col=1)

                # Premium Layout
                fig.update_layout(
                    height=550, plot_bgcolor='#0f172a', paper_bgcolor='#0f172a',
                    legend=dict(font=dict(color='#cbd5e1'), bgcolor='rgba(15,23,42,0.8)'),
                    xaxis=dict(gridcolor='#1e293b', rangeslider=dict(visible=False)),
                    yaxis=dict(gridcolor='#1e293b', tickprefix='₹', font=dict(color='#cbd5e1')),
                    xaxis2=dict(gridcolor='#1e293b'), yaxis2=dict(gridcolor='#1e293b', font=dict(color='#cbd5e1')),
                    margin=dict(l=10, r=10, t=10, b=10)
                )
                st.plotly_chart(fig, use_container_width=True)

            # Candle Data Log
            st.subheader("📋 Session Candle Log & Safety Filter Diagnostics")
            log_df = df_analyzed.copy()
            log_df['open'] = log_df['open'].round(2)
            log_df['high'] = log_df['high'].round(2)
            log_df['low'] = log_df['low'].round(2)
            log_df['close'] = log_df['close'].round(2)
            log_df['vwap'] = log_df['vwap'].round(2)
            log_df['ema_9'] = log_df['ema_9'].round(2)
            log_df['vol_ma20'] = log_df['vol_ma20'].fillna(0).round(0)
            log_df['rsi_5m'] = log_df['rsi_5m'].fillna(50).round(1)

            cols_to_show = ['open', 'close', 'vwap', 'ema_9', 'rsi_5m',
                            'below_pdl', 'rsi_ok', 'not_extended', 'not_chasing', 'candle_ok', 'trigger_signal']

            display_log = log_df[cols_to_show].sort_index(ascending=False)

            def style_rows(row):
                styles = [''] * len(row)
                if row['trigger_signal'] == True:
                    return ['background-color: rgba(239, 68, 68, 0.25); color: #fecaca; font-weight: bold'] * len(row)
                
                # Check specific filters and highlight failures in red, passes in green
                for col in ['below_pdl', 'rsi_ok', 'not_extended', 'not_chasing', 'candle_ok']:
                    idx = cols_to_show.index(col)
                    if row[col] == True:
                        styles[idx] = 'background-color: rgba(16, 185, 129, 0.15); color: #10b981; font-weight: bold;'
                    else:
                        styles[idx] = 'background-color: rgba(239, 68, 68, 0.1); color: #f43f5e;'
                return styles

            st.dataframe(display_log.style.apply(style_rows, axis=1), use_container_width=True)


    # --- TAB 2: REAL-TIME GLOBAL SCANNER ---
    with tab2:
        st.markdown("### 📡 Real-Time Bulk Rejection Engine")
        st.markdown("Scans the pre-filtered weak stocks universe concurrently using multi-threaded batch pipelines.")

        # Active Universe pre-screen details
        cache_count = len(universe_df)
        st.info(f"📁 Pre-Filtered Weak Stock Universe Size: **{cache_count} stocks**")

        if st.button("🚀 Run Lightning Setup Scan", type="primary", use_container_width=True):
            # 1. Speed Pre-Screen
            active_candidates = batch_pre_screen(universe_df)

            if active_candidates.empty:
                st.warning("All stocks in the cache are currently trading stronger than yesterday's close. No bearish candidates found.")
            else:
                # 2. Concurrently fetch and scan active candidates
                st.markdown("#### Scanning Candidates in Parallel...")
                p_bar = st.progress(0.0)

                triggered_df, monitored_df = scan_all_tickers_parallel(
                    active_candidates, pullback_threshold, progress_bar=p_bar, use_demo=use_demo
                )
                p_bar.empty()

                # --- AUTO TRADE RUNNER ---
                if auto_trade and not triggered_df.empty:
                    portfolio = paper_trader.get_portfolio()
                    for _, setup in triggered_df.iterrows():
                        ticker = setup['Ticker']
                        is_active = False
                        if not portfolio.empty:
                            is_active = ticker in portfolio[portfolio['Status'] == 'Active']['Ticker'].values

                        if not is_active:
                            qty = int(capital / setup['Price'])
                            paper_trader.execute_paper_trade(
                                ticker=ticker,
                                trade_type="Bearish Pullback",
                                entry_price=setup['Price'],
                                sl=setup['SL'],
                                qty=qty,
                                token=setup.get('Token', None),
                                strategy="Bearish VWAP Rejection"
                            )
                            st.toast(f"🤖 Auto-Trade Executed: {ticker} (Qty: {qty})", icon="🤖")

                # Display Triggered setups
                st.success(f"Scan Complete! Found **{len(triggered_df)} rejections** and **{len(monitored_df)} monitors**.")

                st.markdown("#### 🚨 Triggered Bearish Rejections (Entry Zones)")
                if not triggered_df.empty:
                    st.dataframe(
                        triggered_df[['Ticker', 'Price', 'Pattern', 'Zone', 'SL', 'Target_1', 'Target_2', 'Risk_Reward']],
                        use_container_width=True
                    )

                    # Bulk trades execute panel
                    st.markdown("##### Quick Trade Actions")
                    action_cols = st.columns(len(triggered_df))
                    for idx, setup in triggered_df.iterrows():
                        ticker = setup['Ticker']
                        with action_cols[idx % len(action_cols)]:
                            portfolio = paper_trader.get_portfolio()
                            is_active = False
                            if not portfolio.empty:
                                is_active = ticker in portfolio[portfolio['Status'] == 'Active']['Ticker'].values

                            if is_active:
                                st.caption(f"✅ {ticker} Active")
                            else:
                                if st.button(f"⚡ Short {ticker}", key=f"short_{ticker}"):
                                    qty = int(capital / setup['Price'])
                                    paper_trader.execute_paper_trade(
                                        ticker=ticker,
                                        trade_type="Bearish Pullback",
                                        entry_price=setup['Price'],
                                        sl=setup['SL'],
                                        qty=qty,
                                        token=setup.get('Token', None),
                                        strategy="Bearish VWAP Rejection"
                                    )
                                    st.success(f"Executed short for {ticker}!")
                                    st.toast(f"Executed paper trade: {ticker}", icon="🚀")
                                    st.rerun()
                else:
                    st.info("No stocks have triggered rejections at this exact moment. Watch the monitoring candidates below.")

                st.markdown("#### 📡 Actively Monitored Stocks (Down Trend)")
                if not monitored_df.empty:
                    st.dataframe(
                        monitored_df[['Ticker', 'LTP', 'VWAP', 'EMA_9', 'Prev_Close', 'Yesterday_Low']],
                        use_container_width=True
                    )

        # --- POSITION TRACKING ---
        st.markdown("---")
        st.subheader("📦 Open Intraday Paper Trade Positions")
        try:
            kite = get_kite_client()
            if kite:
                portfolio_df = paper_trader.update_portfolio_pnl(kite)
            else:
                portfolio_df = paper_trader.get_portfolio()
                if not portfolio_df.empty:
                    # static calculation fallback
                    tickers_list = portfolio_df[portfolio_df['Status'] == 'Active']['Ticker'].tolist()
                    if tickers_list:
                        ticker_symbols = [t + ".NS" for t in tickers_list]
                        quotes = yf.download(ticker_symbols, period="1d", progress=False)
                        if not quotes.empty:
                            if isinstance(quotes.columns, pd.MultiIndex):
                                prices_map = quotes['Close'].iloc[-1].to_dict()
                            else:
                                prices_map = quotes['Close'].to_dict()

                            for idx, row in portfolio_df.iterrows():
                                if row['Status'] == 'Active':
                                    symbol = row['Ticker'] + ".NS"
                                    ltp = prices_map.get(symbol, row['EntryPrice'])
                                    portfolio_df.at[idx, 'Current Price'] = ltp
                                    if "Bullish" in str(row['Type']):
                                        pnl = (ltp - row['EntryPrice']) * row['Qty']
                                    else:
                                        pnl = (row['EntryPrice'] - ltp) * row['Qty']
                                    portfolio_df.at[idx, 'Live P&L'] = pnl
                                    portfolio_df.at[idx, 'Net P&L'] = pnl - row.get('Est. Charges', 0)

            if not portfolio_df.empty:
                active_only = portfolio_df[portfolio_df['Status'] == 'Active']
                if not active_only.empty:
                    def style_pnl(val):
                        try:
                            val = float(val)
                            return f'color: {"#10b981" if val >= 0 else "#ef4444"}; font-weight: bold'
                        except:
                            return ''

                    st.dataframe(
                        active_only[["Ticker", "Type", "EntryPrice", "Current Price", "Qty", "SL", "Live P&L"]].style.format({
                            "EntryPrice": "₹{:.2f}",
                            "Current Price": "₹{:.2f}",
                            "SL": "₹{:.2f}",
                            "Live P&L": "₹{:.2f}"
                        }).map(style_pnl, subset=['Live P&L']),
                        use_container_width=True
                    )

                    # Position Exit selector
                    ex_col1, ex_col2 = st.columns([1, 4])
                    with ex_col1:
                        exit_ticker = st.selectbox("Select Active Position to Close", active_only['Ticker'].tolist())
                        if st.button("🚪 Close Selected Trade", type="secondary", use_container_width=True):
                            price_now = active_only[active_only['Ticker'] == exit_ticker]['Current Price'].values[0]
                            paper_trader.exit_trade(exit_ticker, kite, override_price=price_now)
                            st.success(f"Position closed for {exit_ticker}!")
                            st.toast(f"Closed trade: {exit_ticker}", icon="🚪")
                            st.rerun()
                else:
                    st.info("No active paper trades found.")
            else:
                st.info("No paper trades found.")
        except Exception as e:
            logging.error(f"Error displaying portfolio: {e}")


if __name__ == "__main__":
    main()
