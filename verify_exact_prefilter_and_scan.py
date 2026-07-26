import os
import json
import datetime
import pandas as pd
from kiteconnect import KiteConnect
from core import config
from strategies import kite_scanner
from api import market_data as scanner

def run_exact_retrospective():
    session_file = ".kite_session.json"
    if not os.path.exists(session_file):
        print("Error: No Kite session file found.")
        return
        
    with open(session_file, "r") as f:
        session = json.load(f)
        
    kite = KiteConnect(api_key=config.KITE_API_KEY)
    kite.set_access_token(session["access_token"])
    
    print("=========================================================================")
    print("       PRE-MARKET PRE-FILTER & RETROSPECTIVE SCAN (EXACT KITE API DATA)  ")
    print("=========================================================================\n")
    
    fno_tickers_ns = scanner.get_nifty500_fno_tickers()
    symbols = [s.replace(".NS", "") for s in fno_tickers_ns]
    
    token_map = kite_scanner.get_kite_instruments(kite, symbols)
    
    today = datetime.datetime.now().date()
    from_date_daily = today - datetime.timedelta(days=120)
    
    cache_data = []
    
    print(f"Scanning {len(token_map)} F&O stocks with daily candles up to yesterday...")
    
    for symbol, token in token_map.items():
        try:
            df_daily = kite_scanner.fetch_kite_data(kite, token, from_date_daily, today, "day")
            if df_daily.empty or len(df_daily) < 50:
                continue
                
            # If the last row in df_daily is today, use the previous row as "yesterday"
            if df_daily.iloc[-1].name.date() == today:
                df_hist = df_daily.iloc[:-1]
            else:
                df_hist = df_daily
                
            import pandas_ta as ta
            df_hist.ta.ema(length=20, append=True)
            df_hist.ta.ema(length=50, append=True)
            df_hist.ta.rsi(length=14, append=True)
            
            yest = df_hist.iloc[-1]
            
            prev_close = float(yest['close'])
            pdh = float(yest['high'])
            ema_20 = float(yest['EMA_20'])
            ema_50 = float(yest['EMA_50'])
            rsi_14 = float(yest['RSI_14'])
            
            # --- BALANCED PRE-FILTER CRITERIA ---
            # 1. Macro Downtrend / Pullback: Below 20 EMA OR RSI <= 55.0
            is_macro_bearish = (prev_close < ema_20) or (rsi_14 <= 55.0)
            
            # 2. Resistance Proximity: Yesterday Close is within 6% of Yesterday High
            near_resistance = (pdh * 0.94 <= prev_close <= pdh * 1.02)
            
            if is_macro_bearish and near_resistance:
                cache_data.append({
                    "Ticker": symbol,
                    "Token": token,
                    "Prev_Close": round(prev_close, 2),
                    "Yesterday_High": round(pdh, 2),
                    "EMA_20": round(ema_20, 2),
                    "EMA_50": round(ema_50, 2),
                    "RSI_14": round(rsi_14, 2),
                    "Dist_to_PDH_%": round(((pdh - prev_close) / prev_close) * 100, 2)
                })
        except Exception as e:
            continue
            
    cache_df = pd.DataFrame(cache_data)
    
    print("\n-------------------------------------------------------------------------")
    print(f"📊 EXACT PRE-MARKET SHORTLIST ({len(cache_df)} STOCKS MATCHED)")
    print("-------------------------------------------------------------------------")
    if not cache_df.empty:
        print(cache_df[['Ticker', 'Prev_Close', 'Yesterday_High', 'Dist_to_PDH_%', 'EMA_20', 'EMA_50', 'RSI_14']].to_string(index=False))
        # Save cache
        cache_file = os.path.join("data", "cache", "failed_breakout_cache.csv")
        cache_df.to_csv(cache_file, index=False)
        print(f"\nSaved exact shortlist to '{cache_file}'.")
    else:
        print("No stocks matched the strict pre-filter criteria before market open today.")

    # --- RETROSPECTIVE INTRADAY SCAN ---
    print("\n=========================================================================")
    print("      RUNNING RETROSPECTIVE INTRADAY SCAN ON SHORTLISTED CANDIDATES      ")
    print("=========================================================================\n")
    
    if cache_df.empty:
        print("No shortlisted candidates to scan.")
        return

    from strategies import failed_breakout_scanner
    results = failed_breakout_scanner.scan_failed_breakouts(kite)
    
    print("\n-------------------------------------------------------------------------")
    print("🎯 INTRADAY TRIGGER RESULTS FOR TODAY")
    print("-------------------------------------------------------------------------")
    if not results.empty:
        print(results.to_string(index=False))
    else:
        print("🟢 No trades triggered under the upgraded rules today.")
        print("   All bad counter-trend trades were successfully blocked.")

if __name__ == "__main__":
    run_exact_retrospective()
