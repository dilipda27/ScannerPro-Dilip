import pandas as pd
import pandas_ta as ta
import datetime
import logging
import scanner
import kite_scanner

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_nifty500_symbols():
    import os
    import requests
    import io
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    cache_500 = os.path.join("data", "cache", "nifty500_local_cache.csv")
    url_500 = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
    nifty500_symbols = []
    try:
        r_500 = requests.get(url_500, headers=headers, timeout=10)
        r_500.raise_for_status()
        text_500 = r_500.text
        with open(cache_500, "w", encoding="utf-8") as f:
            f.write(text_500)
        df_500 = pd.read_csv(io.StringIO(text_500))
        nifty500_symbols = df_500['Symbol'].str.strip().tolist()
    except Exception as e:
        logging.warning(f"Error fetching Nifty 500 from NSE: {e}. Trying local cache...")
        if os.path.exists(cache_500):
            try:
                df_500 = pd.read_csv(cache_500)
                nifty500_symbols = df_500['Symbol'].str.strip().tolist()
            except Exception as ce:
                logging.error(f"Failed to read Nifty 500 cache: {ce}")
    
    if not nifty500_symbols:
        # Fallback
        nifty500_symbols = [
            "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "BHARTIARTL", "SBI", "LICI",
            "ITC", "HINDUNILVR", "LT", "BAJFINANCE", "HCLTECH", "MARUTI", "SUNPHARMA",
            "ADANIENT", "KOTAKBANK", "AXISBANK", "TITAN", "ULTRACEMCO", "NTPC", "TATAMOTORS",
            "ONGC", "POWERGRID", "ASIANPAINT", "COALINDIA", "JSWSTEEL", "M&M", "TRENT",
            "NESTLEIND", "TATACHEM", "HINDALCO", "BPCL", "GRASIM", "WIPRO", "TECHM",
            "HDFCLIFE", "SBILIFE", "DRREDDY", "IOC", "CIPLA", "EICHERMOT", "DIVISLAB",
            "INDUSINDBK", "SBICARD", "MUTHOOTFIN", "APOLLOHOSP", "HEROMOTOCO", "SHRIRAMFIN"
        ]
    return sorted(list(set(nifty500_symbols)))

def scan_minervini_vcp(kite, progress_callback=None):
    """
    Scans Nifty 500 stocks for high-probability setups matching Mark Minervini's VCP breakout rules.
    """
    logging.info("Starting Minervini VCP Breakout Scanner...")
    results = []
    
    to_date = datetime.datetime.now()
    # Fetch 450 calendar days of data to guarantee 300+ trading days for stable 52-week high/low,
    # 200-day SMA, and 20-day lookback for SMA trend.
    from_date = to_date - datetime.timedelta(days=450)
    
    if progress_callback:
        progress_callback(0, 100, "Fetching Nifty 500 Symbols...")
        
    nifty500_symbols = get_nifty500_symbols()
    
    logging.info(f"Fetching instrument tokens for {len(nifty500_symbols)} Nifty 500 stocks...")
    token_map = kite_scanner.get_kite_instruments(kite, nifty500_symbols)
    
    if not token_map:
        logging.error("Failed to retrieve instrument tokens for Nifty 500 stocks.")
        return pd.DataFrame()
        
    total_symbols = len(token_map)
    processed = 0
    
    for symbol, token in token_map.items():
        processed += 1
        if progress_callback:
            progress_callback(processed, total_symbols, symbol)
            
        try:
            df = kite_scanner.fetch_kite_data(kite, token, from_date, to_date, "day")
            # We need at least 260 trading sessions to compute 52-week range and 200-day SMAs
            if df.empty or len(df) < 260:
                continue
                
            # Calculate SMAs
            df.ta.sma(length=50, append=True)
            df.ta.sma(length=150, append=True)
            df.ta.sma(length=200, append=True)
            
            # Check required SMA columns
            latest = df.iloc[-1]
            required_cols = ['SMA_50', 'SMA_150', 'SMA_200']
            if latest[required_cols].isna().any():
                continue
                
            close = latest['close']
            volume = latest['volume']
            sma_50 = latest['SMA_50']
            sma_150 = latest['SMA_150']
            sma_200 = latest['SMA_200']
            
            # 1. Minervini's Trend Template
            # - Current Price is above 150-day and 200-day SMA
            if close <= sma_150 or close <= sma_200:
                continue
                
            # - 150-day SMA is above 200-day SMA
            if sma_150 <= sma_200:
                continue
                
            # - 200-day SMA is trending upward (higher than it was 20 days ago)
            if len(df) <= 220:
                continue
            sma_200_20d_ago = df['SMA_200'].iloc[-21]
            if pd.isna(sma_200_20d_ago) or sma_200 <= sma_200_20d_ago:
                continue
                
            # - Current Price is above the 50-day SMA
            if close <= sma_50:
                continue
                
            # - 52-week range calculations (approx 250 trading days)
            df_52w = df.iloc[-250:]
            low_52w = df_52w['low'].min()
            high_52w = df_52w['high'].max()
            
            # - Current Price is at least 30% above 52-week low
            if close < 1.30 * low_52w:
                continue
                
            # - Current Price is within 25% of 52-week high
            if close < 0.75 * high_52w:
                continue
                
            # 2. VCP Proxy
            # - Volatility Contraction: The average daily trading range (High - Low) of the most recent 10 days
            #   must be tighter than the trading range 30 days ago.
            daily_ranges = df['high'] - df['low']
            avg_range_10d = daily_ranges.iloc[-10:].mean()
            range_30d_ago = daily_ranges.iloc[-30]
            
            if avg_range_10d >= range_30d_ago:
                continue
                
            # - Volume Contraction: The average volume over the last 10 days must be lower than the 50-day average volume
            avg_vol_10d = df['volume'].iloc[-10:].mean()
            avg_vol_50d = df['volume'].iloc[-50:].mean()
            
            if avg_vol_10d >= avg_vol_50d:
                continue
                
            # 3. The Breakout (The Trigger)
            # - Price Action: Current close breaks above the highest high of the last 20 days (pivot, excluding today)
            pivot_level = df['high'].iloc[-21:-1].max()
            if close <= pivot_level:
                continue
                
            # - Volume Surge: Today's volume is at least 150% of the 50-day average volume
            if volume < 1.50 * avg_vol_50d:
                continue
                
            # Setup SL & Targets
            sl = max(pivot_level * 0.965, df['low'].iloc[-20:].min())
            tp1 = close + 1.5 * (close - sl)
            tp2 = close + 3.0 * (close - sl)
            
            results.append({
                "Ticker": symbol,
                "Close": round(close, 2),
                "Pivot": round(pivot_level, 2),
                "52W High": round(high_52w, 2),
                "52W Low": round(low_52w, 2),
                "Stop Loss": round(sl, 2),
                "Target 1 (1.5R)": round(tp1, 2),
                "Target 2 (3.0R)": round(tp2, 2),
                "Volume Surge %": round((volume / avg_vol_50d) * 100, 2),
                "Token": token
            })
            
        except Exception as e:
            logging.error(f"Error processing {symbol}: {e}")
            continue
            
    logging.info(f"Minervini VCP Scan complete. Found {len(results)} candidates.")
    return pd.DataFrame(results)
