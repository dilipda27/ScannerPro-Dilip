import pandas as pd

import pandas_ta as ta
import datetime
import time
import logging
import os
import kite_scanner
import scanner

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BEARISH_CACHE_FILE = "bearish_breakdown_cache.csv"

def cache_bearish_candidates(kite, progress_callback=None):
    """
    Phase 1: Pre-Market "Weakness" Filter (9:00 AM - 9:15 AM)
    Identifies F&O stocks that are structurally weak.
    """
    logging.info("🚀 Starting Phase 1: Pre-Market F&O Bearish Weakness Filter...")
    
    # --- CHANGE: Filter for F&O Tickers only ---
    fno_tickers_ns = scanner.get_nifty500_fno_tickers()
    symbols = [s.replace(".NS", "") for s in fno_tickers_ns]
    
    token_map = kite_scanner.get_kite_instruments(kite, symbols)
    
    if not token_map:
        logging.error("Failed to retrieve instrument tokens.")
        return False

    cache_data = []
    to_date = datetime.datetime.now()
    from_date_daily = to_date - datetime.timedelta(days=100)
    
    total = len(token_map)
    processed = 0
    
    for symbol, token in token_map.items():
        processed += 1
        if progress_callback:
            progress_callback(processed, total, symbol)
            
        try:
            df_daily = kite_scanner.fetch_kite_data(kite, token, from_date_daily, to_date, "day")
            if df_daily.empty or len(df_daily) < 50:
                continue
                
            # Indicators
            df_daily.ta.ema(length=50, append=True)
            df_daily.ta.rsi(length=14, append=True)
            
            latest = df_daily.iloc[-1]
            prev = df_daily.iloc[-2]
            
            # --- PHASE 1 CRITERIA (Updated: No Gap Requirement) ---
            # 1. EMA Alignment (Price < 50 EMA) or RSI Weakness
            is_weak = latest['close'] < latest['EMA_50'] or latest['RSI_14'] < 55
            
            # 2. Yesterday's Low (Critical for Phase 3)
            pdl = prev['low']
            
            if is_weak:
                cache_data.append({
                    "Ticker": symbol,
                    "Token": token,
                    "Prev_Close": prev['close'],
                    "Yesterday_Low": round(pdl, 2),
                    "EMA_50": round(latest['EMA_50'], 2),
                    "RSI": round(latest['RSI_14'], 2),
                    "Avg_15m_Vol": 0.0 # Will be populated next

                })
        except Exception as e:
            logging.error(f"Error filtering {symbol}: {e}")
            continue
            
    if cache_data:
        cache_df = pd.DataFrame(cache_data)
        
        logging.info(f"Fetching 5-day average 15-min volume for {len(cache_df)} candidates...")
        for idx, row in cache_df.iterrows():
            try:
                df_hist = kite_scanner.fetch_kite_data(kite, row['Token'], to_date - datetime.timedelta(days=10), to_date, "15minute")
                if not df_hist.empty:
                    first_candles = df_hist[df_hist.index.time == datetime.time(9, 15)]
                    if len(first_candles) > 0:
                        avg_vol = first_candles['volume'].tail(5).mean()
                        cache_df.at[idx, 'Avg_15m_Vol'] = avg_vol
            except: pass
            
        cache_df.to_csv(BEARISH_CACHE_FILE, index=False)
        logging.info(f"Phase 1 Complete. {len(cache_df)} F&O stocks cached.")
        return True
    
    return False

def calculate_vwap(df):
    """Calculate VWAP for intraday data."""
    if df.empty:
        return 0
    tp = (df['high'] + df['low'] + df['close']) / 3
    vwap = (tp * df['volume']).sum() / df['volume'].sum()
    return vwap

