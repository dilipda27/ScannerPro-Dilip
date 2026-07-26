import os
import json
import datetime
import pandas as pd
from kiteconnect import KiteConnect
from core import config
from strategies import kite_scanner

def verify_exact():
    session_file = ".kite_session.json"
    with open(session_file, "r") as f:
        session = json.load(f)
        
    kite = KiteConnect(api_key=config.KITE_API_KEY)
    kite.set_access_token(session["access_token"])
    
    tickers = ["NSE:ACC", "NSE:AUROPHARMA", "NSE:COLPAL", "NSE:CONCOR", "NSE:GLENMARK", "NSE:IGL"]
    
    token_map = kite_scanner.get_kite_instruments(kite, [t.replace("NSE:", "") for t in tickers])
    quotes = kite.quote(tickers)
    
    today = datetime.datetime.now().date()
    from_date = today - datetime.timedelta(days=100)
    
    print("=========================================================================")
    print("           ACTUAL REAL-TIME ZERODHA DATA FOR THE 6 SHORTLISTED STOCKS     ")
    print("=========================================================================\n")
    
    for t_str, token in token_map.items():
        q_key = f"NSE:{t_str}"
        q = quotes.get(q_key)
        
        df_daily = kite_scanner.fetch_kite_data(kite, token, from_date, today, "day")
        import pandas_ta as ta
        df_daily.ta.ema(length=20, append=True)
        df_daily.ta.ema(length=50, append=True)
        df_daily.ta.rsi(length=14, append=True)
        
        latest = df_daily.iloc[-1]
        prev = df_daily.iloc[-2] if len(df_daily) > 1 else latest
        
        pdh = prev['high'] if latest.name.date() == today else latest['high']
        prev_close = prev['close'] if latest.name.date() == today else latest['close']
        
        print(f"TICKER          : {t_str}")
        print(f"   Prev Close   : {prev_close:.2f}")
        print(f"   Yesterday High: {pdh:.2f}")
        print(f"   Today Open   : {q['ohlc']['open']:.2f}")
        print(f"   Today High   : {q['ohlc']['high']:.2f}")
        print(f"   Today Low    : {q['ohlc']['low']:.2f}")
        print(f"   Current LTP  : {q['last_price']:.2f}")
        print(f"   Daily 20 EMA : {latest['EMA_20']:.2f}")
        print(f"   Daily 50 EMA : {latest['EMA_50']:.2f}")
        print(f"   Daily 14 RSI : {latest['RSI_14']:.2f}")
        print("-" * 75)

if __name__ == "__main__":
    verify_exact()
