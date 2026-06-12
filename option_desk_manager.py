import os
import json
import logging
import math
import pandas as pd
import datetime
import threading
import config
import paper_trader
import telegram_agent
from options_bot import get_option_chain

STATE_FILE = "option_desk_state.json"
_monitor_thread = None
_stop_event = threading.Event()
_state_lock = threading.Lock()

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_state():
    with _state_lock:
        if not os.path.exists(STATE_FILE):
            default_state = {
                "is_running": False,
                "index_name": "NIFTY",
                "risk_capital": 250000.0,
                "lots": 3,
                "target_delta": 0.2,
                "stoploss_delta": 0.5,
                "entry_time": "09:20",
                "exit_time": "15:15",
                "stoploss_action": "Roll",
                "realized_pnl": 0.0,
                "status_message": "Not running",
                "last_update": "",
                "qty": 0,
                "ce_ticker": None,
                "pe_ticker": None,
                "ce_entry_price": 0.0,
                "pe_entry_price": 0.0
            }
            with open(STATE_FILE, "w") as f:
                json.dump(default_state, f, indent=4)
            return default_state
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading option desk state: {e}")
            return {}

def save_state(state):
    with _state_lock:
        try:
            state["last_update"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, indent=4)
        except Exception as e:
            logging.error(f"Error saving option desk state: {e}")

