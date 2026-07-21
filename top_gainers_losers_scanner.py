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
import morning_range_scanner

WATCHLIST_FILE = os.path.join("data", "state", ".gainers_losers_watchlist.json")
NOTIFIED_FILE = os.path.join("data", "state", ".gainers_losers_notified.json")

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_watchlist():
    if not os.path.exists(WATCHLIST_FILE):
        return None
    try:
        with open(WATCHLIST_FILE, "r") as f:
            data = json.load(f)
            today_str = datetime.date.today().strftime("%Y-%m-%d")
            if data.get("date") == today_str:
                return data.get("watchlist", {})
    except Exception as e:
        logging.error(f"Error loading gainers/losers watchlist: {e}")
    return None

def save_watchlist(watchlist):
    try:
        os.makedirs(os.path.dirname(WATCHLIST_FILE), exist_ok=True)
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        with open(WATCHLIST_FILE, "w") as f:
            json.dump({"date": today_str, "watchlist": watchlist}, f, indent=4)
        logging.info(f"Saved Top Gainers/Losers watchlist with {len(watchlist)} stocks.")
    except Exception as e:
        logging.error(f"Error saving gainers/losers watchlist: {e}")

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
        logging.error(f"Error loading gainers/losers notified list: {e}")
    return set()

def save_notified(notified_set):
    try:
        os.makedirs(os.path.dirname(NOTIFIED_FILE), exist_ok=True)
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        with open(NOTIFIED_FILE, "w") as f:
            json.dump({"date": today_str, "notified": list(notified_set)}, f, indent=4)
    except Exception as e:
        logging.error(f"Error saving gainers/losers notified list: {e}")

def build_gainers_losers_watchlist(kite):
    """Fetches F&O segment and identifies the top 5 gainers and top 5 losers at 09:30 AM."""
    logging.info("Building 9:30 AM Top Gainers & Losers Watchlist...")
    try:
        fno_symbols = list(kite_scanner.get_nifty500_fno_symbols())
        if not fno_symbols:
            logging.error("No F&O symbols found.")
            return {}
            
        token_map = kite_scanner.get_kite_instruments(kite, fno_symbols)
        if not token_map:
            logging.error("Failed to map instrument tokens.")
            return {}
            
        all_tickers = [f"NSE:{s}" for s in token_map.keys()]
        ohlc_dict = kite_scanner.fetch_ohlc_safe(kite, all_tickers)
        
        candidates = []
        for s, token in token_map.items():
            q = ohlc_dict.get(f"NSE:{s}")
            if q:
                ltp = q['last_price']
                prev_close = q['ohlc']['close']
                if prev_close > 0:
                    pct_change = ((ltp - prev_close) / prev_close) * 100
                    # Filter price to avoid penny or illiquid heavyweights
                    if 100 <= ltp <= 5000:
                        candidates.append({
                            "symbol": s,
                            "token": token,
                            "pct_change": pct_change,
                            "prev_close": prev_close
                        })
                        
        if not candidates:
            return {}
            
        # Sort by percentage change
        candidates.sort(key=lambda x: x["pct_change"])
        
        top_losers = candidates[:5]
        top_gainers = candidates[-5:]
        top_gainers.reverse() # Sort descending
        
        watchlist = {}
        for c in top_gainers:
            watchlist[c["symbol"]] = {
                "token": int(c["token"]),
                "type": "GAINER",
                "pct_change_930": float(c["pct_change"])
            }
        for c in top_losers:
            watchlist[c["symbol"]] = {
                "token": int(c["token"]),
                "type": "LOSER",
                "pct_change_930": float(c["pct_change"])
            }
            
        save_watchlist(watchlist)
        
        # Send Telegram notification of the watchlist
        tel_token = config.TELEGRAM_BOT_TOKEN
        tel_chat_id = getattr(config, 'TELEGRAM_CHAT_ID_INTRADAY', config.TELEGRAM_CHAT_ID)
        
        msg = "📢 *9:30 AM Watchlist Built*\n\n"
        msg += "*Top 5 Gainers (Long Watch):*\n"
        for c in top_gainers:
            msg += f"• {c['symbol']}: {c['pct_change']:.2f}%\n"
        msg += "\n*Top 5 Losers (Short Watch):*\n"
        for c in top_losers:
            msg += f"• {c['symbol']}: {c['pct_change']:.2f}%\n"
            
        telegram_agent.send_message(msg, tel_token, tel_chat_id, parse_mode="Markdown")
        return watchlist
    except Exception as e:
        logging.error(f"Error building gainers/losers watchlist: {e}")
        return {}

