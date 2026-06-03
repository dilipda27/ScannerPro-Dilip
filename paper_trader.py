import pandas as pd
import os
import json
import logging
import threading
from datetime import datetime, timedelta

_trader_lock = threading.Lock()

PORTFOLIO_FILE = "paper_portfolio.csv"
HISTORY_FILE = "paper_trade_history.csv"
SWING_FILE = "swing_trades.csv"
SWING_ARCHIVE_FILE = "swing_trades_archived.csv"
ARCHIVE_FILE = "paper_trade_archive.csv"
_INSTRUMENT_CACHE_FILE = "_instrument_token_cache.json"

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
    if not os.path.exists(PORTFOLIO_FILE):
        return pd.DataFrame(columns=["Ticker", "Type", "EntryPrice", "SL", "Qty", "EntryTime", "Status", "Strategy"])
    try:
        df = pd.read_csv(PORTFOLIO_FILE)
        # Migrate old 'OPEN' status to 'Active'
        if not df.empty and 'Status' in df.columns:
            if (df['Status'] == 'OPEN').any():
                df.loc[df['Status'] == 'OPEN', 'Status'] = 'Active'
                df.to_csv(PORTFOLIO_FILE, index=False) # Save migration
        
        # Migrate/Ensure Strategy column exists
        if not df.empty and 'Strategy' not in df.columns:
            df['Strategy'] = "15-Min ORB"
            df.to_csv(PORTFOLIO_FILE, index=False)
            
        # Migrate/Ensure InitialSL column exists
        if not df.empty and 'InitialSL' not in df.columns:
            df['InitialSL'] = df['SL']
            df.to_csv(PORTFOLIO_FILE, index=False)
            
        # Clean up any existing duplicates (same Ticker and Status)
        if not df.empty:
            df = df.drop_duplicates(subset=['Ticker', 'Status'], keep='first')
            
        return df
    except:
        return pd.DataFrame(columns=["Ticker", "Type", "EntryPrice", "SL", "InitialSL", "Qty", "EntryTime", "Status", "Strategy"])

def execute_paper_trade(ticker, trade_type, entry_price, sl, qty, token=None, strategy="15-Min ORB", target=None):
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
        "Strategy": strategy
    }
    
    if df.empty:
        df = pd.DataFrame([new_trade])
    else:
        df = pd.concat([df, pd.DataFrame([new_trade])], ignore_index=True)
    df.to_csv(PORTFOLIO_FILE, index=False)
    logging.info(f"🚀 Paper Trade Executed: {trade_type} ({strategy}) {ticker} @ {entry_price} (SL: {sl}, Target: {target}, Qty: {qty})")
    return True

def exit_trade(ticker, kite, override_price=None):
    """Exit a trade, calculate final P&L, and move to history."""
    with _trader_lock:
        df = get_portfolio()
        if df.empty:
            return False
        
        # Verify the trade is still active (prevents duplicate exits due to race conditions)
        trade_row = df[(df['Ticker'] == ticker) & (df['Status'] == 'Active')]
        if trade_row.empty:
            logging.warning(f"⚠️ exit_trade: Ticker {ticker} is not Active or already closed. Skipping exit.")
            return False
            
        try:
            # Fetch Exit Price
            if override_price is not None:
                exit_price = override_price
            else:
                quote = kite.ltp([f"NSE:{ticker}"])
                exit_price = quote.get(f"NSE:{ticker}", {}).get('last_price')
            
            if exit_price is None:
                logging.error(f"Could not fetch exit price for {ticker}")
                return False
                
            trade = trade_row.iloc[0].to_dict()
            sl = trade.get('SL', 0)
            
            # Adjust Exit Price if SL was hit (use SL price as benchmark)
            actual_exit_price = exit_price
            if "Bullish" in str(trade['Type']) and sl > 0 and exit_price <= sl:
                actual_exit_price = sl
            elif "Bearish" in str(trade['Type']) and sl > 0 and exit_price >= sl:
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
            if os.path.exists(HISTORY_FILE):
                try:
                    existing_history = pd.read_csv(HISTORY_FILE)
                    
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
                        combined_history.to_csv(HISTORY_FILE, index=False)
                    else:
                        logging.warning(f"⚠️ exit_trade: Duplicate entry for {trade['Ticker']} (Entry: {trade['EntryTime']}) already exists in history. Skipping history append.")
                except Exception as read_err:
                    logging.warning(f"Error appending history: {read_err}. Overwriting.")
                    history_df.to_csv(HISTORY_FILE, index=False)
            else:
                history_df.to_csv(HISTORY_FILE, index=False)
                
            # Update status and exit price in portfolio file instead of removing
            df.loc[(df['Ticker'] == ticker) & (df['Status'] == 'Active'), 'Current Price'] = actual_exit_price
            df.loc[(df['Ticker'] == ticker) & (df['Status'] == 'Active'), 'Status'] = 'Closed'
            df.to_csv(PORTFOLIO_FILE, index=False)
            
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

