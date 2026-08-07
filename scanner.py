import yfinance as yf
import pandas as pd
import pandas_ta as ta
import requests
import io
import os
import datetime
import logging

# Configurable blocklist (e.g. for troublesome or suspended symbols like VEDL)
ENV_BLOCKLIST = os.environ.get("SYMBOL_BLOCKLIST", "VEDL")
BLOCKLIST = {s.strip().upper() for s in ENV_BLOCKLIST.split(",") if s.strip()}

def is_blocked(ticker: str) -> bool:
    """Checks whether a ticker symbol is in the configurable blocklist."""
    base_sym = ticker.replace(".NS", "").replace("NSE:", "").strip().upper()
    return base_sym in BLOCKLIST

def get_nifty500_fno_tickers(as_kite_symbols: bool = False):
    """
    Fetch Nifty 500 stocks and filter for those in the FNO segment.
    If as_kite_symbols is True, returns plain symbols (e.g., 'RELIANCE').
    Otherwise returns '.NS' suffixed symbols for yfinance compatibility (e.g., 'RELIANCE.NS').
    """
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    cache_dir = os.path.join("data", "cache")
    os.makedirs(cache_dir, exist_ok=True)
    cache_500 = os.path.join(cache_dir, "nifty500_local_cache.csv")
    cache_fno = os.path.join(cache_dir, "fo_mktlots_local_cache.csv")
    
    # 1. Fetch Nifty 500
    url_500 = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
    nifty500_symbols = set()
    try:
        r_500 = requests.get(url_500, headers=headers, timeout=10)
        r_500.raise_for_status()
        text_500 = r_500.text
        with open(cache_500, "w", encoding="utf-8") as f:
            f.write(text_500)
        df_500 = pd.read_csv(io.StringIO(text_500))
        nifty500_symbols = set(df_500['Symbol'].str.strip())
    except Exception as e:
        logging.warning(f"Error fetching Nifty 500 from NSE: {e}. Trying local cache...")
        if os.path.exists(cache_500):
            try:
                df_500 = pd.read_csv(cache_500)
                nifty500_symbols = set(df_500['Symbol'].str.strip())
                logging.info("Loaded Nifty 500 from local cache.")
            except Exception as ce:
                logging.error(f"Failed to read Nifty 500 cache: {ce}")
        
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
        with open(cache_fno, "w", encoding="utf-8") as f:
            f.write(text_fno)
        for line in text_fno.split('\n'):
            parts = line.split(',')
            if len(parts) > 2:
                sym = parts[1].strip()
                if sym and sym != "SYMBOL":
                    fno_symbols.add(sym)
    except Exception as e:
        logging.warning(f"Error fetching FNO list from NSE: {e}. Trying local cache...")
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
            kite_inst_file = os.path.join(cache_dir, "kite_instruments_nfo.csv")
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
    if as_kite_symbols:
        return sorted(list(final_symbols))
    tickers = [f"{sym}.NS" for sym in final_symbols]
    return sorted(tickers)

def fetch_data(ticker: str, period: str = "1y", kite=None, token: int = None):
    """
    Fetch historical daily data for a given ticker.
    Supports Kite API when a kite client is provided, with graceful fallback to yfinance.
    """
    base_symbol = ticker.replace(".NS", "").replace("NSE:", "").strip().upper()
    if is_blocked(base_symbol):
        logging.info(f"Skipping blocked symbol: {base_symbol}")
        return None

    # 1. Try Kite API if client is provided
    if kite is not None:
        try:
            from strategies import kite_scanner
            if token is None:
                token_map = kite_scanner.get_kite_instruments(kite, [base_symbol])
                token = token_map.get(base_symbol)

            if token:
                to_date = datetime.datetime.now()
                # Fetch ~400 days of data to cover 1-year period + 200 SMA calculation window
                from_date = to_date - datetime.timedelta(days=400)
                df = kite_scanner.fetch_kite_data(kite, int(token), from_date, to_date, "day")
                if df is not None and not df.empty:
                    # Rename columns to TitleCase for compatibility with pandas-ta / yfinance format
                    rename_map = {c: c.capitalize() for c in df.columns if c.lower() in ['open', 'high', 'low', 'close', 'volume']}
                    df = df.rename(columns=rename_map)
                    return df
        except Exception as ke:
            logging.warning(f"Kite data fetch failed for {base_symbol}: {ke}. Falling back to yfinance.")

    # 2. Fallback to yfinance
    try:
        yf_symbol = f"{base_symbol}.NS"
        stock = yf.Ticker(yf_symbol)
        df = stock.history(period=period)
        if df is not None and not df.empty:
            return df
    except Exception as yfe:
        logging.warning(f"yfinance fetch failed for {ticker}: {yfe}")

    return None

