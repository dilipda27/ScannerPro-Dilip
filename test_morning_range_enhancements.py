import os
import json
import datetime
import pandas as pd
from kiteconnect import KiteConnect
from core import config
from strategies import kite_scanner
from strategies import morning_range_scanner

def test_enhanced_strategy():
    session_file = ".kite_session.json"
    if not os.path.exists(session_file):
        print("Error: Kite session file not found.")
        return
        
    with open(session_file, "r") as f:
        session = json.load(f)
        
    kite = KiteConnect(api_key=config.KITE_API_KEY)
    kite.set_access_token(session["access_token"])
    
    print("=========================================================================")
    print(" 🔬 TESTING ENHANCED MORNING RANGE LOGIC (TOP 15 WATCHLIST + 11:30 CUTOFF)")
    print("=========================================================================\n")
    
    fno_symbols = list(kite_scanner.get_nifty500_fno_symbols())
    token_map = kite_scanner.get_kite_instruments(kite, fno_symbols)
    
    today = datetime.datetime.now()
    start_915 = today.replace(hour=9, minute=15, second=0, microsecond=0)
    end_945 = today.replace(hour=9, minute=45, second=0, microsecond=0)
    
    candidates = []
    
    for symbol, token in token_map.items():
        try:
            df_range = kite_scanner.fetch_kite_data(kite, int(token), start_915, end_945, "minute")
            if df_range.empty or len(df_range) < 25:
                continue
            df_range.columns = [c.lower() for c in df_range.columns]
            
            open_915 = df_range['open'].iloc[0]
            high_945 = df_range['high'].max()
            low_945 = df_range['low'].min()
            close_945 = df_range['close'].iloc[-1]
            range_w = high_945 - low_945
            pct_change_945 = ((close_945 - open_915) / open_915) * 100
            
            if range_w < 0.005 * close_945:
                continue
                
            classification = "NEUTRAL"
            if close_945 > open_915 and ((high_945 - close_945) / range_w) <= 0.15 and pct_change_945 >= 0.75:
                classification = "STRONG"
            elif close_945 < open_915 and ((close_945 - low_945) / range_w) <= 0.15 and pct_change_945 <= -0.75:
                classification = "WEAK"
                
            if classification in ["STRONG", "WEAK"]:
                candidates.append({
                    "symbol": symbol,
                    "token": int(token),
                    "open_915": open_915,
                    "high_945": high_945,
                    "low_945": low_945,
                    "pct_change_945": pct_change_945,
                    "classification": classification
                })
        except Exception:
            continue
            
    # Sort WEAK by lowest pct_change (most weak) & STRONG by highest pct_change
    weak_sorted = sorted([c for c in candidates if c['classification'] == 'WEAK'], key=lambda x: x['pct_change_945'])[:15]
    strong_sorted = sorted([c for c in candidates if c['classification'] == 'STRONG'], key=lambda x: x['pct_change_945'], reverse=True)[:15]
    
    top_watchlist = {c['symbol']: c for c in (weak_sorted + strong_sorted)}
    
    print(f"Watchlist Filtered: Reduced from {len(candidates)} down to TOP {len(top_watchlist)} candidates!")
    print("  Top Weak Tickers:", [c['symbol'] for c in weak_sorted[:8]])
    
    # Simulate scan with 11:30 AM cutoff & 4 max trades
    cutoff_time = today.replace(hour=11, minute=30, second=0, microsecond=0)
    
    signals = []
    
    for symbol, info in top_watchlist.items():
        if len(signals) >= 4: # Max 4 trade limit
            break
            
        try:
            df_full = kite_scanner.fetch_kite_data(kite, info["token"], start_915, cutoff_time, "minute")
            if df_full.empty or len(df_full) < 35:
                continue
            df_full.columns = [c.lower() for c in df_full.columns]
            if df_full.index.tz is not None:
                df_full.index = df_full.index.tz_localize(None)
                
            df_full['vwap'] = morning_range_scanner.calculate_vwap(df_full)
            import pandas_ta as ta
            df_full.ta.rsi(length=14, append=True)
            
            df_post = df_full[df_full.index >= end_945]
            
            low_945 = info["low_945"]
            mid_point = (info["high_945"] + low_945) / 2
            
            for idx in range(1, len(df_post)):
                t_stamp = df_post.index[idx]
                if t_stamp > cutoff_time:
                    break
                    
                candle_c = df_post['close'].iloc[idx]
                prev_c = df_post['close'].iloc[idx-1]
                idx_in_full = df_full.index.get_loc(t_stamp)
                
                vwap = df_full['vwap'].iloc[idx_in_full]
                rsi = df_full['RSI_14'].iloc[idx_in_full] if 'RSI_14' in df_full.columns else 50.0
                
                prev_5_vol = df_full['volume'].iloc[idx_in_full-5:idx_in_full].mean()
                cur_vol = df_full['volume'].iloc[idx_in_full]
                vol_ratio = cur_vol / prev_5_vol if prev_5_vol > 0 else 1.0
                
                c_h = df_post['high'].iloc[idx]
                c_l = df_post['low'].iloc[idx]
                c_o = df_post['open'].iloc[idx]
                c_r = c_h - c_l
                
                if info["classification"] == "WEAK":
                    breakdown_lvl = low_945 * 0.9975
                    is_confirmed = (candle_c <= breakdown_lvl) and (prev_c <= (low_945 * 0.9990))
                    lower_wick = min(c_o, candle_c) - c_l
                    body_r = (c_h - candle_c) / c_r if c_r > 0 else 1.0
                    wick_r = lower_wick / c_r if c_r > 0 else 0.0
                    
                    if is_confirmed and candle_c < vwap and vol_ratio >= 1.5 and body_r >= 0.70 and wick_r <= 0.35 and rsi >= 32.0:
                        sl = min(vwap, mid_point)
                        tp = candle_c - (2 * (sl - candle_c))
                        signals.append({
                            "symbol": symbol,
                            "time": str(t_stamp.strftime("%H:%M")),
                            "entry": candle_c,
                            "sl": sl,
                            "target": tp
                        })
                        break
        except Exception:
            continue
            
    print("\n-------------------------------------------------------------------------")
    print(f"🎯 OPTIMIZED SIGNALS PRODUCED ({len(signals)} TRADES MAX):")
    print("-------------------------------------------------------------------------")
    for s in signals:
        print(f"Ticker: {s['symbol']:<10} | Time: {s['time']} | Entry: ₹{s['entry']:.2f} | SL: ₹{s['sl']:.2f} | Target: ₹{s['target']:.2f}")

if __name__ == "__main__":
    test_enhanced_strategy()
