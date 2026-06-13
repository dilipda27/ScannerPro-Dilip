import schedule
import time
import json
import os
import logging
import datetime
from kiteconnect import KiteConnect
from requests.adapters import HTTPAdapter

# Global patch to increase requests connection pool size for multi-threading stability
_original_kite_init = KiteConnect.__init__
def _patched_kite_init(self, *args, **kwargs):
    _original_kite_init(self, *args, **kwargs)
    if hasattr(self, "reqsession"):
        adapter = HTTPAdapter(pool_connections=100, pool_maxsize=100)
        self.reqsession.mount("https://", adapter)
        self.reqsession.mount("http://", adapter)
KiteConnect.__init__ = _patched_kite_init
import config
import kite_scanner
import high52_scanner
import bearish_vwap_rejection_scanner
import telegram_agent
import image_generator

# Setup logging
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Clear existing handlers to prevent duplicate logs or basicConfig overrides
for handler in list(root_logger.handlers):
    root_logger.removeHandler(handler)

formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# File handler for scheduler.log
file_handler = logging.FileHandler("scheduler.log", encoding="utf-8")
file_handler.setFormatter(formatter)
root_logger.addHandler(file_handler)

# Stream handler for console output
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)
root_logger.addHandler(stream_handler)

def get_kite_instance():
    """Helper to initialize Kite from saved session."""
    session_file = ".kite_session.json"
    if not os.path.exists(session_file):
        logging.error("No active Kite session found. Please login via dashboard.")
        return None
    try:
        with open(session_file, "r") as f:
            session = json.load(f)
        kite = KiteConnect(api_key=config.KITE_API_KEY)
        kite.set_access_token(session["access_token"])
        return kite
    except Exception as e:
        logging.error(f"Kite auth error: {e}")
        return None

def is_market_open():
    """Check if today is a weekday and current time is within market hours (9:15-14:45)."""
    now = datetime.datetime.now()
    if now.weekday() > 4: # Weekend
        return False
    market_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_end = now.replace(hour=14, minute=45, second=0, microsecond=0)
    return market_start <= now <= market_end

def run_automated_orb(scan_label):
    if datetime.datetime.today().weekday() > 4:
        logging.info(f"Skipping {scan_label}: Weekend.")
        return

    logging.info(f"🚀 Starting Automated ORB Scan: {scan_label}")
    kite = get_kite_instance()
    if not kite:
        return
        
    try:
        
        # 2. Run Scan
        results_df = kite_scanner.scan_orb_setups(kite)
        
        # Check active portfolio and filter out existing tickers
        import paper_trader
        portfolio_df = paper_trader.get_portfolio()
        active_tickers = portfolio_df[portfolio_df['Status'] == 'Active']['Ticker'].tolist() if not portfolio_df.empty else []
        results_df = results_df[~results_df['Ticker'].isin(active_tickers)]
        
        if results_df.empty:
            logging.info(f"✅ {scan_label} complete: All breakout candidates already have active trades running.")
            return
            
        # 3. Sort by Volume Spike
        if 'Vol Spike' in results_df.columns:
            results_df = results_df.sort_values(by='Vol Spike', ascending=False)
            
        # 4. Generate Infographic
        img_path = image_generator.create_infographic(results_df, scan_name=f"ORB {scan_label}")
            
        # 5. Execute Paper Trades
        import paper_trader
        for _, row in results_df.iterrows():
            paper_trader.execute_paper_trade(
                ticker=row['Ticker'],
                trade_type=row['Breakout'],
                entry_price=row['Breakout Price'],
                sl=row['Paper SL'],
                qty=row['Paper Qty'],
                strategy="15-Min ORB"
            )
            
        # 6. Dispatch to Telegram (Intraday Channel)
        tel_token = config.TELEGRAM_BOT_TOKEN
        tel_chat_id = getattr(config, 'TELEGRAM_CHAT_ID_INTRADAY', config.TELEGRAM_CHAT_ID)
        
        summary_text = f"🚨 *{scan_label} ORB Alert*\n\nFound {len(results_df)} stocks breaking their 15-min range!"
        
        if img_path:
            telegram_agent.send_photo(img_path, summary_text, tel_token, tel_chat_id, parse_mode="Markdown")
        
        # Also send the detailed table text
        telegram_agent.send_dataframe(results_df, tel_token, tel_chat_id, scan_name=f"ORB {scan_label}")
        
        logging.info(f"✅ {scan_label} results dispatched to Telegram.")
        
    except Exception as e:
        logging.error(f"Error during automated ORB scan: {e}")