def scan_gainers_losers(kite):
    """Monitors the gainers/losers watchlist for consolidation breakouts/breakdowns."""
    now = datetime.datetime.now()
    
    # Restrict to active market hours
    market_start = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_cutoff = now.replace(hour=14, minute=45, second=0, microsecond=0)
    if not (market_start <= now <= market_cutoff):
        return
        
    watchlist = load_watchlist()
    if not watchlist:
        logging.info("Top Gainers/Losers watchlist is empty or has not been built today.")
        return
        
    notified = load_notified()
    
    # Nifty 50 Trend Alignment
    nifty_trend = morning_range_scanner.check_nifty_trend(kite)
    logging.info(f"[G/L Scanner] Nifty 50 Trend: {nifty_trend}")
    
    start_of_day = now.replace(hour=9, minute=15, second=0, microsecond=0)
    portfolio_df = paper_trader.get_portfolio()
    active_tickers = portfolio_df[portfolio_df['Status'] == 'Active']['Ticker'].tolist() if not portfolio_df.empty else []
    
    def monitor_stock(item):
        ticker, info = item
        if ticker in notified or ticker in active_tickers:
            return None
            
        try:
            # Fetch intraday data
            df = kite_scanner.fetch_kite_data(kite, info["token"], start_of_day, datetime.datetime.now(), "5minute")
            if df.empty or len(df) < 5:
                return None
                
            df.columns = [c.lower() for c in df.columns]
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
                
            # Calculate VWAP and EMAs
            df['vwap'] = morning_range_scanner.calculate_vwap(df)
            df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
            df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
            
            latest_candle = df.iloc[-1]
            current_price = latest_candle['close']
            current_vwap = latest_candle['vwap']
            current_ema9 = latest_candle['ema_9']
            current_ema21 = latest_candle['ema_21']
            
            # Use previous 3 candles for consolidation check
            consol_df = df.iloc[-4:-1]
            consol_high = consol_df['high'].max()
            consol_low = consol_df['low'].min()
            consol_spread = (consol_high - consol_low) / current_price * 100
            
            # Calculate volume spike (latest volume vs average of consolidation)
            avg_vol = consol_df['volume'].mean()
            vol_ratio = latest_candle['volume'] / avg_vol if avg_vol > 0 else 1.0
            
            # Check Consolidation Tightness (< 0.6%) and breakout
            if consol_spread <= 0.6:
                # LONG BREAKOUT (GAINER)
                if info["type"] == "GAINER" and nifty_trend == "BULLISH":
                    # Proximity filter: current_price must be > consol_high but within 0.8% of consol_high
                    # EMA Alignment: ema_9 > ema_21 and current_price > ema_9
                    # Volume confirmation: vol_ratio >= 2.0
                    if (consol_high < current_price <= consol_high * 1.008 and 
                        current_price > current_vwap and current_price <= current_vwap * 1.012 and 
                        current_ema9 > current_ema21 and current_price > current_ema9 and
                        vol_ratio >= 2.0):
                        
                        sl = max(consol_low, current_vwap)
                        target = current_price + 2 * (current_price - sl)
                        return {
                            "ticker": ticker,
                            "token": info["token"],
                            "type": "GAINER",
                            "signal": "BUY",
                            "entry": float(current_price),
                            "sl": float(sl),
                            "target": float(target),
                            "vol_ratio": float(vol_ratio),
                            "spread": float(consol_spread)
                        }
                # SHORT BREAKDOWN (LOSER)
                elif info["type"] == "LOSER" and nifty_trend == "BEARISH":
                    # Proximity filter: current_price must be < consol_low but within 0.8% of consol_low
                    # EMA Alignment: ema_9 < ema_21 and current_price < ema_9
                    # Volume confirmation: vol_ratio >= 2.0
                    if (consol_low * 0.992 <= current_price < consol_low and 
                        current_price < current_vwap and current_price >= current_vwap * 0.988 and 
                        current_ema9 < current_ema21 and current_price < current_ema9 and
                        vol_ratio >= 2.0):
                        
                        sl = min(consol_high, current_vwap)
                        target = current_price - 2 * (sl - current_price)
                        return {
                            "ticker": ticker,
                            "token": info["token"],
                            "type": "LOSER",
                            "signal": "SELL",
                            "entry": float(current_price),
                            "sl": float(sl),
                            "target": float(target),
                            "vol_ratio": float(vol_ratio),
                            "spread": float(consol_spread)
                        }
        except Exception as e:
            logging.warning(f"Error monitoring {ticker} in G/L scanner: {e}")
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
                f"🎯 GAINERS/LOSERS BREAKOUT TRIGGERED 🎯\n"
                f"------------------------------------------------------------\n"
                f"Strategy:     Top Gainer/Loser Consolidation Breakout\n"
                f"Ticker:       {ticker}\n"
                f"Trade Type:   {signal}\n"
                f"Entry Price:  ₹{entry:.2f}\n"
                f"Stop Loss:    ₹{sl:.2f}\n"
                f"Target:       ₹{target:.2f}\n"
                f"Quantity:     {qty}\n"
                f"Vol Ratio:    {trigger['vol_ratio']:.2f}x\n"
                f"Consol Spread:{trigger['spread']:.2f}%\n"
                f"============================================================\n"
            )
            
            paper_trader.execute_paper_trade(
                ticker=ticker,
                trade_type="Bullish Breakout" if signal == "BUY" else "Bearish Breakdown",
                entry_price=entry,
                sl=sl,
                qty=qty,
                token=int(trigger["token"]),
                strategy="Gainer/Loser Breakout"
            )
            
            # Telegram Notification
            tel_token = config.TELEGRAM_BOT_TOKEN
            tel_chat_id = getattr(config, 'TELEGRAM_CHAT_ID_INTRADAY', config.TELEGRAM_CHAT_ID)
            emoji = "🚀" if signal == "BUY" else "💥"
            
            msg = (
                f"{emoji} *Gainer/Loser Breakout Alert* {emoji}\n\n"
                f"🎯 *Ticker*: {ticker}\n"
                f"⚡ *Signal*: {signal} (Consolidation Breakout)\n"
                f"🟢 *Entry*: ₹{entry:.2f}\n"
                f"🛡️ *Stop Loss*: ₹{sl:.2f}\n"
                f"🟢 *Target*: ₹{target:.2f}\n"
                f"📊 *Consol Spread*: {trigger['spread']:.2f}%\n"
                f"🔊 *Vol Ratio*: {trigger['vol_ratio']:.2f}x\n"
            )
            telegram_agent.send_message(msg, tel_token, tel_chat_id, parse_mode="Markdown")
            
            notified.add(ticker)
            triggered_count += 1
        except Exception as err:
            logging.error(f"Failed to execute trade for {ticker} in G/L scanner: {err}")
            
    if triggered_count > 0:
        save_notified(notified)
        
    logging.info("✅ Gainer/Loser watch list Scan Complete.")
