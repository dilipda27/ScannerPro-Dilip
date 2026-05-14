import schedule
import time
import json
import os
import logging
import datetime
from kiteconnect import KiteConnect
import config
import kite_scanner
import high52_scanner
import telegram_agent
import image_generator

# Setup logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("scheduler.log"),
        logging.StreamHandler()
    ]
)

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
    """Check if today is a weekday and current time is within market hours (9:15-15:30)."""
    now = datetime.datetime.now()
    if now.weekday() > 4: # Weekend
        return False
    market_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
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
        
        if results_df.empty:
            logging.info(f"✅ {scan_label} complete: No breakouts found.")
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
                qty=row['Paper Qty']
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
        
        if results_df.empty:
            return

        # Notify Telegram (Intraday Channel)
        tel_token = config.TELEGRAM_BOT_TOKEN
        tel_chat_id = getattr(config, 'TELEGRAM_CHAT_ID_INTRADAY', config.TELEGRAM_CHAT_ID)
        
        telegram_agent.send_dataframe(results_df, tel_token, tel_chat_id, scan_name="LIVE: 52W High Breakout")
        logging.info(f"🔥 Found {len(results_df)} 52W breakouts. Sent to Telegram.")
        
    except Exception as e:
        logging.error(f"Error during automated 52WH scan: {e}")

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

# --- Scheduler Config ---

# 9:05 AM IST - Morning Cache
schedule.every().day.at("09:05").do(run_morning_cache)

# 9:31 AM IST - Initial Breakout Scan
schedule.every().day.at("09:31").do(run_automated_orb, scan_label="Initial 9:31 AM")

# 10:00 AM IST - Follow-up Sustainability Scan
schedule.every().day.at("10:00").do(run_automated_orb, scan_label="Sustainability 10:00 AM")

# Continuous 52WH Scan (Every 5 minutes between 9:45 and 15:30)
schedule.every(5).minutes.do(run_automated_52wh)

# 3:25 PM IST - Auto Square-off
schedule.every().day.at("15:25").do(run_auto_square_off)

logging.info("🕰️ Scheduler Service Started. Monitoring slots and 3:25 PM Square-off...")

if __name__ == "__main__":
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            logging.error(f"Scheduler loop error: {e}")
        time.sleep(30)
