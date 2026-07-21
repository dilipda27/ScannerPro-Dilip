import threading
import time
import datetime
import os
import json
import logging
import sys
from kiteconnect import KiteConnect

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Persist instance globally in sys namespace for Streamlit hot-reloads
if not hasattr(sys, "_cache_service_instance"):
    sys._cache_service_instance = None
if not hasattr(sys, "_cache_service_thread"):
    sys._cache_service_thread = None
if not hasattr(sys, "_cache_service_enabled"):
    sys._cache_service_enabled = False

class IntradayCacheService:
    def __init__(self):
        self.running = False
        self.status = "Idle"
        self.log_messages = []
        self.completed_tasks = set()
        self.current_task = None
        self.task_list = [
            {"id": "archive", "name": "Archive Yesterday's History", "scheduled_time": "09:00", "run_missed": True},
            {"id": "unified_morning", "name": "Unified Morning Cache (ORB & 52WH)", "scheduled_time": "09:05", "run_missed": True},
            {"id": "bullish_strength", "name": "Full F&O Strength Cache", "scheduled_time": "09:10", "run_missed": True},
            {"id": "bearish_breakdown", "name": "Full Bearish Breakdown Cache", "scheduled_time": "09:10", "run_missed": True},
            {"id": "vcp_caching", "name": "VCP Proximity & Setup validation", "scheduled_time": "09:10", "run_missed": True},
            {"id": "refresh_orb", "name": "Refresh ORB Cache (Today's Open)", "scheduled_time": "09:25", "run_missed": False},
            {"id": "refresh_bullish", "name": "Refresh Bullish Breakout Cache", "scheduled_time": "09:25", "run_missed": False},
            {"id": "refresh_bearish", "name": "Refresh Bearish Breakdown Cache", "scheduled_time": "09:25", "run_missed": False},
            {"id": "refresh_failed", "name": "Refresh Failed Breakout Cache", "scheduled_time": "09:25", "run_missed": False}
        ]

    def log(self, message):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        formatted = f"[{timestamp}] {message}"
        logging.info(f"[CacheService] {message}")
        self.log_messages.append(formatted)
        if len(self.log_messages) > 100:
            self.log_messages.pop(0)

    def get_kite_client(self):
        session_file = ".kite_session.json"
        if not os.path.exists(session_file):
            self.log("❌ Error: Active Kite session file (.kite_session.json) not found.")
            return None
        try:
            with open(session_file, "r") as f:
                session = json.load(f)
            import config
            kite = KiteConnect(api_key=config.KITE_API_KEY)
            kite.set_access_token(session["access_token"])
            return kite
        except Exception as e:
            self.log(f"❌ Error authenticating Kite: {e}")
            return None

    def execute_task(self, task_id, kite):
        self.log(f"🔄 Executing: {task_id}...")
        self.current_task = task_id
        
        # Inline progress callback to output real-time terminal progress
        def cb(processed, total, symbol):
            pct = (processed / total) * 100 if total > 0 else 0
            sys.stdout.write(f"\r[Cache Progress] {processed}/{total} ({pct:.1f}%) | Processing: {symbol:<10}")
            sys.stdout.flush()
            if processed == total:
                sys.stdout.write("\n")
                sys.stdout.flush()
            # Log to internal list less frequently to prevent memory/log bloat
            if processed % 10 == 0 or processed == total:
                self.log(f"Progress: {processed}/{total} ({pct:.1f}%) | Last: {symbol}")

        try:
            if task_id == "archive":
                import paper_trader
                paper_trader.archive_history()
                self.log("✅ Archived yesterday's paper trade history successfully.")
                
            elif task_id == "unified_morning":
                import kite_scanner
                kite_scanner.run_unified_morning_cache(kite, progress_callback=cb)
                self.log("✅ Unified Morning Cache complete.")
                
            elif task_id == "bullish_strength":
                import bullish_breakout_scanner
                bullish_breakout_scanner.cache_bullish_candidates(kite, progress_callback=cb, refresh_only=False)
                self.log("✅ Full F&O Strength Cache complete.")
                
            elif task_id == "bearish_breakdown":
                import bearish_breakdown_scanner
                bearish_breakdown_scanner.cache_bearish_candidates(kite, progress_callback=cb, refresh_only=False)
                self.log("✅ Full Bearish Breakdown Cache complete.")
                
            elif task_id == "vcp_caching":
                import volatility_contraction_scanner
                symbols = volatility_contraction_scanner.fetch_nifty500_symbols()
                volatility_contraction_scanner.run_stage1_proximity_filter(kite, symbols, progress_callback=cb)
                volatility_contraction_scanner.run_stage2_setup_validation(kite, progress_callback=cb)
                self.log("✅ VCP Stage 1 & Stage 2 Cache complete.")
                
            elif task_id == "refresh_orb":
                import kite_scanner
                kite_scanner.cache_orb_stocks(kite, progress_callback=cb, refresh_shortlist_only=True)
                self.log("✅ ORB Cache refreshed with today's open.")
                
            elif task_id == "refresh_bullish":
                import bullish_breakout_scanner
                bullish_breakout_scanner.cache_bullish_candidates(kite, progress_callback=cb, refresh_only=True)
                self.log("✅ Bullish Breakout Cache refreshed.")
                
            elif task_id == "refresh_bearish":
                import bearish_breakdown_scanner
                bearish_breakdown_scanner.cache_bearish_candidates(kite, progress_callback=cb, refresh_only=True)
                self.log("✅ Bearish Breakdown Cache refreshed.")
                
            elif task_id == "refresh_failed":
                import failed_breakout_scanner
                failed_breakout_scanner.cache_failed_candidates(kite, progress_callback=cb, refresh_only=True)
                self.log("✅ Failed Breakout Cache refreshed.")
                
            self.completed_tasks.add(task_id)
            return True
        except Exception as e:
            self.log(f"⚠️ Task {task_id} failed: {e}")
            return False
        finally:
            self.current_task = None

    def run_service_loop(self):
        self.running = True
        self.log("📡 Cache service loop started.")
        
        while self.running and sys._cache_service_enabled:
            # 1. Get Kite client
            kite = self.get_kite_client()
            if not kite:
                self.status = "Authentication Failure"
                time.sleep(10)
                continue
            
            # Check if weekend
            now = datetime.datetime.now()
            if now.weekday() > 4:
                self.status = "Idle (Weekend)"
                self.log("Market closed (Weekend). Caching service idle.")
                time.sleep(300)
                continue
                
            current_time_str = now.strftime("%H:%M")
            self.status = f"Running (Active) | Time: {current_time_str}"
            
            # Check each task
            any_task_run = False
            for task in self.task_list:
                task_id = task["id"]
                scheduled_time_str = task["scheduled_time"]
                
                # Parse scheduled time
                sched_h, sched_m = map(int, scheduled_time_str.split(":"))
                scheduled_time = now.replace(hour=sched_h, minute=sched_m, second=0, microsecond=0)
                
                # Check if it has been run today
                if task_id in self.completed_tasks:
                    continue
                    
                # Should we run it?
                # Case A: Current time is past scheduled time, and task allows running missed/baseline setup
                # Case B: Current time matches the exact scheduled time (within a 2 min window)
                time_diff = (now - scheduled_time).total_seconds()
                
                if (time_diff >= 0 and task["run_missed"]) or (0 <= time_diff <= 120):
                    self.status = f"Executing: {task['name']}"
                    self.execute_task(task_id, kite)
                    any_task_run = True
                    break # Break to loop again with updated time
            
            # Check if all tasks for today are completed
            all_done = True
            for task in self.task_list:
                if task["id"] not in self.completed_tasks:
                    # If it's a refresh task, and we are way past 9:30 AM (e.g. 10:00 AM) and it wasn't run,
                    # we should skip it since refresh is only meant for early morning open momentum.
                    # We mark it complete so we don't try to run it in the afternoon.
                    scheduled_time_str = task["scheduled_time"]
                    sched_h, sched_m = map(int, scheduled_time_str.split(":"))
                    scheduled_time = now.replace(hour=sched_h, minute=sched_m, second=0, microsecond=0)
                    
                    if not task["run_missed"] and (now - scheduled_time).total_seconds() > 3600:
                        self.log(f"⏰ Skipping refresh task '{task['name']}': past open window (>1hr missed).")
                        self.completed_tasks.add(task["id"])
                        continue
                    all_done = False
            
            if all_done:
                self.status = "Completed for Today"
                self.log("🏁 All intraday caching and refreshes completed for today. Going idle.")
                try:
                    print_caching_summary()
                except Exception as e:
                    logging.error(f"Error printing caching summary: {e}")
                self.running = False
                sys._cache_service_enabled = False
                break
                
            if not any_task_run:
                # Idle wait for next scheduled slot
                self.status = f"Idle (Waiting) | Time: {current_time_str}"
                time.sleep(30)
                
        self.running = False
        self.status = "Ended / Stopped"
        self.log("🔴 Cache service loop ended.")

