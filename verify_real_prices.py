import os
import json
import datetime
import pandas as pd
from kiteconnect import KiteConnect
from core import config

def verify_real_quotes():
    session_file = ".kite_session.json"
    with open(session_file, "r") as f:
        session = json.load(f)
        
    kite = KiteConnect(api_key=config.KITE_API_KEY)
    kite.set_access_token(session["access_token"])
    
    tickers = ["NSE:ACC", "NSE:AUROPHARMA", "NSE:COLPAL", "NSE:CONCOR", "NSE:GLENMARK", "NSE:IGL"]
    
    quotes = kite.quote(tickers)
    
    print("=========================================================================")
    print("                REAL LIVE KITE QUOTES FOR TODAY (2026-07-21)             ")
    print("=========================================================================\n")
    
    for t in tickers:
        q = quotes.get(t)
        if q:
            ohlc = q['ohlc']
            ltp = q['last_price']
            print(f"📌 {t:<15} | Last Price (LTP): ₹{ltp:.2f}")
            print(f"   Today Open: ₹{ohlc['open']:.2f} | Today High: ₹{ohlc['high']:.2f} | Today Low: ₹{ohlc['low']:.2f} | Close (Prev): ₹{ohlc['close']:.2f}")
            print("-" * 75)

if __name__ == "__main__":
    verify_real_quotes()
