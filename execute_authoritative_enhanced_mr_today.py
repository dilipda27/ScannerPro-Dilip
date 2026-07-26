import os
import json
import datetime
import pandas as pd
from kiteconnect import KiteConnect
from core import config
from services import paper_trader

def run_authoritative_simulation():
    session_file = ".kite_session.json"
    if not os.path.exists(session_file):
        print("Error: Kite session file not found.")
        return
        
    with open(session_file, "r") as f:
        session = json.load(f)
        
    kite = KiteConnect(api_key=config.KITE_API_KEY)
    kite.set_access_token(session["access_token"])
    
    print("=========================================================================")
    print(" AUTHORITATIVE NSE RETROSPECTIVE SIMULATION (ENHANCED LOGIC + TRAILING SL)")
    print("=========================================================================\n")
    
    instruments = kite.instruments("NSE")
    nse_token_map = {item['tradingsymbol']: item['instrument_token'] for item in instruments}
    
    # Get F&O symbols
    from strategies import kite_scanner
    fno_symbols = [s for s in kite_scanner.get_nifty500_fno_symbols() if s in nse_token_map]
    
    today_date = datetime.date.today()
    from_dt = datetime.datetime.combine(today_date, datetime.time(9, 15))
    end_945_dt = datetime.datetime.combine(today_date, datetime.time(9, 45))
    cutoff_1130_dt = datetime.datetime.combine(today_date, datetime.time(11, 30))
    to_dt = datetime.datetime.now()
    
    print(f"1. Fetching 09:15-09:45 AM Range Data for {len(fno_symbols)} F&O symbols...")
    
    candidates = []
    
    for symbol in fno_symbols:
        token = nse_token_map[symbol]
        try:
            data = kite.historical_data(token, from_dt, end_945_dt, "minute")
            if not data or len(data) < 25:
                continue
            df_range = pd.DataFrame(data)
            df_range.columns = [c.lower() for c in df_range.columns]
            
            open_915 = df_range['open'].iloc[0]
            high_945 = df_range['high'].max()
            low_945 = df_range['low'].min()
            close_945 = df_range['close'].iloc[-1]
            range_w = high_945 - low_945
            pct_change_945 = ((close_945 - open_915) / open_915) * 100
            
            if range_w < 0.005 * close_945 or range_w > 0.018 * close_945:
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
            
    weak_sorted = sorted([c for c in candidates if c['classification'] == 'WEAK'], key=lambda x: x['pct_change_945'])[:15]
    strong_sorted = sorted([c for c in candidates if c['classification'] == 'STRONG'], key=lambda x: x['pct_change_945'], reverse=True)[:15]
    
    top_watchlist = {c['symbol']: c for c in (weak_sorted + strong_sorted)}
    print(f"   Watchlist Crated: Top {len(top_watchlist)} candidates ({len(weak_sorted)} WEAK, {len(strong_sorted)} STRONG).\n")
    
    print("2. Simulating Minute-by-Minute Execution (09:45 AM - 11:30 AM) & Trailing SL Trajectory...")
    
    triggered_trades = []
    
    for symbol, info in top_watchlist.items():
        if len(triggered_trades) >= 7: # Max 7 trades limit
            break
            
        try:
            token = info["token"]
            data_full = kite.historical_data(token, from_dt, to_dt, "minute")
            if not data_full or len(data_full) < 35:
                continue
            df_full = pd.DataFrame(data_full)
            df_full.columns = [c.lower() for c in df_full.columns]
            
            # Calculate VWAP & RSI
            # VWAP calculation
            tp = (df_full['high'] + df_full['low'] + df_full['close']) / 3
            df_full['vwap'] = (tp * df_full['volume']).cumsum() / df_full['volume'].cumsum()
            
            import pandas_ta as ta
            df_full.ta.rsi(length=14, append=True)
            
            # Filter post 9:45 candles up to 11:30 cutoff
            df_full['time_only'] = df_full['date'].dt.time
            cutoff_time = datetime.time(11, 30)
            start_time = datetime.time(9, 45)
            
            df_post = df_full[(df_full['time_only'] >= start_time) & (df_full['time_only'] <= cutoff_time)]
            if len(df_post) < 2:
                continue
                
            high_945 = info["high_945"]
            low_945 = info["low_945"]
            mid_point = (high_945 + low_945) / 2
            
            for idx in range(1, len(df_post)):
                row_curr = df_post.iloc[idx]
                row_prev = df_post.iloc[idx-1]
                t_stamp = row_curr['date']
                
                candle_c = row_curr['close']
                prev_c = row_prev['close']
                
                full_idx = df_full[df_full['date'] == t_stamp].index[0]
                vwap = df_full['vwap'].iloc[full_idx]
                rsi = df_full['RSI_14'].iloc[full_idx] if 'RSI_14' in df_full.columns else 50.0
                
                prev_5_vol = df_full['volume'].iloc[max(0, full_idx-5):full_idx].mean()
                cur_vol = df_full['volume'].iloc[full_idx]
                vol_ratio = cur_vol / prev_5_vol if prev_5_vol > 0 else 1.0
                
                c_h, c_l, c_o = row_curr['high'], row_curr['low'], row_curr['open']
                c_r = c_h - c_l
                
                if info["classification"] == "WEAK":
                    breakdown_lvl = low_945 * 0.9975
                    is_confirmed = (candle_c <= breakdown_lvl) and (prev_c <= (low_945 * 0.9990))
                    lower_wick = min(c_o, candle_c) - c_l
                    body_r = (c_h - candle_c) / c_r if c_r > 0 else 1.0
                    wick_r = lower_wick / c_r if c_r > 0 else 0.0
                    
                    if is_confirmed and candle_c < vwap and vol_ratio >= 1.2 and body_r >= 0.50 and wick_r <= 0.45:
                        entry_price = float(candle_c)
                        initial_sl = max(vwap * 1.003, mid_point)
                        if (initial_sl - entry_price) / entry_price > 0.015:
                            initial_sl = entry_price * 1.015
                        target = entry_price - (2 * (initial_sl - entry_price))
                        capital = 250000
                        qty = int(capital / entry_price)
                        risk = initial_sl - entry_price
                        
                        # Minute-by-minute trajectory simulation after entry
                        future_df = df_full.iloc[full_idx+1:]
                        current_sl = initial_sl
                        status = "Active"
                        exit_price = None
                        exit_time = None
                        pnl = 0.0
                        
                        for f_idx in range(len(future_df)):
                            f_row = future_df.iloc[f_idx]
                            f_time_str = f_row['date'].strftime("%Y-%m-%d %H:%M")
                            
                            # 1. Check SL
                            if f_row['high'] >= current_sl:
                                status = "Closed"
                                exit_price = current_sl
                                exit_time = f_time_str
                                pnl = (entry_price - exit_price) * qty
                                break
                            # 2. Check Target
                            elif f_row['low'] <= target:
                                status = "Closed"
                                exit_price = target
                                exit_time = f_time_str
                                pnl = (entry_price - target) * qty
                                break
                                
                            # 3. Trailing SL Update
                            # Rule A: Target Lock (80% target dist) -> Lock 60% profit
                            t_dist = entry_price - target
                            if (entry_price - f_row['low']) >= 0.80 * t_dist:
                                new_sl = entry_price - (0.60 * t_dist)
                                if new_sl < current_sl: current_sl = new_sl
                            # Rule B: 1.5R -> Trail to +1.0R
                            elif (entry_price - f_row['low']) >= 1.5 * risk:
                                new_sl = entry_price - (1.0 * risk)
                                if new_sl < current_sl: current_sl = new_sl
                            # Rule C: 1.0R -> Trail to +0.5R
                            elif (entry_price - f_row['low']) >= 1.0 * risk:
                                new_sl = entry_price - (0.5 * risk)
                                if new_sl < current_sl: current_sl = new_sl
                            # Rule D: 0.5R -> Trail to Breakeven
                            elif (entry_price - f_row['low']) >= 0.5 * risk:
                                if entry_price < current_sl: current_sl = entry_price
                                
                        cur_ltp = float(df_full['close'].iloc[-1])
                        if status == "Active":
                            pnl = (entry_price - cur_ltp) * qty
                            
                        triggered_trades.append({
                            "Ticker": symbol,
                            "Type": "Bearish Breakdown",
                            "EntryPrice": entry_price,
                            "SL": float(current_sl),
                            "InitialSL": float(initial_sl),
                            "Target": float(target),
                            "Qty": qty,
                            "Token": float(info["token"]),
                            "EntryTime": str(t_stamp.strftime("%Y-%m-%d %H:%M")),
                            "Status": status,
                            "Strategy": "Morning Range Str/Wk",
                            "ExitPrice": exit_price if exit_price else cur_ltp,
                            "ExitTime": exit_time if exit_time else "",
                            "PnL": pnl,
                            "Current Price": cur_ltp
                        })
                        break
                        
                elif info["classification"] == "STRONG":
                    breakout_lvl = high_945 * 1.0025
                    is_confirmed = (candle_c >= breakout_lvl) and (prev_c >= (high_945 * 1.0010))
                    upper_wick = c_h - max(c_o, candle_c)
                    body_r = (candle_c - c_l) / c_r if c_r > 0 else 1.0
                    wick_r = upper_wick / c_r if c_r > 0 else 0.0
                    
                    if is_confirmed and candle_c > vwap and vol_ratio >= 1.2 and body_r >= 0.50 and wick_r <= 0.45:
                        entry_price = float(candle_c)
                        initial_sl = min(vwap * 0.997, mid_point)
                        if (entry_price - initial_sl) / entry_price > 0.015:
                            initial_sl = entry_price * 0.985
                        target = entry_price + (2 * (entry_price - initial_sl))
                        capital = 250000
                        qty = int(capital / entry_price)
                        risk = entry_price - initial_sl
                        
                        future_df = df_full.iloc[full_idx+1:]
                        current_sl = initial_sl
                        status = "Active"
                        exit_price = None
                        exit_time = None
                        pnl = 0.0
                        
                        for f_idx in range(len(future_df)):
                            f_row = future_df.iloc[f_idx]
                            f_time_str = f_row['date'].strftime("%Y-%m-%d %H:%M")
                            
                            if f_row['low'] <= current_sl:
                                status = "Closed"
                                exit_price = current_sl
                                exit_time = f_time_str
                                pnl = (exit_price - entry_price) * qty
                                break
                            elif f_row['high'] >= target:
                                status = "Closed"
                                exit_price = target
                                exit_time = f_time_str
                                pnl = (target - entry_price) * qty
                                break
                                
                            t_dist = target - entry_price
                            if (f_row['high'] - entry_price) >= 0.80 * t_dist:
                                new_sl = entry_price + (0.60 * t_dist)
                                if new_sl > current_sl: current_sl = new_sl
                            elif (f_row['high'] - entry_price) >= 1.5 * risk:
                                new_sl = entry_price + (1.0 * risk)
                                if new_sl > current_sl: current_sl = new_sl
                            elif (f_row['high'] - entry_price) >= 1.0 * risk:
                                new_sl = entry_price + (0.5 * risk)
                                if new_sl > current_sl: current_sl = new_sl
                            elif (f_row['high'] - entry_price) >= 0.5 * risk:
                                if entry_price > current_sl: current_sl = entry_price
                                
                        cur_ltp = float(df_full['close'].iloc[-1])
                        if status == "Active":
                            pnl = (cur_ltp - entry_price) * qty
                            
                        triggered_trades.append({
                            "Ticker": symbol,
                            "Type": "Bullish Breakout",
                            "EntryPrice": entry_price,
                            "SL": float(current_sl),
                            "InitialSL": float(initial_sl),
                            "Target": float(target),
                            "Qty": qty,
                            "Token": float(info["token"]),
                            "EntryTime": str(t_stamp.strftime("%Y-%m-%d %H:%M")),
                            "Status": status,
                            "Strategy": "Morning Range Str/Wk",
                            "ExitPrice": exit_price if exit_price else cur_ltp,
                            "ExitTime": exit_time if exit_time else "",
                            "PnL": pnl,
                            "Current Price": cur_ltp
                        })
                        break
        except Exception as e:
            print(f"Error evaluating {symbol}: {e}")
            continue
            
    print("-------------------------------------------------------------------------")
    print(f"RESULTS OF AUTHORITATIVE ENHANCED MORNING RANGE SIMULATION ({len(triggered_trades)} TRADES):")
    print("-------------------------------------------------------------------------")
    for t in triggered_trades:
        print(f"Ticker: {t['Ticker']:<10} | Type: {t['Type']:<18} | Entry: Rs. {t['EntryPrice']} ({t['EntryTime']}) | Status: {t['Status']:<6} | Exit/LTP: Rs. {t['ExitPrice']} | PnL: Rs. {t['PnL']:+.2f}")
        
    today_str = today_date.strftime("%Y-%m-%d")
    portfolio_file = os.path.join("data", "trades", "paper_portfolio.csv")
    history_file = os.path.join("data", "trades", "paper_trade_history.csv")
    archive_file = os.path.join("data", "trades", "paper_trade_archive.csv")
    
    # 1. Update Portfolio
    if os.path.exists(portfolio_file):
        df_port = pd.read_csv(portfolio_file)
        mask_mr = (df_port['Strategy'] == 'Morning Range Str/Wk') & (df_port['EntryTime'].astype(str).str.contains(today_str, na=False))
        df_port_other = df_port[~mask_mr].copy()
        
        active_mr_rows = []
        for t in triggered_trades:
            if t['Status'] == 'Active':
                active_mr_rows.append({
                    "Ticker": t['Ticker'],
                    "Type": t['Type'],
                    "EntryPrice": t['EntryPrice'],
                    "SL": t['SL'],
                    "InitialSL": t['InitialSL'],
                    "Target": t['Target'],
                    "Qty": t['Qty'],
                    "Token": t['Token'],
                    "EntryTime": t['EntryTime'],
                    "Status": "Active",
                    "Strategy": t['Strategy'],
                    "Delta": "",
                    "ExitPrice": "",
                    "ExitTime": "",
                    "Capital Deployed": round(t['EntryPrice'] * t['Qty'], 2),
                    "Final P&L": "",
                    "P&L %": "",
                    "Current Price": t['Current Price'],
                    "PnL": t['PnL']
                })
                
        df_port_new = pd.concat([df_port_other, pd.DataFrame(active_mr_rows)], ignore_index=True) if active_mr_rows else df_port_other
        df_port_new.to_csv(portfolio_file, index=False)
        print(f"\nUpdated '{portfolio_file}': Added {len(active_mr_rows)} active Morning Range trades.")

    # 2. Update History
    if os.path.exists(history_file):
        df_hist = pd.read_csv(history_file)
        mask_mr = (df_hist['Strategy'] == 'Morning Range Str/Wk') & (df_hist['EntryTime'].astype(str).str.contains(today_str, na=False))
        df_hist_other = df_hist[~mask_mr].copy()
        
        closed_mr_rows = []
        for t in triggered_trades:
            if t['Status'] == 'Closed':
                closed_mr_rows.append({
                    "Ticker": t['Ticker'],
                    "Type": t['Type'],
                    "EntryPrice": t['EntryPrice'],
                    "ExitPrice": t['ExitPrice'],
                    "SL": t['SL'],
                    "InitialSL": t['InitialSL'],
                    "Target": t['Target'],
                    "Qty": t['Qty'],
                    "Token": t['Token'],
                    "EntryTime": t['EntryTime'],
                    "ExitTime": t['ExitTime'],
                    "Status": "Closed",
                    "Strategy": t['Strategy'],
                    "Delta": "",
                    "Capital Deployed": round(t['EntryPrice'] * t['Qty'], 2),
                    "Final P&L": t['PnL'],
                    "P&L %": (t['PnL'] / (t['EntryPrice'] * t['Qty'])) * 100 if t['Qty'] > 0 else 0.0,
                    "Current Price": "",
                    "PnL": t['PnL']
                })
                
        df_hist_new = pd.concat([df_hist_other, pd.DataFrame(closed_mr_rows)], ignore_index=True) if closed_mr_rows else df_hist_other
        df_hist_new.to_csv(history_file, index=False)
        print(f"Updated '{history_file}': Added {len(closed_mr_rows)} closed Morning Range trades.")

    # 3. Update Archive
    if os.path.exists(archive_file):
        df_arch = pd.read_csv(archive_file)
        mask_mr = (df_arch['Strategy'] == 'Morning Range Str/Wk') & (df_arch['EntryTime'].astype(str).str.contains(today_str, na=False))
        df_arch_other = df_arch[~mask_mr].copy()
        
        df_arch_new = pd.concat([df_arch_other, pd.DataFrame(closed_mr_rows)], ignore_index=True) if closed_mr_rows else df_arch_other
        df_arch_new.to_csv(archive_file, index=False)
        print(f"Updated '{archive_file}': Added {len(closed_mr_rows)} closed Morning Range trades.")

    paper_trader.export_history_to_excel()
    print("Refreshed 'paper_trade_history.xlsx' workbook successfully!")

if __name__ == "__main__":
    run_authoritative_simulation()
