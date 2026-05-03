import pandas as pd
import os
import logging
from datetime import datetime

PORTFOLIO_FILE = "paper_portfolio.csv"

def get_portfolio():
    if not os.path.exists(PORTFOLIO_FILE):
        return pd.DataFrame(columns=["Ticker", "Type", "EntryPrice", "SL", "Qty", "EntryTime", "Status"])
    try:
        return pd.read_csv(PORTFOLIO_FILE)
    except:
        return pd.DataFrame(columns=["Ticker", "Type", "EntryPrice", "SL", "Qty", "EntryTime", "Status"])

def execute_paper_trade(ticker, trade_type, entry_price, sl, qty):
    df = get_portfolio()
    
    # Check if already active in current session (avoid duplicate entries on same day)
    # We only allow one open trade per ticker at a time
    if not df.empty and ticker in df[df['Status'] == 'OPEN']['Ticker'].values:
        return False
        
    new_trade = {
        "Ticker": ticker,
        "Type": trade_type,
        "EntryPrice": entry_price,
        "SL": sl,
        "Qty": qty,
        "EntryTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Status": "OPEN"
    }
    
    df = pd.concat([df, pd.DataFrame([new_trade])], ignore_index=True)
    df.to_csv(PORTFOLIO_FILE, index=False)
    logging.info(f"🚀 Paper Trade Executed: {trade_type} {ticker} @ {entry_price} (SL: {sl}, Qty: {qty})")
    return True

def update_portfolio_pnl(kite):
    """
    Fetches latest prices for all open trades and calculates P&L.
    Returns a DataFrame with live stats.
    """
    df = get_portfolio()
    if df.empty or not (df['Status'] == 'OPEN').any():
        return pd.DataFrame()
        
    open_trades = df[df['Status'] == 'OPEN'].copy()
    tickers = open_trades['Ticker'].tolist()
    
    try:
        # Fetch LTP for all open tickers
        # Note: Kite quotes expect "NSE:RELIANCE"
        quotes = kite.ltp([f"NSE:{t}" for t in tickers])
        
        def get_ltp(ticker):
            q = quotes.get(f"NSE:{ticker}")
            return q['last_price'] if q else None
            
        open_trades['Current Price'] = open_trades['Ticker'].apply(get_ltp)
        
        def calc_pnl(row):
            if row['Current Price'] is None: return 0
            if "Bullish" in str(row['Type']):
                return (row['Current Price'] - row['EntryPrice']) * row['Qty']
            else:
                return (row['EntryPrice'] - row['Current Price']) * row['Qty']
                
        open_trades['Live P&L'] = open_trades.apply(calc_pnl, axis=1)
        return open_trades[["Ticker", "Type", "EntryPrice", "Current Price", "Qty", "SL", "Live P&L"]]
        
    except Exception as e:
        logging.error(f"Error updating portfolio P&L: {e}")
        return open_trades
