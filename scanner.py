import yfinance as yf
import pandas as pd
import pandas_ta as ta
import requests
import io

def get_nifty200_tickers():
    """
    Fetch the latest Nifty 200 constituents from NSE India.
    """
    print("Fetching Nifty 200 stock list from NSE...")
    url = "https://archives.nseindia.com/content/indices/ind_nifty200list.csv"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        df = pd.read_csv(io.StringIO(response.text))
        # Extract symbols and append '.NS' for yfinance
        tickers = [f"{symbol}.NS" for symbol in df['Symbol'].tolist()]
        return tickers
    except Exception as e:
        print(f"Error fetching Nifty 200 list: {e}")
        # Fallback to a small list if NSE blocks the request
        return ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS"]

def fetch_data(ticker, period="1y"):
    """
    Fetch historical daily data for a given ticker.
    """
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=period)
        if df.empty:
            print(f"Warning: No data found for {ticker}")
            return None
        return df
    except Exception as e:
        print(f"Error fetching data for {ticker}: {e}")
        return None

def calculate_indicators(df):
    """
    Calculate technical indicators using pandas-ta.
    """
    # Ensure there's enough data
    if len(df) < 50:
        return df
        
    # Calculate RSI (14)
    df.ta.rsi(length=14, append=True)
    
    # Calculate MACD (12, 26, 9)
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    
    # Calculate SMAs
    df.ta.sma(length=50, append=True)
    df.ta.sma(length=200, append=True)
    
    # Calculate Average Volume (20 days)
    df['Vol_SMA_20'] = df['Volume'].rolling(window=20).mean()
    
    return df

def run_scan():
    tickers = get_nifty200_tickers()
    print(f"Starting NSE Stock Scan for {len(tickers)} tickers...")
    
    results = []
    
    for ticker in tickers:
        if ticker == "VEDL.NS":
            continue
            
        df = fetch_data(ticker)
        
        if df is not None and not df.empty:
            df = calculate_indicators(df)
            
            # Ensure indicators are calculated
            if 'RSI_14' in df.columns and 'MACD_12_26_9' in df.columns and 'MACDs_12_26_9' in df.columns and 'SMA_50' in df.columns and 'SMA_200' in df.columns and 'Vol_SMA_20' in df.columns:
                last_row = df.iloc[-1]
                prev_row = df.iloc[-2]
                
                # Filter criteria for Swing Trading
                
                # Base Trend Condition: Price above 50 and 200 SMA (Uptrend)
                in_uptrend = last_row['Close'] > last_row['SMA_50'] and last_row['Close'] > last_row['SMA_200']
                
                # 1. Pullback in Uptrend: RSI is relatively low but stock is in long-term uptrend
                pullback_setup = in_uptrend and last_row['RSI_14'] < 50
                
                # 2. MACD Bullish Crossover: MACD crosses above Signal
                macd_crossover = (prev_row['MACD_12_26_9'] < prev_row['MACDs_12_26_9']) and \
                                 (last_row['MACD_12_26_9'] > last_row['MACDs_12_26_9'])
                
                # Require volume to be at least above average for momentum
                vol_breakout = last_row['Volume'] > last_row['Vol_SMA_20']
                momentum_setup = in_uptrend and macd_crossover and vol_breakout
                
                if pullback_setup or momentum_setup:
                    reason = []
                    if pullback_setup: reason.append("Uptrend Pullback (RSI < 50)")
                    if momentum_setup: reason.append("MACD Breakout (with Volume)")
                    
                    results.append({
                        "Ticker": ticker,
                        "Close": round(last_row['Close'], 2),
                        "RSI_14": round(last_row['RSI_14'], 2),
                        "Reason": " & ".join(reason)
                    })
                    
    print("\n" + "="*50)
    print("SCAN RESULTS")
    print("="*50)
    
    if not results:
        print("No stocks met the criteria today.")
    else:
        results_df = pd.DataFrame(results)
        print(results_df.to_string(index=False))

if __name__ == "__main__":
    run_scan()
