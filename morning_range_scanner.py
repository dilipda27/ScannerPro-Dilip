import os
import json
import logging
import datetime
import pandas as pd
import numpy as np
import concurrent.futures
from kiteconnect import KiteConnect
import config
import kite_scanner
import telegram_agent
import paper_trader

WATCHLIST_FILE = os.path.join("data", "state", ".morning_range_watchlist.json")
NOTIFIED_FILE = os.path.join("data", "state", ".morning_range_notified.json")

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_watchlist():
    if not os.path.exists(WATCHLIST_FILE):
        return None
    try:
        with open(WATCHLIST_FILE, "r") as f:
            data = json.load(f)
            # Check if watchlist is from today
            today_str = datetime.date.today().strftime("%Y-%m-%d")
            if data.get("date") == today_str:
                return data.get("watchlist", {})
    except Exception as e:
        logging.error(f"Error loading morning range watchlist: {e}")
    return None

def save_watchlist(watchlist):
    try:
        os.makedirs(os.path.dirname(WATCHLIST_FILE), exist_ok=True)
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        with open(WATCHLIST_FILE, "w") as f:
            json.dump({"date": today_str, "watchlist": watchlist}, f, indent=4)
        logging.info(f"💾 Saved morning range watchlist with {len(watchlist)} stocks.")
    except Exception as e:
        logging.error(f"Error saving morning range watchlist: {e}")

def load_notified():
    if not os.path.exists(NOTIFIED_FILE):
        return set()
    try:
        with open(NOTIFIED_FILE, "r") as f:
            data = json.load(f)
            today_str = datetime.date.today().strftime("%Y-%m-%d")
            if data.get("date") == today_str:
                return set(data.get("notified", []))
    except Exception as e:
        logging.error(f"Error loading morning range notified list: {e}")
    return set()

def save_notified(notified_set):
    try:
        os.makedirs(os.path.dirname(NOTIFIED_FILE), exist_ok=True)
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        with open(NOTIFIED_FILE, "w") as f:
            json.dump({"date": today_str, "notified": list(notified_set)}, f, indent=4)
    except Exception as e:
        logging.error(f"Error saving morning range notified list: {e}")

def calculate_vwap(df):
    tp = (df['high'] + df['low'] + df['close']) / 3
    tpv = tp * df['volume']
    cum_tpv = tpv.cumsum()
    cum_vol = df['volume'].cumsum()
    return cum_tpv / cum_vol

def build_morning_watchlist(kite):
    """Fetches morning range (09:15 - 09:45 AM) for all F&O stocks and builds the watchlist."""
    logging.info("Building 9:45 AM Morning Range Watchlist...")
    
    try:
        fno_symbols = list(kite_scanner.get_nifty500_fno_symbols())
        if not fno_symbols:
            logging.error("No F&O symbols found.")
            return {}
        
        logging.info(f"Resolving tokens for {len(fno_symbols)} F&O symbols...")
        token_map = kite_scanner.get_kite_instruments(kite, fno_symbols)
        if not token_map:
            logging.error("Failed to map instrument tokens.")
            return {}
            
        today = datetime.datetime.now()
        start_time = today.replace(hour=9, minute=15, second=0, microsecond=0)
        end_time = today.replace(hour=9, minute=45, second=0, microsecond=0)
        
        watchlist = {}
        
        def process_symbol(symbol):
            token = token_map.get(symbol)
            if not token:
                return None
            try:
                df = kite_scanner.fetch_kite_data(kite, int(token), start_time, end_time, "5minute")
                if df.empty or len(df) < 6:
                    return None
                    
                # Format columns
                df.columns = [c.lower() for c in df.columns]
                
                open_915 = df['open'].iloc[0]
                high_945 = df['high'].max()
                low_945 = df['low'].min()
                current_price = df['close'].iloc[-1]
                range_width = high_945 - low_945
                
                # Filter out low volatility
                if range_width < 0.005 * current_price:
                    return None
                    
                classification = "NEUTRAL"
                if current_price > open_915 and ((high_945 - current_price) / range_width) <= 0.15:
                    classification = "STRONG"
                elif current_price < open_915 and ((current_price - low_945) / range_width) <= 0.15:
                    classification = "WEAK"
                    
                if classification in ["STRONG", "WEAK"]:
                    return symbol, {
                        "token": int(token),
                        "open_915": float(open_915),
                        "high_945": float(high_945),
                        "low_945": float(low_945),
                        "classification": classification
                    }
            except Exception as e:
                logging.debug(f"Error processing {symbol}: {e}")
            return None

        # Process parallel
        with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
            results = list(executor.map(process_symbol, fno_symbols))
            
        for res in results:
            if res:
                symbol, data = res
                watchlist[symbol] = data
                
        save_watchlist(watchlist)
        return watchlist
    except Exception as e:
        logging.error(f"Error building morning range watchlist: {e}")
        return {}

