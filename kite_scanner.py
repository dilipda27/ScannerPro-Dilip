import pandas as pd
import pandas_ta as ta
import datetime
import time
import logging
import requests
import io
import os
import json
import threading
from kiteconnect import KiteConnect
from requests.adapters import HTTPAdapter

# Global patch to increase requests connection pool size for multi-threading stability
_original_kite_init = KiteConnect.__init__
def _patched_kite_init(self, *args, **kwargs):
    _original_kite_init(self, *args, **kwargs)
    self.timeout = 15
    if hasattr(self, "reqsession"):
        adapter = HTTPAdapter(pool_connections=100, pool_maxsize=100)
        self.reqsession.mount("https://", adapter)
        self.reqsession.mount("http://", adapter)
KiteConnect.__init__ = _patched_kite_init

# --- THREAD-SAFE RATE LIMITER FOR KITE API ---
# Kite API allows 3 requests per second.
_kite_rate_limit_lock = threading.Lock()
_last_kite_request_time = 0.0
KITE_REQ_GAP = 0.35 # 0.35s gap ensures max 2.8 requests per second across all threads

def enforce_kite_rate_limit():
    global _last_kite_request_time
    with _kite_rate_limit_lock:
        current_time = time.time()
        elapsed = current_time - _last_kite_request_time
        if elapsed < KITE_REQ_GAP:
            time.sleep(KITE_REQ_GAP - elapsed)
        _last_kite_request_time = time.time()

ORB_CACHE_FILE = os.path.join("data", "cache", "orb_trending_cache.csv")

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_nifty500_symbols():
    """Fetch Nifty 500 list from NSE."""
    import os
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    url_500 = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
    cache_500 = os.path.join("data", "cache", "nifty500_local_cache.csv")
    nifty500_symbols = []
    try:
        r_500 = requests.get(url_500, headers=headers, timeout=10)
        r_500.raise_for_status()
        text_500 = r_500.text
        # Save to cache
        with open(cache_500, "w", encoding="utf-8") as f:
            f.write(text_500)
        df_500 = pd.read_csv(io.StringIO(text_500))
        nifty500_symbols = list(df_500['Symbol'].str.strip())
        return nifty500_symbols
    except Exception as e:
        logging.error(f"Error fetching Nifty 500 from NSE: {e}. Trying local cache...")
        if os.path.exists(cache_500):
            try:
                df_500 = pd.read_csv(cache_500)
                nifty500_symbols = list(df_500['Symbol'].str.strip())
                logging.info("Loaded Nifty 500 from local cache.")
                return nifty500_symbols
            except Exception as ce:
                logging.error(f"Failed to read Nifty 500 cache: {ce}")
        
        # Absolute fallback list
        return [
            "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "BHARTIARTL", "SBI", "LICI",
            "ITC", "HINDUNILVR", "LT", "BAJFINANCE", "HCLTECH", "MARUTI", "SUNPHARMA",
            "ADANIENT", "KOTAKBANK", "AXISBANK", "TITAN", "ULTRACEMCO", "NTPC", "TATAMOTORS",
            "ONGC", "POWERGRID", "ASIANPAINT", "COALINDIA", "JSWSTEEL", "M&M", "TRENT",
            "NESTLEIND", "TATACHEM", "HINDALCO", "BPCL", "GRASIM", "WIPRO", "TECHM",
            "HDFCLIFE", "SBILIFE", "DRREDDY", "IOC", "CIPLA", "EICHERMOT", "DIVISLAB",
            "INDUSINDBK", "SBICARD", "MUTHOOTFIN", "APOLLOHOSP", "HEROMOTOCO", "SHRIRAMFIN"
        ]

