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

def cache_bearish_candidates(kite, progress_callback=None, refresh_only=False):
    """
    Phase 1: Pre-Market "Weakness" Filter (9:00 AM - 9:15 AM)
    Identifies F&O stocks that are structurally weak.
    """
    logging.info("🚀 Starting Phase 1: Pre-Market F&O Bearish Weakness Filter...")
    
    if refresh_only and os.path.exists(BEARISH_CACHE_FILE):
        # Refresh logic: Load existing cache
        cache_df = pd.read_csv(BEARISH_CACHE_FILE)
        symbols = cache_df['Ticker'].tolist()
        logging.info(f"Refreshing {len(symbols)} candidates with early weakness momentum...")
    else:
        # Full scan: Get all F&O tickers
        fno_tickers_ns = scanner.get_nifty500_fno_tickers()
        symbols = [s.replace(".NS", "") for s in fno_tickers_ns]
        logging.info(f"Scanning all {len(symbols)} F&O tickers for structural weakness...")
    
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
            
            # 2. Yesterday's Low (Correctly handle pre-market vs post-open)
            # If run before market open, iloc[-1] is yesterday.
            # If run after market open, iloc[-1] is today, so iloc[-2] is yesterday.
            today_date = datetime.date.today()
            if latest.name.date() == today_date:
                pdl = prev['low']
            else:
                pdl = latest['low']
            
            # 3. Early Weakness Filter (If refreshing between 9:20 - 9:30)
            if refresh_only:
                from_intra = to_date.replace(hour=9, minute=15, second=0, microsecond=0)
                df_intra = kite_scanner.fetch_kite_data(kite, token, from_intra, to_date, "5minute")
                if not df_intra.empty:
                    today_open = df_intra.iloc[0]['open']
                    today_ltp = df_intra.iloc[-1]['close']
                    # STRICT FILTER: Price must be below Today's Open AND near/below PDL
                    if today_ltp > today_open or today_ltp > pdl * 1.002: 
                        continue # Skip stocks showing strength or too far above PDL
            
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
    # Make to_date timezone-aware to match Kite data (IST)
    if to_date.tzinfo is None:
        import pytz
        to_date = pytz.timezone('Asia/Kolkata').localize(to_date)
        
    from_date_intra = to_date - datetime.timedelta(days=4)
    
    total = len(cache_df)
    processed = 0
    
    # --- BROAD MARKET TREND CHECK (NIFTY 50) ---
    nifty_bullish = False
    try:
        nifty_token_map = kite_scanner.get_kite_instruments(kite, ["NIFTY 50"])
        if nifty_token_map and "NIFTY 50" in nifty_token_map:
            nifty_token = nifty_token_map["NIFTY 50"]
            nifty_from = to_date.replace(hour=9, minute=15, second=0, microsecond=0)
            nifty_df = kite_scanner.fetch_kite_data(kite, nifty_token, nifty_from, to_date, "5minute")
            if not nifty_df.empty:
                nifty_open = nifty_df.iloc[0]['open']
                nifty_ltp = nifty_df.iloc[-1]['close']
                nifty_bullish = nifty_ltp > nifty_open
                logging.info(f"Broad Market Check -> Nifty Open: {nifty_open:.2f}, LTP: {nifty_ltp:.2f} | Bullish? {nifty_bullish}")
    except Exception as ne:
        logging.warning(f"Failed to fetch Nifty 50 trend: {ne}")
        
    # --- BATCH PRE-SCREEN (Speed Optimization) ---
    # Fetch LTP for all candidates in one call to see who is actually near or below OR Low/PDL
    logging.info(f"Pre-screening {total} bearish candidates with batch quotes...")
    try:
        all_tickers = [f"NSE:{s}" for s in cache_df['Ticker']]
        quotes = kite.ohlc(all_tickers)
        
        # Filter candidates: Price must be near or below the breakdown level
        active_candidates = []
        for _, row in cache_df.iterrows():
            q = quotes.get(f"NSE:{row['Ticker']}")
            if q:
                ltp = q['last_price']
                breakdown_level = min(row['Yesterday_Low'], 50000) # Dummy high value if not set
                # Only process if price is below breakdown level or within 0.5% of it
                if ltp <= breakdown_level * 1.005:
                    active_candidates.append(row)
        
        if not active_candidates:
            logging.info("No bearish candidates currently near breakdown levels.")
            return pd.DataFrame()
            
        processing_list = pd.DataFrame(active_candidates)
        total = len(processing_list)
        logging.info(f"Reduced processing list to {total} active candidates.")
    except Exception as e:
        logging.warning(f"Batch pre-screen failed: {e}")
        processing_list = cache_df

    for _, row in processing_list.iterrows():
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
                
            # Calculate 5-minute indicators on the historical + today intraday data
            df_intra.ta.rsi(length=14, append=True)
            
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
            
            # --- TRIGGER CONFIRMATION (Smart Completion Logic) ---
            # Check if the latest candle in df_today is fully completed
            latest_candle_data = df_today.iloc[-1]
            candle_start = latest_candle_data.name
            # If current time is past the end of this candle, it's completed
            if to_date >= candle_start + datetime.timedelta(minutes=5):
                confirmed_candle = latest_candle_data
            else:
                # Still running, use the previous one (which is definitely completed)
                confirmed_candle = df_today.iloc[-2] if len(df_today) > 1 else latest_candle_data
            
            confirmed_close = confirmed_candle['close']
            confirmed_high = confirmed_candle['high']
            confirmed_low = confirmed_candle['low']
            
            # --- CONVICTION & OVEREXTENSION FILTERS ---
            # 1. Volume Spike (Demand higher volume if market is bullish)
            first_15m_vol = or_candles['volume'].sum()
            avg_15m_vol = row['Avg_15m_Vol']
            vol_spike_threshold = 1.8 if nifty_bullish else 1.2
            vol_spike = first_15m_vol > (vol_spike_threshold * avg_15m_vol) if avg_15m_vol > 0 else True
            
            vwap = calculate_vwap(df_today)
            below_vwap = ltp < vwap
            
            # 2. RSI Intraday Oversold Filter
            # If 5-min RSI is < 30, the move is extended in the short-term (likely to bounce immediately)
            latest_rsi = latest_candle['RSI_14'] if 'RSI_14' in latest_candle else 50
            is_oversold = latest_rsi < 30
            
            # 3. Daily Extension Filter
            # If already down > 3.0% from yesterday's close, the stock is already extended daily
            day_change_pct = (ltp - row['Prev_Close']) / row['Prev_Close'] * 100
            is_extended = day_change_pct < -3.0
            
            # 4. Candle Shape confirmation
            # The breakdown candle must close in the lower half of its range to ensure bearish dominance
            candle_ok = confirmed_close < (confirmed_high + confirmed_low) / 2
            
            # 5. Consolidation check of last 3 candles before confirmed candle
            confirmed_idx = df_today.index.get_loc(confirmed_candle.name)
            if confirmed_idx >= 3:
                preceding_candles = df_today.iloc[confirmed_idx-3:confirmed_idx]
                preceding_low = preceding_candles['low'].min()
                tight_range = (preceding_candles['high'].max() - preceding_low) / preceding_low * 100 if preceding_low > 0 else 99
                is_consolidating = tight_range <= 0.50
            else:
                is_consolidating = True

            # --- TRIGGER (with Retest Recovery confirmation) ---
            breakdown_level = min(or_low, pdl)
            
            # Find the first breakdown candle in df_today
            bd_idx = -1
            for idx in range(len(df_today)):
                if df_today.iloc[idx]['close'] < breakdown_level:
                    bd_idx = idx
                    break
            
            is_breakdown = False
            if bd_idx != -1:
                confirmed_candle_idx = df_today.index.get_loc(confirmed_candle.name)
                # Case 1: Fresh Breakdown (within the immediate next candle of the breakdown close)
                if confirmed_candle_idx == bd_idx:
                    is_breakdown = True
                else:
                    # Case 2: Breakdown of Retest
                    # Look for a retest (high >= breakdown_level) after the breakdown candle
                    has_retested = False
                    re_idx = -1
                    for idx in range(bd_idx + 1, len(df_today)):
                        if df_today.iloc[idx]['high'] >= breakdown_level:
                            has_retested = True
                            re_idx = idx
                    
                    if has_retested:
                        # Recovery: current price is back below breakdown_level,
                        # and either previous candle closed above it, or the retest was very recent.
                        prev_close = df_today.iloc[-2]['close'] if len(df_today) > 1 else ltp
                        retest_is_recent = (len(df_today) - 1 - re_idx) <= 2
                        if ltp < breakdown_level and (prev_close >= breakdown_level or retest_is_recent):
                            is_breakdown = True
            
            # --- SLIPPAGE / NO-CHASE FILTER (Tightened from 0.8% to 0.4%) ---
            # Discard if price has already dropped > 0.4% from the breakdown level
            slippage_pct = (breakdown_level - ltp) / breakdown_level * 100
            is_chasing = slippage_pct > 0.4
            
            if vol_spike and below_vwap and not is_chasing and not is_oversold and not is_extended and candle_ok and (not nifty_bullish) and is_consolidating:
                # If Post-9:30 and BEFORE 2:45 PM and Breakdown Triggered
                if datetime.time(9, 30) <= to_date.time() <= datetime.time(14, 45) and is_breakdown:

                    # Risk Management (Capital: 250,000 per trade)
                    # Retest limit entry: enter at breakdown_level if touch occurred, else close
                    entry_price = breakdown_level if confirmed_candle['high'] >= breakdown_level else ltp
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
                        "Status": "Closed for Day" if to_date.time() > datetime.time(14, 45) else "Monitoring",
                        "Token": token
                    })

                    
        except Exception as e:
            logging.error(f"Error scanning {symbol}: {e}")
            continue
            
    return pd.DataFrame(results)
