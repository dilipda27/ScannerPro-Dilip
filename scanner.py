import yfinance as yf
import pandas as pd
import pandas_ta as ta
import requests
import io

def get_nifty500_fno_tickers():
    """
    Fetch Nifty 500 stocks and filter for those in the FNO segment.
    """
    import os
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    cache_500 = os.path.join("data", "cache", "nifty500_local_cache.csv")
    cache_fno = os.path.join("data", "cache", "fo_mktlots_local_cache.csv")
    
    # 1. Fetch Nifty 500
    url_500 = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
    nifty500_symbols = set()
    try:
        r_500 = requests.get(url_500, headers=headers, timeout=10)
        r_500.raise_for_status()
        text_500 = r_500.text
        # Save to cache
        with open(cache_500, "w", encoding="utf-8") as f:
            f.write(text_500)
        df_500 = pd.read_csv(io.StringIO(text_500))
        nifty500_symbols = set(df_500['Symbol'].str.strip())
    except Exception as e:
        print(f"Error fetching Nifty 500 from NSE: {e}. Trying local cache...")
        if os.path.exists(cache_500):
            try:
                df_500 = pd.read_csv(cache_500)
                nifty500_symbols = set(df_500['Symbol'].str.strip())
                print("Loaded Nifty 500 from local cache.")
            except Exception as ce:
                print(f"Failed to read Nifty 500 cache: {ce}")
        
        if not nifty500_symbols:
            # Absolute fallback list (approx 50 major stocks)
            nifty500_symbols = {
                "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "BHARTIARTL", "SBI", "LICI",
                "ITC", "HINDUNILVR", "LT", "BAJFINANCE", "HCLTECH", "MARUTI", "SUNPHARMA",
                "ADANIENT", "KOTAKBANK", "AXISBANK", "TITAN", "ULTRACEMCO", "NTPC", "TATAMOTORS",
                "ONGC", "POWERGRID", "ASIANPAINT", "COALINDIA", "JSWSTEEL", "M&M", "TRENT",
                "NESTLEIND", "TATACHEM", "HINDALCO", "BPCL", "Grasim", "WIPRO", "TECHM",
                "HDFCLIFE", "SBILIFE", "DRREDDY", "IOC", "CIPLA", "EICHERMOT", "DIVISLAB",
                "INDUSINDBK", "SBICARD", "MUTHOOTFIN", "APOLLOHOSP", "HEROMOTOCO", "SHRIRAMFIN"
            }

    # 2. Fetch FNO List
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
        print(f"Error fetching FNO list from NSE: {e}. Trying local cache...")
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
                    print("Loaded FNO list from local cache.")
                    loaded_fno_cache = True
            except Exception as ce:
                print(f"Failed to read FNO cache: {ce}")
                
        if not loaded_fno_cache:
            # Parse FNO list from local kite_instruments_nfo.csv if available
            kite_inst_file = os.path.join("data", "cache", "kite_instruments_nfo.csv")
            if os.path.exists(kite_inst_file):
                try:
                    df_inst = pd.read_csv(kite_inst_file)
                    # Get unique name where segment is NFO-FUT
                    nfo_fno_syms = df_inst[df_inst['segment'] == 'NFO-FUT']['name'].dropna().unique()
                    for sym in nfo_fno_syms:
                        fno_symbols.add(sym.strip())
                    if fno_symbols:
                        print(f"Loaded {len(fno_symbols)} FNO symbols from {kite_inst_file}")
                except Exception as ie:
                    print(f"Failed to load FNO symbols from kite instruments: {ie}")
                    
        if not fno_symbols:
            fno_symbols = nifty500_symbols
            
    # Intersection
    final_symbols = nifty500_symbols.intersection(fno_symbols)
    tickers = [f"{sym}.NS" for sym in final_symbols]
    return sorted(tickers)

def fetch_data(ticker, period="1y"):
    """
    Fetch historical daily data for a given ticker.
    """
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=period)
        if df.empty:
            return None
        return df
    except Exception:
        return None

