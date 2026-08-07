import os
import json
import logging
import datetime
import pandas as pd
import numpy as np
import concurrent.futures
from kiteconnect import KiteConnect
from core import config
from strategies import kite_scanner
from services import telegram_agent
from services import paper_trader

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

from utils.indicators import calculate_vwap_cumulative as calculate_vwap  # cumulative: returns Series

def build_morning_watchlist(kite):
    """Fetches morning range (09:15 - 09:45 AM) and EOD indicators for all F&O stocks and builds the watchlist."""
    logging.info("Building 9:45 AM Morning Range Watchlist with EOD Trend Indicators...")
    
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
                # 1. Fetch daily data for EOD Trend (50 EMA) & Volatility (14 ATR)
                from_date_daily = today - datetime.timedelta(days=100)
                df_daily = kite_scanner.fetch_kite_data(kite, int(token), from_date_daily, today, "day")
                if df_daily.empty or len(df_daily) < 50:
                    return None
                    
                import pandas_ta as ta
                df_daily.ta.ema(length=50, append=True)
                df_daily.ta.atr(length=14, append=True)
                
                latest_daily = df_daily.iloc[-1]
                daily_50_ema = float(latest_daily['EMA_50'])
                daily_atr_14 = float(latest_daily['ATRr_14'])
                
                # 2. Fetch morning range 1-min data for exact high/low accuracy
                df = kite_scanner.fetch_kite_data(kite, int(token), start_time, end_time, "minute")
                if df.empty or len(df) < 25:
                    return None
                    
                # Format columns
                df.columns = [c.lower() for c in df.columns]
                
                open_915 = df['open'].iloc[0]
                high_945 = df['high'].max()
                low_945 = df['low'].min()
                current_price = df['close'].iloc[-1]
                range_width = high_945 - low_945
                
                if range_width < 0.005 * current_price or range_width > 0.018 * current_price:
                    return None
                    
                pct_change_945 = ((current_price - open_915) / open_915) * 100
                
                classification = "NEUTRAL"
                if current_price > open_915 and ((high_945 - current_price) / range_width) <= 0.15 and pct_change_945 >= 0.75:
                    classification = "STRONG"
                elif current_price < open_915 and ((current_price - low_945) / range_width) <= 0.15 and pct_change_945 <= -0.75:
                    classification = "WEAK"
                    
                if classification in ["STRONG", "WEAK"]:
                    return symbol, {
                        "token": int(token),
                        "open_915": float(open_915),
                        "high_945": float(high_945),
                        "low_945": float(low_945),
                        "pct_change_945": float(pct_change_945),
                        "classification": classification,
                        "daily_50_ema": daily_50_ema,
                        "daily_atr_14": daily_atr_14
                    }
            except Exception as e:
                logging.debug(f"Error processing {symbol}: {e}")
            return None

        # Process parallel
        try:
            from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
            ctx = get_script_run_ctx()
        except ImportError:
            ctx = None

        def process_symbol_with_ctx(sym):
            if ctx:
                add_script_run_ctx(ctx=ctx)
            return process_symbol(sym)

        with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
            results = list(executor.map(process_symbol_with_ctx, fno_symbols))
            
        all_candidates = [r for r in results if r is not None]
        weak_sorted = sorted([c for c in all_candidates if c[1]['classification'] == 'WEAK'], key=lambda x: x[1]['pct_change_945'])[:15]
        strong_sorted = sorted([c for c in all_candidates if c[1]['classification'] == 'STRONG'], key=lambda x: x[1]['pct_change_945'], reverse=True)[:15]
        
        watchlist = {}
        for symbol, data in (weak_sorted + strong_sorted):
            watchlist[symbol] = data
                
        save_watchlist(watchlist)
        logging.info(f"🏆 Curated Top Morning Range Watchlist with {len(watchlist)} stocks ({len(weak_sorted)} WEAK, {len(strong_sorted)} STRONG).")
        return watchlist
    except Exception as e:
        logging.error(f"Error building morning range watchlist: {e}")
        return {}