def run_automated_52wh():
    """Run the 52-Week High Breakout scanner automated."""
    if not is_market_open():
        return

    logging.info("📡 Running Automated 52-Week High Scan...")
    kite = get_kite_instance()
    if not kite:
        return

    try:
        results_df = high52_scanner.scan_52w_breakouts(kite, only_closed_candles=True)
        
        # Check active portfolio and filter out existing tickers
        import paper_trader
        portfolio_df = paper_trader.get_portfolio()
        active_tickers = portfolio_df[portfolio_df['Status'] == 'Active']['Ticker'].tolist() if not portfolio_df.empty else []
        results_df = results_df[~results_df['Ticker'].isin(active_tickers)]
        
        if results_df.empty:
            return

        # Notify Telegram (Intraday Channel)
        tel_token = config.TELEGRAM_BOT_TOKEN
        tel_chat_id = getattr(config, 'TELEGRAM_CHAT_ID_INTRADAY', config.TELEGRAM_CHAT_ID)
        
        telegram_agent.send_dataframe(results_df, tel_token, tel_chat_id, scan_name="LIVE: 52W High Breakout")
        logging.info(f"🔥 Found {len(results_df)} 52W breakouts. Sent to Telegram.")
        
    except Exception as e:
        logging.error(f"Error during automated 52WH scan: {e}")

def run_automated_bearish_vwap_rejection():
    """Run the Bearish VWAP Rejection scanner automated."""
    if not is_market_open():
        return

    logging.info("📡 Running Automated Bearish VWAP Rejection Scan...")
    kite = get_kite_instance()
    if not kite:
        return

    try:
        triggered_df, monitored_df = bearish_vwap_rejection_scanner.scan_bearish_vwap_rejections(kite)
        
        if triggered_df.empty:
            return

        import notification_helper
        new_tickers = notification_helper.filter_new_tickers("BEARISH_VWAP_REJECTION", triggered_df['Ticker'].tolist())
        
        if not new_tickers:
            return
            
        new_df = triggered_df[triggered_df['Ticker'].isin(new_tickers)]
        
        # Check active portfolio and filter out existing tickers
        portfolio_df = paper_trader.get_portfolio()
        active_tickers = portfolio_df[portfolio_df['Status'] == 'Active']['Ticker'].tolist() if not portfolio_df.empty else []
        new_df = new_df[~new_df['Ticker'].isin(active_tickers)]
        
        if new_df.empty:
            return
        
        # Notify Telegram (Intraday Channel)
        tel_token = config.TELEGRAM_BOT_TOKEN
        tel_chat_id = getattr(config, 'TELEGRAM_CHAT_ID_INTRADAY', config.TELEGRAM_CHAT_ID)
        
        import kite_scanner
        import paper_trader
        
        for _, row in new_df.iterrows():
            # Format and send signal with chart
            msg = (
                f"📉 *Bearish VWAP Rejection Alert* 📉\\n\\n"
                f"🎯 *Ticker*: {row['Ticker']}\\n"
                f"🔴 *Entry (Short)*: ₹{row['Price']}\\n"
                f"🛡️ *Stop Loss*: ₹{row['SL']}\\n"
                f"🟢 *Target 1 (1.5R)*: ₹{row['Target_1']}\\n"
                f"🟢 *Target 2 (3.0R)*: ₹{row['Target_2']}\\n"
                f"📊 *Pattern*: {row['Pattern']}\\n"
                f"🛡️ *Zone*: {row['Zone']} Rejection\\n"
                f"📈 *Risk/Reward*: {row['Risk_Reward']}\\n"
            )
            
            # Fetch chart data for visual context
            try:
                df_chart = kite_scanner.fetch_kite_data(
                    kite, int(row['Token']), 
                    datetime.datetime.now() - datetime.timedelta(days=2), 
                    datetime.datetime.now(), 
                    "5minute"
                )
                telegram_agent.send_signal_with_chart(
                    row['Ticker'], msg, df_chart, 
                    tel_token, tel_chat_id, 
                    "Bearish VWAP Rejection",
                    row_data=row
                )
            except Exception as chart_err:
                logging.error(f"Failed to fetch/send chart for {row['Ticker']}: {chart_err}")
                # Fallback to plain message
                telegram_agent.send_message(msg, tel_token, tel_chat_id)
            
            # Execute Paper Trades
            try:
                capital = 250000 # Default capital per trade
                qty = int(capital / row['Price'])
                
                paper_trader.execute_paper_trade(
                    ticker=row['Ticker'],
                    trade_type="Bearish Pullback",
                    entry_price=row['Price'],
                    sl=row['SL'],
                    qty=qty,
                    token=int(row['Token']),
                    strategy="Bearish VWAP Rejection"
                )
            except Exception as trade_err:
                logging.error(f"Failed to execute auto-trade for {row['Ticker']}: {trade_err}")
            
        logging.info(f"🔥 Found {len(new_df)} Bearish VWAP Rejections. Dispatched to Telegram & Auto-Traded.")
        notification_helper.mark_as_notified("BEARISH_VWAP_REJECTION", new_tickers)
        
    except Exception as e:
        logging.error(f"Error during automated Bearish VWAP Rejection scan: {e}")

