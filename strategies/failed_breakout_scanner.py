import pandas as pd
import pandas_ta as ta
import datetime
import time
import logging
import os
import threading
import concurrent.futures
from strategies import kite_scanner
from api import market_data as scanner
from core.market_context import is_nifty_bullish

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
        if cache_df.empty:
            logging.info("Existing failed breakout cache is empty. Nothing to refresh.")
            return False
        symbols = cache_df['Ticker'].tolist()
        logging.info(f"Refreshing {len(symbols)} candidates with today's early price action...")
        
        tickers = [f"NSE:{sym}" for sym in symbols]
        quotes = {}
        try:
            for i in range(0, len(tickers), 200):
                chunk = tickers[i:i+200]
                quotes.update(kite.ohlc(chunk))
        except Exception as e:
            logging.error(f"Error fetching OHLC quotes in batch: {e}")
            
        cache_data = []
        for idx, row in cache_df.iterrows():
            symbol = row['Ticker']
            quote = quotes.get(f"NSE:{symbol}")
            if quote:
                today_open = quote['ohlc']['open']
                today_ltp = quote['last_price']
                today_high = quote['ohlc']['high']
                pdh = row['Yesterday_High']
                # Skip if stock is gapping up sharply or trading far above PDH, or today's high is not near PDH
                if today_ltp > pdh * 1.015 or today_high < pdh * 0.985: 
                    continue
                cache_data.append(row.to_dict())
                
        if cache_data:
            pd.DataFrame(cache_data).to_csv(FAILED_CACHE_FILE, index=False)
            logging.info(f"Cache Refreshed. {len(cache_data)} failed breakout stocks ready.")
            return True
        else:
            pd.DataFrame(columns=cache_df.columns).to_csv(FAILED_CACHE_FILE, index=False)
            logging.info("Cache Refreshed. No failed breakout stocks matched today's momentum.")
            return True
    else:
        # Full scan: Get all F&O tickers (Optimized with ThreadPoolExecutor)
        fno_tickers_ns = scanner.get_nifty500_fno_tickers()
        symbols = [s.replace(".NS", "") for s in fno_tickers_ns]
        logging.info(f"Scanning all {len(symbols)} F&O tickers for resistance-rejection setups...")
        
        token_map = kite_scanner.get_kite_instruments(kite, symbols)
        if not token_map:
            logging.error("Failed to retrieve instrument tokens.")
            return False

        # Batch price pre-screen
        all_tickers = [f"NSE:{s}" for s in token_map.keys()]
        try:
            ohlc_dict = kite_scanner.fetch_ohlc_safe(kite, all_tickers)
            token_map = {s: t for s, t in token_map.items() if (s in [k.replace("NSE:", "") for k, v in ohlc_dict.items() if 100 <= v.get('last_price', 0) <= 5000])}
        except Exception as e:
            logging.warning(f"Batch price pre-screen failed: {e}")

        cache_data = []
        to_date = datetime.datetime.now()
        from_date_daily = to_date - datetime.timedelta(days=100)
        
        total = len(token_map)
        processed = 0
        _lock = threading.Lock()
        
        def process_symbol(symbol, token):
            nonlocal processed
            try:
                df_daily = kite_scanner.fetch_kite_data(kite, token, from_date_daily, to_date, "day")
                if df_daily.empty or len(df_daily) < 50:
                    return
                    
                df_daily['Vol_SMA_20'] = df_daily['volume'].rolling(window=20).mean()
                df_daily.ta.ema(length=20, append=True)
                df_daily.ta.ema(length=50, append=True)
                df_daily.ta.rsi(length=14, append=True)
                
                latest = df_daily.iloc[-1]
                prev = df_daily.iloc[-2]
                
                is_macro_bearish = (latest['close'] < latest['EMA_20']) and (latest['RSI_14'] <= 48.0)
                
                today_date = datetime.date.today()
                if latest.name.date() == today_date:
                    pdh = prev['high']
                    prev_close = prev['close']
                    prev_open = prev['open']
                    prev_low = prev['low']
                else:
                    pdh = latest['high']
                    prev_close = latest['close']
                    prev_open = latest['open']
                    prev_low = latest['low']
                    
                near_resistance_tight = (pdh * 0.985 <= prev_close <= pdh * 1.01)
                
                upper_wick = pdh - max(prev_open, prev_close)
                candle_range = pdh - prev_low
                has_rejection_wick = (upper_wick >= 0.25 * candle_range) if candle_range > 0 else False
                near_resistance_wick = (pdh * 0.965 <= prev_close <= pdh * 1.01) and has_rejection_wick
                near_resistance = near_resistance_tight or near_resistance_wick
                
                if is_macro_bearish and near_resistance:
                    avg_vol_20 = float(latest['Vol_SMA_20']) if ('Vol_SMA_20' in latest and not pd.isna(latest['Vol_SMA_20'])) else float(df_daily['volume'].tail(20).mean())
                    avg_vol_15m = kite_scanner.fetch_avg_15m_volume(kite, token, to_date, daily_avg_vol=avg_vol_20)
                    with _lock:
                        cache_data.append({
                            "Ticker": symbol,
                            "Token": token,
                            "Prev_Close": prev_close,
                            "Yesterday_High": round(pdh, 2),
                            "EMA_20": round(latest['EMA_20'], 2),
                            "EMA_50": round(latest['EMA_50'], 2),
                            "RSI": round(latest['RSI_14'], 2),
                            "Avg_15m_Vol": avg_vol_15m
                        })
            except Exception as e:
                logging.error(f"Error filtering {symbol}: {e}")
            finally:
                with _lock:
                    processed += 1
                    if progress_callback:
                        progress_callback(processed, total, symbol)
                        
        from core.thread_utils import wrap_thread_ctx
        wrapped_process = wrap_thread_ctx(process_symbol)
        with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
            futures = {executor.submit(wrapped_process, sym, tok): sym for sym, tok in token_map.items()}
            concurrent.futures.wait(futures.keys())
            
        if cache_data:
            pd.DataFrame(cache_data).to_csv(FAILED_CACHE_FILE, index=False)
            logging.info(f"Phase 1 Complete (Full Scan). {len(cache_data)} failed breakout stocks cached.")
            return True
            
        return False