def calculate_indicators(df):
    """
    Calculate technical indicators using pandas-ta.
    """
    if len(df) < 50:
        return df
        
    df.ta.rsi(length=14, append=True)
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    df.ta.sma(length=50, append=True)
    df.ta.sma(length=200, append=True)
    df['Vol_SMA_20'] = df['Volume'].rolling(window=20).mean()
    df['High_20'] = df['High'].rolling(window=20).max()
    
    return df

def scan_swing_candidates(tickers, progress_callback=None):
    results = []
    total = len(tickers)
    processed = 0
    
    for ticker in tickers:
        processed += 1
        if progress_callback:
            progress_callback(processed, total, ticker)
        
        if ticker == "VEDL.NS":
            continue
            
        df = fetch_data(ticker)
        if df is not None and not df.empty:
            df = calculate_indicators(df)
            
            if 'RSI_14' in df.columns and 'MACD_12_26_9' in df.columns and 'SMA_200' in df.columns and 'Vol_SMA_20' in df.columns:
                last_row = df.iloc[-1]
                prev_row = df.iloc[-2]
                
                # Price Filter: Avoid penny stocks or illiquid heavyweights
                if not (100 <= last_row['Close'] <= 5000):
                    continue
                
                in_uptrend = last_row['Close'] > last_row['SMA_50'] and last_row['Close'] > last_row['SMA_200']
                bullish_reversal = last_row['Close'] > prev_row['High']
                
                pullback_setup = in_uptrend and last_row['RSI_14'] < 50 and bullish_reversal
                
                macd_crossover = (prev_row['MACD_12_26_9'] < prev_row['MACDs_12_26_9']) and \
                                 (last_row['MACD_12_26_9'] > last_row['MACDs_12_26_9'])
                vol_breakout = last_row['Volume'] > last_row['Vol_SMA_20']
                momentum_setup = in_uptrend and macd_crossover and vol_breakout and bullish_reversal
                
                if pullback_setup or momentum_setup:
                    reason = []
                    if pullback_setup: reason.append("Uptrend Pullback & Bullish Reversal")
                    if momentum_setup: reason.append("MACD Breakout & Bullish Reversal")
                    
                    # Calculate % Gain
                    pct_gain = ((last_row['Close'] - prev_row['Close']) / prev_row['Close']) * 100
                    
                    results.append({
                        "Ticker": ticker,
                        "Close": round(last_row['Close'], 2),
                        "% Gain": round(pct_gain, 2),
                        "RSI": round(last_row['RSI_14'], 2),
                        "Volume": last_row['Volume'],
                        "Reason": " & ".join(reason)
                    })
    return pd.DataFrame(results)

def scan_breakout_stocks(tickers, progress_callback=None):
    results = []
    total = len(tickers)
    processed = 0
    
    for ticker in tickers:
        processed += 1
        if progress_callback:
            progress_callback(processed, total, ticker)
        
        if ticker == "VEDL.NS":
            continue
            
        df = fetch_data(ticker)
        if df is not None and not df.empty:
            df = calculate_indicators(df)
            
            if 'High_20' in df.columns and 'Vol_SMA_20' in df.columns:
                last_row = df.iloc[-1]
                prev_row = df.iloc[-2]
                
                # Price Filter: Avoid penny stocks or illiquid heavyweights
                if not (100 <= last_row['Close'] <= 5000):
                    continue
                
                price_breakout = last_row['Close'] > prev_row['High_20']
                vol_breakout = last_row['Volume'] > (1.5 * last_row['Vol_SMA_20'])
                
                if price_breakout and vol_breakout:
                    # Calculate % Gain
                    pct_gain = ((last_row['Close'] - prev_row['Close']) / prev_row['Close']) * 100
                    
                    results.append({
                        "Ticker": ticker,
                        "Close": round(last_row['Close'], 2),
                        "% Gain": round(pct_gain, 2),
                        "RSI": round(last_row['RSI_14'], 2) if 'RSI_14' in df.columns else None,
                        "Volume": last_row['Volume'],
                        "Reason": "20-Day High Breakout"
                    })
    return pd.DataFrame(results)