def check_nifty_trend(kite):
    """Returns 'BULLISH', 'BEARISH', or 'NEUTRAL' based on Nifty 50 price vs open today."""
    try:
        nifty_token_map = kite_scanner.get_kite_instruments(kite, ["NIFTY 50"])
        if nifty_token_map and "NIFTY 50" in nifty_token_map:
            nifty_token = nifty_token_map["NIFTY 50"]
            today = datetime.datetime.now()
            nifty_from = today.replace(hour=9, minute=15, second=0, microsecond=0)
            nifty_df = kite_scanner.fetch_kite_data(kite, nifty_token, nifty_from, today, "5minute")
            if not nifty_df.empty:
                nifty_open = nifty_df.iloc[0]['open']
                nifty_ltp = nifty_df.iloc[-1]['close']
                nifty_bullish = nifty_ltp > nifty_open
                logging.info(f"Broad Market Check -> Nifty Open: {nifty_open:.2f}, LTP: {nifty_ltp:.2f} | Bullish? {nifty_bullish}")
                return "BULLISH" if nifty_bullish else "BEARISH"
    except Exception as e:
        logging.warning(f"Failed to check Nifty 50 trend: {e}")
    return "NEUTRAL"

def scan_morning_range(kite):
    """Main function called continuously by the scheduler."""
    now = datetime.datetime.now()
    
    # Check market open
    market_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_cutoff = now.replace(hour=14, minute=45, second=0, microsecond=0)
    if not (market_start <= now <= market_cutoff):
        return
        
    # Check if before 9:45 AM
    watchlist_time = now.replace(hour=9, minute=45, second=0, microsecond=0)
    if now < watchlist_time:
        logging.info("🕰️ Waiting for 9:45 AM morning range to establish...")
        return

    # Load or build watchlist
    watchlist = load_watchlist()
    if not watchlist:
        watchlist = build_morning_watchlist(kite)
        if not watchlist:
            logging.info("No strong or weak stocks found today in morning range.")
            return

    notified = load_notified()
    
    # Fetch Nifty 50 Trend Alignment
    nifty_trend = check_nifty_trend(kite)
    logging.info(f"Broad Market Trend Check -> Nifty 50 Trend: {nifty_trend}")
    
    # Monitor watchlist
    logging.info(f"📡 Monitoring {len(watchlist)} stocks from Morning Range Watchlist...")
    
    start_of_day = now.replace(hour=9, minute=15, second=0, microsecond=0)
    portfolio_df = paper_trader.get_portfolio()
    active_tickers = portfolio_df[portfolio_df['Status'] == 'Active']['Ticker'].tolist() if not portfolio_df.empty else []

    def monitor_stock(item):
        ticker, info = item
        if ticker in notified or ticker in active_tickers:
            return None
            
        try:
            # Fetch today's data up to now
            df = kite_scanner.fetch_kite_data(kite, info["token"], start_of_day, datetime.datetime.now(), "5minute")
            if df.empty or len(df) < 7: # Must have candles post 09:45
                return None
                
            df.columns = [c.lower() for c in df.columns]
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            df['vwap'] = calculate_vwap(df)
            
            # Post-09:45 candles
            post_945_df = df.loc[df.index >= watchlist_time]
            if post_945_df.empty:
                return None
                
            latest_candle = post_945_df.iloc[-1]
            candle_close_5m = latest_candle['close']
            current_price = latest_candle['close']
            current_vwap = latest_candle['vwap']
            
            high_945 = info["high_945"]
            low_945 = info["low_945"]
            mid_point = (high_945 + low_945) / 2
            
            # Calculate volume spike ratio (breakout candle vs 5-candle avg volume)
            volume_ratio = 1.0
            if len(df) >= 7:
                prev_candles = df.iloc[-6:-1]
                avg_volume = prev_candles['volume'].mean()
                latest_volume = latest_candle['volume']
                volume_ratio = latest_volume / avg_volume if avg_volume > 0 else 1.0

            # LONG TRIGGER (STRONG)
            if info["classification"] == "STRONG":
                if nifty_trend == "BULLISH" and candle_close_5m > high_945 and current_price > current_vwap and volume_ratio >= 1.5:
                    sl = max(current_vwap, mid_point)
                    tp = current_price + (2 * (current_price - sl))
                    return {
                        "ticker": ticker,
                        "token": info["token"],
                        "classification": "STRONG",
                        "signal": "BUY",
                        "entry": float(current_price),
                        "sl": float(sl),
                        "target": float(tp),
                        "volume_ratio": float(volume_ratio)
                    }
            
            # SHORT TRIGGER (WEAK)
            elif info["classification"] == "WEAK":
                if nifty_trend == "BEARISH" and candle_close_5m < low_945 and current_price < current_vwap and volume_ratio >= 1.5:
                    sl = min(current_vwap, mid_point)
                    tp = current_price - (2 * (sl - current_price))
                    return {
                        "ticker": ticker,
                        "token": info["token"],
                        "classification": "WEAK",
                        "signal": "SELL",
                        "entry": float(current_price),
                        "sl": float(sl),
                        "target": float(tp),
                        "volume_ratio": float(volume_ratio)
                    }
                    
        except Exception as e:
            logging.warning(f"Error monitoring {ticker}: {e}")
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        triggers = list(executor.map(monitor_stock, watchlist.items()))
        
    triggered_count = 0
    for trigger in triggers:
        if not trigger:
            continue
            
        ticker = trigger["ticker"]
        signal = trigger["signal"]
        entry = trigger["entry"]
        sl = trigger["sl"]
        target = trigger["target"]
        
        # Execute Trade
        try:
            capital = 250000
            qty = int(capital / entry)
            
            logging.info(
                f"\n============================================================\n"
                f"🎯 TRADE TRIGGERED & EXECUTED 🎯\n"
                f"------------------------------------------------------------\n"
                f"Scanner:      Morning Range Strength/Weakness\n"
                f"Ticker:       {ticker}\n"
                f"Trade Type:   {signal} (ORB_VWAP_CONVERGENCE)\n"
                f"Entry Price:  ₹{entry:.2f}\n"
                f"Stop Loss:    ₹{sl:.2f}\n"
                f"Target:       ₹{target:.2f}\n"
                f"Quantity:     {qty}\n"
                f"Vol Ratio:    {trigger.get('volume_ratio', 1.0):.2f}x\n"
                f"============================================================\n"
            )
            
            paper_trader.execute_paper_trade(
                ticker=ticker,
                trade_type="Bullish Breakout" if signal == "BUY" else "Bearish Breakdown",
                entry_price=entry,
                sl=sl,
                qty=qty,
                token=int(trigger["token"]),
                strategy="Morning Range Str/Wk"
            )
            
            # Send Telegram Alert
            tel_token = config.TELEGRAM_BOT_TOKEN
            tel_chat_id = getattr(config, 'TELEGRAM_CHAT_ID_INTRADAY', config.TELEGRAM_CHAT_ID)
            
            emoji = "📈" if signal == "BUY" else "📉"
            msg = (
                f"{emoji} *Morning Range Trigger Alert* {emoji}\n\n"
                f"🎯 *Ticker*: {ticker}\n"
                f"⚡ *Signal*: {signal} (ORB_VWAP_CONVERGENCE)\n"
                f"🟢 *Entry*: ₹{entry:.2f}\n"
                f"🛡️ *Stop Loss*: ₹{sl:.2f}\n"
                f"🟢 *Target*: ₹{target:.2f}\n"
                f"📊 *Classification*: {trigger['classification']}\n"
                f"🔊 *Vol Ratio*: {trigger.get('volume_ratio', 1.0):.2f}x\n"
            )
            telegram_agent.send_message(msg, tel_token, tel_chat_id, parse_mode="Markdown")
            
            notified.add(ticker)
            triggered_count += 1
            
        except Exception as trade_err:
            logging.error(f"Failed to execute trade for {ticker}: {trade_err}")
            
    if triggered_count > 0:
        save_notified(notified)
        
    logging.info("✅ Morning Range Watchlist Scan Complete.")