def print_caching_summary():
    """Prints a summary in the log/terminal of the number of stocks fetched/cached for each scanner."""
    print("\n" + "="*65)
    print("📋 INTRADAY CACHING SUMMARY (STOCKS CACHED)")
    print("="*65)
    
    cache_files = {
        "15-Min ORB Breakout": os.path.join("data", "cache", "orb_trending_cache.csv"),
        "52-Week High Breakout": os.path.join("data", "cache", "high52_cache.csv"),
        "15-Min Bearish Breakdown": os.path.join("data", "cache", "bearish_breakdown_cache.csv"),
        "15-Min Bullish Breakout / Failed Breakout": os.path.join("data", "cache", "fno_strength_cache.csv")
    }
    
    for name, path in cache_files.items():
        count = 0
        if os.path.exists(path):
            try:
                df = pd.read_csv(path)
                count = len(df)
            except Exception:
                pass
        logging.info(f"[Cache Summary] {name}: {count} stocks cached")
        print(f" 🔹 {name:<45} : {count} stocks")

    # VCP Specific Cache Files
    vcp_prox_path = os.path.join("data", "cache", "proximity_filter_cache.json")
    vcp_watch_path = os.path.join("data", "cache", "volatility_contraction_watchlist.json")
    
    vcp_prox_count = 0
    if os.path.exists(vcp_prox_path):
        try:
            with open(vcp_prox_path, "r") as f:
                data = json.load(f)
                vcp_prox_count = len(data)
        except Exception:
            pass
            
    vcp_watch_count = 0
    if os.path.exists(vcp_watch_path):
        try:
            with open(vcp_watch_path, "r") as f:
                data = json.load(f)
                vcp_watch_count = len(data)
        except Exception:
            pass
            
    mr_watch_path = os.path.join("data", "state", ".morning_range_watchlist.json")
    mr_watch_count = 0
    if os.path.exists(mr_watch_path):
        try:
            with open(mr_watch_path, "r") as f:
                data = json.load(f)
                if isinstance(data, dict) and "watchlist" in data:
                    mr_watch_count = len(data["watchlist"])
        except Exception:
            pass

    logging.info(f"[Cache Summary] VCP Proximity Filter: {vcp_prox_count} stocks cached")
    logging.info(f"[Cache Summary] VCP Setup watchlist: {vcp_watch_count} stocks cached")
    logging.info(f"[Cache Summary] Morning Range Watchlist: {mr_watch_count} stocks cached")
    print(f" 🔹 VCP Proximity Filter                         : {vcp_prox_count} stocks")
    print(f" 🔹 VCP Setup Watchlist                          : {vcp_watch_count} stocks")
    print(f" 🔹 Morning Range Watchlist                      : {mr_watch_count} stocks")
    print("="*65 + "\n")