def scan_bearish_breakdowns(kite, progress_callback=None):
    """
    Phase 2: Opening Range Check (9:15 AM - 9:30 AM)
    Phase 3: Breakdown Execution & Paper Trading (Post-9:30 AM)
    """
    logging.info("🔍 Starting Bearish Breakdown Scan (Phase 2 & 3)...")
    
    if not os.path.exists(BEARISH_CACHE_FILE):
        logging.error("Bearish cache file not found. Run Phase 1 first.")
        return pd.DataFrame()
        
    cache_df = pd.read_csv(BEARISH_CACHE_FILE)
    results = []
    
    to_date = datetime.datetime.now()
    from_date_intra = to_date.replace(hour=9, minute=15, second=0, microsecond=0)
    
    total = len(cache_df)
    processed = 0
    
    for _, row in cache_df.iterrows():
        processed += 1
        symbol = row['Ticker']
        token = int(row['Token'])
        pdl = row['Yesterday_Low']
        
        if progress_callback:
            progress_callback(processed, total, symbol)
            
        try:
            df_intra = kite_scanner.fetch_kite_data(kite, token, from_date_intra, to_date, "5minute")
            if df_intra.empty:
                continue
                
            df_today = df_intra[df_intra.index.date == to_date.date()]
            if df_today.empty:
                continue
                
            if len(df_today) < 3:
                continue
                
            or_candles = df_today.iloc[0:3]
            or_high = or_candles['high'].max()
            or_low = or_candles['low'].min()
            
            latest_candle = df_today.iloc[-1]
            ltp = latest_candle['close']
            
            # Use the last completed 5-min candle to confirm the breakdown (Filter fakeouts)
            completed_candle = df_today.iloc[-2] if len(df_today) > 1 else latest_candle
            completed_close = completed_candle['close']
            
            # --- PHASE 2 CRITERIA ---
            # Volume Spike
            first_15m_vol = or_candles['volume'].sum()
            avg_15m_vol = row['Avg_15m_Vol']
            vol_spike = first_15m_vol > (1.2 * avg_15m_vol) if avg_15m_vol > 0 else True
            
            vwap = calculate_vwap(df_today)
            below_vwap = ltp < vwap
            
            # --- PHASE 3 TRIGGER (Updated: 5-min Candle Close below PDL + OR Low) ---
            is_breakdown = completed_close < or_low and completed_close < pdl
            
            if vol_spike and below_vwap:
                # If Post-9:30 and BEFORE 15:00 (3 PM) and Breakdown Triggered
                if datetime.time(9, 30) <= to_date.time() <= datetime.time(15, 0) and is_breakdown:

                    # Risk Management (Capital: 250,000 per trade)
                    entry_price = ltp
                    qty = int(250000 / entry_price)
                    
                    # Structural Stop Loss (VWAP + 0.2% buffer)
                    vwap_sl = vwap * 1.002
                    # Minimum 0.5% risk, maximum 2.5% risk
                    stop_loss = max(vwap_sl, entry_price * 1.005)
                    stop_loss = min(stop_loss, entry_price * 1.025)
                    
                    risk = stop_loss - entry_price
                    target_price = entry_price - (2 * risk)
                    
                    results.append({
                        "Ticker": symbol,
                        "Entry Price": str(round(entry_price, 2)),

                        "Qty": qty,
                        "Invested Capital": str(round(qty * entry_price, 2)),
                        "OR Low": round(or_low, 2),
                        "Yesterday Low": round(pdl, 2),
                        "VWAP": round(vwap, 2),
                        "Stop Loss": str(round(stop_loss, 2)),
                        "Target": str(round(target_price, 2)),

                        "Status": "Triggered",
                        "Token": token
                    })
                    logging.info(f"🔴 Bearish Breakdown Detected: {symbol} at {entry_price}")
                else:
                    # Potential candidate but not yet triggered
                    results.append({
                        "Ticker": symbol,
                        "Entry Price": "Wait < " + str(min(round(or_low, 2), round(pdl, 2))),
                        "Qty": str(int(250000 / ltp)),

                        "Invested Capital": "-",
                        "OR Low": round(or_low, 2),
                        "Yesterday Low": round(pdl, 2),
                        "VWAP": round(vwap, 2),
                        "Stop Loss": "-",
                        "Target": "-",
                        "Status": "Closed for Day" if to_date.time() > datetime.time(15, 0) else "Monitoring",
                        "Token": token
                    })

                    
        except Exception as e:
            logging.error(f"Error scanning {symbol}: {e}")
            continue
            
    return pd.DataFrame(results)
