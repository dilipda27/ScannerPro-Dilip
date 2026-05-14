import threading
import queue
import time
import sqlite3
import pandas as pd
import logging
import datetime
import os
from kiteconnect import KiteTicker
import config
import telegram_agent

# --- GLOBAL STATE ---
system_state = {
    "is_running": False,
    "is_holiday": False,
    "spot_price": 0.0,
    "current_atm": 0.0,
    "pcr": 1.0,
    "ce_oi": 0,
    "pe_oi": 0,
    "pcr_history": [],
    "vix": 0.0,
    "prob_score": 50.0,
    "latest_signal": "Neutral",
    "recommended_trade": "None",
    "index_name": "NIFTY"
}

db_queue = queue.Queue()
bot_threads = []
stop_event = threading.Event()
ticker_instance = None
DB_FILE = "options_ticks.db"
SIGNAL_LOG_FILE = "options_signals.csv"
sent_signals = {} # Cache to prevent spamming Telegram
INSTRUMENT_CACHE_PREFIX = "kite_instruments_"

def get_cached_instruments(kite, exchange):
    """
    Fetches instruments from Kite and caches them in a CSV file to avoid 
    downloading large data (~10MB for NFO) every time.
    TTL: 24 Hours.
    """
    cache_file = f"{INSTRUMENT_CACHE_PREFIX}{exchange.lower()}.csv"
    
    if os.path.exists(cache_file):
        file_time = datetime.datetime.fromtimestamp(os.path.getmtime(cache_file)).date()
        if file_time == datetime.date.today():
            logging.info(f"Loading {exchange} instruments from local cache: {cache_file}")
            return pd.read_csv(cache_file)
    
    logging.info(f"Downloading {exchange} instruments from Kite (Large Data)...")
    try:
        instruments = kite.instruments(exchange)
        df = pd.DataFrame(instruments)
        # Store only essential columns to keep cache small
        essential_df = df[['instrument_token', 'tradingsymbol', 'name', 'expiry', 'strike', 'instrument_type', 'segment', 'exchange']]
        essential_df.to_csv(cache_file, index=False)
        return essential_df
    except Exception as e:
        logging.error(f"Failed to fetch instruments from Kite: {e}")
        if os.path.exists(cache_file):
            logging.warning("Falling back to expired local cache.")
            return pd.read_csv(cache_file)
        return pd.DataFrame()


def is_market_holiday():
    """Returns True if it's weekend or outside 9:15 AM - 3:30 PM IST"""
    now = datetime.datetime.now()
    # Weekend Check
    if now.weekday() >= 5: # Saturday=5, Sunday=6
        return True
    
    # Time Check (IST is UTC+5:30)
    # Simple check for local time if user is in India
    market_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    
    if now < market_start or now > market_end:
        return True
        
    return False