def get_nifty500_fno_symbols():
    """Fetch Nifty 500 stocks and filter for those in the FNO segment."""
    import os
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    nifty500_symbols = set(get_nifty500_symbols())
    cache_fno = os.path.join("data", "cache", "fo_mktlots_local_cache.csv")
    
    url_fno = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"
    fno_symbols = set()
    try:
        r_fno = requests.get(url_fno, headers=headers, timeout=10)
        r_fno.raise_for_status()
        text_fno = r_fno.text
        # Save to cache
        with open(cache_fno, "w", encoding="utf-8") as f:
            f.write(text_fno)
        for line in text_fno.split('\n'):
            parts = line.split(',')
            if len(parts) > 2:
                sym = parts[1].strip()
                if sym and sym != "SYMBOL":
                    fno_symbols.add(sym)
    except Exception as e:
        logging.error(f"Error fetching FNO list from NSE: {e}. Trying local cache...")
        loaded_fno_cache = False
        if os.path.exists(cache_fno):
            try:
                with open(cache_fno, "r", encoding="utf-8") as f:
                    for line in f.read().split('\n'):
                        parts = line.split(',')
                        if len(parts) > 2:
                            sym = parts[1].strip()
                            if sym and sym != "SYMBOL":
                                fno_symbols.add(sym)
                if fno_symbols:
                    logging.info("Loaded FNO list from local cache.")
                    loaded_fno_cache = True
            except Exception as ce:
                logging.error(f"Failed to read FNO cache: {ce}")
                
        if not loaded_fno_cache:
            # Parse FNO list from local kite_instruments_nfo.csv if available
            kite_inst_file = os.path.join("data", "cache", "kite_instruments_nfo.csv")
            if os.path.exists(kite_inst_file):
                try:
                    df_inst = pd.read_csv(kite_inst_file)
                    nfo_fno_syms = df_inst[df_inst['segment'] == 'NFO-FUT']['name'].dropna().unique()
                    for sym in nfo_fno_syms:
                        fno_symbols.add(sym.strip())
                    if fno_symbols:
                        logging.info(f"Loaded {len(fno_symbols)} FNO symbols from {kite_inst_file}")
                except Exception as ie:
                    logging.error(f"Failed to load FNO symbols from kite instruments: {ie}")
                    
        if not fno_symbols:
            fno_symbols = nifty500_symbols
            
    final_symbols = nifty500_symbols.intersection(fno_symbols)
    return sorted(list(final_symbols))

_instruments_cache = None
_instruments_cache_lock = threading.Lock()

def get_kite_instruments(kite, symbols):
    """
    Fetch all NSE instruments from Kite and filter out those that are in the symbols list.
    Uses a global thread-safe cache to avoid repeating a massive 15MB download.
    Returns a dict mapping trading symbol to instrument_token.
    """
    # Quick bypass for Nifty 50 lookup to avoid massive 15MB download during real-time scans
    if len(symbols) == 1 and symbols[0] == "NIFTY 50":
        return {"NIFTY 50": 256265}
        
    global _instruments_cache
    try:
        with _instruments_cache_lock:
            if _instruments_cache is None:
                logging.info("Downloading NSE instruments list from Kite (this may take a few seconds)...")
                _instruments_cache = kite.instruments("NSE")
            instruments = _instruments_cache
            
        df_instruments = pd.DataFrame(instruments)
        
        # Filter instruments for our required symbols
        df_filtered = df_instruments[df_instruments['tradingsymbol'].isin(symbols)]
        
        # Create a mapping of tradingsymbol -> instrument_token
        token_map = dict(zip(df_filtered['tradingsymbol'], df_filtered['instrument_token']))
        return token_map
    except Exception as e:
        logging.error(f"Error fetching instruments from Kite: {e}")
        return {}


def fetch_ohlc_safe(kite, tickers, chunk_size=200, retries=2):
    """
    Fetch OHLC quotes from Kite API in chunks to prevent large URL request errors
    or 502 Bad Gateway errors. Automatically retries on network/gateway errors.
    """
    quotes = {}
    if not tickers:
        return quotes
        
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i+chunk_size]
        success = False
        attempt_err = None
        
        for attempt in range(retries):
            try:
                enforce_kite_rate_limit()
                res = kite.ohlc(chunk)
                if res:
                    quotes.update(res)
                success = True
                break
            except Exception as e:
                attempt_err = e
                error_str = str(e).lower()
                display_error = str(e)
                if "<html>" in error_str or "cloudflare" in error_str or "502" in error_str or "bad gateway" in error_str:
                    display_error = "502 Bad Gateway (Cloudflare/Kite)"
                elif "503" in error_str:
                    display_error = "503 Service Unavailable"
                elif "504" in error_str:
                    display_error = "504 Gateway Timeout"
                
                logging.warning(f"Attempt {attempt+1}/{retries} failed for OHLC chunk starting at {i}: {display_error}")
                time.sleep(1.5 * (attempt + 1))
                
        if not success:
            # If a chunk fails after retries, try sub-chunking it into chunks of 50 to see if smaller size passes
            logging.info(f"Retrying chunk starting at {i} with smaller sub-chunks...")
            for j in range(0, len(chunk), 50):
                sub_chunk = chunk[j:j+50]
                for attempt in range(retries):
                    try:
                        enforce_kite_rate_limit()
                        res = kite.ohlc(sub_chunk)
                        if res:
                            quotes.update(res)
                        break
                    except Exception as sub_e:
                        if attempt == retries - 1:
                            logging.error(f"Sub-chunk starting at {i+j} failed permanently: {sub_e}")
                            raise sub_e
                        time.sleep(1)
    return quotes

