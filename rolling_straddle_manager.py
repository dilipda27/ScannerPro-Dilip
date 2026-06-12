import os
import json
import logging
import datetime
import time
import threading
import pandas as pd
from options_bot import get_option_chain
import paper_trader
import config
import telegram_agent

STATE_FILE = "rolling_straddle_state.json"
_monitor_thread = None
_stop_event = threading.Event()
_state_lock = threading.Lock()

# Setup logging configuration if not already configured
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_state():
    with _state_lock:
        if not os.path.exists(STATE_FILE):
            default_state = {
                "is_running": False,
                "index_name": "NIFTY",
                "lots": 1,
                "start_time": "09:20",
                "end_time": "15:15",
                "rolling_threshold_pct": 0.5,
                "max_sl": 5000.0,
                "max_rolls": 5,
                "current_rolls": 0,
                "initial_spot": 0.0,
                "ce_ticker": None,
                "pe_ticker": None,
                "ce_entry_price": 0.0,
                "pe_entry_price": 0.0,
                "qty": 0,
                "realized_pnl": 0.0,
                "status_message": "Not running",
                "last_update": ""
            }
            with open(STATE_FILE, "w") as f:
                json.dump(default_state, f, indent=4)
            return default_state
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading straddle state: {e}")
            return {}

def save_state(state):
    with _state_lock:
        try:
            state["last_update"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, indent=4)
        except Exception as e:
            logging.error(f"Error saving straddle state: {e}")

def start_strategy(kite, index_name, lots, rolling_threshold_pct, max_sl, max_rolls, start_time_str, end_time_str):
    state = load_state()
    if state.get("is_running"):
        return False, "Strategy is already running."
        
    state["index_name"] = index_name
    state["lots"] = int(lots)
    state["rolling_threshold_pct"] = float(rolling_threshold_pct)
    state["max_sl"] = float(max_sl)
    state["max_rolls"] = int(max_rolls)
    state["start_time"] = start_time_str
    state["end_time"] = end_time_str
    state["current_rolls"] = 0
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
    _monitor_thread = threading.Thread(target=_monitor_loop, args=(kite,), name="straddle_monitor_thread", daemon=True)
    _monitor_thread.start()
    
    return True, f"Strategy initialized for {index_name}. Running in background."

def stop_strategy(kite):
    state = load_state()
    if not state.get("is_running"):
        return False, "Strategy is not running."
        
    global _stop_event
    _stop_event.set()
    
    _exit_active_positions(kite, state, "Manual stop request")
    return True, "Strategy stopped. All active straddle positions squared off."

def init_rolling_straddle_on_startup(kite):
    state = load_state()
    if state.get("is_running"):
        global _monitor_thread, _stop_event
        # Check if thread is already active
        for t in threading.enumerate():
            if t.name == "straddle_monitor_thread" and t.is_alive():
                return
                
        logging.info("Auto-restarting Rolling Straddle background monitor thread on startup...")
        _stop_event.clear()
        _monitor_thread = threading.Thread(target=_monitor_loop, args=(kite,), name="straddle_monitor_thread", daemon=True)
        _monitor_thread.start()

def _monitor_loop(kite):
    logging.info("Starting Rolling Straddle background monitor thread...")
    while not _stop_event.is_set():
        state = load_state()
        if not state.get("is_running"):
            break
            
        try:
            now = datetime.datetime.now()
            start_h, start_m = map(int, state["start_time"].split(":"))
            end_h, end_m = map(int, state["end_time"].split(":"))
            
            start_time = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
            end_time = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
            
            if now < start_time:
                state["status_message"] = f"Waiting for start time {state['start_time']}..."
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
            logging.error(f"Error in Rolling Straddle monitor loop: {e}", exc_info=True)
            
        _stop_event.wait(5)
        
    logging.info("Rolling Straddle background monitor thread stopped.")

