import pandas as pd
import pandas_ta as ta
import datetime
import time
import logging
import os
from kiteconnect import KiteConnect
import kite_scanner

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

CACHE_FILE = os.path.join("data", "cache", "high52_cache.csv")

def cache_daily_data(kite, progress_callback=None):
    """
    Phase 1: Pre-Market Caching
    Fetches historical daily data, calculates 52W High/Low, 14-day ATR, 
    and filters for clear uptrend (last 15 days).
    """
    logging.info("Starting Pre-Market Caching for 52-Week High Scanner...")
    
    symbols = kite_scanner.get_nifty500_fno_symbols()
    token_map = kite_scanner.get_kite_instruments(kite, symbols)
    
    if not token_map:
        logging.error("Failed to retrieve instrument tokens.")
        return False
        
    # --- NEW OPTIMIZATION: Initial Quote Filter ---
    logging.info(f"Pre-filtering {len(token_map)} stocks by price...")
    all_tickers = [f"NSE:{s}" for s in token_map.keys()]
    try:
        # Fetch OHLC for all symbols in one call (safely chunked)
        ohlc_dict = kite_scanner.fetch_ohlc_safe(kite, all_tickers)
        filtered_symbols = []
        for s in token_map.keys():
            quote = ohlc_dict.get(f"NSE:{s}")
            if quote:
                ltp = quote.get('last_price', 0)
                # Apply standard price range filter
                if 100 <= ltp <= 5000:
                    filtered_symbols.append(s)
        
        logging.info(f"Pre-filter complete: {len(filtered_symbols)}/{len(token_map)} stocks passed.")
        token_map = {s: token_map[s] for s in filtered_symbols}
    except Exception as e:
        logging.warning(f"Initial quote filter failed: {e}")

    cache_data = []
    to_date = datetime.datetime.now()
    from_date = to_date - datetime.timedelta(days=400) # Ensure 250+ trading days
    
    total_symbols = len(token_map)
    processed = 0
    
    for symbol, token in token_map.items():
        processed += 1
        if progress_callback:
            progress_callback(processed, total_symbols, symbol)
            
        try:
            df = kite_scanner.fetch_kite_data(kite, token, from_date, to_date, "day")
            if df.empty or len(df) < 250:
                continue
                
            # 1. 52-Week High/Low (last 250 sessions)
            df_52w = df.iloc[-250:]
            high_52w = df_52w['high'].max()
            low_52w = df_52w['low'].min()
            
            # 2. 14-day ATR
            # We use all available data for better ATR smoothing then take latest
            df.ta.atr(length=14, append=True)
            latest_atr = df.iloc[-1]['ATRr_14']
            
            # 3. Robust Trend Filter (EMA Alignment)
            # Logic: Price > 20 EMA > 50 EMA > 200 EMA (Long-term strength)
            df.ta.ema(length=20, append=True)
            df.ta.ema(length=50, append=True)
            df.ta.ema(length=200, append=True)
            
            latest_row = df.iloc[-1]
            is_trending = (latest_row['close'] > latest_row['EMA_20'] > latest_row['EMA_50'] > latest_row['EMA_200'])
            
            # 4. Proximity Filter (Within 3% of 52W High)
            # This ensures we only scan stocks likely to break out TODAY
            dist_from_high = (high_52w - latest_row['close']) / latest_row['close'] * 100
            is_close_to_high = dist_from_high <= 3.0
            
            # 5. Consolidation Filter (within 5% range for the last 10 sessions prior to today)
            # Take last 10 completed daily closed prices (excluding today)
            close_10d = df['close'].iloc[-11:-1] if len(df) >= 11 else df['close']
            if not close_10d.empty:
                max_c = close_10d.max()
                min_c = close_10d.min()
                daily_range_pct = (max_c - min_c) / min_c * 100
                is_consolidating_daily = daily_range_pct <= 5.0
            else:
                is_consolidating_daily = True

            if is_trending and is_close_to_high and is_consolidating_daily:
                cache_data.append({
                    "Ticker": symbol,
                    "Token": token,
                    "52W High": high_52w,
                    "52W Low": low_52w,
                    "ATR_14": latest_atr,
                    "Price_at_Cache": latest_row['close'],
                    "Dist_from_High_%": round(dist_from_high, 2)
                })
        except Exception as e:
            logging.error(f"Error caching {symbol}: {e}")
            continue
            
    if cache_data:
        cache_df = pd.DataFrame(cache_data)
        cache_df.to_csv(CACHE_FILE, index=False)
        logging.info(f"Caching complete. {len(cache_data)} stocks shortlisted.")
        return True
    
    logging.warning("Caching complete, but no stocks matched the uptrend criteria.")
    return False

def calculate_vwap(df):
    """Calculate Daily VWAP from intraday data."""
    if df.empty:
        return df
    # Typical Price * Volume
    v_tp = ((df['high'] + df['low'] + df['close']) / 3) * df['volume']
    df['VWAP'] = v_tp.cumsum() / df['volume'].cumsum()
    return df

