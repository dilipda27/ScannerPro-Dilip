"""
Centralized KiteConnect monkey-patch for connection pool sizing.

Import this module ONCE at application startup (or at the top of your entry point)
to apply the patch globally. Since Python caches modules, the patch runs exactly once
regardless of how many files import this module.

Usage:
    import core.kite_patch  # noqa: F401  (side-effect import)
"""

from kiteconnect import KiteConnect
from requests.adapters import HTTPAdapter

_original_kite_init = KiteConnect.__init__

def _patched_kite_init(self, *args, **kwargs):
    _original_kite_init(self, *args, **kwargs)
    self.timeout = 15  # Sensible default timeout for all Kite API calls
    if hasattr(self, "reqsession"):
        adapter = HTTPAdapter(pool_connections=100, pool_maxsize=100)
        self.reqsession.mount("https://", adapter)
        self.reqsession.mount("http://", adapter)

KiteConnect.__init__ = _patched_kite_init

# --- MONKEY-PATCH FOR GLOBAL AUTHENTICATION ERROR HANDLING ---
import os
import json
import logging
import datetime

_original_request = KiteConnect._request

def _patched_request(self, *args, **kwargs):
    try:
        return _original_request(self, *args, **kwargs)
    except Exception as e:
        error_str = str(e).lower()
        if "incorrect `api_key` or `access_token`" in error_str or "token" in error_str or "api_key" in error_str:
            # Handle authentication failure
            session_file = ".kite_session.json"
            if os.path.exists(session_file):
                try:
                    os.remove(session_file)
                    logging.info("Deleted invalid .kite_session.json session file.")
                except Exception as ex_err:
                    logging.error(f"Failed to delete session file: {ex_err}")
            
            # Clear Streamlit session state credentials if running in Streamlit
            try:
                import streamlit as st
                if st.runtime.exists():
                    st.session_state.kite_access_token = None
                    st.session_state.kite_user_name = None
                    st.session_state.kite_user_id = None
            except Exception:
                pass
            
            # Send Telegram notification (throttled to once per login session)
            try:
                alert_sent_file = os.path.join("data", "state", ".kite_auth_alert_sent")
                if not os.path.exists(alert_sent_file):
                    os.makedirs(os.path.dirname(alert_sent_file), exist_ok=True)
                    with open(alert_sent_file, "w") as f:
                        f.write(f"Alert sent at: {datetime.datetime.now().isoformat()}\nError: {e}")
                    
                    from core import config
                    from services import telegram_agent
                    
                    tel_token = config.TELEGRAM_BOT_TOKEN
                    tel_chat_id = getattr(config, 'TELEGRAM_PERSONAL_CHAT_ID', config.TELEGRAM_CHAT_ID)
                    
                    msg = (
                        f"⚠️ *Kite Session Expired / Invalid* ⚠️\n\n"
                        f"The scanner system encountered an authentication failure:\n"
                        f"`{e}`\n\n"
                        f"Please log in again via the dashboard to resume scanning."
                    )
                    telegram_agent.send_message(msg, tel_token, tel_chat_id, parse_mode="Markdown")
                    logging.info("Sent Kite authentication failure Telegram alert.")
            except Exception as notify_err:
                logging.error(f"Failed to send authentication failure Telegram alert: {notify_err}")
                
        raise e

KiteConnect._request = _patched_request