def _check_and_execute_strategy(kite, state, now):
    index_name = state["index_name"]
    spot_symbol = "NSE:NIFTY 50" if index_name == "NIFTY" else "BSE:SENSEX"
    step = 50 if index_name == "NIFTY" else 100
    lot_size = getattr(config, 'LOT_SIZE_NIFTY', 65) if index_name == "NIFTY" else getattr(config, 'LOT_SIZE_SENSEX', 20)
    qty = state["lots"] * lot_size
    state["qty"] = qty
    
    try:
        quote = kite.quote([spot_symbol])
        spot_price = quote[spot_symbol]['last_price']
    except Exception as e:
        logging.error(f"Failed to fetch spot price for {index_name}: {e}")
        return
        
    # Check active positions in portfolio
    portfolio = paper_trader.get_portfolio()
    active_rows = portfolio[(portfolio['Strategy'] == 'Rolling Straddle') & (portfolio['Status'] == 'Active')]
    active_tickers = active_rows['Ticker'].tolist() if not active_rows.empty else []

    # Check for CE leg stop loss (closed in portfolio but set in state)
    if state.get("ce_ticker") and state["ce_ticker"] not in active_tickers:
        closed_row = portfolio[(portfolio['Ticker'] == state["ce_ticker"]) & (portfolio['Status'] == 'Closed')]
        exit_price = closed_row.iloc[0]['Current Price'] if not closed_row.empty else state["ce_entry_price"] * 2.0
        pnl = (state["ce_entry_price"] - exit_price) * qty
        state["realized_pnl"] += pnl
        logging.info(f"CE Leg {state['ce_ticker']} was stop-lossed. Realized P&L: ₹{pnl:.2f}")
        _send_alert(f"❌ *Rolling Straddle: CE SL Hit* on {state['ce_ticker']} @ ₹{exit_price:.2f}. P&L: ₹{pnl:.2f}")
        state["ce_ticker"] = None
        state["ce_entry_price"] = 0.0
        save_state(state)

    # Check for PE leg stop loss
    if state.get("pe_ticker") and state["pe_ticker"] not in active_tickers:
        closed_row = portfolio[(portfolio['Ticker'] == state["pe_ticker"]) & (portfolio['Status'] == 'Closed')]
        exit_price = closed_row.iloc[0]['Current Price'] if not closed_row.empty else state["pe_entry_price"] * 2.0
        pnl = (state["pe_entry_price"] - exit_price) * qty
        state["realized_pnl"] += pnl
        logging.info(f"PE Leg {state['pe_ticker']} was stop-lossed. Realized P&L: ₹{pnl:.2f}")
        _send_alert(f"❌ *Rolling Straddle: PE SL Hit* on {state['pe_ticker']} @ ₹{exit_price:.2f}. P&L: ₹{pnl:.2f}")
        state["pe_ticker"] = None
        state["pe_entry_price"] = 0.0
        save_state(state)

    ce_ticker = state.get("ce_ticker")
    pe_ticker = state.get("pe_ticker")

    # If both legs are missing (initial entry or double stoploss/full roll needed)
    if not ce_ticker and not pe_ticker:
        atm_strike = round(spot_price / step) * step
        logging.info(f"Initializing Straddle entry for {index_name} at Spot: {spot_price}, ATM: {atm_strike}")
        
        ce_info, pe_info = _find_atm_options(kite, index_name, atm_strike)
        if not ce_info or not pe_info:
            state["status_message"] = "Error: ATM options not found."
            save_state(state)
            return
            
        ce_ticker = ce_info["tradingsymbol"]
        pe_ticker = pe_info["tradingsymbol"]
        exch = "NFO" if index_name == "NIFTY" else "BFO"
        
        try:
            ltps = kite.ltp([f"{exch}:{ce_ticker}", f"{exch}:{pe_ticker}"])
            ce_ltp = ltps[f"{exch}:{ce_ticker}"]['last_price']
            pe_ltp = ltps[f"{exch}:{pe_ticker}"]['last_price']
        except Exception as e:
            logging.error(f"Failed to fetch option LTPs for initial entry: {e}")
            return
            
        paper_trader.execute_paper_trade(
            ticker=ce_ticker,
            trade_type="Options Selling (CE)",
            entry_price=ce_ltp,
            sl=ce_ltp * 2.0,
            qty=qty,
            token=int(ce_info["instrument_token"]),
            strategy="Rolling Straddle",
            target=None
        )
        paper_trader.execute_paper_trade(
            ticker=pe_ticker,
            trade_type="Options Selling (PE)",
            entry_price=pe_ltp,
            sl=pe_ltp * 2.0,
            qty=qty,
            token=int(pe_info["instrument_token"]),
            strategy="Rolling Straddle",
            target=None
        )
        
        state["ce_ticker"] = ce_ticker
        state["pe_ticker"] = pe_ticker
        state["ce_entry_price"] = ce_ltp
        state["pe_entry_price"] = pe_ltp
        state["initial_spot"] = spot_price
        state["status_message"] = f"Active Straddle: ATM {atm_strike} (Spot: {spot_price:.2f})"
        save_state(state)
        
        msg = f"🔔 *Intraday Straddle Initial Entry*\nIndex: {index_name}\nSpot: ₹{spot_price:.2f}\nStrike: {atm_strike}\nCE: {ce_ticker} @ ₹{ce_ltp:.2f}\nPE: {pe_ticker} @ ₹{pe_ltp:.2f}"
        _send_alert(msg)
        return

    # If only CE leg is missing (stoplossed) - re-enter at new ATM strike
    elif not ce_ticker:
        atm_strike = round(spot_price / step) * step
        logging.info(f"Re-entering CE leg for {index_name} at Spot: {spot_price}, ATM: {atm_strike}")
        
        ce_info, _ = _find_atm_options(kite, index_name, atm_strike)
        if not ce_info:
            state["status_message"] = "Error: ATM CE option not found."
            save_state(state)
            return
            
        ce_ticker = ce_info["tradingsymbol"]
        exch = "NFO" if index_name == "NIFTY" else "BFO"
        
        try:
            ltps = kite.ltp([f"{exch}:{ce_ticker}"])
            ce_ltp = ltps[f"{exch}:{ce_ticker}"]['last_price']
        except Exception as e:
            logging.error(f"Failed to fetch CE option LTP for re-entry: {e}")
            return
            
        paper_trader.execute_paper_trade(
            ticker=ce_ticker,
            trade_type="Options Selling (CE)",
            entry_price=ce_ltp,
            sl=ce_ltp * 2.0,
            qty=qty,
            token=int(ce_info["instrument_token"]),
            strategy="Rolling Straddle",
            target=None
        )
        
        state["ce_ticker"] = ce_ticker
        state["ce_entry_price"] = ce_ltp
        state["status_message"] = f"Re-entered CE leg: ATM {atm_strike} (Spot: {spot_price:.2f})"
        save_state(state)
        
        msg = f"🔄 *Rolling Straddle CE Leg Re-entry*\nIndex: {index_name}\nSpot: ₹{spot_price:.2f}\nStrike: {atm_strike}\nCE: {ce_ticker} @ ₹{ce_ltp:.2f}"
        _send_alert(msg)
        return

    # If only PE leg is missing (stoplossed) - re-enter at new ATM strike
    elif not pe_ticker:
        atm_strike = round(spot_price / step) * step
        logging.info(f"Re-entering PE leg for {index_name} at Spot: {spot_price}, ATM: {atm_strike}")
        
        _, pe_info = _find_atm_options(kite, index_name, atm_strike)
        if not pe_info:
            state["status_message"] = "Error: ATM PE option not found."
            save_state(state)
            return
            
        pe_ticker = pe_info["tradingsymbol"]
        exch = "NFO" if index_name == "NIFTY" else "BFO"
        
        try:
            ltps = kite.ltp([f"{exch}:{pe_ticker}"])
            pe_ltp = ltps[f"{exch}:{pe_ticker}"]['last_price']
        except Exception as e:
            logging.error(f"Failed to fetch PE option LTP for re-entry: {e}")
            return
            
        paper_trader.execute_paper_trade(
            ticker=pe_ticker,
            trade_type="Options Selling (PE)",
            entry_price=pe_ltp,
            sl=pe_ltp * 2.0,
            qty=qty,
            token=int(pe_info["instrument_token"]),
            strategy="Rolling Straddle",
            target=None
        )
        
        state["pe_ticker"] = pe_ticker
        state["pe_entry_price"] = pe_ltp
        state["status_message"] = f"Re-entered PE leg: ATM {atm_strike} (Spot: {spot_price:.2f})"
        save_state(state)
        
        msg = f"🔄 *Rolling Straddle PE Leg Re-entry*\nIndex: {index_name}\nSpot: ₹{spot_price:.2f}\nStrike: {atm_strike}\nPE: {pe_ticker} @ ₹{pe_ltp:.2f}"
        _send_alert(msg)
        return

    # If both legs are active, monitor and handle exits or roll trigger
    exch = "NFO" if index_name == "NIFTY" else "BFO"
    try:
        opt_quotes = kite.ltp([f"{exch}:{ce_ticker}", f"{exch}:{pe_ticker}"])
        ce_ltp = opt_quotes[f"{exch}:{ce_ticker}"]['last_price']
        pe_ltp = opt_quotes[f"{exch}:{pe_ticker}"]['last_price']
    except Exception as e:
        logging.error(f"Failed to fetch live option LTPs for monitoring: {e}")
        return
        
    unrealized_pnl = (state["ce_entry_price"] - ce_ltp) * qty + (state["pe_entry_price"] - pe_ltp) * qty
    total_pnl = state["realized_pnl"] + unrealized_pnl
    
    if total_pnl <= -state["max_sl"]:
        logging.warning(f"Daily Max SL Hit! Total PnL: ₹{total_pnl:.2f} <= -₹{state['max_sl']:.2f}")
        _exit_active_positions(kite, state, f"Daily Max SL Breach (PnL: ₹{total_pnl:.2f})")
        return
        
    initial_spot = state["initial_spot"]
    move_pct = abs(spot_price - initial_spot) / initial_spot * 100
    
    if move_pct >= state["rolling_threshold_pct"]:
        current_rolls = state["current_rolls"]
        if current_rolls < state["max_rolls"]:
            logging.info(f"Rolling triggered! Spot moved {move_pct:.2f}% (Limit: {state['rolling_threshold_pct']}%)")
            _execute_roll(kite, state, spot_price, ce_ltp, pe_ltp)
        else:
            state["status_message"] = f"Max Rolls Hit ({current_rolls}) - Holding ATM {initial_spot:.0f} (Spot: {spot_price:.2f}, MTM: ₹{total_pnl:.2f})"
            save_state(state)
    else:
        state["status_message"] = f"Active Straddle | Spot: {spot_price:.2f} (Moved {move_pct:.2f}%) | Rolls: {state['current_rolls']}/{state['max_rolls']} | MTM: ₹{total_pnl:.2f}"
        save_state(state)

