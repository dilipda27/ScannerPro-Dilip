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

BULLISH_CACHE_FILE = os.path.join("data", "cache", "fno_strength_cache.csv")

def cache_bullish_candidates(kite, progress_callback=None, refresh_only=False):
    """
    Phase 1: Pre-Market "Strength" Filter (9:00 AM - 9:15 AM)
    Identifies F&O stocks that are structurally strong.
    If refresh_only is True, it updates the list between 9:20-9:30 with today's early momentum.
    """
    logging.info("🚀 Starting Bullish Strength Filter...")
    
    if refresh_only and os.path.exists(BULLISH_CACHE_FILE):
        # Refresh logic: Load existing cache and filter based on today's 9:15-9:20/9:25 action
        cache_df = pd.read_csv(BULLISH_CACHE_FILE)
        symbols = cache_df['Ticker'].tolist()
        logging.info(f"Refreshing {len(symbols)} existing candidates with today's momentum...")
    else:
        # Full scan: Get all F&O tickers
        fno_tickers_ns = scanner.get_nifty500_fno_tickers()
        symbols = [s.replace(".NS", "") for s in fno_tickers_ns]
        logging.info(f"Scanning all {len(symbols)} F&O tickers for structural strength...")
    
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
            # Daily filter for structural strength
            df_daily = kite_scanner.fetch_kite_data(kite, token, from_date_daily, to_date, "day")
            if df_daily.empty or len(df_daily) < 50:
                continue
                
            # Indicators
            df_daily.ta.ema(length=50, append=True)
            df_daily.ta.rsi(length=14, append=True)
            
            latest = df_daily.iloc[-1]
            prev = df_daily.iloc[-2]
            
            # --- PHASE 1 BULLISH CRITERIA (Most Probable) ---
            # 1. Price above 50 EMA AND RSI > 50 (Strong Momentum)
            is_strong = latest['close'] > latest['EMA_50'] and latest['RSI_14'] > 50
            
            # 2. Yesterday's High (Correctly handle pre-market vs post-open)
            today_date = datetime.date.today()
            if latest.name.date() == today_date:
                pdh = prev['high']
            else:
                pdh = latest['high']
            
            # 3. Early Momentum Filter (If refreshing between 9:20 - 9:30)
            if refresh_only:
                from_intra = to_date.replace(hour=9, minute=15, second=0, microsecond=0)
                if from_intra > to_date:
                    from_intra = from_intra - datetime.timedelta(days=1)
                df_intra = kite_scanner.fetch_kite_data(kite, token, from_intra, to_date, "5minute")
                if not df_intra.empty:
                    today_open = df_intra.iloc[0]['open']
                    today_ltp = df_intra.iloc[-1]['close']
                    # Must be gapping up or trading above open and ideally above PDH early
                    if today_ltp < today_open or today_ltp < pdh * 0.995: 
                        continue # Skip weak opens
            
            if is_strong:
                cache_data.append({
                    "Ticker": symbol,
                    "Token": token,
                    "Prev_Close": prev['close'],
                    "Yesterday_High": round(pdh, 2),
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
            
        cache_df.to_csv(BULLISH_CACHE_FILE, index=False)
        logging.info(f"Cache Updated. {len(cache_df)} stocks shortlist ready.")
        return True
    
    return False

def calculate_vwap(df):
    if df.empty: return 0
    tp = (df['high'] + df['low'] + df['close']) / 3
    vwap = (tp * df['volume']).sum() / df['volume'].sum()
    return vwap

def scan_bullish_breakouts(kite, progress_callback=None):
    """
    Phase 2: Opening Range Check (9:15 AM - 9:30 AM)
    Phase 3: Breakout Execution (Post-9:30 AM)
    """
    logging.info("🔍 Starting Bullish Breakout Scan...")
    
    if not os.path.exists(BULLISH_CACHE_FILE):
        logging.error("Bullish cache file not found. Run Phase 1 first.")
        return pd.DataFrame()
        
    cache_df = pd.read_csv(BULLISH_CACHE_FILE)
    results = []
    
    to_date = datetime.datetime.now()
    # Make to_date timezone-aware to match Kite data (IST)
    if to_date.tzinfo is None:
        import pytz
        to_date = pytz.timezone('Asia/Kolkata').localize(to_date)
        
    from_date_intra = to_date.replace(hour=9, minute=15, second=0, microsecond=0)
    if from_date_intra > to_date:
        from_date_intra = from_date_intra - datetime.timedelta(days=1)
    
    # --- BROAD MARKET TREND CHECK (NIFTY 50) ---
    nifty_bullish = False
    try:
        nifty_token_map = kite_scanner.get_kite_instruments(kite, ["NIFTY 50"])
        if nifty_token_map and "NIFTY 50" in nifty_token_map:
            nifty_token = nifty_token_map["NIFTY 50"]
            nifty_from = to_date.replace(hour=9, minute=15, second=0, microsecond=0)
            if nifty_from > to_date:
                nifty_from = nifty_from - datetime.timedelta(days=1)
            nifty_df = kite_scanner.fetch_kite_data(kite, nifty_token, nifty_from, to_date, "5minute")
            if not nifty_df.empty:
                nifty_open = nifty_df.iloc[0]['open']
                nifty_ltp = nifty_df.iloc[-1]['close']
                nifty_bullish = nifty_ltp > nifty_open
                logging.info(f"Broad Market Check -> Nifty Open: {nifty_open:.2f}, LTP: {nifty_ltp:.2f} | Bullish? {nifty_bullish}")
    except Exception as ne:
        logging.warning(f"Failed to fetch Nifty 50 trend: {ne}")

    total = len(cache_df)
    processed = 0
    
    # --- BATCH PRE-SCREEN (Speed Optimization) ---
    logging.info(f"Pre-screening {total} bullish candidates with batch quotes...")
    try:
        all_tickers = [f"NSE:{s}" for s in cache_df['Ticker']]
        quotes = kite_scanner.fetch_ohlc_safe(kite, all_tickers)
        
        active_candidates = []
        for _, row in cache_df.iterrows():
            q = quotes.get(f"NSE:{row['Ticker']}")
            if q:
                ltp = q['last_price']
                breakout_level = max(row['Yesterday_High'], 0)
                # Only process if price is above breakout level or within 0.5% of it
                if ltp >= breakout_level * 0.995:
                    active_candidates.append(row)
        
        if not active_candidates:
            logging.info("No bullish candidates currently near breakout levels.")
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
        pdh = row['Yesterday_High']
        
        if progress_callback:
            progress_callback(processed, total, symbol)
            
        try:
            df_intra = kite_scanner.fetch_kite_data(kite, token, from_date_intra, to_date, "5minute")
            if df_intra.empty:
                continue
                
            df_today = df_intra[df_intra.index.date == to_date.date()]
            if df_today.empty:
                continue
                
            # Need at least 3 candles (9:15, 9:20, 9:25) to form the 15-min range
            if len(df_today) < 3:
                # Still show in monitoring mode if we have at least 1 candle
                ltp = df_today.iloc[-1]['close']
                results.append({
                    "Ticker": symbol,
                    "Entry Price": "Wait for 15m OR",
                    "Qty": "-",
                    "Invested Capital": "-",
                    "OR High": "-",
                    "Yesterday High": round(pdh, 2),
                    "VWAP": "-",
                    "Stop Loss": "-",
                    "Target": "-",
                    "Status": "Initializing",
                    "Token": token
                })
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
            
            # --- BULLISH CRITERIA ---
            # 1. Volume Spike (demand higher volume if market is bearish/volatile)
            first_15m_vol = or_candles['volume'].sum()
            avg_15m_vol = row['Avg_15m_Vol']
            vol_spike_multiplier = 1.2 if nifty_bullish else 1.8
            vol_spike = first_15m_vol > (vol_spike_multiplier * avg_15m_vol) if avg_15m_vol > 0 else True
            
            # 2. Above VWAP
            vwap = calculate_vwap(df_today)
            above_vwap = ltp > vwap
            
            # 3. BREAKOUT TRIGGER: 5-min close above both OR High and Yesterday High (with Retest Recovery confirmation)
            breakout_level = max(or_high, pdh)
            
            # Find the first breakout candle in df_today
            bo_idx = -1
            for idx in range(len(df_today)):
                if df_today.iloc[idx]['close'] > breakout_level:
                    bo_idx = idx
                    break
            
            is_breakout = False
            if bo_idx != -1:
                confirmed_candle_idx = df_today.index.get_loc(confirmed_candle.name)
                # Case 1: Fresh Breakout (within the immediate next candle of the breakout close)
                if confirmed_candle_idx == bo_idx:
                    is_breakout = True
                else:
                    # Case 2: Breakout of Retest
                    # Look for a retest (low <= breakout_level) after the breakout candle
                    has_retested = False
                    re_idx = -1
                    for idx in range(bo_idx + 1, len(df_today)):
                        if df_today.iloc[idx]['low'] <= breakout_level:
                            has_retested = True
                            re_idx = idx
                    
                    if has_retested:
                        # Recovery: current price is back above breakout_level,
                        # and either previous candle closed below it, or the retest was very recent.
                        prev_close = df_today.iloc[-2]['close'] if len(df_today) > 1 else ltp
                        retest_is_recent = (len(df_today) - 1 - re_idx) <= 2
                        if ltp > breakout_level and (prev_close <= breakout_level or retest_is_recent):
                            is_breakout = True
            
            # 4. Consolidation check of last 3 candles before confirmed candle
            confirmed_idx = df_today.index.get_loc(confirmed_candle.name)
            if confirmed_idx >= 3:
                preceding_candles = df_today.iloc[confirmed_idx-3:confirmed_idx]
                preceding_low = preceding_candles['low'].min()
                tight_range = (preceding_candles['high'].max() - preceding_low) / preceding_low * 100 if preceding_low > 0 else 99
                is_consolidating = tight_range <= 0.50
            else:
                is_consolidating = True

            # --- SLIPPAGE / NO-CHASE FILTER (New) ---
            # Discard if price has already moved > 0.8% from the breakout level
            slippage_pct = (ltp - breakout_level) / breakout_level * 100
            is_chasing = slippage_pct > 0.8
            
            if vol_spike and above_vwap and not is_chasing and nifty_bullish and is_consolidating:
                # Active Trading Hours
                if datetime.time(9, 30) <= to_date.time() <= datetime.time(14, 45) and is_breakout:
                    # Retest limit entry: enter at breakout_level if touch occurred, else close
                    entry_price = breakout_level if confirmed_candle['low'] <= breakout_level else ltp
                    qty = int(250000 / entry_price)
                    
                    # Structural SL: VWAP - 0.2% buffer
                    vwap_sl = vwap * 0.998
                    # Min 0.5% risk, Max 2.5% risk
                    stop_loss = min(vwap_sl, entry_price * 0.995)
                    stop_loss = max(stop_loss, entry_price * 0.975)
                    
                    risk = entry_price - stop_loss
                    target_price = entry_price + (2 * risk)
                    
                    results.append({
                        "Ticker": symbol,
                        "Entry Price": str(round(entry_price, 2)),
                        "Qty": qty,
                        "Invested Capital": str(round(qty * entry_price, 2)),
                        "OR High": round(or_high, 2),
                        "Yesterday High": round(pdh, 2),
                        "VWAP": round(vwap, 2),
                        "Stop Loss": str(round(stop_loss, 2)),
                        "Target": str(round(target_price, 2)),
                        "Status": "Triggered",
                        "Token": token
                    })
                    logging.info(f"🟢 Bullish Breakout Detected: {symbol} at {entry_price}")
                else:
                    results.append({
                        "Ticker": symbol,
                        "Entry Price": "Wait > " + str(max(round(or_high, 2), round(pdh, 2))),
                        "Qty": str(int(250000 / ltp)),
                        "Invested Capital": "-",
                        "OR High": round(or_high, 2),
                        "Yesterday High": round(pdh, 2),
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
