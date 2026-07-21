import pandas as pd
import os
import json
import logging
import threading
from datetime import datetime, timedelta
# Suppress pandas FutureWarnings about DataFrame concatenation and other deprecations
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)
import config

_trader_lock = threading.RLock()

def _safe_to_csv(df, filepath, max_retries=10, delay=0.1):
    """Writes a DataFrame to CSV with retry logic to handle file locking in Windows."""
    import time
    for attempt in range(max_retries):
        try:
            dirname = os.path.dirname(filepath)
            if dirname:
                os.makedirs(dirname, exist_ok=True)
            df.to_csv(filepath, index=False)
            return True
        except PermissionError:
            time.sleep(delay)
    df.to_csv(filepath, index=False)
    return True

def _safe_to_csv_append(df, filepath, max_retries=10, delay=0.1):
    """Appends a DataFrame to CSV with retry logic to handle file locking in Windows."""
    import time
    for attempt in range(max_retries):
        try:
            dirname = os.path.dirname(filepath)
            if dirname:
                os.makedirs(dirname, exist_ok=True)
            df.to_csv(filepath, mode='a', header=False, index=False)
            return True
        except PermissionError:
            time.sleep(delay)
    df.to_csv(filepath, mode='a', header=False, index=False)
    return True

PORTFOLIO_FILE = os.path.join("data", "trades", "paper_portfolio.csv")
HISTORY_FILE = os.path.join("data", "trades", "paper_trade_history.csv")
SWING_FILE = os.path.join("data", "trades", "swing_trades.csv")
SWING_ARCHIVE_FILE = os.path.join("data", "trades", "swing_trades_archived.csv")
ARCHIVE_FILE = os.path.join("data", "trades", "paper_trade_archive.csv")
OPTIONS_HISTORY_FILE = os.path.join("data", "trades", "options_trade_history.csv")
OPTIONS_ARCHIVE_FILE = os.path.join("data", "trades", "options_trade_archive.csv")
_INSTRUMENT_CACHE_FILE = os.path.join("data", "cache", "_instrument_token_cache.json")

def get_exchange_prefix(ticker):
    """Determine the exchange (NSE/NFO/BFO) based on ticker name."""
    ticker_str = str(ticker)
    if any(ticker_str.endswith(x) for x in ["CE", "PE"]):
        if ticker_str.startswith("SENSEX"):
            return "BFO"
        elif ticker_str.startswith("NIFTY") or ticker_str.startswith("BANKNIFTY") or ticker_str.startswith("FINNIFTY"):
            return "NFO"
    return "NSE"

# ---------------------------------------------------------------------------
# LIGHTWEIGHT INSTRUMENT TOKEN CACHE  (avoids repeated kite.instruments calls)
# ---------------------------------------------------------------------------
_instrument_cache = {}   # in-memory cache for this process run

def _load_instrument_cache():
    """Load today's token cache from disk into memory."""
    global _instrument_cache
    if _instrument_cache:
        return  # already loaded this session
    if not os.path.exists(_INSTRUMENT_CACHE_FILE):
        return
    try:
        with open(_INSTRUMENT_CACHE_FILE) as f:
            data = json.load(f)
        # Invalidate if cache is from a previous day
        if data.get("date") == datetime.today().strftime("%Y-%m-%d"):
            _instrument_cache = data.get("tokens", {})
            logging.info(f"Loaded {len(_instrument_cache)} cached instrument tokens.")
    except Exception as e:
        logging.warning(f"Could not load instrument cache: {e}")

