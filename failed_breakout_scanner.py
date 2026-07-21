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

FAILED_CACHE_FILE = os.path.join("data", "cache", "failed_breakout_cache.csv")

def cache_failed_candidates(kite, progress_callback=None, refresh_only=False):
    """
    Phase 1: Pre-Market F&O Failed Breakout Caching (9:00 AM - 9:15 AM)
    Shortlists F&O candidates that are trading near overhead resistance (Yesterday's High / ORB High)
    but are NOT in strong daily momentum (Price < 20 EMA or Daily RSI <= 53).
    """
    logging.info("🚀 Starting Phase 1: Dedicated Pre-Market Failed Breakout Caching...")
    
    if refresh_only and os.path.exists(FAILED_CACHE_FILE):
        cache_df = pd.read_csv(FAILED_CACHE_FILE)
        symbols = cache_df['Ticker'].tolist()
        logging.info(f"Refreshing {len(symbols)} candidates with today's early price action...")
    else:
        # Full scan: Get all F&O tickers
        fno_tickers_ns = scanner.get_nifty500_fno_tickers()
        symbols = [s.replace(".NS", "") for s in fno_tickers_ns]
        logging.info(f"Scanning all {len(symbols)} F&O tickers for resistance-rejection setups...")
    
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
                
            # Calculate indicators
            df_daily.ta.ema(length=20, append=True)
            df_daily.ta.ema(length=50, append=True)
            df_daily.ta.rsi(length=14, append=True)
            
            latest = df_daily.iloc[-1]
            prev = df_daily.iloc[-2]
            
            # --- SEPARATED FAILED BREAKOUT SELECTION CRITERIA ---
            # 1. Macro Downtrend/Pullback: Must be BELOW 20 EMA AND Daily RSI <= 52.0 (True resistance candidate)
            is_macro_bearish = (latest['close'] < latest['EMA_20']) and (latest['RSI_14'] <= 52.0)
            
            # 2. Yesterday's High (Correctly handle pre-market vs post-open)
            today_date = datetime.date.today()
            if latest.name.date() == today_date:
                pdh = prev['high']
                prev_close = prev['close']
            else:
                pdh = latest['high']
                prev_close = latest['close']
            
            # 3. Tight Resistance Proximity: Price starts within 3.5% of Yesterday's High
            near_resistance = (pdh * 0.965 <= prev_close <= pdh * 1.01)
            
            # 4. Early Momentum Filter (If refreshing between 9:25 - 9:30)
            if refresh_only:
                from_intra = to_date.replace(hour=9, minute=15, second=0, microsecond=0)
                if from_intra > to_date:
                    from_intra = from_intra - datetime.timedelta(days=1)
                df_intra = kite_scanner.fetch_kite_data(kite, token, from_intra, to_date, "5minute")
                if not df_intra.empty:
                    today_open = df_intra.iloc[0]['open']
                    today_ltp = df_intra.iloc[-1]['close']
                    today_high = df_intra['high'].max()
                    # Skip if stock is gapping up sharply or trading far above PDH
                    if today_ltp > pdh * 1.015 or today_high < pdh * 0.985: 
                        continue
            
            if is_macro_bearish and near_resistance:
                cache_data.append({
                    "Ticker": symbol,
                    "Token": token,
                    "Prev_Close": prev_close,
                    "Yesterday_High": round(pdh, 2),
                    "EMA_20": round(latest['EMA_20'], 2),
                    "EMA_50": round(latest['EMA_50'], 2),
                    "RSI": round(latest['RSI_14'], 2),
                    "Avg_15m_Vol": 0.0
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
            
        cache_df.to_csv(FAILED_CACHE_FILE, index=False)
        logging.info(f"Phase 1 Complete. {len(cache_df)} F&O Failed Breakout candidates cached into '{FAILED_CACHE_FILE}'.")
        return True
    
    logging.info("Phase 1 Complete. No candidates matched Failed Breakout criteria.")
    return False

def calculate_vwap(df):
    """Calculate VWAP for intraday data."""
    if df.empty:
        return 0
    tp = (df['high'] + df['low'] + df['close']) / 3
    vwap = (tp * df['volume']).sum() / df['volume'].sum()
    return vwap

def scan_failed_breakouts(kite, progress_callback=None):
    """
    Phase 2 & 3: Intraday Failed Breakout (Bull Trap) Real-Time Scan (Post-9:30 AM)
    """
    logging.info("🔍 Starting Intraday Failed Breakout (Bull Trap) Scan...")
    
    if not os.path.exists(FAILED_CACHE_FILE):
        logging.error(f"Failed breakout cache file '{FAILED_CACHE_FILE}' not found. Run Phase 1 first.")
        return pd.DataFrame()
        
    cache_df = pd.read_csv(FAILED_CACHE_FILE)
    results = []
    
    to_date = datetime.datetime.now()
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
            if nifty_from > to_date:
                nifty_from = nifty_from - datetime.timedelta(days=1)
            nifty_df = kite_scanner.fetch_kite_data(kite, nifty_token, nifty_from, to_date, "5minute")
            if not nifty_df.empty:
                nifty_open = nifty_df.iloc[0]['open']
                nifty_ltp = nifty_df.iloc[-1]['close']
                nifty_change_pct = (nifty_ltp - nifty_open) / nifty_open
                nifty_bullish = nifty_change_pct > 0.0005 # Nifty up > 0.05%
                logging.info(f"Broad Market Check -> Nifty Open: {nifty_open:.2f}, LTP: {nifty_ltp:.2f} | Change %: {nifty_change_pct*100:.3f}% | Bullish? {nifty_bullish}")
    except Exception as ne:
        logging.warning(f"Failed to fetch Nifty 50 trend: {ne}")
        
    # STRICT RULE: Reject short trades if broad market is bullish
    if nifty_bullish:
        logging.info("🛑 Broad Market (Nifty 50) is Bullish today. Skipping Failed Breakout Short triggers.")
        return pd.DataFrame()

    # --- BATCH PRE-SCREEN ---
    logging.info(f"Pre-screening {total} candidates with batch quotes...")
    try:
        all_tickers = [f"NSE:{s}" for s in cache_df['Ticker']]
        quotes = kite_scanner.fetch_ohlc_safe(kite, all_tickers)
        
        active_candidates = []
        for _, row in cache_df.iterrows():
            q = quotes.get(f"NSE:{row['Ticker']}")
            if q:
                today_high = q['ohlc']['high']
                yesterday_high = row['Yesterday_High']
                
                # Check if stock has traded near or above Yesterday's High today
                if today_high >= yesterday_high * 0.995:
                    active_candidates.append(row)
        
        if not active_candidates:
            logging.info("No candidates showing breakout activity near Yesterday's High today.")
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
                
            df_intra.ta.rsi(length=14, append=True)
            df_intra['Vol_Avg_5'] = df_intra['volume'].rolling(window=20).mean()
            
            df_today = df_intra[df_intra.index.date == to_date.date()]
            if df_today.empty or len(df_today) < 4:
                continue
                
            # Define 15-minute Opening Range
            or_candles = df_today.iloc[0:3]
            or_high = or_candles['high'].max()
            
            # Structural Resistance Level (R)
            R = max(pdh, or_high)
            
            subsequent = df_today.iloc[3:]
            if subsequent.empty:
                continue
                
            # 1. Breakout Attempt: Has any candle after 9:30 AM touched or closed above R?
            has_breakout_attempt = subsequent['high'].max() > R
            if not has_breakout_attempt:
                continue
                
            # Identify the highest price of the breakout move for SL calculation
            failed_swing_high = df_today.iloc[3:]['high'].max()
            
            # --- TRIGGER CONFIRMATION ---
            latest_candle = df_today.iloc[-1]
            ltp = latest_candle['close']
            candle_start = latest_candle.name
            
            t_now_naive = to_date.replace(tzinfo=None) if to_date.tzinfo is not None else to_date
            c_start_pydt = candle_start.to_pydatetime() if hasattr(candle_start, 'to_pydatetime') else candle_start
            c_start_naive = c_start_pydt.replace(tzinfo=None) if c_start_pydt.tzinfo is not None else c_start_pydt
            
            if t_now_naive >= c_start_naive + datetime.timedelta(minutes=5):
                confirmed_candle = latest_candle
            else:
                confirmed_candle = df_today.iloc[-2] if len(df_today) > 1 else latest_candle
                
            confirmed_close = confirmed_candle['close']
            confirmed_high = confirmed_candle['high']
            confirmed_low = confirmed_candle['low']
            confirmed_volume = confirmed_candle['volume']
            confirmed_vol_avg = confirmed_candle['Vol_Avg_5'] if 'Vol_Avg_5' in confirmed_candle else 1.0
            
            # 2. Failure/Trap Trigger: Confirmed close back below resistance level R
            is_trap_triggered = confirmed_close < R
            
            # 3. Bearish Rejection Shape: Red candle OR shooting star shape
            confirmed_open = confirmed_candle['open']
            is_red = confirmed_close < confirmed_open
            body_size = abs(confirmed_close - confirmed_open)
            upper_wick = confirmed_high - max(confirmed_open, confirmed_close)
            is_bearish_rejection = is_red or (upper_wick > 1.5 * body_size if body_size > 0 else True)
            
            # 3b. Trap Duration Constraint: Max 2 candles (10 mins) above resistance
            consecutive_above = 0
            try:
                idx_trigger = df_today.index.get_loc(confirmed_candle.name)
                for i in range(idx_trigger - 1, -1, -1):
                    prev_c = df_today.iloc[i]
                    if prev_c['close'] > R:
                        consecutive_above += 1
                    else:
                        break
            except Exception as ex:
                logging.warning(f"Error calculating consecutive candles above R: {ex}")
                consecutive_above = 0
                
            # 4. Volume Spike Confirmation: Volume on trigger rejection candle is high (>= 1.5x)
            vol_spike = confirmed_volume >= 1.5 * confirmed_vol_avg if confirmed_vol_avg > 0 else True
            
            # 5. Intraday Trend Alignment: Below VWAP
            vwap = calculate_vwap(df_today)
            below_vwap = ltp < vwap
            
            # 6. RSI Buffer: 5-min RSI > 38 (not oversold)
            latest_rsi = latest_candle['RSI_14'] if 'RSI_14' in latest_candle else 50
            not_oversold = latest_rsi > 38
            
            # 7. No-Chase Rule: Slippage is <= 0.4% from resistance level R
            slippage_pct = (R - ltp) / R * 100
            is_chasing = slippage_pct > 0.4
            
            # Combine all upgraded filters (including max 2-candle trap duration)
            if is_trap_triggered and is_bearish_rejection and (consecutive_above <= 2) and vol_spike and below_vwap and not_oversold and not is_chasing:
                if datetime.time(9, 30) <= to_date.time() <= datetime.time(14, 0):
                    entry_price = ltp
                    qty = int(250000 / entry_price)
                    
                    sl_calculated = failed_swing_high * 1.001
                    stop_loss = max(sl_calculated, entry_price * 1.0075) # Minimum 0.75% SL buffer
                    stop_loss = min(stop_loss, entry_price * 1.02)
                    
                    risk = stop_loss - entry_price
                    target_price = entry_price - (2.0 * risk)
                    
                    results.append({
                        "Ticker": symbol,
                        "Entry Price": str(round(entry_price, 2)),
                        "Qty": qty,
                        "Invested Capital": str(round(qty * entry_price, 2)),
                        "Yesterday High": round(pdh, 2),
                        "OR High": round(or_high, 2),
                        "Resistance Level (R)": round(R, 2),
                        "VWAP": round(vwap, 2),
                        "RSI (5m)": round(latest_rsi, 2),
                        "Stop Loss": str(round(stop_loss, 2)),
                        "Target": str(round(target_price, 2)),
                        "Status": "Triggered",
                        "Token": token
                     })
                    logging.info(f"🔴 Failed Breakout Short Detected: {symbol} at {entry_price}")
        except Exception as e:
            logging.error(f"Error scanning {symbol}: {e}")
            continue
            
    return pd.DataFrame(results)