def scan_52w_breakouts(kite, progress_callback=None, only_closed_candles=True):
    """
    Phase 2 & 3: Intraday Execution Loop & Signal Evaluation
    """
    logging.info("Starting 52-Week High Breakout Scan...")
    
    if not os.path.exists(CACHE_FILE):
        logging.error("Cache file not found. Please run pre-market caching first.")
        return pd.DataFrame()
        
    cache_df = pd.read_csv(CACHE_FILE)
    results = []
    
    # Load Sector Map
    import json
    sector_map = {}
    if os.path.exists(os.path.join("data", "cache", "sector_map.json")):
        try:
            with open(os.path.join("data", "cache", "sector_map.json"), "r") as f:
                sector_map = json.load(f)
        except: pass

    # Fetch Sectoral Statuses
    sector_indices = [
        "NIFTY 50", "NIFTY BANK", "NIFTY IT", "NIFTY AUTO", "NIFTY METAL", 
        "NIFTY PHARMA", "NIFTY FMCG", "NIFTY ENERGY", "NIFTY REALTY", "NIFTY PSU BANK"
    ]
    sector_status = {}
    try:
        idx_quotes = kite.ohlc([f"NSE:{idx}" for idx in sector_indices])
        for idx in sector_indices:
            q = idx_quotes.get(f"NSE:{idx}")
            if q:
                open_val = q['ohlc']['open']
                ltp_val = q['last_price']
                sector_status[idx] = "Bullish" if ltp_val >= open_val else "Bearish"
    except Exception as e:
        logging.warning(f"Failed to fetch sectoral indices: {e}")
    
    now = datetime.datetime.now()
    current_time = now.time()
    
    # Time Filter: 09:45 AM to 02:00 PM
    start_time = datetime.time(9, 45)
    end_time = datetime.time(14, 0)
    
    if not (start_time <= current_time <= end_time):
        logging.warning(f"Scanner idle. Current time {current_time} is outside the 09:45-14:00 window.")
        # We still continue for testing if needed, but in production this would return empty
        # return pd.DataFrame()
    
    to_date = now
    from_date = now.replace(hour=9, minute=15, second=0, microsecond=0)
    if from_date > to_date:
        from_date = from_date - datetime.timedelta(days=1)
    
    total_symbols = len(cache_df)
    processed = 0
    
    # --- NEW OPTIMIZATION: Pre-scan with batch OHLC ---
    logging.info(f"Pre-screening {total_symbols} stocks with batch quotes...")
    all_tickers = [f"NSE:{s}" for s in cache_df['Ticker'].tolist()]
    try:
        # Get live quotes for all cached stocks (safely chunked)
        quotes = kite_scanner.fetch_ohlc_safe(kite, all_tickers)
        # Filter cache_df to only include stocks near or above breakout
        # We use a 0.5% buffer to be safe
        valid_tickers = []
        for _, row in cache_df.iterrows():
            q = quotes.get(f"NSE:{row['Ticker']}")
            if q and q['last_price'] >= (row['52W High'] * 0.995):
                valid_tickers.append(row['Ticker'])
        
        cache_df = cache_df[cache_df['Ticker'].isin(valid_tickers)]
        logging.info(f"Pre-screen complete. {len(cache_df)}/{total_symbols} stocks are near breakout.")
        total_symbols = len(cache_df)
    except Exception as e:
        logging.warning(f"Batch pre-screen failed: {e}")

    for _, row in cache_df.iterrows():
        processed += 1
        symbol = row['Ticker']
        token = int(row['Token'])
        
        if progress_callback:
            progress_callback(processed, total_symbols, symbol)
            
        try:
            # Fetch 5-minute data for today
            df_5m = kite_scanner.fetch_kite_data(kite, token, from_date, to_date, "5minute")
            if df_5m.empty or len(df_5m) < 20: # Need at least 20 candles for Vol SMA
                continue
                
            # Calculate Indicators
            df_5m = calculate_vwap(df_5m)
            df_5m['Vol_SMA_20'] = df_5m['volume'].rolling(window=20).mean()
            
            # --- 5-MINUTE CLOSE LOGIC ---
            # If only_closed_candles is True, we analyze the second to last candle 
            # because the last entry in Kite's 5-minute data is the currently forming candle.
            if only_closed_candles and len(df_5m) >= 2:
                latest_candle = df_5m.iloc[-2]
                candle_time = df_5m.index[-2]
            else:
                latest_candle = df_5m.iloc[-1]
                candle_time = df_5m.index[-1]

            ltp = latest_candle['close']
            vol = latest_candle['volume']
            avg_vol = latest_candle['Vol_SMA_20']
            vwap = latest_candle['VWAP']
            
            high_52w = row['52W High']
            atr_14 = row['ATR_14']
            
            # --- PHASE 3: EVALUATE SIGNALS ---
            
            # 1. Price Breakout: Close > 52W High (with 0.1% confirmation and 0.8% max chase)
            dist_pct = (ltp - high_52w) / high_52w * 100
            cond_breakout = (0.1 <= dist_pct <= 0.8)
            
            # 2. RVOL: Vol > 2.5x SMA(20)
            rvol = vol / avg_vol if avg_vol > 0 else 0
            cond_rvol = rvol >= 2.5
            
            # 3. VWAP Alignment: Close > VWAP
            cond_vwap = ltp > vwap
            
            # 4. ATR Check: ATR >= 1.5% of Price
            atr_percent = (atr_year := atr_14) / ltp * 100
            cond_atr = atr_percent >= 1.5
            
            # 5. Sector Alignment Check
            target_sector = sector_map.get(symbol, "NIFTY 50")
            sec_trend = sector_status.get(target_sector, "Neutral")
            cond_sector = (sec_trend == "Bullish")
            
            if cond_breakout and cond_rvol and cond_vwap and cond_atr and cond_sector:
                results.append({
                    "Ticker": symbol,
                    "LTP": round(ltp, 2),
                    "52W High": round(high_52w, 2),
                    "RVOL": round(rvol, 2),
                    "VWAP": round(vwap, 2),
                    "ATR %": round(atr_percent, 2),
                    "Volume": vol,
                    "Token": token,
                    "Time": candle_time.strftime("%H:%M")
                })
                logging.info(f"🔥 Breakout detected in [{symbol}] at {ltp}")
                
        except Exception as e:
            logging.error(f"Error scanning {symbol}: {e}")
            continue
            
    logging.info(f"Scan complete. Found {len(results)} strong candidates.")
    return pd.DataFrame(results), total_symbols