def start_service():
    """Starts the caching service in a background daemon thread."""
    if sys._cache_service_enabled and sys._cache_service_instance and sys._cache_service_instance.running:
        return True, "Service is already running."
        
    sys._cache_service_enabled = True
    sys._cache_service_instance = IntradayCacheService()
    
    def run():
        sys._cache_service_instance.run_service_loop()
        
    sys._cache_service_thread = threading.Thread(target=run, name="cache_service_thread", daemon=True)
    sys._cache_service_thread.start()
    return True, "Intraday Caching Service started in the background."

def stop_service():
    """Stops the caching service."""
    sys._cache_service_enabled = False
    if sys._cache_service_instance:
        sys._cache_service_instance.running = False
        sys._cache_service_instance.status = "Stopped"
        sys._cache_service_instance.log("Service stop requested by user.")
        return True, "Service stop requested."
    return False, "Service is not running."

def get_service_status():
    """Returns the current status, logs, and task details of the service."""
    running = sys._cache_service_enabled and sys._cache_service_instance is not None and sys._cache_service_instance.running
    status_str = sys._cache_service_instance.status if sys._cache_service_instance else "Offline / Idle"
    logs = sys._cache_service_instance.log_messages if sys._cache_service_instance else []
    completed = list(sys._cache_service_instance.completed_tasks) if sys._cache_service_instance else []
    task_list = sys._cache_service_instance.task_list if sys._cache_service_instance else []
    current_task = sys._cache_service_instance.current_task if sys._cache_service_instance else None
    
    return {
        "running": running,
        "status": status_str,
        "logs": logs,
        "completed_tasks": completed,
        "task_list": task_list,
        "current_task": current_task
    }
