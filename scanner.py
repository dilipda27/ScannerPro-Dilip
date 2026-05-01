import yfinance as yf
import pandas as pd
import pandas_ta as ta
import requests
import io

def get_nifty200_tickers():
    """
    Fetch the latest Nifty 200 constituents from NSE India.
    """
    url = "https://archives.nseindia.com/content/indices/ind_nifty200list.csv"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        df = pd.read_csv(io.StringIO(response.text))
        tickers = [f"{symbol}.NS" for symbol in df['Symbol'].tolist()]
        return tickers
    except Exception as e:
        print(f"Error fetching Nifty 200 list: {e}")
        return ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS"]

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

def scan_swing_candidates(tickers):
    results = []
    
    for ticker in tickers:
        if ticker == "VEDL.NS":
            continue
            
        df = fetch_data(ticker)
        if df is not None and not df.empty:
            df = calculate_indicators(df)
            
            if 'RSI_14' in df.columns and 'MACD_12_26_9' in df.columns and 'SMA_200' in df.columns and 'Vol_SMA_20' in df.columns:
                last_row = df.iloc[-1]
                prev_row = df.iloc[-2]
                
                in_uptrend = last_row['Close'] > last_row['SMA_50'] and last_row['Close'] > last_row['SMA_200']
                pullback_setup = in_uptrend and last_row['RSI_14'] < 50
                
                macd_crossover = (prev_row['MACD_12_26_9'] < prev_row['MACDs_12_26_9']) and \
                                 (last_row['MACD_12_26_9'] > last_row['MACDs_12_26_9'])
                vol_breakout = last_row['Volume'] > last_row['Vol_SMA_20']
                momentum_setup = in_uptrend and macd_crossover and vol_breakout
                
                if pullback_setup or momentum_setup:
                    reason = []
                    if pullback_setup: reason.append("Uptrend Pullback (RSI < 50)")
                    if momentum_setup: reason.append("MACD Breakout (with Volume)")
                    
                    results.append({
                        "Ticker": ticker,
                        "Close": round(last_row['Close'], 2),
                        "RSI": round(last_row['RSI_14'], 2),
                        "Volume": last_row['Volume'],
                        "Reason": " & ".join(reason)
                    })
    return pd.DataFrame(results)

def scan_breakout_stocks(tickers):
    results = []
    
    for ticker in tickers:
        if ticker == "VEDL.NS":
            continue
            
        df = fetch_data(ticker)
        if df is not None and not df.empty:
            df = calculate_indicators(df)
            
            if 'High_20' in df.columns and 'Vol_SMA_20' in df.columns:
                last_row = df.iloc[-1]
                prev_row = df.iloc[-2]
                
                price_breakout = last_row['Close'] > prev_row['High_20']
                vol_breakout = last_row['Volume'] > (1.5 * last_row['Vol_SMA_20'])
                
                if price_breakout and vol_breakout:
                    results.append({
                        "Ticker": ticker,
                        "Close": round(last_row['Close'], 2),
                        "RSI": round(last_row['RSI_14'], 2) if 'RSI_14' in df.columns else None,
                        "Volume": last_row['Volume'],
                        "Reason": "20-Day High Breakout"
                    })
    return pd.DataFrame(results)
