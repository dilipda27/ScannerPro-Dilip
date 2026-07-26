import os
import json
import datetime
import pandas as pd
import pandas_ta as ta
from kiteconnect import KiteConnect
from core import config
from strategies import kite_scanner
from strategies import morning_range_scanner

def run_stress_test():
    session_file = ".kite_session.json"
    if not os.path.exists(session_file):
        print("Error: Kite session file not found.")
        return
        
    with open(session_file, "r") as f:
        session = json.load(f)
        
    kite = KiteConnect(api_key=config.KITE_API_KEY)
    kite.set_access_token(session["access_token"])
    
    print("=========================================================================")
    print(" 🔬 STRESS TEST: UPGRADED 4-GUARDRAIL STRATEGY vs REAL TODAY DATA        ")
    print("=========================================================================\n")
    
    fno_symbols = list(kite_scanner.get_nifty500_fno_symbols())
    token_map = kite_scanner.get_kite_instruments(kite, fno_symbols)
    
    today = datetime.datetime.now()
    start_915 = today.replace(hour=9, minute=15, second=0, microsecond=0)
    end_945 = today.replace(hour=9, minute=45, second=0, microsecond=0)
    
    print(f"1. Building Morning Watchlist using 1-minute resolution (09:15 - 09:45) for {len(token_map)} stocks...")
    
    watchlist = {}
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
            
            if range_w < 0.005 * close_945:
                continue
                
            classification = "NEUTRAL"
            if close_945 > open_915 and ((high_945 - close_945) / range_w) <= 0.15:
                classification = "STRONG"
            elif close_945 < open_915 and ((close_945 - low_945) / range_w) <= 0.15:
                classification = "WEAK"
                
            if classification in ["STRONG", "WEAK"]:
                watchlist[symbol] = {
                    "token": int(token),
                    "open_915": open_915,
                    "high_945": high_945,
                    "low_945": low_945,
                    "classification": classification
                }
        except Exception:
            continue
            
    print(f"   Watchlist Built: {len(watchlist)} candidates classified ({sum(1 for v in watchlist.values() if v['classification']=='WEAK')} WEAK, {sum(1 for v in watchlist.values() if v['classification']=='STRONG')} STRONG).\n")
    
    print("2. Simulating Minute-by-Minute Live Scan Post-09:45 AM with New Guardrails...\n")
    
    triggered_signals = []
    
    for symbol, info in watchlist.items():
        try:
            df_full = kite_scanner.fetch_kite_data(kite, info["token"], start_915, today, "minute")
            if df_full.empty or len(df_full) < 35:
                continue
            df_full.columns = [c.lower() for c in df_full.columns]
            if df_full.index.tz is not None:
                df_full.index = df_full.index.tz_localize(None)
                
            df_full['vwap'] = morning_range_scanner.calculate_vwap(df_full)
            df_full.ta.rsi(length=14, append=True)
            
            df_post = df_full[df_full.index >= end_945]
            if len(df_post) < 2:
                continue
                
            high_945 = info["high_945"]
            low_945 = info["low_945"]
            mid_point = (high_945 + low_945) / 2
            
            # Loop minute by minute post 9:45
            for idx in range(1, len(df_post)):
                candle_c = df_post['close'].iloc[idx]
                prev_c = df_post['close'].iloc[idx-1]
                t_stamp = df_post.index[idx]
                
                # Check volume ratio
                idx_in_full = df_full.index.get_loc(t_stamp)
                if idx_in_full < 6:
                    continue
                prev_5_vol = df_full['volume'].iloc[idx_in_full-5:idx_in_full].mean()
                cur_vol = df_full['volume'].iloc[idx_in_full]
                vol_ratio = cur_vol / prev_5_vol if prev_5_vol > 0 else 1.0
                
                vwap = df_full['vwap'].iloc[idx_in_full]
                rsi = df_full['RSI_14'].iloc[idx_in_full] if 'RSI_14' in df_full.columns else 50.0
                
                # Candle body & wick quality
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
                        
                        # Calculate post-entry price movement up to now
                        future_df = df_full.iloc[idx_in_full+1:]
                        max_f_high = future_df['high'].max() if not future_df.empty else candle_c
                        min_f_low = future_df['low'].min() if not future_df.empty else candle_c
                        cur_ltp = df_full['close'].iloc[-1]
                        
                        sl_hit = max_f_high >= sl
                        tp_hit = min_f_low <= tp
                        
                        triggered_signals.append({
                            "symbol": symbol,
                            "type": "SHORT",
                            "time": str(t_stamp.strftime("%H:%M")),
                            "entry": round(candle_c, 2),
                            "sl": round(sl, 2),
                            "target": round(tp, 2),
                            "vol_ratio": round(vol_ratio, 2),
                            "rsi": round(rsi, 1),
                            "current_price": round(cur_ltp, 2),
                            "lowest_reached": round(min_f_low, 2),
                            "highest_against": round(max_f_high, 2),
                            "status": "STOP LOSS HIT" if sl_hit else ("TARGET HIT" if tp_hit else "RUNNING IN PROFIT" if cur_ltp < candle_c else "RUNNING IN LOSS")
                        })
                        break # Triggered once per stock
                        
                elif info["classification"] == "STRONG":
                    breakout_lvl = high_945 * 1.0025
                    is_confirmed = (candle_c >= breakout_lvl) and (prev_c >= (high_945 * 1.0010))
                    upper_wick = c_h - max(c_o, candle_c)
                    body_r = (candle_c - c_l) / c_r if c_r > 0 else 1.0
                    wick_r = upper_wick / c_r if c_r > 0 else 0.0
                    
                    if is_confirmed and candle_c > vwap and vol_ratio >= 1.5 and body_r >= 0.70 and wick_r <= 0.35 and rsi <= 68.0:
                        sl = max(vwap, mid_point)
                        tp = candle_c + (2 * (candle_c - sl))
                        
                        future_df = df_full.iloc[idx_in_full+1:]
                        max_f_high = future_df['high'].max() if not future_df.empty else candle_c
                        min_f_low = future_df['low'].min() if not future_df.empty else candle_c
                        cur_ltp = df_full['close'].iloc[-1]
                        
                        sl_hit = min_f_low <= sl
                        tp_hit = max_f_high >= tp
                        
                        triggered_signals.append({
                            "symbol": symbol,
                            "type": "LONG",
                            "time": str(t_stamp.strftime("%H:%M")),
                            "entry": round(candle_c, 2),
                            "sl": round(sl, 2),
                            "target": round(tp, 2),
                            "vol_ratio": round(vol_ratio, 2),
                            "rsi": round(rsi, 1),
                            "current_price": round(cur_ltp, 2),
                            "lowest_against": round(min_f_low, 2),
                            "highest_reached": round(max_f_high, 2),
                            "status": "STOP LOSS HIT" if sl_hit else ("TARGET HIT" if tp_hit else "RUNNING IN PROFIT" if cur_ltp > candle_c else "RUNNING IN LOSS")
                        })
                        break
        except Exception:
            continue
            
    print("-------------------------------------------------------------------------")
    print(f"🎯 ALL SIGNALS GENERATED BY NEW 4-GUARDRAIL LOGIC TODAY ({len(triggered_signals)} TRADES):")
    print("-------------------------------------------------------------------------")
    for s in triggered_signals:
        print(f"Ticker: {s['symbol']:<10} | Signal: {s['type']:<5} | Time: {s['time']} | Entry: ₹{s['entry']} | SL: ₹{s['sl']} | Target: ₹{s['target']} | LTP: ₹{s['current_price']} | Outcome: {s['status']}")
        
    print("\n-------------------------------------------------------------------------")
    print(f"📊 STRATEGY PERFORMANCE SUMMARY TODAY:")
    print("-------------------------------------------------------------------------")
    if triggered_signals:
        df_res = pd.DataFrame(triggered_signals)
        win_count = len(df_res[df_res['status'].isin(['TARGET HIT', 'RUNNING IN PROFIT'])])
        loss_count = len(df_res[df_res['status'].isin(['STOP LOSS HIT'])])
        print(f"  Total Signals: {len(df_res)}")
        print(f"  Profitable / Winning Signals: {win_count} ({win_count/len(df_res)*100:.1f}%)")
        print(f"  Stop Loss Hit Signals: {loss_count}")
    else:
        print("  No signals triggered today under the 4 upgraded guardrails.")

if __name__ == "__main__":
    run_stress_test()
