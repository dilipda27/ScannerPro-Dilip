import schedule
import time
import json
import os
import logging
import datetime
from kiteconnect import KiteConnect
import config
import kite_scanner
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

def run_automated_orb(scan_label):
    # Check if today is a weekday
    if datetime.datetime.today().weekday() > 4:
        logging.info(f"Skipping {scan_label}: Market is closed today (Weekend).")
        return

    logging.info(f"🚀 Starting Automated ORB Scan: {scan_label}")
    
    # 1. Load Session
    session_file = ".kite_session.json"
    if not os.path.exists(session_file):
        logging.error("No active Kite session found. Please login via the Streamlit dashboard first to generate .kite_session.json")
        return
        
    try:
        with open(session_file, "r") as f:
            session = json.load(f)
            
        kite = KiteConnect(api_key=config.KITE_API_KEY)
        kite.set_access_token(session["access_token"])
        
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
            
        # 6. Dispatch to Telegram
        tel_token = config.TELEGRAM_BOT_TOKEN
        tel_chat_id = config.TELEGRAM_CHAT_ID
        
        summary_text = f"🚨 *{scan_label} ORB Alert*\n\nFound {len(results_df)} stocks breaking their 15-min range!"
        
        if img_path:
            telegram_agent.send_photo(img_path, summary_text, tel_token, tel_chat_id, parse_mode="Markdown")
        
        # Also send the detailed table text
        telegram_agent.send_dataframe(results_df, tel_token, tel_chat_id, scan_name=f"ORB {scan_label}")
        
        logging.info(f"✅ {scan_label} results dispatched to Telegram.")
        
    except Exception as e:
        logging.error(f"Error during automated scan: {e}")

# --- Scheduler Config ---

# 9:31 AM IST - Initial Breakout Scan
schedule.every().day.at("09:31").do(run_automated_orb, scan_label="Initial 9:31 AM")

# 10:00 AM IST - Follow-up Sustainability Scan
schedule.every().day.at("10:00").do(run_automated_orb, scan_label="Sustainability 10:00 AM")

logging.info("🕰️ Scheduler Service Started. Monitoring for 09:31 and 10:00 slots (Mon-Fri)...")

if __name__ == "__main__":
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            logging.error(f"Scheduler loop error: {e}")
        time.sleep(30)
