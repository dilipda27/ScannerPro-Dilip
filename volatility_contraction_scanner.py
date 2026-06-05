"""
Volatility Contraction Scanner Module (Multi-Stage Stock Scanner)
==================================================================
This module implements a three-stage algorithmic scanner using the Zerodha Kite Connect REST API
and KiteTicker WebSocket streaming.

Stage 1: End-of-Day (EOD) Proximity Filter
- Scans Nifty 500 universe for liquid stocks (20-day Avg Volume > 500,000).
- Filters for stocks trading within 3% of their 20-day high (Resistance) or 20-day low (Support).
- Saves filtered tokens to a cache file.

Stage 2: Setup Validation (Volatility Contraction)
- Validates shortlisted stocks for tight consolidation.
- Confirms volatility contraction by checking if 5-day ATR < 14-day ATR.
- Determines the exact breakout (20-day high) and breakdown (20-day low) triggers.

Stage 3: Intraday Live Monitor & Paper Execution
- Connects to KiteTicker WebSocket and streams real-time prices for the watchlist.
- Executes simulated paper trades on breakout/breakdown triggers.
- Automatically resets triggers to prevent duplicate executions.
"""

import os
import io
import json
import time
import logging
import datetime
import threading
import requests
import pandas as pd
import pandas_ta as ta
from kiteconnect import KiteConnect, KiteTicker

# Import project configurations and optional helper modules
import config
try:
    import paper_trader
    HAS_PAPER_TRADER = True
except ImportError:
    HAS_PAPER_TRADER = False

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
)

# Constants
CACHE_FILE = "proximity_filter_cache.json"
WATCHLIST_FILE = "volatility_contraction_watchlist.json"
NIFTY500_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"


# ==============================================================================
# KITE CONNECT AUTHENTICATION & UTILITIES
# ==============================================================================

def get_kite_instance():
    """
    Initializes a KiteConnect REST API instance by reading the active logged-in
    session credentials from '.kite_session.json'.
    """
    session_file = ".kite_session.json"
    if not os.path.exists(session_file):
        logging.error("❌ No active Kite session found. Please login via your dashboard first.")
        return None
    try:
        with open(session_file, "r") as f:
            session = json.load(f)
        
        kite = KiteConnect(api_key=config.KITE_API_KEY)
        kite.set_access_token(session["access_token"])
        logging.info("✅ Kite REST API successfully authenticated from session cache.")
        return kite
    except Exception as e:
        logging.error(f"❌ Failed to authenticate Kite session: {e}", exc_info=True)
        return None


def fetch_nifty500_symbols():
    """
    Downloads the list of FNO symbols directly from NSE.
    Includes a robust fallback list of highly liquid tickers in case of NSE timeout.
    """
    logging.info("📡 Fetching Nifty F&O symbols from NSE...")
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    # 1. Fetch Nifty 500
    nifty500_symbols = set()
    try:
        response = requests.get(NIFTY500_URL, headers=headers, timeout=10)
        df = pd.read_csv(io.StringIO(response.text))
        nifty500_symbols = set(df['Symbol'].str.strip().tolist())
    except Exception as e:
        logging.warning(f"⚠️ Failed to fetch Nifty 500 from NSE: {e}. Using fallback.")
        nifty500_symbols = {"RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK"}
        
    # 2. Fetch FNO List
    url_fno = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"
    fno_symbols = set()
    try:
        r_fno = requests.get(url_fno, headers=headers, timeout=10)
        for line in r_fno.text.split('\n'):
            parts = line.split(',')
            if len(parts) > 2:
                sym = parts[1].strip()
                if sym and sym != "SYMBOL":
                    fno_symbols.add(sym)
    except Exception as e:
        logging.error(f"Error fetching FNO list: {e}")
        fno_symbols = nifty500_symbols  # Fallback
        
    final_symbols = nifty500_symbols.intersection(fno_symbols)
    logging.info(f"✅ Successfully retrieved and filtered {len(final_symbols)} F&O symbols.")
    return sorted(list(final_symbols))


def get_token_map(kite, symbols):
    """
    Queries Kite's instrument master list to construct a dictionary mapping 
    each trading symbol to its Zerodha Instrument Token.
    """
    logging.info(f"🔍 Mapping {len(symbols)} symbols to Kite instrument tokens...")
    try:
        instruments = kite.instruments("NSE")
        df_inst = pd.DataFrame(instruments)
        
        # Filter for active stock contracts in our target symbol set
        df_filtered = df_inst[df_inst['tradingsymbol'].isin(symbols)]
        token_map = dict(zip(df_filtered['tradingsymbol'], df_filtered['instrument_token']))
        logging.info(f"✅ mapped {len(token_map)} / {len(symbols)} symbols to instrument tokens.")
        return token_map
    except Exception as e:
        logging.error(f"❌ Error downloading instrument mapping from Kite: {e}", exc_info=True)
        return {}