def run_morning_cache():
    """Pre-calculate data for both scanners and archive yesterday's history at 9:05 AM."""
    if datetime.datetime.today().weekday() > 4:
        return

    logging.info("🧹 Archiving history and running morning caching...")
    
    try:
        import paper_trader
        paper_trader.archive_history()
    except Exception as e:
        logging.error(f"Error archiving history: {e}")

    kite = get_kite_instance()
    if not kite:
        return

    try:
        # Run the ULTRA-OPTIMIZED unified cache (Handles both ORB and 52W High)
        kite_scanner.run_unified_morning_cache(kite)
        logging.info("✅ Morning tasks complete (Archiving + Unified Caching).")
    except Exception as e:
        logging.error(f"Error during automated morning tasks: {e}")

def run_auto_square_off():
    """Square off all open intraday trades at 3:25 PM."""
    if datetime.datetime.today().weekday() > 4:
        return

    logging.info("🚪 Starting Auto Square-off at 3:25 PM...")
    kite = get_kite_instance()
    if not kite:
        return

    try:
        import paper_trader
        portfolio = paper_trader.get_portfolio()
        if portfolio.empty or not (portfolio['Status'] == 'OPEN').any():
            logging.info("No open intraday trades to square off.")
            return

        open_tickers = portfolio[portfolio['Status'] == 'OPEN']['Ticker'].tolist()
        for ticker in open_tickers:
            paper_trader.exit_trade(ticker, kite)
            
        logging.info(f"✅ Successfully squared off {len(open_tickers)} trades.")
    except Exception as e:
        logging.error(f"Error during auto square-off: {e}")