CACHE_DIR = os.path.join("data", "cache", "kite_historical_cache")


def fetch_kite_data(kite, instrument_token, from_date, to_date, interval, retries=2):
    """
    Fetch historical data from Kite with rate limit handling and retry logic for network stability.
    Kite limit is typically 3 requests per second.
    Uses a local file cache for daily data to avoid repeated API calls.
    """
    if interval == "day":
        cache_subdir = os.path.join(CACHE_DIR, "day")
        os.makedirs(cache_subdir, exist_ok=True)
        cache_file = os.path.join(cache_subdir, f"{instrument_token}.csv")
        
        # Check if cache exists and was modified today
        if os.path.exists(cache_file):
            file_time = datetime.datetime.fromtimestamp(os.path.getmtime(cache_file)).date()
            if file_time == datetime.date.today():
                try:
                    df = pd.read_csv(cache_file)
                    if not df.empty:
                        df['date'] = pd.to_datetime(df['date'])
                        df.set_index('date', inplace=True)
                        return df
                except Exception as cache_err:
                    logging.warning(f"Failed to read daily cache for {instrument_token}: {cache_err}")
                    
    for attempt in range(retries):
        try:
            enforce_kite_rate_limit()
            data = kite.historical_data(
                instrument_token=instrument_token,
                from_date=from_date,
                to_date=to_date,
                interval=interval,
                continuous=False,
                oi=False
            )
            
            if data:
                df = pd.DataFrame(data)
                df['date'] = pd.to_datetime(df['date'])
                
                # Save daily data to cache
                if interval == "day":
                    try:
                        df.to_csv(cache_file, index=False)
                    except Exception as save_err:
                        logging.warning(f"Failed to save daily cache for {instrument_token}: {save_err}")
                        
                df.set_index('date', inplace=True)
                return df
            return pd.DataFrame()
        except Exception as e:
            error_str = str(e).lower()
            
            # Clean up the error message if it contains HTML (Cloudflare / 5xx responses) to prevent console pollution
            display_error = str(e)
            if "<html>" in error_str or "cloudflare" in error_str or "502" in error_str or "bad gateway" in error_str:
                display_error = "[HTML Error Page (502 Bad Gateway / Cloudflare)]"
                if "502" in error_str:
                    display_error = "502 Bad Gateway (Cloudflare)"
                elif "503" in error_str:
                    display_error = "503 Service Unavailable (Cloudflare)"
                elif "504" in error_str:
                    display_error = "504 Gateway Timeout (Cloudflare)"
            
            # Expanded network error detection including gateway/server errors (5xx)
            is_network_error = any(keyword in error_str for keyword in [
                "failed to resolve", "timeout", "connection", "disconnected", 
                "network", "stream", "protocol", "ssl", "dns", "502", "503", "504",
                "bad gateway", "service unavailable", "gateway timeout", "cloudflare"
            ])
            
            # Handle Rate Limiting (429) specifically if it appears
            if "429" in error_str or "too many requests" in error_str:
                logging.warning(f"Rate limit hit for token {instrument_token}. Cooling down for 5s...")
                time.sleep(5)
                continue

            if is_network_error and attempt < retries - 1:
                # Exponential backoff with a bit of jitter: 3, 6, 12, 24...
                wait_time = (2 ** (attempt + 1)) + (attempt * 2) 
                logging.warning(f"Network / Gateway error for token {instrument_token} (Attempt {attempt+1}/{retries}). Retrying in {wait_time}s... Error: {display_error}")
                time.sleep(wait_time)
                continue
                
            logging.error(f"Error fetching data for token {instrument_token}: {display_error}")
            return pd.DataFrame()
    return pd.DataFrame()