def calculate_wilders_atr(df, period=14):
    """
    Calculates Wilder's Average True Range (ATR) mathematically.
    This custom implementation avoids reliance on external libraries that may fail
    or append columns with unpredictable names.
    
    Wilder's ATR uses an exponential moving average with alpha = 1 / period.
    """
    high_low = df['high'] - df['low']
    high_prev_close = (df['high'] - df['close'].shift(1)).abs()
    low_prev_close = (df['low'] - df['close'].shift(1)).abs()
    
    # True Range is the maximum of the three price difference vectors
    tr = pd.concat([high_low, high_prev_close, low_prev_close], axis=1).max(axis=1)
    
    # Wilder's Smoothing: ewm with alpha = 1/period
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    return atr


# ==============================================================================
# STAGE 1: END-OF-DAY (EOD) PROXIMITY FILTER
# ==============================================================================

def run_stage1_proximity_filter(kite, symbols_list):
    """
    Stage 1: Filters the Nifty 500 universe for liquid stocks trading within 
    3% of their 20-day high or 20-day low.
    
    Saves a filtered JSON cache mapping token -> metadata (symbol, resistance, support).
    """
    logging.info("🚀 Starting Stage 1: End-of-Day (EOD) Proximity Filter...")
    
    token_map = get_token_map(kite, symbols_list)
    if not token_map:
        logging.error("❌ Aborting Stage 1: No valid instrument tokens mapped.")
        return []

    shortlisted = {}
    to_date = datetime.datetime.now()
    # Fetch 100 daily candles to comfortably compute 50-day EMA and other indicators
    from_date = to_date - datetime.timedelta(days=100)
    
    total = len(token_map)
    processed = 0

    for symbol, token in token_map.items():
        processed += 1
        if processed % 50 == 0 or processed == total:
            logging.info(f"Progress: Processed {processed}/{total} stocks...")

        # Rate limiting: Kite API limit is 3 requests per second.
        # Sleeping 0.35 seconds per request maintains safe rps threshold (~2.8 rps).
        time.sleep(0.35)

        try:
            # Fetch historical daily data
            data = kite.historical_data(
                instrument_token=token,
                from_date=from_date,
                to_date=to_date,
                interval="day"
            )
            
            if not data or len(data) < 50:
                continue
                
            df = pd.DataFrame(data)
            
            # --- Technical Metrics ---
            # 1. 20-day High (Resistance) & 20-day Low (Support)
            resistance = float(df['high'].rolling(window=20).max().iloc[-1])
            support = float(df['low'].rolling(window=20).min().iloc[-1])
            
            # 2. 20-day Volume SMA
            volume_sma_20 = float(df['volume'].rolling(window=20).mean().iloc[-1])
            
            latest_close = float(df['close'].iloc[-1])
            
            # Calculate daily trend confirmation indicators
            df.ta.ema(length=50, append=True)
            df.ta.rsi(length=14, append=True)
            
            ema_50 = float(df['EMA_50'].iloc[-1]) if 'EMA_50' in df.columns else latest_close
            rsi_14 = float(df['RSI_14'].iloc[-1]) if 'RSI_14' in df.columns else 50.0
            
            # --- Scan Filters ---
            # Condition A: Liquidity constraint (Average 20-day volume > 500,000 shares)
            if volume_sma_20 <= 500000:
                continue
                
            # Condition B: Price proximity to 20-day High or 20-day Low (within 3%)
            dist_res = abs(latest_close - resistance) / resistance
            dist_sup = abs(latest_close - support) / support
            
            # Condition C: Volatility Contraction Pattern (VCP) consolidation width check
            # The entire 20-day High to 20-day Low range must be tight (e.g. <= 12%)
            # This ensures we filter out highly volatile stocks with wide ranges/huge resistance
            range_width = (resistance - support) / support
            is_range_tight = range_width <= 0.12
            
            # Dominant trend confirmation filters:
            # - For Resistance proximity (Bullish Breakout): Price must be above daily EMA 50 AND RSI 14 > 50
            # - For Support proximity (Bearish Breakdown): Price must be below daily EMA 50 AND RSI 14 < 50
            is_near_resistance = is_range_tight and (dist_res <= 0.03) and (latest_close > ema_50) and (rsi_14 > 50)
            is_near_support = is_range_tight and (dist_sup <= 0.03) and (latest_close < ema_50) and (rsi_14 < 50)
            
            if is_near_resistance or is_near_support:
                shortlisted[str(token)] = {
                    "symbol": symbol,
                    "token": int(token),
                    "resistance": round(resistance, 2),
                    "support": round(support, 2),
                    "latest_close": round(latest_close, 2),
                    "volume_sma": round(volume_sma_20, 2),
                    "near_level": "Resistance" if is_near_resistance else "Support"
                }
                logging.info(f"✨ Shortlisted {symbol} (Close: ₹{latest_close:.2f} | R: ₹{resistance:.2f} | S: ₹{support:.2f} | Vol SMA: {volume_sma_20:,.0f} | RSI: {rsi_14:.1f})")
                
        except Exception as e:
            logging.error(f"⚠️ Error scanning symbol {symbol}: {e}")
            continue

    # Persist Stage 1 cache
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(shortlisted, f, indent=4)
        logging.info(f"📊 Stage 1 Complete. Cached {len(shortlisted)} candidates into '{CACHE_FILE}'.")
    except Exception as e:
        logging.error(f"❌ Failed to write cache file: {e}")

    return shortlisted