def _execute_roll(kite, state, current_spot, ce_exit_price, pe_exit_price):
    index_name = state["index_name"]
    step = 50 if index_name == "NIFTY" else 100
    qty = state["qty"]
    
    paper_trader.exit_trade(state["ce_ticker"], kite, override_price=ce_exit_price)
    paper_trader.exit_trade(state["pe_ticker"], kite, override_price=pe_exit_price)
    
    pnl_ce = (state["ce_entry_price"] - ce_exit_price) * qty
    pnl_pe = (state["pe_entry_price"] - pe_exit_price) * qty
    cycle_pnl = pnl_ce + pnl_pe
    state["realized_pnl"] += cycle_pnl
    
    atm_strike = round(current_spot / step) * step
    ce_info, pe_info = _find_atm_options(kite, index_name, atm_strike)
    
    if not ce_info or not pe_info:
        state["ce_ticker"] = None
        state["pe_ticker"] = None
        state["status_message"] = "Roll failed: New ATM options not found."
        save_state(state)
        logging.error("Failed to find new ATM option instruments during roll.")
        return
        
    ce_ticker = ce_info["tradingsymbol"]
    pe_ticker = pe_info["tradingsymbol"]
    exch = "NFO" if index_name == "NIFTY" else "BFO"
    
    try:
        ltps = kite.ltp([f"{exch}:{ce_ticker}", f"{exch}:{pe_ticker}"])
        ce_ltp = ltps[f"{exch}:{ce_ticker}"]['last_price']
        pe_ltp = ltps[f"{exch}:{pe_ticker}"]['last_price']
    except Exception as e:
        logging.error(f"Failed to fetch option LTPs for new rolled straddle: {e}")
        state["ce_ticker"] = None
        state["pe_ticker"] = None
        state["status_message"] = "Roll failed: Failed to fetch new LTPs."
        save_state(state)
        return
        
    paper_trader.execute_paper_trade(
        ticker=ce_ticker,
        trade_type="Options Selling (CE)",
        entry_price=ce_ltp,
        sl=ce_ltp * 2.0,
        qty=qty,
        token=int(ce_info["instrument_token"]),
        strategy="Rolling Straddle",
        target=None
    )
    paper_trader.execute_paper_trade(
        ticker=pe_ticker,
        trade_type="Options Selling (PE)",
        entry_price=pe_ltp,
        sl=pe_ltp * 2.0,
        qty=qty,
        token=int(pe_info["instrument_token"]),
        strategy="Rolling Straddle",
        target=None
    )
    
    state["current_rolls"] += 1
    state["ce_ticker"] = ce_ticker
    state["pe_ticker"] = pe_ticker
    state["ce_entry_price"] = ce_ltp
    state["pe_entry_price"] = pe_ltp
    state["initial_spot"] = current_spot
    state["status_message"] = f"Rolled to ATM {atm_strike} (Spot: {current_spot:.2f})"
    save_state(state)
    
    msg = f"🔄 *Straddle Position Rolled*\nIndex: {index_name}\nSpot: ₹{current_spot:.2f}\nStrike: {atm_strike}\nCE: {ce_ticker} @ ₹{ce_ltp:.2f}\nPE: {pe_ticker} @ ₹{pe_ltp:.2f}\nCycle P&L: ₹{cycle_pnl:.2f}\nTotal Realized P&L: ₹{state['realized_pnl']:.2f}"
    _send_alert(msg)

