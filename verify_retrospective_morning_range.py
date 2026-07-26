import os
import json
import datetime
import pandas as pd
from kiteconnect import KiteConnect
from core import config
from strategies import kite_scanner
from strategies import morning_range_scanner

def run_retrospective():
    session_file = ".kite_session.json"
    if not os.path.exists(session_file):
        print("Error: Kite session file not found.")
        return
        
    with open(session_file, "r") as f:
        session = json.load(f)
        
    kite = KiteConnect(api_key=config.KITE_API_KEY)
    kite.set_access_token(session["access_token"])
    
    print("=========================================================================")
    print("  RETROSPECTIVE MORNING RANGE VALIDATION (EXACT 1-MIN KITE API DATA)    ")
    print("=========================================================================\n")
    
    today = datetime.datetime.now()
    start_915 = today.replace(hour=9, minute=15, second=0, microsecond=0)
    end_945 = today.replace(hour=9, minute=45, second=0, microsecond=0)
    
    # Load today's paper trades
    portfolio_file = os.path.join("data", "trades", "paper_portfolio.csv")
    history_file = os.path.join("data", "trades", "paper_trade_history.csv")
    
    today_str = today.strftime("%Y-%m-%d")
    
    trades = []
    for fpath in [portfolio_file, history_file]:
        if os.path.exists(fpath):
            df = pd.read_csv(fpath)
            if not df.empty and 'Strategy' in df.columns:
                mr_trades = df[(df['Strategy'] == 'Morning Range Str/Wk') & (df['EntryTime'].astype(str).str.contains(today_str, na=False))]
                for _, r in mr_trades.iterrows():
                    trades.append({
                        "file": fpath,
                        "ticker": r['Ticker'],
                        "entry_price": float(r['EntryPrice']),
                        "entry_time": str(r['EntryTime']),
                        "status": str(r['Status']),
                        "type": str(r['Type'])
                    })
                    
    # Deduplicate by ticker
    seen = set()
    unique_trades = []
    for t in trades:
        if t['ticker'] not in seen:
            seen.add(t['ticker'])
            unique_trades.append(t)
            
    print(f"Found {len(unique_trades)} Morning Range trades executed today ({today_str}):")
    for t in unique_trades:
        print(f" - {t['ticker']}: Entry={t['entry_price']}, Time={t['entry_time']}, Type={t['type']}, Status={t['status']}")
    print("\n-------------------------------------------------------------------------\n")
    
    invalid_tickers = []
    valid_tickers = []
    
    for t in unique_trades:
        symbol = t['ticker']
        token_map = kite_scanner.get_kite_instruments(kite, [symbol])
        token = token_map.get(symbol)
        if not token:
            print(f"⚠️ {symbol}: Token not found.")
            continue
            
        # 1. Fetch exact 1-minute data for 09:15 - 09:45 range
        df_range = kite_scanner.fetch_kite_data(kite, int(token), start_915, end_945, "minute")
        if df_range.empty:
            print(f"⚠️ {symbol}: No 1-min range data available.")
            continue
            
        df_range.columns = [c.lower() for c in df_range.columns]
        open_915 = df_range['open'].iloc[0]
        high_945 = df_range['high'].max()
        low_945 = df_range['low'].min()
        close_945 = df_range['close'].iloc[-1]
        range_w = high_945 - low_945
        
        # Classification
        classification = "NEUTRAL"
        if close_945 > open_915 and ((high_945 - close_945) / range_w) <= 0.15:
            classification = "STRONG"
        elif close_945 < open_915 and ((close_945 - low_945) / range_w) <= 0.15:
            classification = "WEAK"
            
        # 2. Fetch post-09:45 candles up to now (1-min)
        df_post = kite_scanner.fetch_kite_data(kite, int(token), end_945, today, "minute")
        if df_post.empty:
            print(f"⚠️ {symbol}: No post-9:45 1-min data.")
            continue
            
        df_post.columns = [c.lower() for c in df_post.columns]
        
        # Check if breakout/breakdown conditions were ever met under TRUE 1-min range
        breakdown_level = low_945 * 0.9985
        breakout_level = high_945 * 1.0015
        
        entry_time_dt = datetime.datetime.strptime(t['entry_time'], "%Y-%m-%d %H:%M")
        
        # Check candles at/before recorded entry time
        df_at_entry = df_post[df_post.index.tz_localize(None) <= entry_time_dt] if df_post.index.tz is not None else df_post[df_post.index <= entry_time_dt]
        
        valid_trigger = False
        if t['type'] == 'Bearish Breakdown' or classification == 'WEAK':
            min_post_close = df_post['close'].min()
            min_at_entry = df_at_entry['close'].min() if not df_at_entry.empty else min_post_close
            
            if min_at_entry <= breakdown_level:
                valid_trigger = True
            print(f"🔍 {symbol} (Short Trade):")
            print(f"   True 1-Min Range [09:15-09:45]: Low = {low_945:.2f} | Open = {open_915:.2f} | High = {high_945:.2f}")
            print(f"   Breakdown Level (0.9985x): {breakdown_level:.2f}")
            print(f"   Lowest 1-Min Close at Entry ({t['entry_time']}): {min_at_entry:.2f}")
            print(f"   Lowest 1-Min Close Post-09:45: {min_post_close:.2f}")
            print(f"   Verdict: {'✅ VALID TRIGGER' if valid_trigger else '❌ INVALID TRIGGER (FALSE ENTRY)'}")
            print("-------------------------------------------------------------------------")
            
        elif t['type'] == 'Bullish Breakout' or classification == 'STRONG':
            max_post_close = df_post['close'].max()
            max_at_entry = df_at_entry['close'].max() if not df_at_entry.empty else max_post_close
            
            if max_at_entry >= breakout_level:
                valid_trigger = True
            print(f"🔍 {symbol} (Long Trade):")
            print(f"   True 1-Min Range [09:15-09:45]: High = {high_945:.2f} | Open = {open_915:.2f} | Low = {low_945:.2f}")
            print(f"   Breakout Level (1.0015x): {breakout_level:.2f}")
            print(f"   Highest 1-Min Close at Entry ({t['entry_time']}): {max_at_entry:.2f}")
            print(f"   Verdict: {'✅ VALID TRIGGER' if valid_trigger else '❌ INVALID TRIGGER (FALSE ENTRY)'}")
            print("-------------------------------------------------------------------------")
            
        if valid_trigger:
            valid_tickers.append(symbol)
        else:
            invalid_tickers.append(symbol)
            
    print(f"\n📊 SUMMARY OF RETROSPECTIVE VERIFICATION:")
    print(f"  Valid Trades ({len(valid_tickers)}): {valid_tickers}")
    print(f"  Invalid Trades to Remove ({len(invalid_tickers)}): {invalid_tickers}")

if __name__ == "__main__":
    run_retrospective()