def run_ai_position_advisor():
    """Periodically monitors active positions, asks Gemini AI for conviction recommendations, and sends them to Telegram."""
    import ai_advisor
    if not ai_advisor.is_ai_advisor_enabled():
        return
        
    now = datetime.datetime.now()
    if now.weekday() > 4: # Weekend
        return
        
    # Time filter: 9:45 AM to 2:45 PM
    current_time = now.time()
    start_time = datetime.time(9, 45)
    end_time = datetime.time(14, 45)
    if not (start_time <= current_time <= end_time):
        return
        
    # Get Gemini key
    gemini_key = getattr(config, 'GEMINI_API_KEY', '')
    if not gemini_key:
        logging.error("AI Advisor: GEMINI_API_KEY is not configured in config.py.")
        return
        
    logging.info("📡 Running AI Active Positions Advisor...")
    kite = get_kite_instance()
    if not kite:
        return
        
    try:
        import paper_trader
        portfolio_df = paper_trader.update_portfolio_pnl(kite)
        
        if portfolio_df.empty:
            logging.info("AI Advisor: Portfolio is empty.")
            return
            
        active_df = portfolio_df[portfolio_df['Status'] == 'Active'].copy()
        if active_df.empty:
            logging.info("AI Advisor: No active positions to analyze.")
            return
            
        chart_summaries = []
        for _, row in active_df.iterrows():
            ticker = row['Ticker']
            token = int(row['Token'])
            
            # Fetch recent 5m candle data for context
            candle_str = ""
            try:
                # Fetch last 4 hours of data to have sufficient context
                df_chart = kite_scanner.fetch_kite_data(
                    kite, token,
                    datetime.datetime.now() - datetime.timedelta(hours=4),
                    datetime.datetime.now(),
                    "5minute"
                )
                if not df_chart.empty:
                    last_candles = df_chart.tail(12).copy()
                    for t, c in last_candles.iterrows():
                        vwap_val = f"₹{c['vwap']:.2f}" if 'vwap' in c else "N/A"
                        candle_str += f"- {t.strftime('%H:%M')} | O: ₹{c['open']:.2f} | H: ₹{c['high']:.2f} | L: ₹{c['low']:.2f} | C: ₹{c['close']:.2f} | VWAP: {vwap_val}\n"
                else:
                    candle_str = "No recent candle data available."
            except Exception as e:
                candle_str = f"Error fetching candle data: {e}"
                
            qty = float(row['Qty'])
            entry = float(row['EntryPrice'])
            ltp = float(row['Current Price'])
            pnl = float(row['Live P&L'])
            net_pnl = float(row['Net P&L'])
            pnl_pct = (pnl / (entry * qty) * 100) if entry > 0 and qty > 0 else 0.0
            
            chart_summaries.append({
                "ticker": ticker,
                "strategy": row['Strategy'],
                "type": row['Type'],
                "entry": entry,
                "sl": float(row['SL']),
                "qty": qty,
                "ltp": ltp,
                "pnl": pnl,
                "net_pnl": net_pnl,
                "pnl_pct": pnl_pct,
                "recent_candles": candle_str
            })
            
        # Construct the detailed quantitative prompt for Gemini
        prompt = """
You are an elite quantitative trading supervisor. Analyze my currently active running positions and provide direct, actionable suggestions (HOLD, EXIT NOW, TRAIL SL, or TAKE PARTIAL PROFIT) for each trade.
Analyze each trade by evaluating its entry price, stop-loss price, current price (LTP), P&L percentage, and its relationship to the recent 5-minute candles and VWAP level.

Here is the live portfolio data:
"""
        for item in chart_summaries:
            prompt += f"""
---
🎯 Ticker: {item['ticker']}
🛡️ Strategy: {item['strategy']} ({item['type']})
📥 Entry Price: ₹{item['entry']:.2f} | SL: ₹{item['sl']:.2f} | Qty: {item['qty']}
📈 Current Price (LTP): ₹{item['ltp']:.2f}
💰 Live P&L: ₹{item['pnl']:.2f} ({item['pnl_pct']:.2f}%) | Net P&L (inc charges): ₹{item['net_pnl']:.2f}
📊 Recent 5m Candle History (IST):
{item['recent_candles']}
"""
            
        prompt += """
Based on the data above, provide a professional, direct, and concise opinion for each ticker.
Use this format exactly (do not include introductory greetings or disclaimers, get straight to the point):

🤖 **[Ticker] ({strategy}) - AI Suggestion**
* **Current State**: [Describe what the stock is doing, e.g. consolidating above entry, facing rejection at VWAP, or trending close to SL]
* **Suggestion**: **[HOLD / EXIT NOW / TRAIL SL TO ₹XXXX / TAKE PARTIAL PROFIT]** (Provide a firm, bold recommendation)
* **Rationale**: [Brief 1-2 sentence technical explanation linking the recent candle trend, VWAP position, and P&L to the suggested action]
"""
        
        # Call Gemini AI
        import ai_advisor
        ai_opinion = ai_advisor.analyze_active_positions(prompt, gemini_key)
        
        # Send to Telegram (Intraday Channel)
        tel_token = config.TELEGRAM_BOT_TOKEN
        tel_chat_id = getattr(config, 'TELEGRAM_CHAT_ID_INTRADAY', config.TELEGRAM_CHAT_ID)
        
        telegram_message = f"🤖 **AI Active Positions Advisor** 🤖\n\n{ai_opinion}"
        telegram_agent.send_message(telegram_message, tel_token, tel_chat_id, parse_mode="Markdown")
        logging.info("✅ AI Positions report dispatched to Telegram.")
        
    except Exception as e:
        logging.error(f"Error in run_ai_position_advisor: {e}")