def _save_instrument_cache():
    """Persist the in-memory token cache to disk."""
    try:
        data = {"date": datetime.today().strftime("%Y-%m-%d"), "tokens": _instrument_cache}
        with open(_INSTRUMENT_CACHE_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logging.warning(f"Could not save instrument cache: {e}")

def _get_tokens_for(kite, symbols: list) -> dict:
    """
    Return {symbol: token} for the given symbols.
    Uses a daily file cache so kite.instruments() is called at most once per day.
    """
    global _instrument_cache
    _load_instrument_cache()

    missing = [s for s in symbols if s not in _instrument_cache]
    if missing:
        try:
            instruments = kite.instruments("NSE")
            df_inst = pd.DataFrame(instruments)
            new_map = dict(zip(df_inst['tradingsymbol'], df_inst['instrument_token']))
            _instrument_cache.update(new_map)
            _save_instrument_cache()
        except Exception as e:
            logging.warning(f"Failed to fetch instruments: {e}")

    return {s: _instrument_cache[s] for s in symbols if s in _instrument_cache}

def get_portfolio():
    with _trader_lock:
        if not os.path.exists(PORTFOLIO_FILE):
            return pd.DataFrame(columns=["Ticker", "Type", "EntryPrice", "SL", "InitialSL", "Qty", "EntryTime", "Status", "Strategy", "Delta"])
        try:
            df = pd.read_csv(PORTFOLIO_FILE)
            # Migrate old 'OPEN' status to 'Active'
            if not df.empty and 'Status' in df.columns:
                if (df['Status'] == 'OPEN').any():
                    df.loc[df['Status'] == 'OPEN', 'Status'] = 'Active'
                    _safe_to_csv(df, PORTFOLIO_FILE) # Save migration
            
            # Migrate/Ensure Strategy column exists
            if not df.empty and 'Strategy' not in df.columns:
                df['Strategy'] = "15-Min ORB"
                _safe_to_csv(df, PORTFOLIO_FILE)
                
            # Migrate/Ensure InitialSL column exists
            if not df.empty and 'InitialSL' not in df.columns:
                df['InitialSL'] = df['SL']
                _safe_to_csv(df, PORTFOLIO_FILE)
                
            # Migrate/Ensure Delta column exists
            if not df.empty and 'Delta' not in df.columns:
                df['Delta'] = None
                _safe_to_csv(df, PORTFOLIO_FILE)
                
            # Clean up any existing duplicates (same Ticker and Status)
            if not df.empty:
                df = df.drop_duplicates(subset=['Ticker', 'Status'], keep='first')
                
            return df
        except:
            return pd.DataFrame(columns=["Ticker", "Type", "EntryPrice", "SL", "InitialSL", "Qty", "EntryTime", "Status", "Strategy", "Delta"])

def execute_paper_trade(ticker, trade_type, entry_price, sl, qty, token=None, strategy="15-Min ORB", target=None, delta=None):
    with _trader_lock:
        # Stop taking fresh intraday equity trades post 2:00 PM (14:00)
        is_option = str(strategy).lower() in ['option desk', 'rolling straddle'] or \
                    (any(str(ticker).endswith(x) for x in ["CE", "PE"]) and any(c.isdigit() for c in str(ticker)))
        if not is_option:
            now = datetime.now()
            if now.hour >= 14:
                logging.info(f"🚫 Blocked fresh intraday equity trade for {ticker} post 2:00 PM ({now.strftime('%H:%M')})")
                return False

        df = get_portfolio()
        
        # Check if already active in current session (avoid duplicate entries on same day)
        # We only allow one active trade per ticker at a time
        if not df.empty and ticker in df[df['Status'] == 'Active']['Ticker'].values:
            return False
            
        new_trade = {
            "Ticker": ticker,
            "Type": trade_type,
            "EntryPrice": entry_price,
            "SL": sl,
            "InitialSL": sl,
            "Target": target,
            "Qty": qty,
            "Token": token,
            "EntryTime": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "Status": "Active",
            "Strategy": strategy,
            "Delta": delta
        }
        
        if df.empty:
            df = pd.DataFrame([new_trade])
        else:
            df = pd.concat([df, pd.DataFrame([new_trade])], ignore_index=True)
        _safe_to_csv(df, PORTFOLIO_FILE)
        logging.info(f"🚀 Paper Trade Executed: {trade_type} ({strategy}) {ticker} @ {entry_price} (SL: {sl}, Target: {target}, Qty: {qty})")
        return True

def exit_trade(ticker, kite, override_price=None, entry_time=None):
    """Exit a trade, calculate final P&L, and move to history."""
    with _trader_lock:
        df = get_portfolio()
        if df.empty:
            return False
        
        # Verify the trade is still active (prevents duplicate exits due to race conditions)
        if entry_time is not None:
            trade_row = df[(df['Ticker'] == ticker) & (df['Status'] == 'Active') & (df['EntryTime'] == entry_time)]
        else:
            trade_row = df[(df['Ticker'] == ticker) & (df['Status'] == 'Active')]
            
        if trade_row.empty:
            logging.warning(f"⚠️ exit_trade: Ticker {ticker} is not Active or already closed. Skipping exit.")
            return False
            
        try:
            # Fetch Exit Price
            if override_price is not None:
                exit_price = override_price
            else:
                exch = get_exchange_prefix(ticker)
                quote = kite.ltp([f"{exch}:{ticker}"])
                exit_price = quote.get(f"{exch}:{ticker}", {}).get('last_price')
            
            if exit_price is None:
                logging.error(f"Could not fetch exit price for {ticker}")
                return False
                
            matched_idx = trade_row.index[0]
            trade = df.loc[matched_idx].to_dict()
            sl = trade.get('SL', 0)
            
            # Adjust Exit Price if SL was hit (use SL price as benchmark)
            actual_exit_price = exit_price
            if "Bullish" in str(trade['Type']) and sl > 0 and exit_price <= sl:
                actual_exit_price = sl
            elif ("Bearish" in str(trade['Type']) or "Failed Breakout" in str(trade['Type'])) and sl > 0 and exit_price >= sl:
                actual_exit_price = sl
                
            trade['ExitPrice'] = actual_exit_price
            trade['ExitTime'] = datetime.now().strftime("%Y-%m-%d %H:%M")
            trade['Capital Deployed'] = trade['EntryPrice'] * trade['Qty']
            
            if "Bullish" in str(trade['Type']):
                trade['Final P&L'] = (actual_exit_price - trade['EntryPrice']) * trade['Qty']
            else:
                trade['Final P&L'] = (trade['EntryPrice'] - actual_exit_price) * trade['Qty']
                
            trade['P&L %'] = (trade['Final P&L'] / trade['Capital Deployed']) * 100
            trade['Status'] = 'CLOSED'
            
            # Save to History (with duplicate checking to prevent multiple entries)
            history_df = pd.DataFrame([trade])
            is_option = str(trade.get('Strategy', '')).lower() in ['option desk', 'rolling straddle'] or (any(str(trade.get('Ticker', '')).endswith(x) for x in ["CE", "PE"]) and any(c.isdigit() for c in str(trade.get('Ticker', ''))))
            dest_file = OPTIONS_HISTORY_FILE if is_option else HISTORY_FILE
            
            if os.path.exists(dest_file):
                try:
                    existing_history = pd.read_csv(dest_file)
                    
                    # Prevent writing duplicate records for the same ticker and entry time
                    is_duplicate = False
                    if not existing_history.empty:
                        matching = existing_history[
                            (existing_history['Ticker'] == trade['Ticker']) & 
                            (existing_history['EntryTime'] == trade['EntryTime'])
                        ]
                        if not matching.empty:
                            is_duplicate = True
                            
                    if not is_duplicate:
                        if existing_history.empty:
                            combined_history = history_df
                        else:
                            combined_history = pd.concat([existing_history, history_df], ignore_index=True, sort=False)
                        _safe_to_csv(combined_history, dest_file)
                    else:
                        logging.warning(f"⚠️ exit_trade: Duplicate entry for {trade['Ticker']} (Entry: {trade['EntryTime']}) already exists in history. Skipping history append.")
                except Exception as read_err:
                    logging.warning(f"Error appending history: {read_err}. Overwriting.")
                    _safe_to_csv(history_df, dest_file)
            else:
                _safe_to_csv(history_df, dest_file)
                
            # Update status and exit price in portfolio file instead of removing
            df.loc[matched_idx, 'Current Price'] = actual_exit_price
            df.loc[matched_idx, 'Status'] = 'Closed'
            _safe_to_csv(df, PORTFOLIO_FILE)
            
            # Auto-clear strategy state variables on manual close
            strategy_name = trade.get('Strategy')
            if strategy_name == "Option Desk":
                try:
                    import option_desk_manager
                    state_od = option_desk_manager.load_state()
                    if state_od.get("ce_ticker") == ticker:
                        state_od["ce_ticker"] = None
                        state_od["ce_entry_price"] = 0.0
                        option_desk_manager.save_state(state_od)
                    elif state_od.get("pe_ticker") == ticker:
                        state_od["pe_ticker"] = None
                        state_od["pe_entry_price"] = 0.0
                        option_desk_manager.save_state(state_od)
                except Exception as ex:
                    logging.error(f"Failed to update option desk state on exit_trade: {ex}")
            elif strategy_name == "Rolling Straddle":
                try:
                    import rolling_straddle_manager
                    state_rs = rolling_straddle_manager.load_state()
                    if state_rs.get("ce_ticker") == ticker:
                        state_rs["ce_ticker"] = None
                        state_rs["ce_entry_price"] = 0.0
                        rolling_straddle_manager.save_state(state_rs)
                    elif state_rs.get("pe_ticker") == ticker:
                        state_rs["pe_ticker"] = None
                        state_rs["pe_entry_price"] = 0.0
                        rolling_straddle_manager.save_state(state_rs)
                except Exception as ex:
                    logging.error(f"Failed to update straddle state on exit_trade: {ex}")

            logging.info(f"🚪 Paper Trade Closed & Archived: {ticker} @ {exit_price}")
            return True
        except Exception as e:
            logging.error(f"Error exiting trade {ticker}: {e}")
            return False

def get_history():
    """Fetch archived trades history."""
    if not os.path.exists(HISTORY_FILE):
        return pd.DataFrame()
    try:
        return pd.read_csv(HISTORY_FILE)
    except:
        return pd.DataFrame()

def get_options_history():
    """Fetch archived options trades history."""
    if not os.path.exists(OPTIONS_HISTORY_FILE):
        return pd.DataFrame()
    try:
        return pd.read_csv(OPTIONS_HISTORY_FILE)
    except:
        return pd.DataFrame()

def archive_history():
    """Move daily history to permanent archive and clear daily file."""
    for hist_file, arch_file in [(HISTORY_FILE, ARCHIVE_FILE), (OPTIONS_HISTORY_FILE, OPTIONS_ARCHIVE_FILE)]:
        if not os.path.exists(hist_file):
            continue
        
        try:
            df = pd.read_csv(hist_file)
            if df.empty:
                continue
    
            # Append to archive (aligning columns properly)
            if os.path.exists(arch_file):
                try:
                    arch_df = pd.read_csv(arch_file)
                    combined = pd.concat([arch_df, df], ignore_index=True, sort=False)
                    _safe_to_csv(combined, arch_file)
                except Exception as e:
                    logging.error(f"Error merging with archive {arch_file}, falling back to append: {e}")
                    _safe_to_csv_append(df, arch_file)
            else:
                _safe_to_csv(df, arch_file)
                
            # Delete daily history file
            os.remove(hist_file)
            logging.info(f"📁 Archived {len(df)} trades to {arch_file}")
            
        except Exception as e:
            logging.error(f"Error archiving history file {hist_file}: {e}")
            
    # Also clean up the portfolio file (remove Closed trades from previous days)
    if os.path.exists(PORTFOLIO_FILE):
        try:
            pdf = pd.read_csv(PORTFOLIO_FILE)
            pdf = pdf[pdf['Status'] == 'Active'] # Only keep active trades for the new day
            p_safe_to_csv(df, PORTFOLIO_FILE)
        except Exception as e:
            logging.error(f"Error cleaning up portfolio file: {e}")

def clear_portfolio():
    """Clear all paper trades."""
    if os.path.exists(PORTFOLIO_FILE):
        os.remove(PORTFOLIO_FILE)
    logging.info("🧹 Paper Portfolio Cleared")
    return True

def clear_portfolio_by_strategy(strategy: str):
    """Clear paper trades for a specific strategy."""
    df = get_portfolio()
    if not df.empty and 'Strategy' in df.columns:
        filtered_df = df[df['Strategy'] != strategy]
        _safe_to_csv(filtered_df, PORTFOLIO_FILE)
        logging.info(f"🧹 Paper Portfolio Cleared for strategy: {strategy}")
    return True

def export_history_to_excel(excel_path="paper_trade_history.xlsx"):
    """
    Exports all paper trading tables (Active Portfolio, Today's History, and Permanent Archive)
    into a single Excel workbook with multiple sheets.
    """
    try:
        import pandas as pd
        with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
            # Sheet 1: Active Portfolio
            portfolio_df = get_portfolio()
            if not portfolio_df.empty:
                portfolio_df.to_excel(writer, sheet_name='Active Portfolio', index=False)
            else:
                pd.DataFrame(columns=["Status", "Message"]).to_excel(writer, sheet_name='Active Portfolio', index=False)
            
            # Sheet 2: Realized Today (History)
            history_df = get_history()
            if not history_df.empty:
                history_df.to_excel(writer, sheet_name='Realized Today', index=False)
            else:
                pd.DataFrame(columns=["Status", "Message"]).to_excel(writer, sheet_name='Realized Today', index=False)
                
            # Sheet 3: Options Realized Today
            opt_history_df = get_options_history()
            if not opt_history_df.empty:
                opt_history_df.to_excel(writer, sheet_name='Options Realized Today', index=False)
            else:
                pd.DataFrame(columns=["Status", "Message"]).to_excel(writer, sheet_name='Options Realized Today', index=False)
                
            # Sheet 4: Permanent Archive
            if os.path.exists(ARCHIVE_FILE):
                try:
                    archive_df = pd.read_csv(ARCHIVE_FILE)
                    if not archive_df.empty:
                        archive_df.to_excel(writer, sheet_name='Permanent Archive', index=False)
                except Exception as arc_err:
                    logging.warning(f"Could not read permanent archive for excel export: {arc_err}")
                    
            # Sheet 5: Options Permanent Archive
            if os.path.exists(OPTIONS_ARCHIVE_FILE):
                try:
                    opt_archive_df = pd.read_csv(OPTIONS_ARCHIVE_FILE)
                    if not opt_archive_df.empty:
                        opt_archive_df.to_excel(writer, sheet_name='Options Permanent Archive', index=False)
                except Exception as arc_err:
                    logging.warning(f"Could not read options permanent archive for excel export: {arc_err}")
                    
        if isinstance(excel_path, str):
            logging.info(f"📊 Exported paper trade tables to {excel_path}")
        else:
            logging.debug("📊 Exported paper trade tables to in-memory buffer")
        return True
    except Exception as e:
        logging.error(f"Error exporting to Excel: {e}")
        return False

def exit_all_trades(kite):
    """Exit all active paper trades in the portfolio and export the update to Excel."""
    df = get_portfolio()
    if df.empty:
        return 0
    
    active_trades = df[df['Status'] == 'Active']
    if active_trades.empty:
        return 0
        
    count = 0
    for _, row in active_trades.iterrows():
        ticker = row['Ticker']
        if exit_trade(ticker, kite, entry_time=row['EntryTime']):
            count += 1
            
    # After exiting all, export updated tables to Excel
    export_history_to_excel("paper_trade_history.xlsx")
    return count


def apply_multi_stage_trailing_sl(row, ltp):
    """
    Applies unified 3-stage trailing stop-loss logic for both Bullish and Bearish trades.
    Returns the new SL price if trailing is triggered, else returns the current SL.
    """
    try:
        entry = float(row['EntryPrice'])
        current_sl = float(row['SL'])
        # Use fixed InitialSL for consistent R-Unit risk calculation
        initial_sl = float(row.get('InitialSL', current_sl))
        trade_type = str(row['Type'])
        
        if "Bullish" in trade_type:
            initial_risk = entry - initial_sl
            if initial_risk <= 0: return current_sl
            
            # Stage 3: Price >= +2.0R -> Trail to +1.0R
            if ltp >= entry + (2.0 * initial_risk):
                new_sl = entry + (1.0 * initial_risk)
                if new_sl > current_sl: return new_sl
            # Stage 2: Price >= +1.5R -> Trail to +0.5R
            elif ltp >= entry + (1.5 * initial_risk):
                new_sl = entry + (0.5 * initial_risk)
                if new_sl > current_sl: return new_sl
            # Stage 1: Price >= +1.0R -> Trail to Break-Even (Entry)
            elif ltp >= entry + (1.0 * initial_risk):
                if entry > current_sl: return entry
                
        elif "Bearish" in trade_type or "Failed Breakout" in trade_type:
            initial_risk = initial_sl - entry
            if initial_risk <= 0: return current_sl
            
            # Stage 3: Price <= -2.0R -> Trail to +1.0R (downwards)
            if ltp <= entry - (2.0 * initial_risk):
                new_sl = entry - (1.0 * initial_risk)
                if new_sl < current_sl: return new_sl
            # Stage 2: Price <= -1.5R -> Trail to +0.5R
            elif ltp <= entry - (1.5 * initial_risk):
                new_sl = entry - (0.5 * initial_risk)
                if new_sl < current_sl: return new_sl
            # Stage 1: Price <= -1.0R -> Trail to Break-Even (Entry)
            elif ltp <= entry - (1.0 * initial_risk):
                if entry < current_sl: return entry
    except Exception as e:
        logging.error(f"Error calculating trailing SL: {e}")
        
    return row['SL']

def archive_and_clear_old_option_trades():
    """
    Finds closed option trades from previous days in paper_portfolio.csv,
    ensures they are archived in paper_trade_history.csv and paper_trade_archive.csv,
    and removes them from paper_portfolio.csv.
    """
    with _trader_lock:
        if not os.path.exists(PORTFOLIO_FILE):
            return
            
        try:
            df = pd.read_csv(PORTFOLIO_FILE)
            if df.empty:
                return
                
            today_str = datetime.now().strftime("%Y-%m-%d")
            
            # Identify option trades
            def is_option_trade(row):
                ticker = str(row.get('Ticker', ''))
                strat = str(row.get('Strategy', ''))
                return "Option" in strat or (any(ticker.endswith(x) for x in ["CE", "PE"]) and any(c.isdigit() for c in ticker))
                
            # Filter for closed option trades from previous days
            to_clear_mask = []
            for idx, row in df.iterrows():
                if is_option_trade(row) and str(row.get('Status', '')).lower() == 'closed':
                    # Parse exit date
                    exit_time = str(row.get('ExitTime', ''))
                    # If exit time is empty, fall back to EntryTime
                    time_to_check = exit_time if exit_time else str(row.get('EntryTime', ''))
                    
                    if time_to_check:
                        # Extract YYYY-MM-DD
                        date_str = time_to_check.split(' ')[0]
                        # If it's a previous day, or if it is today and we are past the end of the option trading window (15:15)
                        now_dt = datetime.now()
                        is_past_eod = now_dt.hour > 15 or (now_dt.hour == 15 and now_dt.minute >= 15)
                        if date_str != today_str or is_past_eod:
                            to_clear_mask.append(idx)
                            
            if not to_clear_mask:
                return
                
            # Separate trades to clear
            cleared_df = df.loc[to_clear_mask].copy()
            
            # Archive these trades in options_trade_history.csv (OPTIONS_HISTORY_FILE) and options_trade_archive.csv (OPTIONS_ARCHIVE_FILE)
            for dest_file in [OPTIONS_HISTORY_FILE, OPTIONS_ARCHIVE_FILE]:
                if os.path.exists(dest_file):
                    try:
                        dest_df = pd.read_csv(dest_file)
                    except Exception:
                        dest_df = pd.DataFrame()
                else:
                    dest_df = pd.DataFrame()
                    
                # Append cleared trades that aren't already present (check by Ticker & EntryTime)
                for _, row in cleared_df.iterrows():
                    if not dest_df.empty:
                        dup = dest_df[(dest_df['Ticker'] == row['Ticker']) & (dest_df['EntryTime'] == row['EntryTime'])]
                        if not dup.empty:
                            continue
                    dest_df = pd.concat([dest_df, pd.DataFrame([row.to_dict()])], ignore_index=True, sort=False)
                    
                _safe_to_csv(dest_df, dest_file)
                
            # Remove from portfolio and save
            df = df.drop(to_clear_mask)
            _safe_to_csv(df, PORTFOLIO_FILE)
            logging.info(f"🧹 Archived and cleared {len(to_clear_mask)} old option trades from {PORTFOLIO_FILE}")
            
        except Exception as e:
            logging.error(f"Error archiving and clearing old option trades: {e}")

def update_portfolio_pnl(kite):
    """
    Fetches latest prices for all open trades and calculates P&L.
    Returns a DataFrame with live stats.
    """
    with _trader_lock:
        archive_and_clear_old_option_trades()
        try:
            df = get_portfolio()
            if df.empty:
                return pd.DataFrame()
            
            # Split into Active and Closed
            active_mask = df['Status'] == 'Active'
            closed_mask = df['Status'] == 'Closed'
            
            active_trades = df[active_mask].copy()
            closed_trades = df[closed_mask].copy()
            
            # --- AUTO-FIX MISSING TOKENS (cached — no repeat API calls) ---
            if 'Token' not in active_trades.columns:
                active_trades['Token'] = None
    
            if active_trades['Token'].isna().any():
                try:
                    missing_tickers = active_trades[active_trades['Token'].isna()]['Ticker'].tolist()
                    if missing_tickers:
                        token_map = _get_tokens_for(kite, missing_tickers)
                        for t_sym, t_val in token_map.items():
                            df.loc[(df['Ticker'] == t_sym) & (df['Status'] == 'Active'), 'Token'] = t_val
                            active_trades.loc[active_trades['Ticker'] == t_sym, 'Token'] = t_val
                        _safe_to_csv(df, PORTFOLIO_FILE)
                except Exception as e:
                    logging.warning(f"Intraday token auto-fix failed: {e}")
                    
            # Calculate metrics for active trades
            if not active_trades.empty:
                tickers = active_trades['Ticker'].tolist()
                try:
                    # Fetch LTP for all active tickers
                    quotes = kite.ltp([f"{get_exchange_prefix(t)}:{t}" for t in tickers])
                    
                    def get_ltp(ticker):
                        exch = get_exchange_prefix(ticker)
                        q = quotes.get(f"{exch}:{ticker}")
                        return q['last_price'] if q else None
                        
                    active_trades['Current Price'] = active_trades['Ticker'].apply(get_ltp)
                except Exception as e:
                    logging.error(f"Error fetching LTP: {e}")
    
            # Combine for processing P&L and charges
            processed_df = pd.concat([active_trades, closed_trades], ignore_index=True)
            if processed_df.empty:
                return pd.DataFrame()
    
            def calc_pnl(row):
                if row['Current Price'] is None: return 0
                price_for_pnl = row['Current Price']
                sl = row['SL']
                
                # Use SL price if hit (to lock the P&L)
                if "Bullish" in str(row['Type']) and price_for_pnl <= sl:
                    price_for_pnl = sl
                elif ("Bearish" in str(row['Type']) or "Failed Breakout" in str(row['Type']) or ("Options Selling" in str(row['Type']) and row.get('Strategy') != 'Option Desk')) and price_for_pnl >= sl:
                    price_for_pnl = sl
                    
                if "Bullish" in str(row['Type']):
                    return (price_for_pnl - row['EntryPrice']) * row['Qty']
                else:
                    return (row['EntryPrice'] - price_for_pnl) * row['Qty']
                    
            processed_df['Live P&L'] = processed_df.apply(calc_pnl, axis=1)
            
            # Check for SL hits and Auto-Exit (Only for Active ones)
            processed_df['SL Status'] = "✅ Active"
            for idx, row in processed_df.iterrows():
                if row['Status'] == 'Closed':
                    processed_df.at[idx, 'SL Status'] = "🏁 Closed"
                    continue
                    
                # --- TRAILING SL LOGIC (Multi-Stage R-Based) ---
                if row['Status'] == 'Active' and row['Current Price'] is not None:
                    entry = row['EntryPrice']
                    current_sl = row['SL']
                    ltp = row['Current Price']
                    
                    new_sl = round(float(apply_multi_stage_trailing_sl(row, ltp)), 2)
                    current_sl_val = round(float(current_sl), 2)
                    if new_sl != current_sl_val:
                        df.loc[(df['Ticker'] == row['Ticker']) & (df['Status'] == 'Active'), 'SL'] = new_sl
                        processed_df.at[idx, 'SL'] = new_sl
                        row['SL'] = new_sl # Update for the hit check below
                        _safe_to_csv(df, PORTFOLIO_FILE) # Persist trail
                        logging.info(f"🛡️ Multi-Stage Trail: {row['Ticker']} Stop-Loss moved from ₹{current_sl:.2f} to ₹{new_sl:.2f} (Entry: ₹{entry:.2f}, LTP: ₹{ltp:.2f})")
    
                is_hit = False
                exit_reason = "SL Hit"
                sl = row['SL']
                target = row.get('Target')
                
                # 1. Check Stop Loss
                if "Bullish" in str(row['Type']) and row['Current Price'] is not None and row['Current Price'] <= sl:
                    is_hit = True
                    exit_reason = "SL Hit"
                elif ("Bearish" in str(row['Type']) or "Failed Breakout" in str(row['Type']) or ("Options Selling" in str(row['Type']) and row.get('Strategy') != 'Option Desk')) and row['Current Price'] is not None and row['Current Price'] >= sl:
                    is_hit = True
                    exit_reason = "SL Hit"
                    
                # 2. Check Target (if defined in portfolio records)
                elif target is not None and pd.notna(target):
                    target = float(target)
                    if "Bullish" in str(row['Type']) and row['Current Price'] is not None and row['Current Price'] >= target:
                        is_hit = True
                        exit_reason = "Target Hit"
                    elif ("Bearish" in str(row['Type']) or "Failed Breakout" in str(row['Type'])) and row['Current Price'] is not None and row['Current Price'] <= target:
                        is_hit = True
                        exit_reason = "Target Hit"
                
                if is_hit:
                    if exit_reason == "Target Hit":
                        logging.info(f"🎯 Target Hit for {row['Ticker']}. Auto-exiting at ₹{target:.2f}")
                        exit_trade(row['Ticker'], kite, override_price=target, entry_time=row['EntryTime'])
                        processed_df.at[idx, 'SL Status'] = "🎯 TARGET HIT (EXITED)"
                        processed_df.at[idx, 'Status'] = "Closed"
                        processed_df.at[idx, 'Current Price'] = target
                    else:
                        logging.info(f"🚨 SL Hit for {row['Ticker']}. Auto-exiting at ₹{sl:.2f}")
                        exit_trade(row['Ticker'], kite, override_price=sl, entry_time=row['EntryTime'])
                        processed_df.at[idx, 'SL Status'] = "❌ SL HIT (EXITED)"
                        processed_df.at[idx, 'Status'] = "Closed"
                        processed_df.at[idx, 'Current Price'] = sl
    
    
            # --- ESTIMATED ZERODHA INTRADAY CHARGES ---
            def calc_intraday_charges(row):
                if row['Current Price'] is None: return 0
                buy_val = row['EntryPrice'] * row['Qty']
                sell_val = row['Current Price'] * row['Qty']
                turnover = buy_val + sell_val
                
                # Check if this is an option trade
                is_option = "Option" in str(row.get('Strategy', '')) or any(str(row['Ticker']).endswith(x) for x in ["CE", "PE"])
                
                if is_option:
                    if row.get('Status') == 'Closed':
                        brok = 20.0 + 20.0
                        stt = 0.000625 * buy_val  # STT 0.0625% on sell side premium
                        trans = 0.00053 * turnover # Trans charges ~0.053% of premium turnover
                        gst = 0.18 * (brok + trans) # GST is 18% of brokerage + transaction charges
                        sebi = (turnover / 10000000) * 10
                        stamp = 0.00003 * sell_val # Stamp duty 0.003% on buy/exit premium
                        return brok + stt + trans + gst + sebi + stamp
                    else:
                        brok = 20.0
                        stt = 0.000625 * buy_val  # STT on entry sell premium
                        trans = 0.00053 * buy_val # Trans charges on entry
                        gst = 0.18 * (brok + trans)
                        sebi = (buy_val / 10000000) * 10
                        stamp = 0.0
                        return brok + stt + trans + gst + sebi + stamp
                else:
                    brok = min(20, 0.0003 * buy_val) + min(20, 0.0003 * sell_val)
                    stt = 0.00025 * sell_val
                    trans = 0.0000345 * turnover
                    gst = 0.18 * (brok + trans)
                    sebi = (turnover / 10000000) * 10
                    stamp = 0.00003 * buy_val
                    return brok + stt + trans + gst + sebi + stamp
    
            processed_df['Est. Charges'] = processed_df.apply(calc_intraday_charges, axis=1)
            processed_df['Net P&L'] = processed_df['Live P&L'] - processed_df['Est. Charges']
    
            # --- ESTIMATED OPTION MARGINS (CAPITAL DEPLOYED) ---
            processed_df['Margin Required'] = 0.0
            
            # Filter for active option trades
            active_options = processed_df[(processed_df['Status'] == 'Active') & 
                                          (processed_df['Ticker'].apply(lambda t: any(str(t).endswith(x) for x in ["CE", "PE"])))].copy()
                                          
            # Group active options by underlying index (e.g. SENSEX vs NIFTY)
            underlying_groups = {}
            for idx, row in active_options.iterrows():
                ticker = row['Ticker']
                und = "SENSEX" if ticker.startswith("SENSEX") else ("NIFTY" if ticker.startswith("NIFTY") else "OTHER")
                if und not in underlying_groups:
                    underlying_groups[und] = []
                underlying_groups[und].append((idx, row))
                
            for und, items in underlying_groups.items():
                qty = items[0][1]['Qty']
                exch = "BFO" if und == "SENSEX" else "NFO"
                import config
                lot_size = getattr(config, 'LOT_SIZE_SENSEX', 20) if und == "SENSEX" else getattr(config, 'LOT_SIZE_NIFTY', 65)
                lots = qty / lot_size
                
                # Check if both CE and PE are active in this group (for hedging/netting fallback)
                has_ce = any("CE" in item[1]['Ticker'] for item in items)
                has_pe = any("PE" in item[1]['Ticker'] for item in items)
                both_sides = has_ce and has_pe
                
                success = False
                try:
                    orders = []
                    for _, row in items:
                        orders.append({
                            "exchange": exch,
                            "tradingsymbol": row['Ticker'],
                            "transaction_type": "SELL",
                            "variety": "regular",
                            "product": "NRML",
                            "order_type": "MARKET",
                            "quantity": int(row['Qty'])
                        })
                    # Call basket margins API to get exact portfolio-netted/hedged margin
                    res = kite.basket_order_margins(orders)
                    if res and "final" in res and "total" in res["final"]:
                        total_margin = float(res["final"]["total"])
                        # Distribute netted margin equally among active positions in the group
                        for idx, _ in items:
                            processed_df.at[idx, 'Margin Required'] = total_margin / len(items)
                        success = True
                except Exception as me:
                    logging.error(f"Error fetching basket margin for {und}: {me}")
                    
                if not success:
                    # Fallback: Hedged CE+PE margin is ~1.1x of a single leg margin rather than 2.0x
                    multiplier = 1.1 if both_sides else 1.0
                    total_margin = float(lots * 200000.0 * multiplier)
                    for idx, _ in items:
                        processed_df.at[idx, 'Margin Required'] = total_margin / len(items)
                        
            # Calculate capital for standard non-option active trades
            for idx, row in processed_df.iterrows():
                if row['Status'] == 'Active':
                    is_option = "Option" in str(row.get('Strategy', '')) or any(str(row['Ticker']).endswith(x) for x in ["CE", "PE"])
                    if not is_option:
                        processed_df.at[idx, 'Margin Required'] = float(row['EntryPrice'] * row['Qty'])
            
            # Ensure Strategy is present in processed_df
            if 'Strategy' not in processed_df.columns:
                processed_df['Strategy'] = "15-Min ORB"
                
            # Ensure Delta is present in processed_df
            if 'Delta' not in processed_df.columns:
                processed_df['Delta'] = None
                
            return processed_df[["Ticker", "Type", "Strategy", "EntryPrice", "Current Price", "Qty", "SL", "SL Status", "Status", "Live P&L", "Est. Charges", "Net P&L", "EntryTime", "Token", "Margin Required", "Delta"]]
            
        except Exception as e:
            logging.error(f"Error updating portfolio P&L: {e}")
            return pd.DataFrame()

# --- POSITIONAL SWING TRADING (3:15 PM) ---

def execute_swing_trade(ticker, entry_price, target, sl, qty, token=None):
    """Execute a positional swing trade and save to swing_trades.csv."""
    if os.path.exists(SWING_FILE):
        df = pd.read_csv(SWING_FILE)
    else:
        df = pd.DataFrame(columns=["Ticker", "EntryPrice", "Target", "SL", "Qty", "EntryDate", "Status", "Current Price", "Live P&L", "Return %"])
    
    # Check if already active
    if not df.empty and ticker in df[df['Status'] == 'OPEN']['Ticker'].values:
        return False
        
    new_trade = {
        "Ticker": ticker,
        "EntryPrice": entry_price,
        "Target": target,
        "SL": sl,
        "Qty": qty,
        "Token": token,
        "EntryDate": datetime.now().strftime("%Y-%m-%d"),
        "Status": "OPEN",
        "Current Price": entry_price,
        "Live P&L": 0.0,
        "Return %": 0.0
    }
    
    if df.empty:
        df = pd.DataFrame([new_trade])
    else:
        df = pd.concat([df, pd.DataFrame([new_trade])], ignore_index=True)
    _safe_to_csv(df, SWING_FILE)
    logging.info(f"📈 Positional Swing Trade Executed: {ticker} @ {entry_price} (Target: {target}, SL: {sl})")
    return True

def update_swing_portfolio(kite):
    """
    Updates the positional swing portfolio with live P&L and days since entry.
    """
    if not os.path.exists(SWING_FILE):
        return pd.DataFrame()
        
    df = pd.read_csv(SWING_FILE)
    if df.empty or not (df['Status'] == 'OPEN').any():
        return pd.DataFrame()
        
    open_trades = df[df['Status'] == 'OPEN'].copy()
    
    # --- AUTO-FIX MISSING TOKENS (cached — no repeat API calls) ---
    if 'Token' not in open_trades.columns:
        open_trades['Token'] = None

    if open_trades['Token'].isna().any():
        try:
            missing_tickers = open_trades[open_trades['Token'].isna()]['Ticker'].tolist()
            if missing_tickers:
                token_map = _get_tokens_for(kite, missing_tickers)
                for t_sym, t_val in token_map.items():
                    df.loc[(df['Ticker'] == t_sym) & (df['Status'] == 'OPEN'), 'Token'] = t_val
                    open_trades.loc[open_trades['Ticker'] == t_sym, 'Token'] = t_val
                _safe_to_csv(df, SWING_FILE)
        except Exception as e:
            logging.warning(f"Token auto-fix failed: {e}")

    tickers = open_trades['Ticker'].tolist()
    
    try:
        instrument_tokens = [f"NSE:{t}" for t in tickers]
        ohlc_quotes = kite.ohlc(instrument_tokens)
        
        def get_price_data(ticker):
            q = ohlc_quotes.get(f"NSE:{ticker}")
            if q:
                return q['last_price'], q['ohlc']['close'], q['ohlc'].get('open')
            return None, None, None
            
        price_info = open_trades['Ticker'].apply(get_price_data)
        open_trades['Current Price'] = [p[0] for p in price_info]
        open_trades['Prev Close'] = [p[1] for p in price_info]
        open_trades['Open Price'] = [p[2] for p in price_info]
        
        # Get the actual 'Last Trading Day' from the market data itself
        # This prevents issues when running the dashboard on weekends
        now = datetime.now()
        # If Saturday (5), last trading day was Friday
        # If Sunday (6), last trading day was Friday
        if now.weekday() == 5:
            last_trading_day = now - timedelta(days=1)
        elif now.weekday() == 6:
            last_trading_day = now - timedelta(days=2)
        else:
            last_trading_day = now
            
        last_trading_day_str = last_trading_day.strftime("%Y-%m-%d")
        
        # Calculate Day's P&L:
        # If entry is today: (LTP - EntryPrice) * Qty
        # If entry is older: (LTP - PrevClose) * Qty
        def calc_day_pnl(row):
            if row['Current Price'] is None or row['Prev Close'] is None:
                return 0
            
            entry_date_str = str(row['EntryDate']).split(' ')[0]
            
            # Logic: If the stock was bought AFTER the last 'Prev Close' was established,
            # then 'Today's P&L' starts from the Entry Price.
            # If it was bought ON or BEFORE the last 'Prev Close', then 'Today's P&L' starts from Prev Close.
            
            # Since 'Prev Close' is the close of the PREVIOUS trading session,
            # any trade with EntryDate == CurrentSessionDate should use EntryPrice.
            
            # To handle weekends: If today is Saturday/Sunday, 'last_trading_day' is Friday.
            # If EntryDate was Friday, it's still 'Today's P&L' for that session.
            
            if entry_date_str == last_trading_day_str:
                return (row['Current Price'] - row['EntryPrice']) * row['Qty']
            else:
                # Carry forward - Day P&L is always from the most recent closing price
                return (row['Current Price'] - row['Prev Close']) * row['Qty']
            
        open_trades['Day P&L'] = open_trades.apply(calc_day_pnl, axis=1)
        
        # Calculate Days Since Entry
        def calc_days(entry_date_str):
            try:
                entry_date = datetime.strptime(entry_date_str, "%Y-%m-%d")
                return (datetime.now() - entry_date).days
            except:
                return 0
        
        open_trades['Days Held'] = open_trades['EntryDate'].apply(calc_days)
        
        # Calculate P&L (Current Price - Buying Price)
        def calc_swing_pnl(row):
            if row['Current Price'] is None:
                return 0
            return (row['Current Price'] - row['EntryPrice']) * row['Qty']

        open_trades['Live P&L'] = open_trades.apply(calc_swing_pnl, axis=1)
        open_trades['Return %'] = (open_trades['Live P&L'] / (open_trades['EntryPrice'] * open_trades['Qty'])) * 100
        
        # Check for auto-exit (SL or Target hit)
        for idx, row in open_trades.iterrows():
            if row['Current Price'] is None: continue
            
            exit_triggered = False
            exit_price = row['Current Price']
            new_status = row['Status']
            open_price = row.get('Open Price')
            
            if row['Current Price'] >= row['Target']:
                new_status = "TARGET HIT"
                if open_price is not None and open_price >= row['Target']:
                    exit_price = open_price
                else:
                    exit_price = row['Target']
                exit_triggered = True
            elif row['Current Price'] <= row['SL']:
                new_status = "SL HIT"
                if open_price is not None and open_price <= row['SL']:
                    exit_price = open_price
                else:
                    exit_price = row['SL']
                exit_triggered = True
                
            if exit_triggered:
                # Update original df to lock the price and change status
                df.loc[df['Ticker'] == row['Ticker'], 'Status'] = new_status
                df.loc[df['Ticker'] == row['Ticker'], 'Current Price'] = exit_price
                
                # Lock the P&L at the exit level
                locked_pnl = (exit_price - row['EntryPrice']) * row['Qty']
                df.loc[df['Ticker'] == row['Ticker'], 'Live P&L'] = locked_pnl
                df.loc[df['Ticker'] == row['Ticker'], 'Return %'] = (locked_pnl / (row['EntryPrice'] * row['Qty'])) * 100
                
                # Record the exit date
                exit_date = datetime.now().strftime("%Y-%m-%d")
                if 'ExitDate' not in df.columns:
                    df['ExitDate'] = None
                df['ExitDate'] = df['ExitDate'].astype(object)
                df.loc[df['Ticker'] == row['Ticker'], 'ExitDate'] = exit_date
                
                # Reflect in open_trades for the current return
                open_trades.loc[idx, 'Status'] = new_status
                open_trades.loc[idx, 'Current Price'] = exit_price
                open_trades.loc[idx, 'Live P&L'] = locked_pnl
                open_trades.loc[idx, 'Return %'] = (locked_pnl / (row['EntryPrice'] * row['Qty'])) * 100
                if 'ExitDate' not in open_trades.columns:
                    open_trades['ExitDate'] = None
                open_trades['ExitDate'] = open_trades['ExitDate'].astype(object)
                open_trades.loc[idx, 'ExitDate'] = exit_date
                logging.info(f"🔔 Swing Auto-Exit: {row['Ticker']} at {exit_price} ({new_status})")
            else:
                # --- TRAILING SL LOGIC ---
                # Level 1: Gain > 3% -> SL to Breakeven
                # Level 2: Gain > 6% -> SL to Entry + 2%
                # Level 3: Gain > 10% -> SL to Entry + 5%
                
                ret_pct = row['Return %']
                entry = row['EntryPrice']
                current_sl = row['SL']
                new_sl = current_sl
                
                if ret_pct >= 10.0:
                    new_sl = round(entry * 1.05, 2)
                elif ret_pct >= 6.0:
                    new_sl = round(entry * 1.02, 2)
                elif ret_pct >= 3.0:
                    new_sl = round(entry, 2)
                
                if new_sl > current_sl:
                    df.loc[df['Ticker'] == row['Ticker'], 'SL'] = new_sl
                    open_trades.loc[idx, 'SL'] = new_sl
                    logging.info(f"🛡️ Trailing SL Updated for {row['Ticker']}: {current_sl} -> {new_sl} ({ret_pct:.1f}% gain)")

        # Update df with all calculated columns from open_trades
        if 'ExitDate' not in df.columns:
            df['ExitDate'] = None
        df['ExitDate'] = df['ExitDate'].astype(object)
        
        for col in ['Current Price', 'Live P&L', 'Return %', 'Day P&L', 'Days Held', 'ExitDate']:
            if col in open_trades.columns:
                for idx, row in open_trades.iterrows():
                    df.loc[df['Ticker'] == row['Ticker'], col] = row[col]
        
        # Calculate charges for the returned dataframe
        def calc_final_charges(row):
            if pd.isna(row.get('Current Price')): return 0
            buy_val = row['EntryPrice'] * row['Qty']
            sell_val = row['Current Price'] * row['Qty']
            turnover = buy_val + sell_val
            stt = 0.001 * turnover
            trans = 0.0000345 * turnover
            gst = 0.18 * trans
            sebi = (turnover / 10000000) * 10
            stamp = 0.00015 * buy_val
            return stt + trans + gst + sebi + stamp

        df['Est. Charges'] = df.apply(calc_final_charges, axis=1)
        df['Net P&L'] = df['Live P&L'] - df['Est. Charges']
        
        # --- ARCHIVE CLOSED TRADES ---
        closed_mask = df['Status'].str.contains('HIT', na=False)
        if closed_mask.any():
            closed_trades = df[closed_mask].copy()
            if os.path.exists(SWING_ARCHIVE_FILE):
                archive_df = pd.read_csv(SWING_ARCHIVE_FILE)
                archive_df = pd.concat([archive_df, closed_trades], ignore_index=True)
            else:
                archive_df = closed_trades
            _safe_to_csv(archive_df, SWING_ARCHIVE_FILE)
            logging.info(f"📁 Archived {len(closed_trades)} completed swing trades to {SWING_ARCHIVE_FILE}")
            
            # Remove from active file
            df = df[~closed_mask]

        _safe_to_csv(df, SWING_FILE)
        return df
        
    except Exception as e:
        logging.error(f"Error updating swing portfolio: {e}")
        return open_trades


def manual_exit_swing_trade(ticker: str, exit_price: float):
    """
    Manually exit an active swing trade, calculate P&L, 
    add it to archived P&L (swing_trades_archived.csv), 
    and clear it from the active position file (swing_trades.csv).
    """
    if not os.path.exists(SWING_FILE):
        return False, "No swing trades file found."
        
    try:
        with _trader_lock:
            df = pd.read_csv(SWING_FILE)
            if df.empty:
                return False, "Swing portfolio is empty."
                
            # Find the active trade
            mask = (df['Ticker'] == ticker) & (df['Status'] == 'OPEN')
            if not mask.any():
                return False, f"No active/open swing trade found for {ticker}."
                
            # Extract the trade row
            trade_row = df[mask].copy().iloc[0]
            
            # Update fields
            exit_price = round(float(exit_price), 2)
            live_pnl = (exit_price - trade_row['EntryPrice']) * trade_row['Qty']
            ret_pct = (live_pnl / (trade_row['EntryPrice'] * trade_row['Qty'])) * 100
            
            # Calculate charges
            buy_val = trade_row['EntryPrice'] * trade_row['Qty']
            sell_val = exit_price * trade_row['Qty']
            turnover = buy_val + sell_val
            stt = 0.001 * turnover
            trans = 0.0000345 * turnover
            gst = 0.18 * trans
            sebi = (turnover / 10000000) * 10
            stamp = 0.00015 * buy_val
            est_charges = stt + trans + gst + sebi + stamp
            net_pnl = live_pnl - est_charges
            
            # Create the closed trade dict
            closed_trade = {
                "Ticker": ticker,
                "EntryPrice": trade_row['EntryPrice'],
                "Target": trade_row['Target'],
                "SL": trade_row['SL'],
                "Qty": trade_row['Qty'],
                "Token": trade_row.get('Token') if pd.notna(trade_row.get('Token')) else None,
                "EntryDate": trade_row['EntryDate'],
                "Status": "MANUAL EXIT",
                "Current Price": exit_price,
                "Live P&L": round(live_pnl, 2),
                "Return %": round(ret_pct, 2),
                "ExitDate": datetime.now().strftime("%Y-%m-%d"),
                "Est. Charges": round(est_charges, 2),
                "Net P&L": round(net_pnl, 2)
            }
            
            # Load or create archive
            if os.path.exists(SWING_ARCHIVE_FILE):
                archive_df = pd.read_csv(SWING_ARCHIVE_FILE)
                # Align columns
                for col in closed_trade:
                    if col not in archive_df.columns:
                        archive_df[col] = None
                archive_df = pd.concat([archive_df, pd.DataFrame([closed_trade])], ignore_index=True)
            else:
                archive_df = pd.DataFrame([closed_trade])
                
            _safe_to_csv(archive_df, SWING_ARCHIVE_FILE)
            
            # Remove from active swing file
            df = df[~mask]
            _safe_to_csv(df, SWING_FILE)
            
            logging.info(f"🚪 Manual Exit Executed: {ticker} @ {exit_price} (Net P&L: ₹{net_pnl:.2f})")
            return True, f"Successfully exited {ticker} at ₹{exit_price:.2f}."
            
    except Exception as e:
        logging.error(f"Error executing manual exit for {ticker}: {e}")
        return False, f"Error: {e}"


# ---------------------------------------------------------------------------
# PAPER TRADING EQUITY CURVE CALCULATIONS (Separate Intraday & Swing)
# ---------------------------------------------------------------------------

def get_intraday_equity_curve(kite=None):
    """
    Computes today's intraday paper trading equity curve.
    Combines today's closed trades from HISTORY_FILE and today's active trades from get_portfolio().
    Returns a DataFrame with columns: ['Time', 'Ticker', 'Type', 'Strategy', 'P&L', 'Cumulative P&L']
    """
    points = []
    
    # 1. Closed Trades of Today (from HISTORY_FILE)
    history_df = get_history()
    if not history_df.empty:
        for _, row in history_df.iterrows():
            exit_time_str = row.get('ExitTime')
            if pd.isna(exit_time_str) or not exit_time_str:
                exit_time_str = row.get('EntryTime')
            
            final_pnl = float(row.get('Final P&L', 0))
            
            entry_price = float(row.get('EntryPrice', 0))
            exit_price = float(row.get('ExitPrice', entry_price))
            qty = float(row.get('Qty', 0))
            
            # Compute Zerodha intraday charges
            buy_val = entry_price * qty
            sell_val = exit_price * qty
            turnover = buy_val + sell_val
            brok = min(20, 0.0003 * buy_val) + min(20, 0.0003 * sell_val)
            stt = 0.00025 * sell_val
            trans = 0.0000345 * turnover
            gst = 0.18 * (brok + trans)
            sebi = (turnover / 10000000) * 10
            stamp = 0.00003 * buy_val
            charges = brok + stt + trans + gst + sebi + stamp
            
            net_pnl = final_pnl - charges
            
            points.append({
                'Time': exit_time_str,
                'Ticker': row.get('Ticker', 'Unknown'),
                'Type': 'Closed',
                'Strategy': row.get('Strategy', '15-Min ORB'),
                'P&L': net_pnl
            })
            
    # 2. Active Trades of Today (from update_portfolio_pnl)
    if kite:
        try:
            active_df = update_portfolio_pnl(kite)
            if not active_df.empty:
                active_only = active_df[active_df['Status'] == 'Active']
                for _, row in active_only.iterrows():
                    net_pnl = float(row.get('Net P&L', 0))
                    current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                    points.append({
                        'Time': current_time_str,
                        'Ticker': row.get('Ticker', 'Unknown'),
                        'Type': 'Active',
                        'Strategy': row.get('Strategy', '15-Min ORB'),
                        'P&L': net_pnl
                    })
        except Exception as e:
            logging.error(f"Error fetching active trades for intraday curve: {e}")
            
    if not points:
        return pd.DataFrame(columns=['Time', 'Ticker', 'Type', 'Strategy', 'P&L', 'Cumulative P&L'])
        
    df_curve = pd.DataFrame(points)
    
    # Parse timestamps
    df_curve['Time_parsed'] = pd.to_datetime(df_curve['Time'])
    
    # Base starting point of the day: 9:15 AM
    today_str = datetime.now().strftime("%Y-%m-%d")
    start_time_str = f"{today_str} 09:15"
    
    baseline = pd.DataFrame([{
        'Time': start_time_str,
        'Time_parsed': pd.to_datetime(start_time_str),
        'Ticker': 'Start',
        'Type': 'Start',
        'Strategy': 'Start',
        'P&L': 0.0
    }])
    
    df_combined = pd.concat([baseline, df_curve], ignore_index=True)
    df_combined = df_combined.sort_values('Time_parsed').reset_index(drop=True)
    
    # Aggregate by Time to merge concurrent/simultaneous trades (e.g. active positions at the same time)
    df_aggregated = df_combined.groupby('Time').agg({
        'Time_parsed': 'first',
        'P&L': 'sum',
        'Ticker': lambda x: ", ".join(x.unique()),
        'Strategy': lambda x: ", ".join(x.unique()),
        'Type': lambda x: ", ".join(x.unique())
    }).reset_index().sort_values('Time_parsed').reset_index(drop=True)
    
    # Calculate Cumulative P&L
    df_aggregated['Cumulative P&L'] = df_aggregated['P&L'].cumsum()
    
    return df_aggregated


def get_swing_equity_curve(kite=None):
    """
    Computes the persistent lifetime Swing equity curve.
    Combines archived closed swing trades and active swing trades.
    Returns a DataFrame with columns: ['Date', 'Ticker', 'Type', 'P&L', 'Cumulative P&L']
    """
    points = []
    
    # 1. Closed/Archived Swing Trades (from SWING_ARCHIVE_FILE)
    if os.path.exists(SWING_ARCHIVE_FILE):
        try:
            archive_df = pd.read_csv(SWING_ARCHIVE_FILE)
            if not archive_df.empty:
                for _, row in archive_df.iterrows():
                    net_pnl = float(row.get('Net P&L', 0))
                    
                    # Retrieve or calculate Exit Date
                    exit_date = row.get('ExitDate')
                    if pd.isna(exit_date) or not exit_date:
                        # Fallback calculation: EntryDate + Days Held
                        entry_date_str = str(row.get('EntryDate', '')).split(' ')[0]
                        try:
                            entry_date = datetime.strptime(entry_date_str, "%Y-%m-%d")
                            days_held = float(row.get('Days Held', 0))
                            exit_date_parsed = entry_date + timedelta(days=int(days_held))
                            exit_date = exit_date_parsed.strftime("%Y-%m-%d")
                        except Exception as e:
                            exit_date = entry_date_str if entry_date_str else datetime.now().strftime("%Y-%m-%d")
                            
                    points.append({
                        'Date': exit_date,
                        'Ticker': row.get('Ticker', 'Unknown'),
                        'Type': 'Closed',
                        'P&L': net_pnl
                    })
        except Exception as e:
            logging.error(f"Error reading swing archive for equity curve: {e}")
            
    # 2. Active Swing Trades (from update_swing_portfolio)
    if kite:
        try:
            swing_df = update_swing_portfolio(kite)
            if not swing_df.empty:
                active_swing = swing_df[swing_df['Status'] == 'OPEN']
                for _, row in active_swing.iterrows():
                    net_pnl = float(row.get('Net P&L', 0))
                    today_str = datetime.now().strftime("%Y-%m-%d")
                    points.append({
                        'Date': today_str,
                        'Ticker': row.get('Ticker', 'Unknown'),
                        'Type': 'Active',
                        'P&L': net_pnl
                    })
        except Exception as e:
            logging.error(f"Error reading active swing trades for equity curve: {e}")
            
    if not points:
        return pd.DataFrame(columns=['Date', 'Ticker', 'Type', 'P&L', 'Cumulative P&L'])
        
    df_curve = pd.DataFrame(points)
    
    df_curve['Date_parsed'] = pd.to_datetime(df_curve['Date'])
    df_curve = df_curve.sort_values('Date_parsed').reset_index(drop=True)
    
    # Aggregate by Date to ensure one point per date
    df_aggregated = df_curve.groupby('Date').agg({
        'Date_parsed': 'first',
        'P&L': 'sum',
        'Ticker': lambda x: ", ".join(x.unique())
    }).reset_index().sort_values('Date_parsed').reset_index(drop=True)
    
    # Prepend a baseline starting point
    if not df_aggregated.empty:
        first_date = df_aggregated.iloc[0]['Date_parsed']
        baseline_date = first_date - timedelta(days=1)
        baseline = pd.DataFrame([{
            'Date': baseline_date.strftime("%Y-%m-%d"),
            'Date_parsed': baseline_date,
            'P&L': 0.0,
            'Ticker': 'Baseline'
        }])
        df_aggregated = pd.concat([baseline, df_aggregated], ignore_index=True)
        
    df_aggregated['Cumulative P&L'] = df_aggregated['P&L'].cumsum()
    
    return df_aggregated


INTRADAY_PNL_LOG_FILE = os.path.join("data", "trades", "options_intraday_pnl_log.csv")

def log_intraday_pnl_snapshot(kite):
    """
    Computes active/realized options strategies P&L and logs a snapshot to INTRADAY_PNL_LOG_FILE.
    Runs in background cycles from strategy managers.
    """
    if kite is None:
        return
        
    try:
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        
        # Load or create file, keeping only today's data to avoid bloat
        existing_df = pd.DataFrame()
        if os.path.exists(INTRADAY_PNL_LOG_FILE):
            try:
                existing_df = pd.read_csv(INTRADAY_PNL_LOG_FILE)
                if not existing_df.empty and 'Timestamp' in existing_df.columns:
                    existing_df['Date'] = existing_df['Timestamp'].apply(lambda x: str(x).split(' ')[0])
                    existing_df = existing_df[existing_df['Date'] == today_str].drop(columns=['Date'])
            except Exception as e:
                logging.error(f"Error reading existing intraday P&L log: {e}")
                
        # Gather active option positions from the portfolio
        portfolio_df = get_portfolio()
        if portfolio_df.empty:
            active_options = pd.DataFrame()
        else:
            active_options = portfolio_df[
                (portfolio_df['Status'] == 'Active') & 
                ((portfolio_df['Strategy'].isin(['Option Desk', 'Rolling Straddle'])) | 
                 (portfolio_df['Ticker'].apply(lambda t: any(str(t).endswith(x) for x in ["CE", "PE"]))))
            ].copy()
            
        # Get quotes for spot prices
        spot_nifty = 0.0
        spot_sensex = 0.0
        try:
            quotes = kite.quote(["NSE:NIFTY 50", "BSE:SENSEX"])
            spot_nifty = quotes.get("NSE:NIFTY 50", {}).get('last_price', 0.0)
            spot_sensex = quotes.get("BSE:SENSEX", {}).get('last_price', 0.0)
        except Exception as qe:
            logging.error(f"LTP fetch error in PnL logger: {qe}")
            
        strategies = ['Option Desk', 'Rolling Straddle']
        new_rows = []
        
        for strat in strategies:
            realized = 0.0
            if strat == 'Option Desk':
                import option_desk_manager
                try:
                    realized = option_desk_manager.load_state().get("realized_pnl", 0.0)
                except Exception:
                    pass
            elif strat == 'Rolling Straddle':
                import rolling_straddle_manager
                try:
                    realized = rolling_straddle_manager.load_state().get("realized_pnl", 0.0)
                except Exception:
                    pass
                    
            strat_active = active_options[active_options['Strategy'] == strat] if not active_options.empty else pd.DataFrame()
            unrealized = 0.0
            capital = 0.0
            
            if not strat_active.empty:
                tickers = strat_active['Ticker'].tolist()
                exch_tickers = []
                for t in tickers:
                    exch = "BFO" if t.startswith("SENSEX") else "NFO"
                    exch_tickers.append(f"{exch}:{t}")
                    
                opt_quotes = {}
                try:
                    opt_quotes = kite.quote(exch_tickers)
                except Exception:
                    pass
                    
                for _, row in strat_active.iterrows():
                    t = row['Ticker']
                    exch = "BFO" if t.startswith("SENSEX") else "NFO"
                    ltp = opt_quotes.get(f"{exch}:{t}", {}).get('last_price', row['EntryPrice'])
                    qty = row['Qty']
                    
                    # selling premium calculation: entry - current
                    unrealized += (row['EntryPrice'] - ltp) * qty
                    
                    margin_req = row.get('Margin Required', 0.0)
                    if pd.isna(margin_req) or margin_req == 0.0:
                        is_nifty = t.startswith("NIFTY")
                        lot_size = getattr(config, 'LOT_SIZE_NIFTY', 65) if is_nifty else getattr(config, 'LOT_SIZE_SENSEX', 20)
                        lots = qty / lot_size
                        margin_req = lots * 200000.0 # Fallback margin per lot
                    capital += margin_req
                    
            total_pnl = realized + unrealized
            spot = spot_nifty if strat_active.empty or not strat_active.iloc[0]['Ticker'].startswith("SENSEX") else spot_sensex
            
            new_rows.append({
                "Timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                "Strategy": strat,
                "SpotPrice": spot,
                "RealizedPnL": round(realized, 2),
                "UnrealizedPnL": round(unrealized, 2),
                "TotalPnL": round(total_pnl, 2),
                "CapitalDeployed": round(capital, 2)
            })
            
        new_df = pd.DataFrame(new_rows)
        if not existing_df.empty:
            combined = pd.concat([existing_df, new_df], ignore_index=True)
        else:
            combined = new_df
            
        _safe_to_csv(combined, INTRADAY_PNL_LOG_FILE)
        
    except Exception as e:
        logging.error(f"Error logging intraday P&L snapshot: {e}", exc_info=True)


def force_clear_strategy_trades(strategy_name):
    """Force close all active trades for a strategy in the portfolio without fetching LTP or placing trades."""
    with _trader_lock:
        df = get_portfolio()
        if df.empty:
            return
        
        mask = (df['Strategy'] == strategy_name) & (df['Status'] == 'Active')
        if mask.any():
            for idx, row in df[mask].iterrows():
                trade = row.to_dict()
                trade['ExitPrice'] = trade.get('Current Price', trade['EntryPrice'])
                if pd.isnull(trade['ExitPrice']):
                    trade['ExitPrice'] = trade['EntryPrice']
                trade['ExitTime'] = datetime.now().strftime("%Y-%m-%d %H:%M")
                trade['Capital Deployed'] = trade['EntryPrice'] * trade['Qty']
                if "Bullish" in str(trade['Type']):
                    trade['Final P&L'] = (trade['ExitPrice'] - trade['EntryPrice']) * trade['Qty']
                else:
                    trade['Final P&L'] = (trade['EntryPrice'] - trade['ExitPrice']) * trade['Qty']
                trade['P&L %'] = (trade['Final P&L'] / trade['Capital Deployed']) * 100 if trade['Capital Deployed'] > 0 else 0
                trade['Status'] = 'CLOSED'
                
                # Append to history
                is_option = str(trade.get('Strategy', '')).lower() in ['option desk', 'rolling straddle'] or (any(str(trade.get('Ticker', '')).endswith(x) for x in ["CE", "PE"]) and any(c.isdigit() for c in str(trade.get('Ticker', ''))))
                dest_file = OPTIONS_HISTORY_FILE if is_option else HISTORY_FILE
                history_df = pd.DataFrame([trade])
                if os.path.exists(dest_file):
                    try:
                        existing_history = pd.read_csv(dest_file)
                        combined_history = pd.concat([existing_history, history_df], ignore_index=True, sort=False)
                        _safe_to_csv(combined_history, dest_file)
                    except Exception:
                        _safe_to_csv(history_df, dest_file)
                else:
                    _safe_to_csv(history_df, dest_file)
            
            # Now mark them closed in the portfolio file
            df.loc[mask, 'Status'] = 'Closed'
            _safe_to_csv(df, PORTFOLIO_FILE)
            logging.info(f"Force cleared active trades for strategy: {strategy_name}")

