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

BEARISH_CACHE_FILE = os.path.join("data", "cache", "bearish_breakdown_cache.csv")

def cache_bearish_candidates(kite, progress_callback=None, refresh_only=False):
    """
    Phase 1: Pre-Market "Weakness" Filter (9:00 AM - 9:15 AM)
    Identifies F&O stocks that are structurally weak.
    """
    logging.info("🚀 Starting Phase 1: Pre-Market F&O Bearish Weakness Filter...")
    
    if refresh_only and os.path.exists(BEARISH_CACHE_FILE):
        # Refresh logic: Load existing cache
        cache_df = pd.read_csv(BEARISH_CACHE_FILE)
        if cache_df.empty:
            logging.info("Existing bearish cache is empty. Nothing to refresh.")
            return False
        symbols = cache_df['Ticker'].tolist()
        logging.info(f"Refreshing {len(symbols)} candidates with early weakness momentum...")
        
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
                pdl = row['Yesterday_Low']
                # STRICT FILTER: Price must be below Today's Open AND near/below PDL
                if today_ltp > today_open or today_ltp > pdl * 1.002: 
                    continue # Skip stocks showing strength or too far above PDL
                cache_data.append(row.to_dict())
                
        if cache_data:
            pd.DataFrame(cache_data).to_csv(BEARISH_CACHE_FILE, index=False)
            logging.info(f"Cache Refreshed. {len(cache_data)} bearish stocks shortlist ready.")
            return True
        else:
            pd.DataFrame(columns=cache_df.columns).to_csv(BEARISH_CACHE_FILE, index=False)
            logging.info("Cache Refreshed. No bearish stocks matched today's momentum.")
            return True
    else:
        # Full scan: Get all F&O tickers (Optimized with ThreadPoolExecutor)
        fno_tickers_result = scanner.get_nifty500_fno_tickers()
        if isinstance(fno_tickers_result, tuple) and len(fno_tickers_result) == 2 and isinstance(fno_tickers_result[0], set):
            # Handle tuple of sets format: (nifty500_symbols, fno_symbols)
            final_symbols = fno_tickers_result[0].intersection(fno_tickers_result[1])
            symbols = list(final_symbols)
        else:
            # Handle list of strings format: ["RELIANCE.NS", ...]
            symbols = [s.replace(".NS", "") for s in fno_tickers_result]
        logging.info(f"Scanning all {len(symbols)} F&O tickers for structural weakness...")
        
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
                df_daily.ta.ema(length=50, append=True)
                df_daily.ta.rsi(length=14, append=True)
                
                latest = df_daily.iloc[-1]
                prev = df_daily.iloc[-2]
                
                is_weak = latest['close'] < latest['EMA_50'] or latest['RSI_14'] < 55
                
                today_date = datetime.date.today()
                if latest.name.date() == today_date:
                    pdl = prev['low']
                    prev_close = prev['close']
                else:
                    pdl = latest['low']
                    prev_close = latest['close']
                    
                if is_weak:
                    avg_vol_20 = float(latest['Vol_SMA_20']) if ('Vol_SMA_20' in latest and not pd.isna(latest['Vol_SMA_20'])) else float(df_daily['volume'].tail(20).mean())
                    avg_vol_15m = kite_scanner.fetch_avg_15m_volume(kite, token, to_date, daily_avg_vol=avg_vol_20)
                    with _lock:
                        cache_data.append({
                            "Ticker": symbol,
                            "Token": token,
                            "Prev_Close": prev_close,
                            "Yesterday_Low": round(pdl, 2),
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
            pd.DataFrame(cache_data).to_csv(BEARISH_CACHE_FILE, index=False)
            logging.info(f"Phase 1 Complete (Full Scan). {len(cache_data)} F&O stocks cached.")
            return True
            
        return False

from utils.indicators import calculate_vwap_scalar as calculate_vwap  # scalar: returns float

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
    nifty_bullish = is_nifty_bullish(kite)
        
    # --- BATCH PRE-SCREEN (Speed Optimization) ---
    # Fetch LTP for all candidates in one call to see who is actually near or below OR Low/PDL
    logging.info(f"Pre-screening {total} bearish candidates with batch quotes...")
    try:
        all_tickers = [f"NSE:{s}" for s in cache_df['Ticker']]
        quotes = kite_scanner.fetch_ohlc_safe(kite, all_tickers)
        
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
                if datetime.time(9, 30) <= to_date.time() <= datetime.time(14, 0) and is_breakdown:

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
                        "Status": "Closed for Day" if to_date.time() > datetime.time(14, 0) else "Monitoring",
                        "Token": token
                    })

                    
        except Exception as e:
            logging.error(f"Error scanning {symbol}: {e}")
            continue
            
    return pd.DataFrame(results)