# ==============================================================================
# STAGE 2: SETUP VALIDATION (VOLATILITY CONTRACTION)
# ==============================================================================

def run_stage2_setup_validation(kite):
    """
    Stage 2: Loads EOD proximity filter cached stocks, fetches daily candles,
    calculates Wilder's ATR over 5 and 14 days, and filters for volatility contraction.
    
    Returns a live monitoring watchlist dictionary.
    """
    logging.info("🚀 Starting Stage 2: Setup Validation (Volatility Contraction)...")
    
    if not os.path.exists(CACHE_FILE):
        logging.error(f"❌ Stage 2 failed: Cache file '{CACHE_FILE}' does not exist. Run Stage 1 first.")
        return {}

    try:
        with open(CACHE_FILE, "r") as f:
            shortlist_cache = json.load(f)
    except Exception as e:
        logging.error(f"❌ Error reading cache file: {e}")
        return {}

    if not shortlist_cache:
        logging.info("⚠️ Cache file is empty. No candidates to validate.")
        return {}

    watchlist = {}
    to_date = datetime.datetime.now()
    # Fetch 50 daily candles to ensure sufficient history for 14-day Wilder's ATR
    from_date = to_date - datetime.timedelta(days=90)
    
    logging.info(f"🔍 Validating {len(shortlist_cache)} candidates for recent Volatility Contraction...")

    for token_str, metadata in shortlist_cache.items():
        token = int(token_str)
        symbol = metadata["symbol"]
        
        # Enforce REST API rate limits
        time.sleep(0.35)

        try:
            data = kite.historical_data(
                instrument_token=token,
                from_date=from_date,
                to_date=to_date,
                interval="day"
            )
            
            if not data or len(data) < 25:
                continue
                
            df = pd.DataFrame(data)
            
            # --- Volatility Contraction Calculations ---
            # Calculate Wilder's smoothed ATR for 5-day and 14-day intervals
            atr_5 = calculate_wilders_atr(df, period=5)
            atr_14 = calculate_wilders_atr(df, period=14)
            
            latest_atr_5 = atr_5.iloc[-1]
            latest_atr_14 = atr_14.iloc[-1]
            latest_close = df['close'].iloc[-1]
            
            # Additional VCP confirmations:
            # 1. Volatility Squeeze Tightness: 5-day ATR must represent less than 3.5% of the stock price
            atr_percent = (latest_atr_5 / latest_close) * 100
            is_tight = atr_percent <= 3.5
            
            # 2. Volume Contraction: 5-day Volume SMA must be less than 20-day Volume SMA (Volume dry-up)
            volume_sma_5 = df['volume'].rolling(window=5).mean().iloc[-1]
            volume_sma_20 = df['volume'].rolling(window=20).mean().iloc[-1]
            is_volume_contracted = volume_sma_5 < volume_sma_20

            # 3. Price Horizontal Squeeze: 5-day closing price range must be within 4% to ensure real tight accumulation
            close_5d = df['close'].iloc[-5:]
            price_spread_pct = (close_5d.max() - close_5d.min()) / close_5d.min() * 100
            is_price_flat = price_spread_pct <= 4.0
            
            # 4. Pivot Contraction Waves (VCP Waves)
            df_peaks = df.iloc[-40:].copy()
            df_peaks['peak'] = df_peaks['high'] == df_peaks['high'].rolling(5, center=True).max()
            df_peaks['trough'] = df_peaks['low'] == df_peaks['low'].rolling(5, center=True).min()
            
            peaks = df_peaks[df_peaks['peak']]['high'].tolist()
            troughs = df_peaks[df_peaks['trough']]['low'].tolist()
            
            is_contracting_waves = True
            depths = []
            if len(peaks) >= 2 and len(troughs) >= 2:
                peak_indices = df_peaks[df_peaks['peak']].index
                for p_idx, p_val in zip(peak_indices, peaks):
                    post_troughs = df_peaks[df_peaks['trough']].loc[p_idx:]
                    if not post_troughs.empty:
                        t_val = post_troughs['low'].iloc[0]
                        depth = (p_val - t_val) / p_val * 100
                        depths.append(depth)
                if len(depths) >= 2:
                    is_contracting_waves = depths[-1] < depths[-2]

            # Setup Validation Criteria: 5-day ATR < 14-day ATR AND Tight Squeeze AND Volume dry-up AND Flat Closes AND Contracting Waves
            if (latest_atr_5 < latest_atr_14) and is_tight and is_volume_contracted and is_price_flat and is_contracting_waves:
                # Triggers are the 20-day rolling highs and lows calculated in Stage 1
                watchlist[token] = {
                    "symbol": symbol,
                    "trigger_buy": metadata["resistance"],  # Breakout trigger (20-day high)
                    "trigger_sell": metadata["support"],     # Breakdown trigger (20-day low)
                    "atr_5": round(float(latest_atr_5), 2)
                }
                logging.info(f"🔥 VALIDATED: {symbol} - Volatility contracting (5-day ATR: ₹{latest_atr_5:.2f} < 14-day ATR: ₹{latest_atr_14:.2f} | ATR %: {atr_percent:.2f}% | 5D Close Range: {price_spread_pct:.2f}% | Depths: {[round(d, 2) for d in depths]})")
            else:
                reason = []
                if latest_atr_5 >= latest_atr_14: reason.append("No ATR contraction")
                if not is_tight: reason.append(f"ATR not tight enough ({atr_percent:.2f}%)")
                if not is_volume_contracted: reason.append("No volume dry-up")
                if not is_price_flat: reason.append(f"Closes too wide ({price_spread_pct:.2f}%)")
                if not is_contracting_waves: reason.append(f"Waves not contracting ({[round(d, 2) for d in depths]})")
                logging.info(f"❌ Filtered Out: {symbol} - {', '.join(reason)}")
                
        except Exception as e:
            logging.error(f"⚠️ Error validating symbol {symbol} (Token: {token}): {e}")
            continue

    logging.info(f"📋 Stage 2 Complete. Formed live monitoring watchlist with {len(watchlist)} stocks.")
    
    # Persist Stage 2 watchlist to prevent duplicate scanning on dashboard reload
    try:
        with open(WATCHLIST_FILE, "w") as f:
            # Convert keys to string for JSON serialization compatibility
            serializable_watchlist = {str(k): v for k, v in watchlist.items()}
            json.dump(serializable_watchlist, f, indent=4)
        logging.info(f"💾 Persisted Stage 2 watchlist to '{WATCHLIST_FILE}'.")
    except Exception as e:
        logging.error(f"❌ Failed to persist watchlist file: {e}")
        
    return watchlist


