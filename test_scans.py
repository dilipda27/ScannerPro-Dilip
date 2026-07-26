import time
import json
import os
import datetime
import logging
from kiteconnect import KiteConnect

# Configure logging to stdout
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_kite_instance():
    session_file = ".kite_session.json"
    if not os.path.exists(session_file):
        logging.error("No active Kite session found.")
        return None
    try:
        with open(session_file, "r") as f:
            session = json.load(f)
        from core import config
        kite = KiteConnect(api_key=config.KITE_API_KEY)
        kite.set_access_token(session["access_token"])
        return kite
    except Exception as e:
        logging.error(f"Kite auth error: {e}")
        return None

def test_scanners():
    kite = get_kite_instance()
    if not kite:
        return
        
    logging.info("Starting diagnostics run...")
    
    # 1. Test Nifty 50 Instrument lookup
    t0 = time.time()
    try:
        from strategies import kite_scanner
        logging.info("Testing get_kite_instruments for NIFTY 50...")
        nifty_map = kite_scanner.get_kite_instruments(kite, ["NIFTY 50"])
        logging.info(f"Nifty 50 Map: {nifty_map} (Took {time.time() - t0:.2f}s)")
    except Exception as e:
        logging.error(f"Error testing get_kite_instruments: {e}")
        
    # 2. Test Failed Breakout Scanner
    t0 = time.time()
    try:
        from strategies import failed_breakout_scanner
        logging.info("Testing Failed Breakout Scanner...")
        res = failed_breakout_scanner.scan_failed_breakouts(kite, progress_callback=lambda p, t, s: logging.info(f"Progress: {p}/{t} - {s}"))
        logging.info(f"Failed Breakout Scanner finished. Found {len(res)} results. (Took {time.time() - t0:.2f}s)")
        if not res.empty:
            logging.info(res.head(2))
    except Exception as e:
        logging.error(f"Error in Failed Breakout Scanner: {e}")

    # 3. Test Bullish Breakout Scanner
    t0 = time.time()
    try:
        from strategies import bullish_breakout_scanner
        logging.info("Testing Bullish Breakout Scanner...")
        res = bullish_breakout_scanner.scan_bullish_breakouts(kite, progress_callback=lambda p, t, s: logging.info(f"Progress: {p}/{t} - {s}"))
        logging.info(f"Bullish Breakout Scanner finished. Found {len(res)} results. (Took {time.time() - t0:.2f}s)")
    except Exception as e:
        logging.error(f"Error in Bullish Breakout Scanner: {e}")

if __name__ == "__main__":
    test_scanners()