def archive_history():
    """Move daily history to permanent archive and clear daily file."""
    if not os.path.exists(HISTORY_FILE):
        return
    
    try:
        df = pd.read_csv(HISTORY_FILE)
        if df.empty:
            return

        # Append to archive
        if os.path.exists(ARCHIVE_FILE):
            df.to_csv(ARCHIVE_FILE, mode='a', header=False, index=False)
        else:
            df.to_csv(ARCHIVE_FILE, index=False)
            
        # Delete daily history file
        os.remove(HISTORY_FILE)
        logging.info(f"📁 Archived {len(df)} trades to {ARCHIVE_FILE}")
        
        # Also clean up the portfolio file (remove Closed trades from previous days)
        if os.path.exists(PORTFOLIO_FILE):
            pdf = pd.read_csv(PORTFOLIO_FILE)
            pdf = pdf[pdf['Status'] == 'Active'] # Only keep active trades for the new day
            pdf.to_csv(PORTFOLIO_FILE, index=False)
            
    except Exception as e:
        logging.error(f"Error archiving history: {e}")

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
        filtered_df.to_csv(PORTFOLIO_FILE, index=False)
        logging.info(f"🧹 Paper Portfolio Cleared for strategy: {strategy}")
    return True

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
                
        elif "Bearish" in trade_type:
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