def norm_cdf(x):
    """Cumulative distribution function for the standard normal distribution."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def calculate_delta(spot, strike, days_to_expiry, iv, option_type, r=0.07):
    """Calculates Option Delta using the Black-Scholes formula."""
    if days_to_expiry <= 0:
        if option_type == "CE":
            return 1.0 if spot >= strike else 0.0
        else:
            return -1.0 if spot <= strike else 0.0
            
    T = days_to_expiry / 365.0
    sigma = max(0.01, iv) # prevent division by zero
    
    d1 = (math.log(spot / strike) + (r + (sigma ** 2) / 2.0) * T) / (sigma * math.sqrt(T))
    
    if option_type == "CE":
        return norm_cdf(d1)
    else:
        return norm_cdf(d1) - 1.0

def get_days_to_expiry(expiry_dt):
    """Calculate days to expiry from datetime or timestamp."""
    try:
        if isinstance(expiry_dt, str):
            expiry_dt = pd.to_datetime(expiry_dt.split(' ')[0])
        elif isinstance(expiry_dt, datetime.datetime):
            pass
        else:
            expiry_dt = pd.to_datetime(expiry_dt)
        today = datetime.datetime.now().date()
        expiry_date = expiry_dt.date()
        return max(0, (expiry_date - today).days)
    except Exception as e:
        logging.error(f"Error parsing expiry date: {e}")
        return 0

def find_strike_by_delta(kite, active_chain, spot, days_to_expiry, vix, option_type, target_delta=0.2):
    """Finds the option strike closest to the target delta."""
    iv = vix / 100.0 if vix > 0 else 0.16
    
    best_strike = None
    best_symbol = None
    best_token = None
    min_diff = float('inf')
    best_delta = 0.0
    
    # Calculate delta for each strike
    for _, row in active_chain[active_chain['instrument_type'] == option_type].iterrows():
        strike = float(row['strike'])
        delta = calculate_delta(spot, strike, days_to_expiry, iv, option_type)
        diff = abs(abs(delta) - target_delta)
        
        if diff < min_diff:
            min_diff = diff
            best_strike = strike
            best_symbol = row['tradingsymbol']
            best_token = int(row['instrument_token'])
            best_delta = delta
            
    # Fetch LTP for the best symbol
    exch = "BFO" if best_symbol.startswith("SENSEX") else "NFO"
    ltp = 0.0
    try:
        quote = kite.ltp([f"{exch}:{best_symbol}"])
        ltp = quote.get(f"{exch}:{best_symbol}", {}).get('last_price', 0.0)
    except Exception as e:
        logging.error(f"Error fetching LTP for strike search: {e}")
        
    return {
        "strike": best_strike,
        "symbol": best_symbol,
        "token": best_token,
        "delta": best_delta,
        "ltp": ltp
    }

def add_notification_to_shared(ticker, msg, category="Options"):
    """Appends a notification to the shared file."""
    SHARED_NOTIFICATIONS_FILE = "shared_notifications.json"
    try:
        data = []
        if os.path.exists(SHARED_NOTIFICATIONS_FILE):
            with open(SHARED_NOTIFICATIONS_FILE, "r") as f:
                data = json.load(f)
        data.append({
            "ticker": ticker,
            "msg": msg,
            "category": category,
            "time": datetime.datetime.now().strftime("%H:%M")
        })
        with open(SHARED_NOTIFICATIONS_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logging.error(f"Failed to append shared notification: {e}")

def _send_alert(msg):
    bot_token = getattr(config, 'TELEGRAM_BOT_TOKEN', '')
    chat_id = getattr(config, 'TELEGRAM_PERSONAL_CHAT_ID', '') or getattr(config, 'TELEGRAM_CHAT_ID_INTRADAY', getattr(config, 'TELEGRAM_CHAT_ID', ''))
    if bot_token and chat_id:
        telegram_agent.send_message(msg, bot_token, chat_id, parse_mode="Markdown")
    add_notification_to_shared("OptionDesk", msg.replace("*", "").replace("`", ""))

def start_strategy(kite, index_name, capital, target_delta, stoploss_delta, entry_time_str, exit_time_str, stoploss_action, lots):
    state = load_state()
    if state.get("is_running"):
        return False, "Option Desk Strategy is already running."
        
    state["index_name"] = index_name
    state["risk_capital"] = float(capital)
    state["lots"] = int(lots)
    state["target_delta"] = float(target_delta)
    state["stoploss_delta"] = float(stoploss_delta)
    state["entry_time"] = entry_time_str
    state["exit_time"] = exit_time_str
    state["stoploss_action"] = stoploss_action
    state["realized_pnl"] = 0.0
    state["ce_ticker"] = None
    state["pe_ticker"] = None
    state["ce_entry_price"] = 0.0
    state["pe_entry_price"] = 0.0
    state["is_running"] = True
    state["status_message"] = "Initializing..."
    save_state(state)
    
    global _monitor_thread, _stop_event
    _stop_event.clear()
    _monitor_thread = threading.Thread(target=_monitor_loop, args=(kite,), name="option_desk_monitor_thread", daemon=True)
    _monitor_thread.start()
    
    return True, f"Option Desk strategy initialized for {index_name}. Running in background."

def stop_strategy(kite):
    state = load_state()
    if not state.get("is_running"):
        return False, "Strategy is not running."
        
    global _stop_event
    _stop_event.set()
    
    _exit_active_positions(kite, state, "Manual stop request")
    return True, "Option Desk Strategy stopped. All active positions squared off."

def init_option_desk_on_startup(kite):
    state = load_state()
    if state.get("is_running"):
        global _monitor_thread, _stop_event
        # Check if thread is already active
        for t in threading.enumerate():
            if t.name == "option_desk_monitor_thread" and t.is_alive():
                return
                
        logging.info("Auto-restarting Option Desk background monitor thread on startup...")
        _stop_event.clear()
        _monitor_thread = threading.Thread(target=_monitor_loop, args=(kite,), name="option_desk_monitor_thread", daemon=True)
        _monitor_thread.start()

def _monitor_loop(kite):
    logging.info("Starting Option Desk background monitor thread...")
    while not _stop_event.is_set():
        state = load_state()
        if not state.get("is_running"):
            break
            
        try:
            now = datetime.datetime.now()
            start_h, start_m = map(int, state["entry_time"].split(":"))
            end_h, end_m = map(int, state["exit_time"].split(":"))
            
            start_time = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
            end_time = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
            
            if now < start_time:
                state["status_message"] = f"Waiting for entry time {state['entry_time']}..."
                save_state(state)
                _stop_event.wait(10)
                continue
                
            if now >= end_time:
                _exit_active_positions(kite, state, "EOD Time Square-off")
                break
                
            _check_and_execute_strategy(kite, state, now)
            
            # Log intraday P&L snapshot for options
            try:
                paper_trader.log_intraday_pnl_snapshot(kite)
            except Exception as le:
                logging.error(f"Failed to log intraday PnL snapshot: {le}")
            
        except Exception as e:
            logging.error(f"Error in Option Desk monitor loop: {e}", exc_info=True)
            
        _stop_event.wait(10)
        
    logging.info("Option Desk background monitor thread stopped.")

def _check_and_execute_strategy(kite, state, now):
    index_name = state["index_name"]
    spot_symbol = "NSE:NIFTY 50" if index_name == "NIFTY" else "BSE:SENSEX"
    nifty_lot = getattr(config, 'LOT_SIZE_NIFTY', 65)
    sensex_lot = getattr(config, 'LOT_SIZE_SENSEX', 20)
    lots = int(state.get("lots", 3))
    qty = lots * nifty_lot if index_name == "NIFTY" else lots * sensex_lot
    state["qty"] = qty
    
    # 1. Fetch Option Chain
    active_chain, step = get_option_chain(kite, index_name)
    if active_chain is None or active_chain.empty:
        logging.error("Failed to fetch option chain in background monitor.")
        return
        
    # 2. Get Spot Price
    try:
        spot = kite.quote([spot_symbol])[spot_symbol]['last_price']
    except Exception as e:
        logging.error(f"Failed to get spot price: {e}")
        return
        
    # 3. Get VIX
    try:
        vix = kite.quote(["NSE:INDIA VIX"])["NSE:INDIA VIX"]['last_price']
    except Exception:
        vix = 15.0
        
    expiry_date = active_chain.iloc[0]['expiry']
    days_to_expiry = get_days_to_expiry(expiry_date)
    iv = vix / 100.0
    
    # Check if either position is missing and needs entry
    ce_ticker = state.get("ce_ticker")
    pe_ticker = state.get("pe_ticker")
    
    # Check current active portfolio
    portfolio = paper_trader.get_portfolio()
    active_symbols = portfolio[portfolio['Status'] == 'Active']['Ticker'].values if not portfolio.empty else []
    
    # CE Entry
    if not ce_ticker or ce_ticker not in active_symbols:
        ce_setup = find_strike_by_delta(kite, active_chain, spot, days_to_expiry, vix, "CE", target_delta=state["target_delta"])
        if ce_setup["symbol"]:
            paper_trader.execute_paper_trade(
                ticker=ce_setup["symbol"],
                trade_type="Options Selling (CE)",
                entry_price=ce_setup["ltp"],
                sl=state["stoploss_delta"],
                qty=qty,
                token=ce_setup["token"],
                strategy="Option Desk",
                target=None,
                delta=round(abs(ce_setup["delta"]), 2)
            )
            state["ce_ticker"] = ce_setup["symbol"]
            state["ce_entry_price"] = ce_setup["ltp"]
            save_state(state)
            _send_alert(f"🟢 *Option Desk: Sold CE* {ce_setup['symbol']} @ ₹{ce_setup['ltp']:.2f} (Delta: {ce_setup['delta']:.2f})")
            
    # PE Entry
    if not pe_ticker or pe_ticker not in active_symbols:
        pe_setup = find_strike_by_delta(kite, active_chain, spot, days_to_expiry, vix, "PE", target_delta=state["target_delta"])
        if pe_setup["symbol"]:
            paper_trader.execute_paper_trade(
                ticker=pe_setup["symbol"],
                trade_type="Options Selling (PE)",
                entry_price=pe_setup["ltp"],
                sl=state["stoploss_delta"],
                qty=qty,
                token=pe_setup["token"],
                strategy="Option Desk",
                target=None,
                delta=round(abs(pe_setup["delta"]), 2)
            )
            state["pe_ticker"] = pe_setup["symbol"]
            state["pe_entry_price"] = pe_setup["ltp"]
            save_state(state)
            _send_alert(f"🟢 *Option Desk: Sold PE* {pe_setup['symbol']} @ ₹{pe_setup['ltp']:.2f} (Delta: {pe_setup['delta']:.2f})")

    # Update state tickers to match active symbols
    ce_ticker = state.get("ce_ticker")
    pe_ticker = state.get("pe_ticker")
    
    # 4. Monitor active legs
    for opt_type, ticker in [("CE", ce_ticker), ("PE", pe_ticker)]:
        if not ticker or ticker not in active_symbols:
            continue
            
        # Parse strike
        strike_val = 0.0
        matching_inst = active_chain[active_chain['tradingsymbol'] == ticker]
        if not matching_inst.empty:
            strike_val = float(matching_inst.iloc[0]['strike'])
        else:
            try:
                strike_str = ticker.replace(index_name, "")
                for char in ["C", "E", "P"]:
                    strike_str = strike_str.replace(char, "")
                digits = "".join([c for c in strike_str if c.isdigit()])
                strike_val = float(digits[-5:])
            except Exception:
                continue
                
        # Calculate current delta
        current_delta = calculate_delta(spot, strike_val, days_to_expiry, iv, opt_type)
        abs_delta = abs(current_delta)
        
        # Fetch LTP
        exch = "BFO" if index_name == "SENSEX" else "NFO"
        try:
            opt_quote = kite.ltp([f"{exch}:{ticker}"])
            current_price = opt_quote.get(f"{exch}:{ticker}", {}).get('last_price', 0.0)
        except Exception:
            continue
            
        # Check Stop Loss Delta Hit
        if abs_delta >= state["stoploss_delta"]:
            logging.info(f"🚨 Option Desk: SL Hit (Delta: {abs_delta:.2f} >= {state['stoploss_delta']:.2f}) for {ticker}.")
            paper_trader.exit_trade(ticker, kite, override_price=current_price)
            
            # Calculate realized P&L for this leg
            row = portfolio[(portfolio['Ticker'] == ticker) & (portfolio['Status'] == 'Active')].iloc[0]
            pnl = (row['EntryPrice'] - current_price) * qty
            state["realized_pnl"] += pnl
            
            _send_alert(f"❌ *Option Desk: SL Hit* on {ticker} @ ₹{current_price:.2f} (Delta: {abs_delta:.2f}). Leg Profit/Loss: ₹{pnl:.2f}")
            
            if state["stoploss_action"] == "Roll":
                # Roll: Find new strike matching target delta
                new_setup = find_strike_by_delta(kite, active_chain, spot, days_to_expiry, vix, opt_type, target_delta=state["target_delta"])
                if new_setup["symbol"]:
                    paper_trader.execute_paper_trade(
                        ticker=new_setup["symbol"],
                        trade_type=f"Options Selling ({opt_type})",
                        entry_price=new_setup["ltp"],
                        sl=state["stoploss_delta"],
                        qty=qty,
                        token=new_setup["token"],
                        strategy="Option Desk",
                        target=None,
                        delta=round(abs(new_setup["delta"]), 2)
                    )
                    if opt_type == "CE":
                        state["ce_ticker"] = new_setup["symbol"]
                        state["ce_entry_price"] = new_setup["ltp"]
                    else:
                        state["pe_ticker"] = new_setup["symbol"]
                        state["pe_entry_price"] = new_setup["ltp"]
                    save_state(state)
                    _send_alert(f"🔄 *Option Desk: Rolled* CE/PE to {new_setup['symbol']} @ ₹{new_setup['ltp']:.2f} (Delta: {new_setup['delta']:.2f})")
            else:
                # Stoploss Action is Close: Nullify ticker in state so it doesn't re-enter or roll
                if opt_type == "CE":
                    state["ce_ticker"] = None
                else:
                    state["pe_ticker"] = None
                save_state(state)
        else:
            # Update current price & delta in active portfolio
            try:
                df = paper_trader.get_portfolio()
                if not df.empty:
                    df.loc[(df['Ticker'] == ticker) & (df['Status'] == 'Active'), 'Current Price'] = current_price
                    df.loc[(df['Ticker'] == ticker) & (df['Status'] == 'Active'), 'Delta'] = round(abs_delta, 2)
                    df.to_csv(paper_trader.PORTFOLIO_FILE, index=False)
            except Exception as pe:
                logging.error(f"Error updating Option Desk active positions: {pe}")
                
    # Calculate live MTM stats for display
    active_desk = portfolio[(portfolio['Strategy'] == 'Option Desk') & (portfolio['Status'] == 'Active')]
    unrealized_pnl = 0.0
    for _, row in active_desk.iterrows():
        t = row['Ticker']
        exch = "BFO" if index_name == "SENSEX" else "NFO"
        try:
            cp = kite.ltp([f"{exch}:{t}"]).get(f"{exch}:{t}", {}).get('last_price', row['EntryPrice'])
            unrealized_pnl += (row['EntryPrice'] - cp) * qty
        except Exception:
            pass
            
    total_pnl = state["realized_pnl"] + unrealized_pnl
    state["status_message"] = f"Running | CE: {state.get('ce_ticker')} | PE: {state.get('pe_ticker')} | MTM: ₹{total_pnl:.2f}"
    save_state(state)

def _exit_active_positions(kite, state, reason):
    portfolio = paper_trader.get_portfolio()
    if portfolio.empty:
        return
        
    active_desk = portfolio[(portfolio['Strategy'] == 'Option Desk') & (portfolio['Status'] == 'Active')].copy()
    qty = state.get("qty", 0)
    
    for _, row in active_desk.iterrows():
        ticker = row['Ticker']
        exch = "BFO" if state["index_name"] == "SENSEX" else "NFO"
        exit_price = row['EntryPrice']
        try:
            quote = kite.ltp([f"{exch}:{ticker}"])
            exit_price = quote.get(f"{exch}:{ticker}", {}).get('last_price', exit_price)
        except Exception:
            pass
            
        paper_trader.exit_trade(ticker, kite, override_price=exit_price)
        pnl = (row['EntryPrice'] - exit_price) * qty
        state["realized_pnl"] += pnl
        
    state["ce_ticker"] = None
    state["pe_ticker"] = None
    state["is_running"] = False
    state["status_message"] = f"Stopped: {reason} | Final MTM: ₹{state['realized_pnl']:.2f}"
    save_state(state)
    _send_alert(f"🛑 *Option Desk Strategy Stopped*\nIndex: {state['index_name']}\nReason: {reason}\nFinal Day MTM: *₹{state['realized_pnl']:.2f}*")

def update_desk_portfolio_and_roll(kite):
    """
    Called periodically on page refresh. Updates option prices and deltas in the portfolio.
    Exits/rolls are handled by the background thread if active, but we can update deltas for the UI.
    """
    portfolio = paper_trader.get_portfolio()
    if portfolio.empty:
        return
        
    active_desk = portfolio[(portfolio['Strategy'] == 'Option Desk') & (portfolio['Status'] == 'Active')].copy()
    if active_desk.empty:
        return
        
    # Get quotes
    try:
        quotes = kite.quote(["NSE:NIFTY 50", "BSE:SENSEX", "NSE:INDIA VIX"])
        spot_nifty = quotes.get("NSE:NIFTY 50", {}).get('last_price', 0.0)
        spot_sensex = quotes.get("BSE:SENSEX", {}).get('last_price', 0.0)
        vix = quotes.get("NSE:INDIA VIX", {}).get('last_price', 15.0)
    except Exception:
        return
        
    for idx, row in active_desk.iterrows():
        ticker = row['Ticker']
        is_nifty = ticker.startswith("NIFTY")
        spot = spot_nifty if is_nifty else spot_sensex
        index_name = "NIFTY" if is_nifty else "SENSEX"
        opt_type = "CE" if "CE" in ticker else "PE"
        
        active_chain, _ = get_option_chain(kite, index_name)
        if active_chain is None or active_chain.empty:
            continue
            
        expiry_date = active_chain.iloc[0]['expiry']
        days_to_expiry = get_days_to_expiry(expiry_date)
        
        strike_val = 0.0
        matching_inst = active_chain[active_chain['tradingsymbol'] == ticker]
        if not matching_inst.empty:
            strike_val = float(matching_inst.iloc[0]['strike'])
        else:
            try:
                strike_str = ticker.replace(index_name, "")
                for char in ["C", "E", "P"]:
                    strike_str = strike_str.replace(char, "")
                digits = "".join([c for c in strike_str if c.isdigit()])
                strike_val = float(digits[-5:])
            except Exception:
                continue
                
        iv = vix / 100.0
        current_delta = calculate_delta(spot, strike_val, days_to_expiry, iv, opt_type)
        abs_delta = abs(current_delta)
        
        exch = "BFO" if is_nifty is False else "NFO"
        try:
            opt_quote = kite.ltp([f"{exch}:{ticker}"])
            current_price = opt_quote.get(f"{exch}:{ticker}", {}).get('last_price', row['EntryPrice'])
        except Exception:
            current_price = row['EntryPrice']
            
        try:
            df = paper_trader.get_portfolio()
            if not df.empty:
                df.loc[(df['Ticker'] == ticker) & (df['Status'] == 'Active'), 'Current Price'] = current_price
                df.loc[(df['Ticker'] == ticker) & (df['Status'] == 'Active'), 'Delta'] = round(abs_delta, 2)
                df.to_csv(paper_trader.PORTFOLIO_FILE, index=False)
        except Exception:
            pass