def send_daily_swing_report():
    """Compiles and sends daily swing portfolio report with stats, P&L, ROI%, and equity curve chart at 3:15 PM."""
    if datetime.datetime.today().weekday() > 4:
        logging.info("Skipping Swing Report: Weekend.")
        return

    logging.info("📊 Compiling Daily Swing Trades Report...")
    kite = get_kite_instance()
    if not kite:
        logging.error("Failed to get Kite client for swing report.")
        return

    try:
        import pandas as pd
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import paper_trader
        
        # 1. Fetch swing trades
        swing_df = paper_trader.update_swing_portfolio(kite)
        active_swing = pd.DataFrame()
        if not swing_df.empty:
            active_swing = swing_df[swing_df['Status'] == 'OPEN'].copy()

        # 2. Fetch Archived stats
        total_realized_swing = 0
        total_invested_archive = 0
        archive_roi_pct = 0
        
        if os.path.exists(paper_trader.SWING_ARCHIVE_FILE):
            archive_df = pd.read_csv(paper_trader.SWING_ARCHIVE_FILE)
            if not archive_df.empty:
                total_realized_swing = archive_df['Net P&L'].sum()
                total_invested_archive = (archive_df['EntryPrice'] * archive_df['Qty']).sum()
                archive_roi_pct = (total_realized_swing / total_invested_archive * 100) if total_invested_archive > 0 else 0

        # 3. Calculate active stats
        total_active_investment = 0
        active_day_pnl = 0
        active_total_pnl = 0
        active_pnl_pct = 0
        
        position_status_details = ""
        if not active_swing.empty:
            total_active_investment = (active_swing['EntryPrice'] * active_swing['Qty']).sum()
            active_day_pnl = active_swing['Day P&L'].sum()
            active_total_pnl = active_swing['Live P&L'].sum()
            active_pnl_pct = (active_total_pnl / total_active_investment * 100) if total_active_investment > 0 else 0
            
            for _, row in active_swing.iterrows():
                pnl_sign = "+" if row['Net P&L'] >= 0 else ""
                position_status_details += f"📌 *{row['Ticker']}*: Qty {row['Qty']} | Entry: ₹{row['EntryPrice']:.2f} | LTP: ₹{row['Current Price']:.2f} | P&L: {pnl_sign}₹{row['Net P&L']:.2f} ({row['Return %']:.2f}%)\n"
        else:
            position_status_details = "_No active positions._\n"

        # Overall Net P&L (Active + Realized)
        net_swing_pnl = total_realized_swing + active_total_pnl
        net_pnl_sign = "+" if net_swing_pnl >= 0 else ""

        # 4. Generate Equity Curve Chart
        swing_curve = paper_trader.get_swing_equity_curve(kite)
        chart_filename = "swing_equity_curve.png"
        has_chart = False
        
        if not swing_curve.empty and len(swing_curve) > 1:
            # Generate the plot
            plt.figure(figsize=(10, 5))
            plt.plot(swing_curve['Date'], swing_curve['Cumulative P&L'], color='#10b981', linewidth=2.5, marker='o', markersize=5)
            plt.fill_between(swing_curve['Date'], swing_curve['Cumulative P&L'], color='#10b981', alpha=0.1)
            plt.title("Lifetime Swing Equity Curve", fontsize=14, fontweight='bold', pad=15)
            plt.xlabel("Date", fontsize=11)
            plt.ylabel("Cumulative P&L (₹)", fontsize=11)
            plt.grid(True, linestyle='--', alpha=0.5)
            plt.xticks(rotation=45)
            plt.tight_layout()
            plt.savefig(chart_filename, dpi=300)
            plt.close()
            has_chart = True

        # 5. Format Telegram Message
        active_day_sign = "+" if active_day_pnl >= 0 else ""
        active_total_sign = "+" if active_total_pnl >= 0 else ""
        realized_sign = "+" if total_realized_swing >= 0 else ""

        msg = (
            f"📊 *Daily Swing Trades Report (3:15 PM)* 📊\n\n"
            f"📈 *Active Swing Positions:*\n"
            f"• Total Investment: ₹{total_active_investment:,.2f}\n"
            f"• Day's P&L: {active_day_sign}₹{active_day_pnl:,.2f}\n"
            f"• Total P&L: {active_total_sign}₹{active_total_pnl:,.2f} ({active_pnl_pct:.2f}%)\n\n"
            f"📚 *Swing Trade Archive (Realized):*\n"
            f"• Total Realized P&L: {realized_sign}₹{total_realized_swing:,.2f}\n"
            f"• Overall Strategy ROI: {archive_roi_pct:.2f}%\n\n"
            f"💼 *Overall Swing Performance:*\n"
            f"• Net Swing P&L (Active + Realized): {net_pnl_sign}₹{net_swing_pnl:,.2f}\n\n"
            f"📝 *Position Details:*\n"
            f"{position_status_details}"
        )

        tel_token = config.TELEGRAM_BOT_TOKEN
        tel_chat_id = config.TELEGRAM_CHAT_ID # Send to primary swing channel
        
        if has_chart and os.path.exists(chart_filename):
            telegram_agent.send_photo(chart_filename, msg, tel_token, tel_chat_id, parse_mode="Markdown")
            try:
                os.remove(chart_filename) # Cleanup local image
            except: pass
        else:
            telegram_agent.send_message(msg, tel_token, tel_chat_id, parse_mode="Markdown")
            
        logging.info("✅ Daily swing report sent to Telegram.")
    except Exception as e:
        logging.error(f"Error compiling/sending swing report: {e}")