def _exit_active_positions(kite, state, reason):
    index_name = state["index_name"]
    qty = state["qty"]
    
    ce_ticker = state.get("ce_ticker")
    pe_ticker = state.get("pe_ticker")
    exch = "NFO" if index_name == "NIFTY" else "BFO"
    
    ce_exit = state.get("ce_entry_price", 0.0)
    pe_exit = state.get("pe_entry_price", 0.0)
    
    if ce_ticker and pe_ticker:
        try:
            opt_quotes = kite.ltp([f"{exch}:{ce_ticker}", f"{exch}:{pe_ticker}"])
            ce_exit = opt_quotes[f"{exch}:{ce_ticker}"]['last_price']
            pe_exit = opt_quotes[f"{exch}:{pe_ticker}"]['last_price']
        except Exception as e:
            logging.error(f"Failed to fetch option LTPs for square-off: {e}")
            
        paper_trader.exit_trade(ce_ticker, kite, override_price=ce_exit)
        paper_trader.exit_trade(pe_ticker, kite, override_price=pe_exit)
        
        pnl_ce = (state["ce_entry_price"] - ce_exit) * qty
        pnl_pe = (state["pe_entry_price"] - pe_exit) * qty
        cycle_pnl = pnl_ce + pnl_pe
        state["realized_pnl"] += cycle_pnl
        
    state["ce_ticker"] = None
    state["pe_ticker"] = None
    state["is_running"] = False
    state["status_message"] = f"Stopped: {reason} | Final MTM: ₹{state['realized_pnl']:.2f}"
    save_state(state)
    
    msg = f"🛑 *Straddle Strategy Stopped*\nIndex: {index_name}\nReason: {reason}\nFinal Day MTM: *₹{state['realized_pnl']:.2f}*"
    _send_alert(msg)