# ==============================================================================
# STAGE 3: INTRADAY LIVE MONITOR & PAPER EXECUTION
# ==============================================================================

def send_telegram_alert_async(kite_client, symbol, token, last_price, sl_level, trade_type):
    """
    Dispatches a Telegram channel alert in a separate daemon thread to ensure
    the real-time WebSocket connection loop is never blocked by network latency.
    """
    def run():
        try:
            import telegram_agent
            import config
            import kite_scanner
            import pandas as pd
            
            # Format row data to match expected fields in telegram_agent
            row_data = {
                "Ticker": symbol,
                "Token": token,
                "Price": round(last_price, 2),
                "Entry Price": round(last_price, 2),
                "Stop Loss": round(sl_level, 2),
                "SL": round(sl_level, 2),
                "Paper SL": round(sl_level, 2),
                "Paper Target": round(last_price * 1.06, 2) if trade_type == "Bullish Breakout" else round(last_price * 0.94, 2),
                "Breakout": trade_type,
                "Timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            row_series = pd.Series(row_data)
            
            msg = telegram_agent.format_signal_message(row_series, f"VCP {trade_type}")
            
            # Fetch chart data (5m candles for last 2 days)
            to_date = datetime.datetime.now()
            from_date = to_date - datetime.timedelta(days=2)
            df_chart = kite_scanner.fetch_kite_data(kite_client, token, from_date, to_date, "5minute")
            
            tel_chat_id_intraday = getattr(config, 'TELEGRAM_CHAT_ID_INTRADAY', config.TELEGRAM_CHAT_ID)
            
            telegram_agent.send_signal_with_chart(
                ticker=symbol,
                message=msg,
                df_5m=df_chart,
                bot_token=config.TELEGRAM_BOT_TOKEN,
                chat_id=tel_chat_id_intraday,
                scan_name=f"VCP {trade_type}",
                row_data=row_series
            )
            logging.info(f"📤 Sent Telegram VCP alert with chart for {symbol}")
        except Exception as te:
            logging.error(f"Error sending async Telegram VCP alert for {symbol}: {te}")
            
    threading.Thread(target=run, daemon=True).start()


class IntradayLiveMonitor:
    """
    Stage 3: Intraday WebSocket connection runner. Streams quotes in real-time
    and triggers mock (paper) trades on price breakouts/breakdowns.
    """
    def __init__(self, api_key, access_token, watchlist):
        self.api_key = api_key
        self.access_token = access_token
        self.watchlist = watchlist  # Dict: token -> {symbol, trigger_buy, trigger_sell}
        self.tokens = list(watchlist.keys())
        self.kws = None
        self._lock = threading.Lock()
        
        # Initialize Kite REST client for async historical data fetches/charts
        self.kite_client = KiteConnect(api_key=api_key)
        self.kite_client.set_access_token(access_token)
        
    def log_trade(self, order_type, symbol, price):
        """Logs the paper trade cleanly in a simulated console ledger."""
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        border = "=" * 80
        trade_msg = (
            f"\n{border}\n"
            f"🔔 [PAPER TRADE TRIGGER] 🔔\n"
            f"👉 ACTION    : {order_type}\n"
            f"👉 SYMBOL    : NSE:{symbol}\n"
            f"👉 PRICE     : ₹{price:.2f}\n"
            f"👉 TIMESTAMP : {timestamp}\n"
            f"{border}\n"
        )
        print(trade_msg)
        logging.info(f"🚨 logged PAPER TRADE: {order_type} {symbol} @ ₹{price:.2f}")

    def on_ticks(self, ws, ticks):
        """WebSocket on_ticks callback. Evaluates real-time price updates against levels."""
        # Enforce start time restriction: Do not trigger trades before 09:30 AM to filter out morning whipsaws
        current_time = datetime.datetime.now().time()
        if current_time < datetime.time(9, 30):
            return

        with self._lock:
            for tick in ticks:
                token = tick.get('instrument_token')
                if token not in self.watchlist:
                    continue
                
                last_price = tick.get('last_price')
                if last_price is None:
                    continue
                
                metadata = self.watchlist[token]
                symbol = metadata["symbol"]
                trigger_buy = metadata.get("trigger_buy")
                trigger_sell = metadata.get("trigger_sell")
                
                # Fetch today's open and previous day's close from the tick data for momentum & extension checks
                tick_ohlc = tick.get('ohlc', {})
                today_open = tick_ohlc.get('open')
                prev_close = tick_ohlc.get('close')
                
                # --- A. Bullish Breakout Check ---
                if trigger_buy is not None and last_price >= trigger_buy:
                    # Enforce that price must be above today's open to confirm positive intraday momentum
                    if today_open and last_price <= today_open:
                        continue # Skip false breakouts on negative intraday momentum
                    
                    # Check first-hour range constraint: if the high-to-low range so far is too wide
                    today_high = tick_ohlc.get('high')
                    today_low = tick_ohlc.get('low')
                    if today_high and today_low and today_low > 0:
                        today_range_pct = (today_high - today_low) / today_low * 100
                        if today_range_pct > 3.0:
                            logging.info(f"🚫 Skipping breakout for {symbol}: Intraday range ({today_range_pct:.2f}%) is too wide (no tight consolidation today).")
                            metadata["trigger_buy"] = None
                            continue
                    
                    # Prevent entering if the stock is already extended (e.g., > 5.5% gain on the day from previous close or open)
                    if prev_close and prev_close > 0:
                        day_gain_pct = (last_price - prev_close) / prev_close * 100
                        if day_gain_pct > 5.5:
                            logging.info(f"🚫 Skipping breakout for {symbol}: Stock is already up {day_gain_pct:.2f}% today (extended).")
                            metadata["trigger_buy"] = None
                            continue
                    if today_open and today_open > 0:
                        gain_from_open_pct = (last_price - today_open) / today_open * 100
                        if gain_from_open_pct > 5.5:
                            logging.info(f"🚫 Skipping breakout for {symbol}: Stock has run up {gain_from_open_pct:.2f}% from open today (extended).")
                            metadata["trigger_buy"] = None
                            continue
                        
                    self.log_trade("PAPER TRADE BUY (Breakout)", symbol, last_price)
                    
                    # TIGHT INTRADAY ADAPTIVE STOP LOSS & TARGET:
                    # Priority is 1.0 * Daily ATR_5. We set a protective cap of 2.0% to let the trade breathe.
                    atr_val = metadata.get("atr_5")
                    if atr_val:
                        atr_sl = last_price - (1.0 * float(atr_val))
                        sl_level = max(atr_sl, last_price * 0.98) # 2.0% cap
                    else:
                        sl_level = last_price * 0.985 # 1.5% fallback
                        
                    # Strict 2.0% intraday profit target
                    target_level = last_price * 1.02
                        
                    # Dispatch dynamic Telegram alert with chart asynchronously (non-blocking)
                    send_telegram_alert_async(self.kite_client, symbol, token, last_price, sl_level, "Bullish Breakout")
                    
                    # Execute on existing paper_trader.py module if available
                    if HAS_PAPER_TRADER:
                        try:
                            qty = int(250000 / last_price)
                            paper_trader.execute_paper_trade(
                                ticker=symbol,
                                trade_type="Bullish Breakout",
                                entry_price=last_price,
                                sl=round(sl_level, 2),
                                qty=qty,
                                token=token,
                                strategy="Volatility Contraction",
                                target=round(target_level, 2)
                            )
                        except Exception as pe:
                            logging.error(f"Failed to submit dashboard paper trade: {pe}")
                            
                    # Immediately clear the trigger value to prevent duplicates
                    metadata["trigger_buy"] = None
 
                # --- B. Bearish Breakdown Check ---
                elif trigger_sell is not None and last_price <= trigger_sell:
                    # Enforce that price must be below today's open to confirm negative intraday momentum
                    if today_open and last_price >= today_open:
                        continue # Skip false breakdowns on positive intraday momentum
                    
                    # Check first-hour range constraint: if the high-to-low range so far is too wide
                    today_high = tick_ohlc.get('high')
                    today_low = tick_ohlc.get('low')
                    if today_high and today_low and today_low > 0:
                        today_range_pct = (today_high - today_low) / today_low * 100
                        if today_range_pct > 3.0:
                            logging.info(f"🚫 Skipping breakdown for {symbol}: Intraday range ({today_range_pct:.2f}%) is too wide (no tight consolidation today).")
                            metadata["trigger_sell"] = None
                            continue
                        
                    # Prevent entering if the stock is already extended (e.g., > 5.5% drop on the day from previous close or open)
                    if prev_close and prev_close > 0:
                        day_loss_pct = (prev_close - last_price) / prev_close * 100
                        if day_loss_pct > 5.5:
                            logging.info(f"🚫 Skipping breakdown for {symbol}: Stock is already down {day_loss_pct:.2f}% today (extended).")
                            metadata["trigger_sell"] = None
                            continue
                    if today_open and today_open > 0:
                        loss_from_open_pct = (today_open - last_price) / today_open * 100
                        if loss_from_open_pct > 5.5:
                            logging.info(f"🚫 Skipping breakdown for {symbol}: Stock has dropped {loss_from_open_pct:.2f}% from open today (extended).")
                            metadata["trigger_sell"] = None
                            continue
                        
                    self.log_trade("PAPER TRADE SELL (Breakdown)", symbol, last_price)
                    
                    # TIGHT INTRADAY ADAPTIVE STOP LOSS & TARGET:
                    # Priority is 1.0 * Daily ATR_5. We set a protective cap of 2.0% to let the trade breathe.
                    atr_val = metadata.get("atr_5")
                    if atr_val:
                        atr_sl = last_price + (1.0 * float(atr_val))
                        sl_level = min(atr_sl, last_price * 1.02) # 2.0% cap
                    else:
                        sl_level = last_price * 1.015 # 1.5% fallback
                        
                    # Strict 2.0% intraday profit target
                    target_level = last_price * 0.98
                        
                    # Dispatch dynamic Telegram alert with chart asynchronously (non-blocking)
                    send_telegram_alert_async(self.kite_client, symbol, token, last_price, sl_level, "Bearish Breakdown")
                    
                    # Execute on existing paper_trader.py module if available
                    if HAS_PAPER_TRADER:
                        try:
                            qty = int(250000 / last_price)
                            paper_trader.execute_paper_trade(
                                ticker=symbol,
                                trade_type="Bearish Breakdown",
                                entry_price=last_price,
                                sl=round(sl_level, 2),
                                qty=qty,
                                token=token,
                                strategy="Volatility Contraction",
                                target=round(target_level, 2)
                            )
                        except Exception as pe:
                            logging.error(f"Failed to submit dashboard paper trade: {pe}")
                            
                    # Immediately clear the trigger value to prevent duplicates
                    metadata["trigger_sell"] = None

    def on_connect(self, ws, response):
        """WebSocket on_connect callback. Subscribes to target tokens."""
        logging.info(f"✅ WebSocket connected. Subscribing to {len(self.tokens)} watchlist instruments...")
        # Subscribe to watchlist tokens and stream quotes
        ws.subscribe(self.tokens)
        ws.set_mode(ws.MODE_QUOTE, self.tokens)
        logging.info("📈 WebSocket subscription modeQuote initialized successfully.")

    def on_close(self, ws, code, reason):
        logging.info(f"🔌 WebSocket connection closed. Code: {code} | Reason: {reason}")

    def on_error(self, ws, code, reason):
        logging.error(f"❌ WebSocket experienced error: Code {code} - {reason}")

    def start_monitoring(self):
        """Launches the WebSocket streaming loop."""
        if not self.tokens:
            logging.warning("⚠️ No instruments in the watchlist. Skipping Live Monitor Stage 3.")
            return

        logging.info("📡 Starting Stage 3: Intraday Live Monitor...")
        self.kws = KiteTicker(self.api_key, self.access_token)
        
        # Bind WebSocket events
        self.kws.on_ticks = self.on_ticks
        self.kws.on_connect = self.on_connect
        self.kws.on_close = self.on_close
        self.kws.on_error = self.on_error
        self.kws.reconnect = True
        
        try:
            # Block and stream ticks (Ctrl+C to terminate)
            self.kws.connect(threaded=False)
        except KeyboardInterrupt:
            logging.info("🚪 Manual termination requested. Shutting down WebSocket Monitor...")
            self.stop_monitoring()
        except Exception as e:
            logging.error(f"❌ WebSocket runtime crash: {e}", exc_info=True)

    def stop_monitoring(self):
        """Gracefully tears down the running WebSocket connection."""
        if self.kws:
            try:
                self.kws.close()
                logging.info("✅ WebSocket closed successfully.")
            except Exception as e:
                logging.error(f"⚠️ Error closing WebSocket: {e}")


# ==============================================================================
# ASYNCHRONOUS WEBSOCKET CONTROLS FOR STREAMLIT
# ==============================================================================

import sys

# Persist the running instance globally in the system module namespace
# to avoid losing thread references on Streamlit hot-reloads/re-imports.
if not hasattr(sys, "_vcp_monitor_instance"):
    sys._vcp_monitor_instance = None
if not hasattr(sys, "_vcp_monitor_thread"):
    sys._vcp_monitor_thread = None
if not hasattr(sys, "_vcp_monitor_enabled"):
    sys._vcp_monitor_enabled = False

def start_live_monitor(kite, watchlist):
    """
    Starts the Stage 3 real-time WebSocket monitor in a background daemon thread
    so it does not freeze or block the Streamlit dashboard app process.
    """
    # 1. Thread verification: Prevent spawning a new thread if one is already running in background
    for t in threading.enumerate():
        if t.name == "vcp_monitor_thread" and t.is_alive():
            sys._vcp_monitor_enabled = True
            logging.warning("⚠️ vcp_monitor_thread is already running in background. Skipping spawn.")
            return True, "Live Monitor is already running in the background."

    if sys._vcp_monitor_instance and sys._vcp_monitor_instance.kws and sys._vcp_monitor_instance.kws.is_connected():
        sys._vcp_monitor_enabled = True
        return True, "Live Monitor is already running."
        
    session_file = ".kite_session.json"
    if not os.path.exists(session_file):
        return False, "Active Kite session file not found. Please log in first."
        
    try:
        with open(session_file, "r") as f:
            session = json.load(f)
            
        sys._vcp_monitor_instance = IntradayLiveMonitor(
            api_key=config.KITE_API_KEY,
            access_token=session["access_token"],
            watchlist=watchlist
        )
        
        def run():
            sys._vcp_monitor_instance.start_monitoring()
            
        sys._vcp_monitor_thread = threading.Thread(target=run, name="vcp_monitor_thread", daemon=True)
        sys._vcp_monitor_thread.start()
        sys._vcp_monitor_enabled = True
        logging.info("🟢 Volatility Contraction Live WebSocket Monitor thread spawned.")
        return True, "Live monitor WebSocket successfully started in the background."
    except Exception as e:
        sys._vcp_monitor_enabled = False
        logging.error(f"❌ Failed to start live monitor thread: {e}")
        return False, f"Failed to start live monitor: {e}"

def stop_live_monitor():
    """Tears down the running background WebSocket connection."""
    sys._vcp_monitor_enabled = False
    if sys._vcp_monitor_instance:
        try:
            sys._vcp_monitor_instance.stop_monitoring()
            if sys._vcp_monitor_thread and sys._vcp_monitor_thread.is_alive():
                sys._vcp_monitor_thread.join(timeout=1.0)
            sys._vcp_monitor_instance = None
            sys._vcp_monitor_thread = None
            logging.info("🔴 Volatility Contraction Live WebSocket Monitor thread stopped.")
            return True, "Live monitor WebSocket stopped."
        except Exception as e:
            logging.error(f"⚠️ Error stopping live monitor: {e}")
            return False, f"Error stopping live monitor: {e}"
    return False, "Live monitor is not currently running."

def is_live_monitor_running():
    """Returns True if the background live monitor instance is active."""
    if not getattr(sys, "_vcp_monitor_enabled", False):
        return False
    # Also verify if the thread is alive in the process
    thread_alive = any(t.name == "vcp_monitor_thread" and t.is_alive() for t in threading.enumerate())
    instance_connected = sys._vcp_monitor_instance is not None and sys._vcp_monitor_instance.kws and sys._vcp_monitor_instance.kws.is_connected()
    return thread_alive or instance_connected


# ==============================================================================
# PIPELINE RUNNER & MAIN CLI EXECUTION
# ==============================================================================

def run_unified_scanner():
    """
    Ties Stage 1, Stage 2, and Stage 3 together into a single, fully automated
    algorithmic stock scanner pipeline.
    """
    logging.info("🏁 Initializing Unified Volatility Contraction Scanner Pipeline...")
    
    # 1. Initialize Kite REST Instance
    kite = get_kite_instance()
    if not kite:
        return

    # 2. Stage 1: EOD Proximity Filter
    symbols = fetch_nifty500_symbols()
    run_stage1_proximity_filter(kite, symbols)
    
    # 3. Stage 2: Volatility Contraction Check
    watchlist = run_stage2_setup_validation(kite)
    if not watchlist:
        logging.warning("⚠️ No stocks passed EOD filter and volatility checks. Scanner stopping.")
        return

    # 4. Stage 3: Intraday WebSocket Streaming & Paper Execution
    session_file = ".kite_session.json"
    with open(session_file, "r") as f:
        session = json.load(f)
        
    monitor = IntradayLiveMonitor(
        api_key=config.KITE_API_KEY,
        access_token=session["access_token"],
        watchlist=watchlist
    )
    monitor.start_monitoring()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Multi-Stage Volatility Contraction Stock Scanner CLI")
    parser.add_argument("--stage", type=int, choices=[1, 2, 3], help="Specify a single stage to run (1, 2, or 3). Runs all combined by default.")
    args = parser.parse_args()

    if args.stage == 1:
        kite = get_kite_instance()
        if kite:
            symbols = fetch_nifty500_symbols()
            run_stage1_proximity_filter(kite, symbols)
    elif args.stage == 2:
        kite = get_kite_instance()
        if kite:
            run_stage2_setup_validation(kite)
    elif args.stage == 3:
        # Load watchlist from mock config or build it from cached Stage 2 output
        # For simplicity in stage 3 standalone execution, we fetch active Stage 2 output
        kite = get_kite_instance()
        if kite:
            watchlist = run_stage2_setup_validation(kite)
            if watchlist:
                session_file = ".kite_session.json"
                with open(session_file, "r") as f:
                    session = json.load(f)
                monitor = IntradayLiveMonitor(
                    api_key=config.KITE_API_KEY,
                    access_token=session["access_token"],
                    watchlist=watchlist
                )
                monitor.start_monitoring()
            else:
                logging.warning("⚠️ Watchlist empty. Run stages 1 and 2 to populate cache.")
    else:
        # Execute entire pipeline (Stage 1 -> Stage 2 -> Stage 3)
        run_unified_scanner()