def update_portfolio_pnl(kite):
    """
    Fetches latest prices for all open trades and calculates P&L.
    Returns a DataFrame with live stats.
    """
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
                    df.to_csv(PORTFOLIO_FILE, index=False)
            except Exception as e:
                logging.warning(f"Intraday token auto-fix failed: {e}")
                
        # Calculate metrics for active trades
        if not active_trades.empty:
            tickers = active_trades['Ticker'].tolist()
            try:
                # Fetch LTP for all active tickers
                quotes = kite.ltp([f"NSE:{t}" for t in tickers])
                
                def get_ltp(ticker):
                    q = quotes.get(f"NSE:{ticker}")
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
            elif "Bearish" in str(row['Type']) and price_for_pnl >= sl:
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
                
                new_sl = apply_multi_stage_trailing_sl(row, ltp)
                if new_sl != current_sl:
                    df.loc[(df['Ticker'] == row['Ticker']) & (df['Status'] == 'Active'), 'SL'] = new_sl
                    processed_df.at[idx, 'SL'] = new_sl
                    row['SL'] = new_sl # Update for the hit check below
                    df.to_csv(PORTFOLIO_FILE, index=False) # Persist trail
                    logging.info(f"🛡️ Multi-Stage Trail: {row['Ticker']} Stop-Loss moved from ₹{current_sl:.2f} to ₹{new_sl:.2f} (Entry: ₹{entry:.2f}, LTP: ₹{ltp:.2f})")

            is_hit = False
            exit_reason = "SL Hit"
            sl = row['SL']
            target = row.get('Target')
            
            # 1. Check Stop Loss
            if "Bullish" in str(row['Type']) and row['Current Price'] is not None and row['Current Price'] <= sl:
                is_hit = True
                exit_reason = "SL Hit"
            elif "Bearish" in str(row['Type']) and row['Current Price'] is not None and row['Current Price'] >= sl:
                is_hit = True
                exit_reason = "SL Hit"
                
            # 2. Check Target (if defined in portfolio records)
            elif target is not None and pd.notna(target):
                target = float(target)
                if "Bullish" in str(row['Type']) and row['Current Price'] is not None and row['Current Price'] >= target:
                    is_hit = True
                    exit_reason = "Target Hit"
                elif "Bearish" in str(row['Type']) and row['Current Price'] is not None and row['Current Price'] <= target:
                    is_hit = True
                    exit_reason = "Target Hit"
            
            if is_hit:
                if exit_reason == "Target Hit":
                    logging.info(f"🎯 Target Hit for {row['Ticker']}. Auto-exiting at ₹{target:.2f}")
                    exit_trade(row['Ticker'], kite, override_price=target)
                    processed_df.at[idx, 'SL Status'] = "🎯 TARGET HIT (EXITED)"
                    processed_df.at[idx, 'Status'] = "Closed"
                    processed_df.at[idx, 'Current Price'] = target
                else:
                    logging.info(f"🚨 SL Hit for {row['Ticker']}. Auto-exiting at ₹{sl:.2f}")
                    exit_trade(row['Ticker'], kite, override_price=sl)
                    processed_df.at[idx, 'SL Status'] = "❌ SL HIT (EXITED)"
                    processed_df.at[idx, 'Status'] = "Closed"
                    processed_df.at[idx, 'Current Price'] = sl


        # --- ESTIMATED ZERODHA INTRADAY CHARGES ---
        def calc_intraday_charges(row):
            if row['Current Price'] is None: return 0
            buy_val = row['EntryPrice'] * row['Qty']
            sell_val = row['Current Price'] * row['Qty']
            turnover = buy_val + sell_val
            brok = min(20, 0.0003 * buy_val) + min(20, 0.0003 * sell_val)
            stt = 0.00025 * sell_val
            trans = 0.0000345 * turnover
            gst = 0.18 * (brok + trans)
            sebi = (turnover / 10000000) * 10
            stamp = 0.00003 * buy_val
            return brok + stt + trans + gst + sebi + stamp

        processed_df['Est. Charges'] = processed_df.apply(calc_intraday_charges, axis=1)
        processed_df['Net P&L'] = processed_df['Live P&L'] - processed_df['Est. Charges']
        
        # Ensure Strategy is present in processed_df
        if 'Strategy' not in processed_df.columns:
            processed_df['Strategy'] = "15-Min ORB"
            
        return processed_df[["Ticker", "Type", "Strategy", "EntryPrice", "Current Price", "Qty", "SL", "SL Status", "Status", "Live P&L", "Est. Charges", "Net P&L", "EntryTime", "Token"]]
        
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
    df.to_csv(SWING_FILE, index=False)
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
                df.to_csv(SWING_FILE, index=False)
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
                df.loc[df['Ticker'] == row['Ticker'], 'ExitDate'] = exit_date
                
                # Reflect in open_trades for the current return
                open_trades.loc[idx, 'Status'] = new_status
                open_trades.loc[idx, 'Current Price'] = exit_price
                open_trades.loc[idx, 'Live P&L'] = locked_pnl
                open_trades.loc[idx, 'Return %'] = (locked_pnl / (row['EntryPrice'] * row['Qty'])) * 100
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
            archive_df.to_csv(SWING_ARCHIVE_FILE, index=False)
            logging.info(f"📁 Archived {len(closed_trades)} completed swing trades to {SWING_ARCHIVE_FILE}")
            
            # Remove from active file
            df = df[~closed_mask]

        df.to_csv(SWING_FILE, index=False)
        return df
        
    except Exception as e:
        logging.error(f"Error updating swing portfolio: {e}")
        return open_trades


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