from utils.indicators import calculate_vwap_scalar as calculate_vwap  # scalar: returns float

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
    nifty_bullish = is_nifty_bullish(kite)
        
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
                
            # 4. Volume Spike Confirmation: Volume on trigger rejection candle is high (>= 1.0x)
            vol_spike = confirmed_volume >= 1.0 * confirmed_vol_avg if confirmed_vol_avg > 0 else True
            
            # 5. Intraday Trend Alignment: Calculate VWAP for metadata/logging
            vwap = calculate_vwap(df_today)
            
            # 6. RSI Buffer: 5-min RSI > 38 (not oversold)
            latest_rsi = latest_candle['RSI_14'] if 'RSI_14' in latest_candle else 50
            not_oversold = latest_rsi > 38
            
            # 7. No-Chase Rule: Slippage is <= 0.8% from resistance level R
            slippage_pct = (R - ltp) / R * 100
            is_chasing = slippage_pct > 0.8
            
            # Combine all upgraded filters (including max 5-candle trap duration)
            if is_trap_triggered and is_bearish_rejection and (consecutive_above <= 5) and vol_spike and not_oversold and not is_chasing:
                if datetime.time(9, 30) <= to_date.time() <= datetime.time(14, 0):
                    entry_price = ltp
                    qty = int(250000 / entry_price)
                    
                    sl_calculated = failed_swing_high * 1.001
                    stop_loss = max(sl_calculated, entry_price * 1.0125) # Minimum 1.25% SL buffer
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