def run_automated_315_swing():
    """Runs 3:15 PM Swing Strategy, runs Gemini AI Advisor, executes finalized paper trades, sends swing report, and terminates the service."""
    if datetime.datetime.today().weekday() > 4:
        logging.info("Skipping Swing Strategy: Weekend.")
        return

    logging.info("🚀 Starting Automated 3:15 PM Swing Strategy...")
    kite = get_kite_instance()
    if not kite:
        logging.error("Failed to get Kite client for 3:15 PM Swing setup.")
        return

    gemini_key = getattr(config, 'GEMINI_API_KEY', '')
    if not gemini_key:
        logging.error("AI Advisor: GEMINI_API_KEY is not configured in config.py.")
        return

    try:
        # 1. Scan for candidates
        results_df = kite_scanner.scan_315_setups(kite)
        if results_df.empty:
            logging.info("No swing candidates found today.")
        else:
            # 2. Run AI Advisor Conviction picks
            import ai_advisor
            ai_opinion = ai_advisor.analyze_stocks(results_df, gemini_key, strategy_name="3:15 PM Swing Setup")
            logging.info(f"AI Opinion received:\n{ai_opinion}")
            
            # 3. Extract finalized tickers
            candidates_tickers = results_df['Ticker'].tolist()
            finalized_tickers = []
            for ticker in candidates_tickers:
                for line in ai_opinion.split('\n'):
                    if "Pick" in line and ticker.upper() in line.upper():
                        finalized_tickers.append(ticker)
                        break
                        
            logging.info(f"Finalized Swing Tickers: {finalized_tickers}")
            
            # 4. Execute Swing Trades
            import paper_trader
            executed_trades = []
            for ticker in finalized_tickers:
                row = results_df[results_df['Ticker'] == ticker].iloc[0]
                qty = round(100000 / row['LTP']) if row['LTP'] > 0 else 0
                if qty > 0:
                    success = paper_trader.execute_swing_trade(
                        ticker=row['Ticker'],
                        entry_price=row['LTP'],
                        target=row['Target'],
                        sl=row['Stop Loss'],
                        qty=qty,
                        token=int(row['Token'])
                    )
                    if success:
                        executed_trades.append(f"🟢 Executed Swing Trade: {ticker} (Qty {qty} @ ₹{row['LTP']:.2f})")
                        
            # 5. Dispatch Telegram Summary
            tel_token = config.TELEGRAM_BOT_TOKEN
            tel_chat_id = config.TELEGRAM_CHAT_ID # Swing channel
            
            trades_text = "\n".join(executed_trades) if executed_trades else "No trades executed."
            report_msg = (
                f"🎯 *Automated 3:15 PM Swing Execution* 🎯\n\n"
                f"🤖 *AI Conviction Opinion:*\n{ai_opinion}\n\n"
                f"💼 *Trades Executed:*\n{trades_text}"
            )
            telegram_agent.send_message(report_msg, tel_token, tel_chat_id, parse_mode="Markdown")
            
        # 5. Send daily swing report
        try:
            send_daily_swing_report()
        except Exception as report_err:
            logging.error(f"Failed to send daily swing report: {report_err}")
            
        # 6. Stop the service process
        logging.info("🚪 Terminating scheduler service process after swing execution.")
        import os
        os._exit(0)
        
    except Exception as e:
        logging.error(f"Error in automated 3:15 PM Swing strategy: {e}")
        import os
        os._exit(0)

