import os
import json
import datetime
import pandas as pd
from kiteconnect import KiteConnect
from core import config
from strategies import kite_scanner
from strategies import morning_range_scanner
from services import paper_trader

def run_retrospective_audit():
    session_file = ".kite_session.json"
    if not os.path.exists(session_file):
        print("Error: Kite session file not found.")
        return
        
    with open(session_file, "r") as f:
        session = json.load(f)
        
    kite = KiteConnect(api_key=config.KITE_API_KEY)
    kite.set_access_token(session["access_token"])
    
    print("=========================================================================")
    print("   RETROSPECTIVE AUDIT: TODAY'S MORNING RANGE TRADES vs NEW GUARDRAILS   ")
    print("=========================================================================\n")
    
    today = datetime.datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    
    portfolio_file = os.path.join("data", "trades", "paper_portfolio.csv")
    history_file = os.path.join("data", "trades", "paper_trade_history.csv")
    
    mr_trades = []
    for fpath in [portfolio_file, history_file]:
        if os.path.exists(fpath):
            df = pd.read_csv(fpath)
            if not df.empty and 'Strategy' in df.columns:
                sub = df[(df['Strategy'] == 'Morning Range Str/Wk') & (df['EntryTime'].astype(str).str.contains(today_str, na=False))]
                for _, r in sub.iterrows():
                    mr_trades.append({
                        "ticker": r['Ticker'],
                        "entry_price": float(r['EntryPrice']),
                        "entry_time": str(r['EntryTime']),
                        "status": str(r['Status']),
                        "type": str(r['Type'])
                    })
                    
    # Deduplicate
    seen = set()
    unique_trades = []
    for t in mr_trades:
        if t['ticker'] not in seen:
            seen.add(t['ticker'])
            unique_trades.append(t)
            
    print(f"Auditing {len(unique_trades)} Morning Range trades taken today ({today_str}):\n")
    
    audit_results = []
    
    for t in unique_trades:
        symbol = t['ticker']
        token_map = kite_scanner.get_kite_instruments(kite, [symbol])
        token = token_map.get(symbol)
        if not token:
            continue
            
        start_915 = today.replace(hour=9, minute=15, second=0, microsecond=0)
        end_945 = today.replace(hour=9, minute=45, second=0, microsecond=0)
        
        # 1-min range data
        df_range = kite_scanner.fetch_kite_data(kite, int(token), start_915, end_945, "minute")
        if df_range.empty or len(df_range) < 25:
            continue
            
        df_range.columns = [c.lower() for c in df_range.columns]
        open_915 = df_range['open'].iloc[0]
        high_945 = df_range['high'].max()
        low_945 = df_range['low'].min()
        close_945 = df_range['close'].iloc[-1]
        range_w = high_945 - low_945
        
        classification = "NEUTRAL"
        if close_945 > open_915 and ((high_945 - close_945) / range_w) <= 0.15:
            classification = "STRONG"
        elif close_945 < open_915 and ((close_945 - low_945) / range_w) <= 0.15:
            classification = "WEAK"
            
        # Post-9:45 1-min data
        df_post = kite_scanner.fetch_kite_data(kite, int(token), end_945, today, "minute")
        if df_post.empty:
            continue
        df_post.columns = [c.lower() for c in df_post.columns]
        if df_post.index.tz is not None:
            df_post.index = df_post.index.tz_localize(None)
            
        breakdown_level_025 = low_945 * 0.9975
        breakout_level_025 = high_945 * 1.0025
        
        entry_time_dt = datetime.datetime.strptime(t['entry_time'], "%Y-%m-%d %H:%M")
        
        # Evaluate 2-candle consecutive breakdown at/before recorded entry time
        df_eval = df_post[df_post.index <= entry_time_dt]
        
        is_valid_under_new_rules = False
        reason = ""
        
        if classification == "WEAK":
            # Check 2-candle confirmation
            if len(df_eval) >= 2:
                c1 = df_eval['close'].iloc[-2]
                c2 = df_eval['close'].iloc[-1]
                
                # Wick check
                latest_c = df_eval.iloc[-1]
                c_range = latest_c['high'] - latest_c['low']
                lower_wick = min(latest_c['open'], latest_c['close']) - latest_c['low']
                wick_ratio = lower_wick / c_range if c_range > 0 else 0
                
                if c2 > breakdown_level_025:
                    reason = f"Close {c2:.2f} failed 0.25% breakdown threshold ({breakdown_level_025:.2f})"
                elif c1 > (low_945 * 0.9990):
                    reason = f"Previous candle close {c1:.2f} failed 2-candle confirmation"
                elif wick_ratio > 0.35:
                    reason = f"Lower wick rejection ratio {wick_ratio*100:.1f}% > 35%"
                else:
                    is_valid_under_new_rules = True
            else:
                reason = "Insufficient candles for 2-candle confirmation"
                
        elif classification == "STRONG":
            if len(df_eval) >= 2:
                c1 = df_eval['close'].iloc[-2]
                c2 = df_eval['close'].iloc[-1]
                if c2 >= breakout_level_025 and c1 >= (high_945 * 1.0010):
                    is_valid_under_new_rules = True
                else:
                    reason = "Failed breakout 0.25% / 2-candle threshold"
            else:
                reason = "Insufficient candles"
                
        print(f"Ticker: {symbol:<10} | Type: {t['type']:<18} | Recorded Entry: {t['entry_price']} ({t['entry_time']})")
        print(f"  Range Low: {low_945:.2f} | 0.25% Breakdown Threshold: {breakdown_level_025:.2f}")
        print(f"  New Guardrails Verdict: {'✅ PASS (KEEP)' if is_valid_under_new_rules else '❌ REJECT (REMOVE)'}")
        if not is_valid_under_new_rules:
            print(f"  Reason for rejection: {reason}")
        print("-" * 75)
        
        audit_results.append({
            "ticker": symbol,
            "keep": is_valid_under_new_rules,
            "reason": reason
        })

    to_remove = [r['ticker'] for r in audit_results if not r['keep']]
    to_keep = [r['ticker'] for r in audit_results if r['keep']]
    
    print(f"\n=========================================================================")
    print(f"SUMMARY OF NEW GUARDRAILS AUDIT:")
    print(f"  Trades Passing New Guardrails ({len(to_keep)}): {to_keep}")
    print(f"  Trades Failing New Guardrails ({len(to_remove)}): {to_remove}")
    print(f"=========================================================================\n")
    
    if to_remove:
        print("Cleaning non-qualifying trades from portfolio & history...")
        for fpath in [portfolio_file, history_file]:
            if os.path.exists(fpath):
                df = pd.read_csv(fpath)
                if not df.empty and 'Ticker' in df.columns:
                    mask = (df['Ticker'].isin(to_remove)) & \
                           (df['Strategy'] == 'Morning Range Str/Wk') & \
                           (df['EntryTime'].astype(str).str.contains(today_str, na=False))
                    removed = df[mask]['Ticker'].tolist()
                    df_cleaned = df[~mask]
                    df_cleaned.to_csv(fpath, index=False)
                    print(f"Purged {len(removed)} trades from {fpath}: {removed}")
        paper_trader.export_history_to_excel()
        print("Updated paper_trade_history.xlsx workbook.")

if __name__ == "__main__":
    run_retrospective_audit()