def calculate_indicators(df):
    """
    Calculate technical indicators using pandas-ta.
    """
    if df is None or len(df) < 50:
        return df

    # Ensure standard TitleCase column names for pandas-ta
    cols_map = {c: c.capitalize() for c in df.columns if c.lower() in ['open', 'high', 'low', 'close', 'volume']}
    if cols_map:
        df = df.rename(columns=cols_map)
        
    df.ta.rsi(length=14, append=True)
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    df.ta.sma(length=50, append=True)
    df.ta.sma(length=200, append=True)
    df['Vol_SMA_20'] = df['Volume'].rolling(window=20).mean()
    df['High_20'] = df['High'].rolling(window=20).max()
    
    return df

def scan_swing_candidates(tickers, progress_callback=None, kite=None):
    """
    Scan for swing trading candidates (Uptrend Pullback & MACD Breakout).
    Supports optional kite client for real-time Kite API data.
    """
    results = []
    total = len(tickers)
    processed = 0

    token_map = {}
    if kite is not None:
        try:
            from strategies import kite_scanner
            clean_syms = [t.replace(".NS", "").replace("NSE:", "").strip().upper() for t in tickers if not is_blocked(t)]
            token_map = kite_scanner.get_kite_instruments(kite, clean_syms)
        except Exception as e:
            logging.warning(f"Failed to pre-fetch Kite tokens: {e}")

    for ticker in tickers:
        processed += 1
        if progress_callback:
            progress_callback(processed, total, ticker)
        
        if is_blocked(ticker):
            continue

        base_sym = ticker.replace(".NS", "").replace("NSE:", "").strip().upper()
        token = token_map.get(base_sym)
            
        df = fetch_data(ticker, period="1y", kite=kite, token=token)
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
                    
                    pct_gain = ((last_row['Close'] - prev_row['Close']) / prev_row['Close']) * 100
                    
                    results.append({
                        "Ticker": ticker,
                        "Close": round(last_row['Close'], 2),
                        "% Gain": round(pct_gain, 2),
                        "RSI": round(last_row['RSI_14'], 2),
                        "Volume": int(last_row['Volume']),
                        "Reason": " & ".join(reason)
                    })
    return pd.DataFrame(results)

def scan_breakout_stocks(tickers, progress_callback=None, kite=None):
    """
    Scan for 20-day high volume breakout candidates.
    Supports optional kite client for real-time Kite API data.
    """
    results = []
    total = len(tickers)
    processed = 0

    token_map = {}
    if kite is not None:
        try:
            from strategies import kite_scanner
            clean_syms = [t.replace(".NS", "").replace("NSE:", "").strip().upper() for t in tickers if not is_blocked(t)]
            token_map = kite_scanner.get_kite_instruments(kite, clean_syms)
        except Exception as e:
            logging.warning(f"Failed to pre-fetch Kite tokens: {e}")

    for ticker in tickers:
        processed += 1
        if progress_callback:
            progress_callback(processed, total, ticker)
        
        if is_blocked(ticker):
            continue

        base_sym = ticker.replace(".NS", "").replace("NSE:", "").strip().upper()
        token = token_map.get(base_sym)
            
        df = fetch_data(ticker, period="1y", kite=kite, token=token)
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
                    pct_gain = ((last_row['Close'] - prev_row['Close']) / prev_row['Close']) * 100
                    
                    results.append({
                        "Ticker": ticker,
                        "Close": round(last_row['Close'], 2),
                        "% Gain": round(pct_gain, 2),
                        "RSI": round(last_row['RSI_14'], 2) if 'RSI_14' in df.columns else None,
                        "Volume": int(last_row['Volume']),
                        "Reason": "20-Day High Breakout"
                    })
    return pd.DataFrame(results)
