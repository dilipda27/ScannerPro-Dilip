import os
import json
import logging
import datetime
import threading
import config
import telegram_agent

class BaseStrategy:
    """
    Base Strategy Class consolidating state management, background threading, 
    auto-restarters on startup, and notification dispatching.
    """
    def __init__(self, name, state_file, default_state):
        self.name = name
        self.state_file = state_file
        self.default_state = default_state
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._monitor_thread = None

    def load_state(self):
        """Thread-safe loading of the strategy state JSON file."""
        with self._state_lock:
            if not os.path.exists(self.state_file):
                # Ensure parent directories exist
                os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
                with open(self.state_file, "w") as f:
                    json.dump(self.default_state, f, indent=4)
                return self.default_state.copy()
            try:
                with open(self.state_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"[{self.name}] Error loading state: {e}")
                return self.default_state.copy()

    def save_state(self, state):
        """Thread-safe saving of the strategy state JSON file."""
        with self._state_lock:
            try:
                state["last_update"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with open(self.state_file, "w") as f:
                    json.dump(state, f, indent=4)
            except Exception as e:
                logging.error(f"[{self.name}] Error saving state: {e}")

    def add_notification_to_shared(self, ticker, msg, category="Options"):
        """Appends a UI notification alert to the shared JSON file."""
        SHARED_NOTIFICATIONS_FILE = os.path.join("data", "state", "shared_notifications.json")
        try:
            os.makedirs(os.path.dirname(SHARED_NOTIFICATIONS_FILE), exist_ok=True)
            data = []
            if os.path.exists(SHARED_NOTIFICATIONS_FILE):
                try:
                    with open(SHARED_NOTIFICATIONS_FILE, "r") as f:
                        data = json.load(f)
                except:
                    data = []
            data.append({
                "ticker": ticker,
                "msg": msg,
                "category": category,
                "time": datetime.datetime.now().strftime("%H:%M")
            })
            with open(SHARED_NOTIFICATIONS_FILE, "w") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logging.error(f"[{self.name}] Failed to append shared notification: {e}")

    def send_alert(self, msg):
        """Dispatches an alert to Telegram and appends it to shared notification file."""
        bot_token = getattr(config, 'TELEGRAM_BOT_TOKEN', '')
        chat_id = getattr(config, 'TELEGRAM_PERSONAL_CHAT_ID', '') or getattr(config, 'TELEGRAM_CHAT_ID_INTRADAY', getattr(config, 'TELEGRAM_CHAT_ID', ''))
        if bot_token and chat_id:
            try:
                telegram_agent.send_message(msg, bot_token, chat_id, parse_mode="Markdown")
            except Exception as te:
                logging.error(f"[{self.name}] Telegram send failed: {te}")
        self.add_notification_to_shared(self.name, msg.replace("*", "").replace("`", ""))

    def start_thread(self, kite, monitor_func, thread_name):
        """Starts the background worker monitoring thread if not already running."""
        state = self.load_state()
        
        # Check if thread is already running
        for t in threading.enumerate():
            if t.name == thread_name and t.is_alive():
                return False, f"Strategy thread '{thread_name}' is already running."
        
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=monitor_func, 
            args=(kite,), 
            name=thread_name, 
            daemon=True
        )
        self._monitor_thread.start()
        return True, "Thread started."

    def stop_thread(self):
        """Signals the background thread to exit."""
        self._stop_event.set()

    def init_on_startup(self, kite, monitor_func, thread_name):
        """Automatically restarts the daemon thread during application initialization."""
        state = self.load_state()
        if state.get("is_running"):
            # Check if thread is already active
            for t in threading.enumerate():
                if t.name == thread_name and t.is_alive():
                    return
            logging.info(f"[{self.name}] Auto-restarting background thread on startup...")
            self._stop_event.clear()
            self._monitor_thread = threading.Thread(
                target=monitor_func, 
                args=(kite,), 
                name=thread_name, 
                daemon=True
            )
            self._monitor_thread.start()

    def is_stopped(self):
        """Returns True if the stop event signal has been set."""
        return self._stop_event.is_set()

    def wait(self, seconds):
        """Waits for the specified duration or until stop signal is set."""
        self._stop_event.wait(seconds)