# --- HELPER FUNCTIONS ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS ticks
                 (timestamp TEXT, instrument_token INTEGER, last_price REAL, volume INTEGER, oi INTEGER)''')
    conn.commit()
    conn.close()

def log_signal(index, signal_type, score, pcr, spot, recommendation="None"):
    """Logs simulated trades to a CSV file"""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_data = pd.DataFrame([{
        "Timestamp": timestamp,
        "Index": index,
        "Signal": signal_type,
        "Score": score,
        "PCR": pcr,
        "Spot": spot,
        "Recommendation": recommendation
    }])
    
    if os.path.exists(SIGNAL_LOG_FILE):
        new_data.to_csv(SIGNAL_LOG_FILE, mode='a', header=False, index=False)
    else:
        new_data.to_csv(SIGNAL_LOG_FILE, index=False)

def get_option_chain(kite, index_name):
    """Auto-fetches the current active option chain for the selected index."""
    logging.info(f"Fetching option chain for {index_name}...")
    try:
        # Determine exchange and symbol prefix
        if index_name == "NIFTY":
            exch = "NFO"
            name = "NIFTY"
            step = 50
        elif index_name == "SENSEX":
            exch = "BFO"
            name = "SENSEX"
            step = 100
        else:
            return None
            
        instruments_df = get_cached_instruments(kite, exch)
        if instruments_df.empty:
            return None, None
            
        # Filter for current expiry options
        df_opt = instruments_df[(instruments_df['name'] == name) & (instruments_df['segment'] == exch + '-OPT')]
        if df_opt.empty:
            return None, None
            
        # Get the closest expiry date
        df_opt['expiry'] = pd.to_datetime(df_opt['expiry'])
        current_expiry = df_opt['expiry'].min()
        
        active_chain = df_opt[df_opt['expiry'] == current_expiry]
        return active_chain, step

    except Exception as e:
        logging.error(f"Error fetching option chain: {e}")
        return None, None

# --- THREAD 1: WEBSOCKET FEEDER ---
def websocket_feeder_thread(api_key, access_token, underlying_token):
    """
    Connects to KiteTicker, streams real-time spot price,
    updates system_state instantly, and queues ticks for DB logging.
    """
    global ticker_instance
    logging.info("Starting Websocket Feeder Thread...")
    
    def on_ticks(ws, ticks):
        if not system_state["is_running"]:
            ws.close()
            return
            
        for tick in ticks:
            if tick['instrument_token'] == underlying_token:
                system_state["spot_price"] = tick['last_price']
                
            # Push to queue for background DB logging
            db_queue.put({
                "timestamp": datetime.datetime.now().isoformat(),
                "instrument_token": tick['instrument_token'],
                "last_price": tick['last_price'],
                "volume": tick.get('volume_traded', 0),
                "oi": tick.get('oi', 0)
            })

    def on_connect(ws, response):
        logging.info("Websocket Connected. Subscribing to spot...")
        ws.subscribe([underlying_token])
        ws.set_mode(ws.MODE_FULL, [underlying_token])
        system_state["latest_signal"] = "🟢 WebSocket Connected"

    def on_error(ws, code, reason):
        logging.error(f"Websocket Error: {code} - {reason}")
        if "handshake timeout" in str(reason).lower():
            system_state["latest_signal"] = "🟠 Connection Timeout... Retrying"
        else:
            system_state["latest_signal"] = f"🔴 Connection Error: {reason}"

    def on_close(ws, code, reason):
        logging.info(f"Websocket Closed: {code} - {reason}")

    def on_reconnect(ws, attempts_count):
        logging.info(f"Reconnecting to Websocket... Attempt: {attempts_count}")
        system_state["latest_signal"] = f"🟡 Reconnecting ({attempts_count})..."

    def on_noreconnect(ws):
        logging.error("Websocket failed to reconnect after max attempts.")
        system_state["latest_signal"] = "🔴 Connection Failed (Max Retries)"

    while not stop_event.is_set() and system_state["is_running"]:
        try:
            ticker_instance = KiteTicker(api_key, access_token)
            ticker_instance.on_ticks = on_ticks
            ticker_instance.on_connect = on_connect
            ticker_instance.on_error = on_error
            ticker_instance.on_close = on_close
            ticker_instance.on_reconnect = on_reconnect
            ticker_instance.on_noreconnect = on_noreconnect
            ticker_instance.reconnect = True
            
            logging.info("Attempting WebSocket connection...")
            ticker_instance.connect(threaded=False)
        except Exception as e:
            if stop_event.is_set(): break
            logging.error(f"WebSocket Connection Failed: {e}. Retrying in 10s...")
            system_state["latest_signal"] = f"🔴 Connection Error... Retrying"
            stop_event.wait(10)

# --- THREAD 2: OPTIONS SNAPSHOT ENGINE ---
def options_snapshot_engine(kite, active_chain, step):
    """
    Wakes up every 3 mins. Calculates ATM.
    Fetches 40 active options via a SINGLE bulk kite.quote() call.
    Calculates PCR and OI momentum.
    """
    logging.info("Starting Options Snapshot Engine...")
    while not stop_event.is_set() and system_state["is_running"]:
        try:
            # Fetch India VIX (token 264969)
            try:
                vix_data = kite.quote(["NSE:INDIA VIX"])["NSE:INDIA VIX"]
                system_state["vix"] = vix_data['last_price']
            except:
                pass

            spot = system_state["spot_price"]
            if spot == 0.0:
                stop_event.wait(5)
                continue
                
            # Calculate ATM Strike
            atm_strike = round(spot / step) * step
            system_state["current_atm"] = atm_strike
            
            # Select 10 strikes above and 10 below ATM
            strikes = [atm_strike + (i * step) for i in range(-10, 11)]
            
            target_options = active_chain[active_chain['strike'].isin(strikes)]
            
            # Prepare instrument list for bulk quote (Max 500 allowed, we send 40)
            quote_symbols = [f"{row['exchange']}:{row['tradingsymbol']}" for _, row in target_options.iterrows()]
            
            if quote_symbols:
                quotes = kite.quote(quote_symbols)
                
                total_ce_oi = 0
                total_pe_oi = 0
                
                for _, row in target_options.iterrows():
                    sym = f"{row['exchange']}:{row['tradingsymbol']}"
                    q = quotes.get(sym)
                    if q:
                        oi = q.get('oi', 0)
                        if row['instrument_type'] == 'CE':
                            total_ce_oi += oi
                        elif row['instrument_type'] == 'PE':
                            total_pe_oi += oi
                
                # Update State
                system_state["ce_oi"] = total_ce_oi
                system_state["pe_oi"] = total_pe_oi
                
                if total_ce_oi > 0:
                    pcr = total_pe_oi / total_ce_oi
                    system_state["pcr"] = round(pcr, 4)
                    system_state["pcr_history"].append(pcr)
                    # Keep history manageable
                    if len(system_state["pcr_history"]) > 100:
                        system_state["pcr_history"].pop(0)
                        
                logging.info(f"Snapshot Updated -> ATM: {atm_strike}, PCR: {system_state['pcr']}")
                
            # Sleep using Event wait for instant interruption
            stop_event.wait(180)
            
        except Exception as e:
            logging.error(f"Snapshot Engine Error: {e}")
            time.sleep(60)

# --- THREAD 3: DATABASE LOGGER ---
def database_logger_thread():
    """
    Continuously pulls from db_queue and writes to SQLite in batches.
    Decoupled to ensure main loops experience zero latency.
    """
    logging.info("Starting Database Logger Thread...")
    init_db()
    
    while system_state["is_running"]:
        batch = []
        try:
            # Gather up to 500 ticks or wait 1 second
            while len(batch) < 500:
                tick = db_queue.get(timeout=1.0)
                batch.append((tick["timestamp"], tick["instrument_token"], tick["last_price"], tick["volume"], tick["oi"]))
                db_queue.task_done()
        except queue.Empty:
            pass # Timeout reached, process whatever is in the batch
            
        if batch:
            try:
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.executemany("INSERT INTO ticks VALUES (?, ?, ?, ?, ?)", batch)
                conn.commit()
                conn.close()
            except Exception as e:
                logging.error(f"DB Write Error: {e}")

# --- THREAD 4: STRATEGY & SCORING ENGINE ---
def strategy_engine(index_name):
    """
    Reads from in-memory system_state.
    Calculates Bullish Probability Score based on weighted parameters.
    Logs simulated paper trades if threshold is crossed.
    """
    logging.info("Starting Strategy & Scoring Engine...")
    
    while system_state["is_running"]:
        try:
            score = 50.0 # Base neutral
            
            # 1. PCR Trend (35% weight)
            pcr = system_state["pcr"]
            pcr_hist = system_state["pcr_history"]
            
            pcr_score = 0
            if len(pcr_hist) > 3:
                # Is PCR rising?
                if pcr > pcr_hist[-3]:
                    pcr_score = 35 * min(1.0, (pcr - pcr_hist[-3]) * 5) # Scale rising
                elif pcr < pcr_hist[-3]:
                    pcr_score = -35 * min(1.0, (pcr_hist[-3] - pcr) * 5)
                    
            score += pcr_score
            
            # 2. OI Buildup Momentum (30% weight)
            ce_oi = system_state["ce_oi"]
            pe_oi = system_state["pe_oi"]
            
            oi_score = 0
            if ce_oi > 0 and pe_oi > 0:
                diff = (pe_oi - ce_oi) / (pe_oi + ce_oi)
                oi_score = 30 * diff
            
            score += oi_score
            
            # 3. Price Action Placeholder (20% weight)
            # Placeholder: Assume mildly bullish if above ATM
            spot = system_state["spot_price"]
            atm = system_state["current_atm"]
            pa_score = 0
            if spot > 0 and atm > 0:
                if spot > atm: pa_score = 10
                else: pa_score = -10
            score += pa_score
            
            # 4. Multi-Day Trend Placeholder (15% weight)
            # Placeholder: Neutral 0
            mdt_score = 0 
            score += mdt_score
            
            # Bound score between 0 and 100
            final_score = max(0, min(100, score))
            system_state["prob_score"] = round(final_score, 2)
            
            # Volatility Filter (VIX) - Placeholder check
            vix = system_state["vix"]
            
            # Generate Signals
            signal = "Neutral"
            if final_score > 75:
                if vix < 13: signal = "Bull Put Spread (Low VIX)"
                else: signal = "Bullish Directional"
            elif final_score < 25:
                if vix < 13: signal = "Bear Call Spread (Low VIX)"
                else: signal = "Bearish Directional"
                
            system_state["latest_signal"] = signal
            
            # --- Specific Trade Recommendation Logic ---
            recommendation = "Wait"
            if signal != "Neutral":
                if "Bullish" in signal:
                    recommendation = "Sell ATM PE" if vix > 15 else "Sell OTM PE (-200)"
                elif "Bearish" in signal:
                    recommendation = "Sell ATM CE" if vix > 15 else "Sell OTM CE (+200)"
            elif 45 <= final_score <= 55:
                recommendation = "Short Straddle" if vix > 15 else "Short Strangle"
            
            system_state["recommended_trade"] = recommendation
            
            # If actionable signal, log it (Debounce logic: only log if changed or every X mins)
            if signal != "Neutral":
                # Very basic debounce: log every 5 mins if still strong
                log_signal(index_name, signal, final_score, pcr, spot, recommendation)
                
                # Telegram Dispatch for high conviction signals
                now = datetime.datetime.now()
                cache_key = f"{index_name}_{signal}"
                
                if cache_key not in sent_signals or (now - sent_signals[cache_key]).seconds > 300:
                    tel_token = getattr(config, 'TELEGRAM_BOT_TOKEN', '')
                    tel_chat_id = getattr(config, 'TELEGRAM_CHAT_ID_INTRADAY', config.TELEGRAM_CHAT_ID)
                    
                    msg = (
                        f"🎯 *Options Bot Alert: {index_name}*\n\n"
                        f"🔥 *Signal:* {signal}\n"
                        f"📈 *Prob Score:* {final_score}%\n"
                        f"📊 *PCR:* {pcr}\n"
                        f"💰 *Spot:* {spot}\n\n"
                        f"🤖 _Single Signal Mode: Bot is now stopping._"
                    )
                    
                    if tel_token and tel_chat_id:
                        telegram_agent.send_message(msg, tel_token, tel_chat_id, parse_mode="Markdown")
                        sent_signals[cache_key] = now
                
                # --- ONE SIGNAL MODE: Terminate after first signal ---
                logging.info(f"Signal generated: {signal}. Stopping bot as per One-Signal policy.")
                stop_bot()
                break
                
            stop_event.wait(60) # Run scoring every minute

            
        except Exception as e:
            logging.error(f"Strategy Engine Error: {e}")
            time.sleep(10)

# --- BOT CONTROL INTERFACE ---
def start_bot(kite, index_name="NIFTY"):
    if system_state["is_running"]:
        return False, "Bot is already running."
        
    system_state["index_name"] = index_name
    stop_event.clear()
    
    # --- RESET SESSION STATE / CLEAR CACHE ---
    system_state["pcr_history"] = []
    system_state["latest_signal"] = "Initializing..."
    system_state["recommended_trade"] = "Wait"
    system_state["vix"] = 0.0
    sent_signals.clear()
    
    active_chain, step = get_option_chain(kite, index_name)

    if active_chain is None:
        return False, f"Failed to fetch option chain for {index_name}."
        
    # Get Underlying Token (Spot)
    try:
        if index_name == "NIFTY":
            spot_inst = kite.quote(["NSE:NIFTY 50"])["NSE:NIFTY 50"]
        else:
            spot_inst = kite.quote(["BSE:SENSEX"])["BSE:SENSEX"]
            
        underlying_token = spot_inst['instrument_token']
        system_state["spot_price"] = spot_inst['last_price']
        spot = spot_inst['last_price']
    except Exception as e:
        return False, f"Failed to fetch spot token: {e}"

    # Check for Holiday Mode
    if is_market_holiday():
        system_state["is_holiday"] = True
        system_state["is_running"] = False # Not running background loops
        
        # Perform Single Pass Analysis
        logging.info(f"Market Holiday Detected. Performing One-Off Analysis for {index_name}...")
        
        # Calculate ATM and Fetch Snapshot Once
        try:
            vix_data = kite.quote(["NSE:INDIA VIX"])["NSE:INDIA VIX"]
            system_state["vix"] = vix_data['last_price']
            logging.info(f"Holiday VIX Fetched: {system_state['vix']}")
        except Exception as e:
            logging.warning(f"Failed to fetch VIX on holiday: {e}")
            system_state["vix"] = 0.0 # Clear if failed

        atm_strike = round(spot / step) * step
        system_state["current_atm"] = atm_strike
        strikes = [atm_strike + (i * step) for i in range(-10, 11)]
        target_options = active_chain[active_chain['strike'].isin(strikes)]
        quote_symbols = [f"{row['exchange']}:{row['tradingsymbol']}" for _, row in target_options.iterrows()]
        
        if quote_symbols:
            quotes = kite.quote(quote_symbols)
            total_ce_oi = 0
            total_pe_oi = 0
            for _, row in target_options.iterrows():
                q = quotes.get(f"{row['exchange']}:{row['tradingsymbol']}")
                if q:
                    oi = q.get('oi', 0)
                    if row['instrument_type'] == 'CE': total_ce_oi += oi
                    elif row['instrument_type'] == 'PE': total_pe_oi += oi
            
            system_state["ce_oi"] = total_ce_oi
            system_state["pe_oi"] = total_pe_oi
            if total_ce_oi > 0:
                system_state["pcr"] = round(total_pe_oi / total_ce_oi, 4)
                system_state["pcr_history"].append(system_state["pcr"])

        # Run Strategy Once
        score = 50.0
        if system_state["ce_oi"] > 0 and system_state["pe_oi"] > 0:
            diff = (system_state["pe_oi"] - system_state["ce_oi"]) / (system_state["pe_oi"] + system_state["ce_oi"])
            score += 30 * diff
        
        atm = system_state["current_atm"]
        if spot > atm: score += 10
        else: score -= 10
        
        system_state["prob_score"] = round(score, 2)
        system_state["latest_signal"] = f"🏖️ Holiday Mode: {'Bullish' if score > 60 else 'Bearish' if score < 40 else 'Neutral'} (Last Data)"
        
        return True, f"🏖️ Market is closed. Holiday analysis complete for {index_name}."

    # Normal Market Hours Logic
    system_state["is_holiday"] = False
    system_state["is_running"] = True
    
    # Start Threads
    t1 = threading.Thread(target=websocket_feeder_thread, args=(kite.api_key, kite.access_token, underlying_token), daemon=True)
    t2 = threading.Thread(target=options_snapshot_engine, args=(kite, active_chain, step), daemon=True)
    t3 = threading.Thread(target=database_logger_thread, daemon=True)
    t4 = threading.Thread(target=strategy_engine, args=(index_name,), daemon=True)
    
    global bot_threads
    bot_threads = [t1, t2, t3, t4]
    
    for t in bot_threads:
        t.start()
        
    logging.info(f"Options Bot Started for {index_name}")
    return True, f"Started successfully for {index_name}."

def stop_bot():
    logging.info("Stopping Options Bot...")
    system_state["is_running"] = False
    stop_event.set()
    
    global ticker_instance
    if ticker_instance:
        try:
            ticker_instance.close()
        except:
            pass
            
    return True, "Bot stopped."

def get_state():
    return system_state

def execute_bot_recommendation(kite, index_name):
    """
    Resolves the current recommended_trade to actual instrument symbols
    and executes them as paper trades.
    """
    rec = system_state["recommended_trade"]
    if rec in ["Wait", "None"]:
        return False, "No actionable recommendation at the moment."
    
    active_chain, step = get_option_chain(kite, index_name)
    if active_chain is None:
        return False, "Failed to fetch option chain."
    
    spot = system_state["spot_price"]
    atm = system_state["current_atm"]
    
    import paper_trader
    
    # helper to fetch LTP and execute
    def place_trade(strike, opt_type, trade_label):
        row = active_chain[(active_chain['strike'] == strike) & (active_chain['instrument_type'] == opt_type)]
        if not row.empty:
            sym = row.iloc[0]['tradingsymbol']
            token = row.iloc[0]['instrument_token']
            try:
                ltp = kite.ltp([f"NFO:{sym}"] if index_name == "NIFTY" else [f"BFO:{sym}"])[f"{'NFO' if index_name == 'NIFTY' else 'BFO'}:{sym}"]['last_price']
                # Qty: 1 lot (50 for Nifty, 10 for Sensex - simplify to 50/10)
                qty = 50 if index_name == "NIFTY" else 10
                paper_trader.execute_paper_trade(
                    ticker=sym,
                    trade_type=f"Options Selling ({trade_label})",
                    entry_price=ltp,
                    sl=ltp * 1.5, # 50% SL for options selling
                    qty=qty,
                    token=token
                )
                return True, sym
            except:
                return False, sym
        return False, "Strike not found"

    success_trades = []
    if rec == "Sell ATM CE":
        ok, s = place_trade(atm, "CE", "ATM CE")
        if ok: success_trades.append(s)
    elif rec == "Sell ATM PE":
        ok, s = place_trade(atm, "PE", "ATM PE")
        if ok: success_trades.append(s)
    elif rec == "Sell OTM CE (+200)":
        ok, s = place_trade(atm + 200, "CE", "OTM CE")
        if ok: success_trades.append(s)
    elif rec == "Sell OTM PE (-200)":
        ok, s = place_trade(atm - 200, "PE", "OTM PE")
        if ok: success_trades.append(s)
    elif rec == "Short Straddle":
        ok1, s1 = place_trade(atm, "CE", "Straddle CE")
        ok2, s2 = place_trade(atm, "PE", "Straddle PE")
        if ok1: success_trades.append(s1)
        if ok2: success_trades.append(s2)
    elif rec == "Short Strangle":
        ok1, s1 = place_trade(atm + 200, "CE", "Strangle CE")
        ok2, s2 = place_trade(atm - 200, "PE", "Strangle PE")
        if ok1: success_trades.append(s1)
        if ok2: success_trades.append(s2)

    if success_trades:
        return True, f"Executed {len(success_trades)} trades: {', '.join(success_trades)}"
    return False, "Failed to execute trades. Check logs."