def scan_315_setups(kite, progress_callback=None):
    """
    Core scanning logic for 3:15 PM setup.
    """
    logging.info("Starting 3:15 PM Nifty 500 Scan...")
    
    symbols = get_nifty500_symbols()
    token_map = get_kite_instruments(kite, symbols)
    
    if not token_map:
        logging.error("Failed to retrieve instrument tokens. Aborting scan.")
        return pd.DataFrame()
        
    results = []
    
    # Timeframes
    to_date = datetime.datetime.now()
    from_date_daily = to_date - datetime.timedelta(days=300) # Fetch 300 days to ensure enough data for 200 EMA
    from_date_intraday = to_date - datetime.timedelta(days=5) # Fetch last 5 days to handle weekends/holidays
    
    total_symbols = len(token_map)
    processed = 0
    
    if progress_callback:
        progress_callback(0, total_symbols, "Initializing Batch Pre-screen...")
            
    # --- STAGE 1: BATCH OHLC PRE-SCREEN (Speed Boost) ---
    logging.info(f"Pre-screening {total_symbols} stocks using batch OHLC...")
    try:
        all_tickers = [f"NSE:{s}" for s in token_map.keys()]
        # Fetch OHLC for all stocks in one go (chunked safely to avoid 502 Bad Gateway)
        ohlc_dict = fetch_ohlc_safe(kite, all_tickers)
        
        filtered_tokens = {}
        for s, t in token_map.items():
            q = ohlc_dict.get(f"NSE:{s}")
            if q:
                ltp = q['last_price']
                o = q['ohlc']['open']
                h = q['ohlc']['high']
                # 3:15 PM Criteria: Positive day AND near day high
                if (100 <= ltp <= 5000) and (ltp > o) and (ltp >= h * 0.99):
                    filtered_tokens[s] = t
        
        token_map = filtered_tokens
        total_symbols = len(token_map)
        logging.info(f"Batch pre-screen complete. Reduced {len(all_tickers)} to {total_symbols} candidates.")
        if total_symbols == 0:
            return pd.DataFrame()
            
    except Exception as e:
        logging.warning(f"Batch pre-screen failed, falling back to full scan: {e}")

    # --- STAGE 2: PROCESS CANDIDATES ---
    for symbol, token in token_map.items():
        processed += 1
        if processed % 50 == 0:
            logging.info(f"Processed {processed}/{total_symbols} stocks...")
            
        if progress_callback:
            progress_callback(processed, total_symbols, symbol)
            
        # 1. Fetch Daily Data
        df_daily = fetch_kite_data(kite, token, from_date_daily, to_date, "day")
        if df_daily.empty or len(df_daily) < 200:
            continue
            
        # Calculate Daily Indicators
        df_daily.ta.ema(length=50, append=True)
        df_daily.ta.ema(length=200, append=True)
        df_daily.ta.rsi(length=14, append=True)
        df_daily['Vol_SMA_20'] = df_daily['volume'].rolling(window=20).mean()
        
        # Get latest daily metrics (which includes today's partial data if fetched today)
        latest_daily = df_daily.iloc[-1]
        ltp_now = latest_daily['close']
        
        # Price Filter: Avoid penny stocks or illiquid heavyweights
        if not (100 <= ltp_now <= 5000):
            continue
        
        # Condition 1: Trend Filter (Daily)
        if latest_daily['close'] <= latest_daily['EMA_50'] or latest_daily['close'] <= latest_daily['EMA_200']:
            continue
            
        # Condition 2: Momentum Filter (Daily)
        rsi = latest_daily['RSI_14']
        if pd.isna(rsi) or not (60 <= rsi <= 80):
            continue
            
        # 2. Fetch Intraday Data (5-minute)
        df_intra = fetch_kite_data(kite, token, from_date_intraday, to_date, "5minute")
        if df_intra.empty:
            continue
            
        # Handle weekends/holidays by only looking at the last available trading day's intraday data
        last_trading_date = df_intra.index[-1].date()
        df_intra = df_intra[df_intra.index.date == last_trading_date]
        
        if df_intra.empty:
            continue
            
        # Ensure we are looking at data up to 3:15 PM roughly
        latest_intra = df_intra.iloc[-1]
        ltp = latest_intra['close']
        
        # Calculate Today's Day High (from intraday data)
        day_high = df_intra['high'].max()
        
        # Condition 3: Volume Anomaly
        # Today's total volume so far
        today_volume = df_intra['volume'].sum()
        avg_vol_20 = latest_daily['Vol_SMA_20']
        
        # Avoid division by zero
        if pd.isna(avg_vol_20) or avg_vol_20 == 0:
            continue
            
        vol_spike_ratio = today_volume / avg_vol_20
        if vol_spike_ratio <= 1.5:
            continue
            
        # Condition 4: Closing Conviction (Intraday)
        # LTP must be within 2% of the day's High
        if ltp < (day_high * 0.98):
            continue
            
        # All conditions met! Calculate Risk Management
        entry_price = ltp
        target_price = entry_price * 1.09 # Approx 9% target (middle of 8-10%)
        
        # SL: 4% below entry or previous day's low
        sl_fixed = entry_price * 0.96
        prev_day_low = df_daily.iloc[-2]['low']
        prev_close = df_daily.iloc[-2]['close']
        stop_loss = max(sl_fixed, prev_day_low) # "whichever is closer" means the higher value of the two below the price
        
        # Calculate % Gain for the day
        percent_gain = ((entry_price - prev_close) / prev_close) * 100
        
        results.append({
            "Ticker": symbol,
            "Token": token,
            "LTP": round(entry_price, 2),
            "% Gain": round(percent_gain, 2),
            "Day High": round(day_high, 2),
            "RSI (Daily)": round(rsi, 2),
            "Volume Spike Ratio": round(vol_spike_ratio, 2),
            "Target": round(target_price, 2),
            "Stop Loss": round(stop_loss, 2),
            "Price History": df_daily['close'].tail(20).tolist()
        })

    logging.info(f"Scan complete. Found {len(results)} candidates.")
    return pd.DataFrame(results)