# --- Scheduler Config ---
# 9:05 AM IST - Morning Cache
schedule.every().day.at("09:05").do(run_morning_cache)

# 3:15 PM IST - Automated Swing Setup & Report
schedule.every().day.at("15:15").do(run_automated_315_swing)

# 9:31 AM IST - Initial Breakout Scan
schedule.every().day.at("09:31").do(run_automated_orb, scan_label="Initial 9:31 AM")

# 10:00 AM IST - Follow-up Sustainability Scan
schedule.every().day.at("10:00").do(run_automated_orb, scan_label="Sustainability 10:00 AM")

# Continuous 52WH Scan (Every 5 minutes between 9:45 and 14:45)
schedule.every(5).minutes.do(run_automated_52wh)

# Continuous Bearish VWAP Rejection Scan (Every 5 minutes between 9:45 and 14:45)
schedule.every(5).minutes.do(run_automated_bearish_vwap_rejection)

# AI Advisor Active Positions monitor (Every 10 minutes between 9:45 AM and 2:45 PM)
schedule.every(10).minutes.do(run_ai_position_advisor)

# 3:25 PM IST - Auto Square-off
schedule.every().day.at("15:25").do(run_auto_square_off)

logging.info("🕰️ Scheduler Service Started. Monitoring slots, AI position analysis, and 3:25 PM Square-off...")

if __name__ == "__main__":
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            logging.error(f"Scheduler loop error: {e}")
        time.sleep(30)