from core.market_context import check_nifty_trend, is_nifty_bullish, is_nifty_bearish

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
    start_of_day = now.replace(hour=9, minute=15, second=0, microsecond=0)
    watchlist = load_watchlist()
    if not watchlist:
        watchlist = build_morning_watchlist(kite)
        if not watchlist:
            logging.info("No strong or weak stocks found today in morning range.")
            return

    nifty_trend = check_nifty_trend(kite)
    notified = load_notified()
    
    # Check entry cutoff time (11:30 AM)
    entry_cutoff = now.replace(hour=11, minute=30, second=0, microsecond=0)
    if now > entry_cutoff:
        logging.info("⏰ Morning Range entry window closed (past 11:30 AM). Skipping new entries.")
        return

    # Check strategy max active positions limit (7 trades max)
    portfolio_df = paper_trader.get_portfolio()
    if not portfolio_df.empty and 'Strategy' in portfolio_df.columns and 'Status' in portfolio_df.columns:
        mr_active_count = len(portfolio_df[(portfolio_df['Strategy'] == 'Morning Range Str/Wk') & (portfolio_df['Status'] == 'Active')])
        if mr_active_count >= 7:
            logging.info("✋ Max active Morning Range positions (7) reached. Skipping new entries.")
            return

    active_tickers = portfolio_df[portfolio_df['Status'] == 'Active']['Ticker'].tolist() if not portfolio_df.empty else []

    def monitor_stock(item):
        ticker, info = item
        if ticker in notified:
            return {"status": "NOTIFIED", "ticker": ticker}
        if ticker in active_tickers:
            return {"status": "ACTIVE_TRADE", "ticker": ticker}
            
        reasons = []
        try:
            # Fetch today's data up to now in 1-minute resolution for rapid execution
            df = kite_scanner.fetch_kite_data(kite, info["token"], start_of_day, datetime.datetime.now(), "minute")
            if df.empty or len(df) < 15: # Must have sufficient 1m candles post 09:45
                return {"status": "INSUFFICIENT_DATA", "ticker": ticker, "reasons": ["Insufficient 1m candles"]}
                
            df.columns = [c.lower() for c in df.columns]
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            df['vwap'] = calculate_vwap(df)
            
            # Post-09:45 candles
            post_945_df = df.loc[df.index >= watchlist_time]
            if post_945_df.empty:
                return {"status": "NO_POST_945_DATA", "ticker": ticker, "reasons": ["No candles post 9:45 AM"]}
                
            latest_candle = post_945_df.iloc[-1]
            candle_close_1m = latest_candle['close']
            current_price = latest_candle['close']
            current_vwap = latest_candle['vwap']
            
            high_945 = info["high_945"]
            low_945 = info["low_945"]
            mid_point = (high_945 + low_945) / 2
            
            # Calculate volume spike ratio (breakout 1m candle vs 5-candle avg 1m volume)
            volume_ratio = 1.0
            if len(df) >= 7:
                prev_candles = df.iloc[-6:-1]
                avg_volume = prev_candles['volume'].mean()
                latest_volume = latest_candle['volume']
                volume_ratio = latest_volume / avg_volume if avg_volume > 0 else 1.0

            # --- OPTIMIZATION FILTERS ---
            daily_50_ema = info.get("daily_50_ema")
            daily_atr_14 = info.get("daily_atr_14")
            
            # 1. Daily 50 EMA trend filter
            if daily_50_ema:
                if info["classification"] == "STRONG" and current_price < daily_50_ema:
                    reasons.append(f"Price below Daily 50 EMA ({current_price:.1f} < {daily_50_ema:.1f})")
                if info["classification"] == "WEAK" and current_price > daily_50_ema:
                    reasons.append(f"Price above Daily 50 EMA ({current_price:.1f} > {daily_50_ema:.1f})")

            # 2. Daily ATR Exhaustion check (85%)
            today_high = df['high'].max()
            today_low = df['low'].min()
            today_range = today_high - today_low
            if daily_atr_14 and today_range > 0.85 * daily_atr_14:
                reasons.append(f"ATR Exhausted (range {today_range:.1f} > 85% ATR {0.85*daily_atr_14:.1f})")

            # 3. Breakout Candle Body Quality & Wick Rejection Check
            c_high = latest_candle['high']
            c_low = latest_candle['low']
            c_range = c_high - c_low
            if c_range > 0:
                if info["classification"] == "STRONG":
                    body_ratio = (candle_close_1m - c_low) / c_range
                    upper_wick = c_high - max(latest_candle['open'], candle_close_1m)
                    if body_ratio < 0.50:
                        reasons.append(f"Bad candle body quality (body ratio: {body_ratio:.2f} < 0.50)")
                    elif (upper_wick / c_range) > 0.45:
                        reasons.append(f"Rejection wick too long (wick ratio: {upper_wick/c_range:.2f} > 0.45)")
                elif info["classification"] == "WEAK":
                    body_ratio = (c_high - candle_close_1m) / c_range
                    lower_wick = min(latest_candle['open'], candle_close_1m) - c_low
                    if body_ratio < 0.50:
                        reasons.append(f"Bad candle body quality (body ratio: {body_ratio:.2f} < 0.50)")
                    elif (lower_wick / c_range) > 0.45:
                        reasons.append(f"Rejection wick too long (wick ratio: {lower_wick/c_range:.2f} > 0.45)")

            # 4. Tighter Overextension Check (Closeness to VWAP within 1.0%)
            if info["classification"] == "STRONG":
                if current_price > current_vwap * 1.010:
                    reasons.append(f"Overextended from VWAP ({current_price:.1f} > {current_vwap*1.010:.1f})")
            elif info["classification"] == "WEAK":
                if current_price < current_vwap * 0.990:
                    reasons.append(f"Overextended from VWAP ({current_price:.1f} < {current_vwap*0.990:.1f})")

            # 5. RSI Extremes Guardrail (Removed to allow extreme momentum breakouts)

            # 6. Multi-Candle Confirmation (Check previous 1-min candle close)
            prev_candle_close = post_945_df.iloc[-2]['close'] if len(post_945_df) >= 2 else candle_close_1m

            # LONG TRIGGER (STRONG) - 0.15% Level Buffer + 2-Candle Confirmation
            if info["classification"] == "STRONG":
                breakout_level = high_945 * 1.0015
                is_breakout_confirmed = (candle_close_1m >= breakout_level) and (prev_candle_close >= high_945 * 1.0005)
                
                # Check if breakout was attempted (crossed high_945)
                breakout_attempted = current_price >= high_945
                if breakout_attempted:
                    if nifty_trend == "BEARISH":
                        reasons.append("Broad market nifty trend is BEARISH")
                    if not is_breakout_confirmed:
                        reasons.append("Breakout not confirmed by 2 candles")
                    if current_price <= current_vwap:
                        reasons.append("Price below or equal to VWAP")
                    if volume_ratio < 1.2:
                        reasons.append(f"Volume spike too low ({volume_ratio:.2f}x < 1.2x)")
                    
                    if not reasons:
                        # Set SL below both VWAP (with 0.3% buffer) and Mid-point to allow the trade to breathe
                        sl = min(current_vwap * 0.997, mid_point)
                        # Cap SL at max 1.5% risk
                        if (current_price - sl) / current_price > 0.015:
                            sl = current_price * 0.985
                        tp = current_price + (2 * (current_price - sl))
                        return {
                            "status": "TRIGGERED",
                            "ticker": ticker,
                            "token": info["token"],
                            "classification": "STRONG",
                            "signal": "BUY",
                            "entry": float(current_price),
                            "sl": float(sl),
                            "target": float(tp),
                            "volume_ratio": float(volume_ratio)
                        }
                    else:
                        return {"status": "REJECTED_ATTEMPT", "ticker": ticker, "reasons": reasons, "price": current_price, "level": high_945}
                else:
                    return {"status": "NO_BREAKOUT", "ticker": ticker, "reasons": ["Inside 9:45 range"]}
            
            # SHORT TRIGGER (WEAK) - 0.15% Level Buffer + 2-Candle Confirmation
            elif info["classification"] == "WEAK":
                breakdown_level = low_945 * 0.9985
                is_breakdown_confirmed = (candle_close_1m <= breakdown_level) and (prev_candle_close <= low_945 * 0.9995)
                
                # Check if breakdown was attempted (crossed low_945)
                breakdown_attempted = current_price <= low_945
                if breakdown_attempted:
                    if nifty_trend == "BULLISH":
                        reasons.append("Broad market nifty trend is BULLISH")
                    if not is_breakdown_confirmed:
                        reasons.append("Breakdown not confirmed by 2 candles")
                    if current_price >= current_vwap:
                        reasons.append("Price above or equal to VWAP")
                    if volume_ratio < 1.2:
                        reasons.append(f"Volume spike too low ({volume_ratio:.2f}x < 1.2x)")
                    
                    if not reasons:
                        # Set SL above both VWAP (with 0.3% buffer) and Mid-point to allow the trade to breathe
                        sl = max(current_vwap * 1.003, mid_point)
                        # Cap SL at max 1.5% risk
                        if (sl - current_price) / current_price > 0.015:
                            sl = current_price * 1.015
                        tp = current_price - (2 * (sl - current_price))
                        return {
                            "status": "TRIGGERED",
                            "ticker": ticker,
                            "token": info["token"],
                            "classification": "WEAK",
                            "signal": "SELL",
                            "entry": float(current_price),
                            "sl": float(sl),
                            "target": float(tp),
                            "volume_ratio": float(volume_ratio)
                        }
                    else:
                        return {"status": "REJECTED_ATTEMPT", "ticker": ticker, "reasons": reasons, "price": current_price, "level": low_945}
                else:
                    return {"status": "NO_BREAKOUT", "ticker": ticker, "reasons": ["Inside 9:45 range"]}
                    
        except Exception as e:
            logging.warning(f"Error monitoring {ticker}: {e}")
            return {"status": "ERROR", "ticker": ticker, "reasons": [str(e)]}

    try:
        from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
        ctx = get_script_run_ctx()
    except ImportError:
        ctx = None

    def monitor_stock_with_ctx(item):
        if ctx:
            add_script_run_ctx(ctx=ctx)
        return monitor_stock(item)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(monitor_stock_with_ctx, watchlist.items()))
        
    triggered_count = 0
    active_rejections = []
    no_breakout_count = 0
    notified_count = 0
    active_trade_count = 0
    
    for r in results:
        if not r:
            continue
            
        status = r["status"]
        ticker = r["ticker"]
        
        if status == "NOTIFIED":
            notified_count += 1
        elif status == "ACTIVE_TRADE":
            active_trade_count += 1
        elif status == "NO_BREAKOUT":
            no_breakout_count += 1
        elif status == "REJECTED_ATTEMPT":
            active_rejections.append(f"{ticker} (LTP: {r['price']:.1f} vs Level: {r['level']:.1f}) -> {', '.join(r['reasons'])}")
        elif status == "TRIGGERED":
            signal = r["signal"]
            entry = r["entry"]
            sl = r["sl"]
            target = r["target"]
            
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
                    f"Vol Ratio:    {r.get('volume_ratio', 1.0):.2f}x\n"
                    f"============================================================\n"
                )
                
                paper_trader.execute_paper_trade(
                    ticker=ticker,
                    trade_type="Bullish Breakout" if signal == "BUY" else "Bearish Breakdown",
                    entry_price=entry,
                    sl=sl,
                    qty=qty,
                    token=int(r["token"]),
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
                    f"📊 *Classification*: {r['classification']}\n"
                    f"🔊 *Vol Ratio*: {r.get('volume_ratio', 1.0):.2f}x\n"
                )
                telegram_agent.send_message(msg, tel_token, tel_chat_id, parse_mode="Markdown")
                
                notified.add(ticker)
                triggered_count += 1
                
            except Exception as trade_err:
                logging.error(f"Failed to execute trade for {ticker}: {trade_err}")

    # Log clean, detailed summary of this scan cycle
    summary_parts = [f"Monitored {len(watchlist)} stocks"]
    if triggered_count > 0:
        summary_parts.append(f"{triggered_count} triggered")
    if active_trade_count > 0:
        summary_parts.append(f"{active_trade_count} with active trades")
    if notified_count > 0:
        summary_parts.append(f"{notified_count} already notified")
    if no_breakout_count > 0:
        summary_parts.append(f"{no_breakout_count} inside range")
    
    logging.info(f"[Morning Range Scan] {', '.join(summary_parts)}")
    
    if active_rejections:
        logging.info("⚠️ Discarded Breakout/Breakdown Attempts:")
        for rej in active_rejections:
            logging.info(f" ❌ {rej}")

    if triggered_count > 0:
        save_notified(notified)
        
    logging.info("✅ Morning Range Watchlist Scan Complete.")