def cache_orb_stocks(kite, progress_callback=None, refresh_shortlist_only=False):
    """
    Pre-Market/Early Morning Caching for ORB.
    Shortlists stocks based on Daily Trend and Momentum.
    If refresh_shortlist_only is True, it only updates the existing cached stocks.
    """
    logging.info("Starting Morning Caching for 15-Min ORB Scanner...")
    
    if refresh_shortlist_only and os.path.exists(ORB_CACHE_FILE):
        logging.info("Refreshing existing ORB shortlist only.")
        existing_df = pd.read_csv(ORB_CACHE_FILE)
        symbols = existing_df['Ticker'].tolist()
    else:
        symbols = get_nifty500_fno_symbols()
        
    token_map = get_kite_instruments(kite, symbols)
    
    if not token_map:
        logging.error("Failed to retrieve instrument tokens.")
        return False

    # --- NEW OPTIMIZATION: Initial Quote Filter ---
    # Fetch OHLC/LTP for all 500 symbols in 1-2 calls to filter by price
    logging.info(f"Pre-filtering {len(token_map)} stocks by price...")
    all_tickers = [f"NSE:{s}" for s in token_map.keys()]
    try:
        # Kite allows up to 500 symbols per quote/ohlc call (chunked safely to avoid 502 Bad Gateway)
        ohlc_dict = fetch_ohlc_safe(kite, all_tickers)
        
        # Filter symbols that are within our tradeable price range (100 - 5000)
        # and ensure they have some volume
        filtered_symbols = []
        for s in token_map.keys():
            quote = ohlc_dict.get(f"NSE:{s}")
            if quote:
                ltp = quote.get('last_price', 0)
                if 100 <= ltp <= 5000:
                    filtered_symbols.append(s)
        
        logging.info(f"Pre-filter complete: {len(filtered_symbols)}/{len(token_map)} stocks passed price filter.")
        # Re-build token map with only filtered symbols
        token_map = {s: token_map[s] for s in filtered_symbols}
    except Exception as e:
        logging.warning(f"Initial quote filter failed (skipping to full scan): {e}")

    cache_data = []
    to_date = datetime.datetime.now()
    from_date_daily = to_date - datetime.timedelta(days=300)
    
    total_symbols = len(token_map)
    processed = 0
    
    for symbol, token in token_map.items():
        processed += 1
        if progress_callback:
            progress_callback(processed, total_symbols, symbol)
            
        try:
            # We already checked price, now fetch historical data
            df_daily = fetch_kite_data(kite, token, from_date_daily, to_date, "day")
            if df_daily.empty or len(df_daily) < 200:
                continue
                
            df_daily.ta.ema(length=20, append=True)
            df_daily.ta.ema(length=50, append=True)
            df_daily.ta.ema(length=200, append=True)
            df_daily.ta.rsi(length=14, append=True)
            df_daily.ta.atr(length=14, append=True)
            
            # Calculate Daily Ranges for NR4/NR7
            df_daily['Range'] = df_daily['high'] - df_daily['low']
            df_daily['Avg_Vol_20'] = df_daily['volume'].rolling(window=20).mean()
            
            latest_daily = df_daily.iloc[-1]
            prev_daily = df_daily.iloc[-2]
            prev_2_daily = df_daily.iloc[-3]
            
            ema_20 = latest_daily['EMA_20']
            ema_50 = latest_daily['EMA_50']
            ema_200 = latest_daily['EMA_200']
            rsi = latest_daily['RSI_14']
            atr = latest_daily['ATRr_14']
            ltp = latest_daily['close']
            
            atr_pct = (atr / ltp * 100) if ltp > 0 else 0
            
            # --- VOLATILITY CONTRACTION (Yesterday's Data) ---
            # 1. Inside Bar (Yesterday vs Day Before)
            is_inside = (prev_daily['high'] < prev_2_daily['high']) and (prev_daily['low'] > prev_2_daily['low'])
            
            # 2. NR4 / NR7 (Yesterday's range is smallest of last N days)
            last_4_ranges = df_daily['Range'].iloc[-5:-1] # Last 4 completed days
            is_nr4 = prev_daily['Range'] == last_4_ranges.min()
            
            last_7_ranges = df_daily['Range'].iloc[-8:-1] # Last 7 completed days
            is_nr7 = prev_daily['Range'] == last_7_ranges.min()
            
            contraction = ""
            if is_inside: contraction += "Inside "
            if is_nr7: contraction += "NR7 "
            elif is_nr4: contraction += "NR4 "
            
            # --- RVOL CALCULATION ---
            # Default to yesterday's RVOL for pre-market identification
            rvol = prev_daily['volume'] / latest_daily['Avg_Vol_20'] if latest_daily['Avg_Vol_20'] > 0 else 0
            
            # If market is open (post 9:15), we could potentially fetch today's volume via quote
            # but for the 'once in the morning' cache, we'll focus on stocks that HAD volume yesterday
            # or we can add a quote check if needed.
            
            # --- STRICT FILTERS ---
            # 1. EMA Alignment
            bullish_trend = ltp > ema_20 > ema_50 > ema_200
            bearish_trend = ltp < ema_20 < ema_50 < ema_200
            
            # 2. ATR % Filter (> 2.0%)
            is_volatile = atr_pct >= 2.0
            
            # 3. RSI Filter (Keep existing or refine)
            bullish_mom = rsi > 55
            bearish_mom = rsi < 45
            
            is_bullish = bullish_trend and bullish_mom and is_volatile
            is_bearish = bearish_trend and bearish_mom and is_volatile
            
            if is_bullish or is_bearish:
                cache_data.append({
                    "Ticker": symbol,
                    "Token": token,
                    "EMA_20": round(ema_20, 2),
                    "EMA_50": round(ema_50, 2),
                    "EMA_200": round(ema_200, 2),
                    "RSI_14": round(rsi, 2),
                    "ATR_Pct": round(atr_pct, 2),
                    "RVOL": round(rvol, 2),
                    "Contraction": contraction.strip(),
                    "Prev_Close": prev_daily['close'],
                    "Prev_Day_High": prev_daily['high'],
                    "Prev_Day_Low": prev_daily['low'],
                    "Type": "Bullish" if is_bullish else "Bearish",
                    "Price History": df_daily['close'].tail(20).tolist()
                })
        except Exception as e:
            logging.error(f"Error caching {symbol}: {e}")
            continue
            
    if cache_data:
        cache_df = pd.DataFrame(cache_data)
        cache_df.to_csv(ORB_CACHE_FILE, index=False)
        logging.info(f"ORB Caching complete. {len(cache_data)} stocks shortlisted.")
        return True
    
    logging.warning("ORB Caching complete, but no stocks matched the trending criteria.")
    return False