def _find_atm_options(kite, index_name, strike):
    active_chain, step = get_option_chain(kite, index_name)
    if active_chain is None or active_chain.empty:
        logging.error(f"No option chain returned for {index_name}")
        return None, None
        
    chain_at_strike = active_chain[active_chain['strike'] == float(strike)]
    if chain_at_strike.empty:
        available_strikes = active_chain['strike'].unique()
        if len(available_strikes) > 0:
            closest_strike = min(available_strikes, key=lambda x: abs(x - strike))
            chain_at_strike = active_chain[active_chain['strike'] == closest_strike]
            logging.info(f"Exact ATM strike {strike} not found. Using closest strike: {closest_strike}")
            
    if chain_at_strike.empty:
        return None, None
        
    ce_row = chain_at_strike[chain_at_strike['instrument_type'] == 'CE']
    pe_row = chain_at_strike[chain_at_strike['instrument_type'] == 'PE']
    
    ce_info = ce_row.iloc[0].to_dict() if not ce_row.empty else None
    pe_info = pe_row.iloc[0].to_dict() if not pe_row.empty else None
    
    return ce_info, pe_info

def _send_alert(msg):
    bot_token = getattr(config, 'TELEGRAM_BOT_TOKEN', '')
    chat_id = getattr(config, 'TELEGRAM_PERSONAL_CHAT_ID', '') or getattr(config, 'TELEGRAM_CHAT_ID_INTRADAY', getattr(config, 'TELEGRAM_CHAT_ID', ''))
    if bot_token and chat_id:
        telegram_agent.send_message(msg, bot_token, chat_id, parse_mode="Markdown")
        
    try:
        import option_desk_manager
        option_desk_manager.add_notification_to_shared("Straddle", msg.replace("*", "").replace("`", ""))
    except Exception as e:
        logging.error(f"Failed to save shared notification for straddle: {e}")