def get_trending_orb_list():
    """Load cached ORB list if it exists and was created today."""
    if not os.path.exists(ORB_CACHE_FILE):
        return None
        
    # Check if file was modified today
    file_time = datetime.datetime.fromtimestamp(os.path.getmtime(ORB_CACHE_FILE)).date()
    if file_time != datetime.date.today():
        logging.info("ORB cache is outdated.")
        return None
        
    return pd.read_csv(ORB_CACHE_FILE)

def calculate_vwap(df):
    """Calculate VWAP for a given intraday dataframe."""
    if df.empty:
        return 0
    # VWAP = Sum(Typical Price * Volume) / Sum(Volume)
    # Typical Price = (High + Low + Close) / 3
    tp = (df['high'] + df['low'] + df['close']) / 3
    vwap = (tp * df['volume']).sum() / df['volume'].sum()
    return vwap

import orb_scanner

def scan_orb_setups(kite, progress_callback=None):
    """Delegated to orb_scanner module."""
    return orb_scanner.scan_orb_setups(kite, progress_callback=progress_callback)
            
    logging.info(f"Refined ORB Scan complete. Found {len(results)} candidates.")
    return pd.DataFrame(results), total_symbols


def run_unified_morning_cache(kite, progress_callback=None):
    """
    ULTRA-OPTIMIZED CACHING:
    Runs at 9:05 AM. Fetches data once and populates BOTH:
    1. high52_cache.csv
    2. orb_trending_cache.csv
    Saves ~500 API calls and ~3 minutes of execution time.
    """
    logging.info("🚀 Starting Unified Morning Caching (ORB + 52W High)...")
    
    symbols = get_nifty500_fno_symbols()
    token_map = get_kite_instruments(kite, symbols)
    
    if not token_map:
        logging.error("Failed to retrieve instrument tokens.")
        return False

    # 1. Pre-filter by Price (100 - 5000)
    logging.info(f"Pre-filtering {len(token_map)} stocks by price...")
    all_tickers = [f"NSE:{s}" for s in token_map.keys()]
    try:
        ohlc_dict = fetch_ohlc_safe(kite, all_tickers)
        filtered_symbols = []
        for s in token_map.keys():
            quote = ohlc_dict.get(f"NSE:{s}")
            if quote and 100 <= quote.get('last_price', 0) <= 5000:
                filtered_symbols.append(s)
        token_map = {s: token_map[s] for s in filtered_symbols}
        logging.info(f"Pre-filter complete: {len(token_map)} stocks passed.")
    except Exception as e:
        logging.warning(f"Initial quote filter failed: {e}")
    import concurrent.futures

    orb_data = []
    h52_data = []
    
    to_date = datetime.datetime.now()
    from_date = to_date - datetime.timedelta(days=400) # Ensure 250+ days for 52W
    
    total = len(token_map)
    processed = 0
    _lock = threading.Lock()
    
    def process_symbol(symbol, token):
        nonlocal processed
        try:
            df = fetch_kite_data(kite, token, from_date, to_date, "day")
            if df.empty or len(df) < 250:
                return
                
            # --- COMMON INDICATORS ---
            df.ta.ema(length=20, append=True)
            df.ta.ema(length=50, append=True)
            df.ta.ema(length=200, append=True)
            df.ta.rsi(length=14, append=True)
            df.ta.atr(length=14, append=True)
            df['Range'] = df['high'] - df['low']
            
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            prev_2 = df.iloc[-3]
            ltp = latest['close']
            
            # --- 52-WEEK HIGH LOGIC ---
            df_52w = df.iloc[-250:]
            high_52w = df_52w['high'].max()
            is_trending_h52 = (ltp > latest['EMA_20'] > latest['EMA_50'] > latest['EMA_200'])
            dist_from_h52 = (high_52w - ltp) / ltp * 100
            
            # --- ORB TRENDING LOGIC ---
            last_4_ranges = df['Range'].iloc[-5:-1]
            last_7_ranges = df['Range'].iloc[-8:-1]
            is_inside = (prev['high'] < prev_2['high']) and (prev['low'] > prev_2['low'])
            is_nr4 = prev['Range'] == last_4_ranges.min()
            is_nr7 = prev['Range'] == last_7_ranges.min()
            
            contraction = ""
            if is_inside: contraction += "Inside "
            if is_nr7: contraction += "NR7 "
            elif is_nr4: contraction += "NR4 "
            
            avg_vol_20 = df['volume'].rolling(window=20).mean().iloc[-1]
            rvol = prev['volume'] / avg_vol_20 if avg_vol_20 > 0 else 0
            atr_pct = (latest['ATRr_14'] / ltp * 100) if ltp > 0 else 0
            
            bullish_orb = ltp > latest['EMA_20'] and latest['RSI_14'] > 55
            bearish_orb = ltp < latest['EMA_20'] and latest['RSI_14'] < 50
            
            with _lock:
                if is_trending_h52 and dist_from_h52 <= 3.0:
                    h52_data.append({
                        "Ticker": symbol, "Token": token, "52W High": high_52w,
                        "52W Low": df_52w['low'].min(), "ATR_14": latest['ATRr_14'],
                        "Price_at_Cache": ltp, "Dist_from_High_%": round(dist_from_h52, 2)
                    })
                if (bullish_orb or bearish_orb) and atr_pct >= 2.0:
                    orb_data.append({
                        "Ticker": symbol, "Token": token, "EMA_200": latest['EMA_200'],
                        "EMA_50": latest['EMA_50'], "EMA_20": latest['EMA_20'],
                        "RSI": latest['RSI_14'], "ATR_Pct": round(atr_pct, 2),
                        "RVOL": round(rvol, 2), "Contraction": contraction.strip(),
                        "Prev_Day_High": prev['high'], "Prev_Day_Low": prev['low'],
                        "Prev_Day_Close": prev['close'], "Trend": "Bullish" if bullish_orb else "Bearish"
                    })
        except Exception as e:
            logging.error(f"Error caching {symbol}: {e}")
        finally:
            with _lock:
                processed += 1
                if progress_callback:
                    progress_callback(processed, total, symbol)

    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(process_symbol, sym, tok): sym for sym, tok in token_map.items()}
        concurrent.futures.wait(futures.keys())

    # Save both caches
    if h52_data:
        pd.DataFrame(h52_data).to_csv(os.path.join("data", "cache", "high52_cache.csv"), index=False)
    if orb_data:
        pd.DataFrame(orb_data).to_csv(os.path.join("data", "cache", "orb_trending_cache.csv"), index=False)
        
    logging.info(f"✅ Unified caching complete. H52: {len(h52_data)}, ORB: {len(orb_data)}")
    return True
    """
    Placeholder for automated market/limit order execution logic.
    """
    logging.info(f"Placeholder: Executing {order_type} BUY order for {ticker}, Qty: {qty}")
    # Example Kite API call for placing order:
    # try:
    #     order_id = kite.place_order(
    #         tradingsymbol=ticker,
    #         exchange=kite.EXCHANGE_NSE,
    #         transaction_type=kite.TRANSACTION_TYPE_BUY,
    #         quantity=qty,
    #         variety=kite.VARIETY_REGULAR,
    #         order_type=kite.ORDER_TYPE_MARKET if order_type == "MARKET" else kite.ORDER_TYPE_LIMIT,
    #         product=kite.PRODUCT_CNC, # CNC for delivery/swing
    #         validity=kite.VALIDITY_DAY,
    #         price=price
    #     )
    #     logging.info(f"Order placed. ID is: {order_id}")
    #     return order_id
    # except Exception as e:
    #     logging.error(f"Order placement failed: {e}")
    #     return None
    pass

