import os
import json
from kiteconnect import KiteConnect
from requests.adapters import HTTPAdapter
from core.config import KITE_API_KEY
from core.exceptions import KiteAuthError
from core.logger import logger

# Patch KiteConnect to increase requests connection pool size for multi-threading stability
_original_kite_init = KiteConnect.__init__
def _patched_kite_init(self, *args, **kwargs):
    _original_kite_init(self, *args, **kwargs)
    self.timeout = 15 # Set a sensible default timeout
    if hasattr(self, "reqsession"):
        adapter = HTTPAdapter(pool_connections=100, pool_maxsize=100)
        self.reqsession.mount("https://", adapter)
        self.reqsession.mount("http://", adapter)
KiteConnect.__init__ = _patched_kite_init

def get_kite_client(session_file: str = ".kite_session.json") -> KiteConnect:
    """
    Helper to initialize and return a configured KiteConnect instance from a saved session.
    Raises KiteAuthError if session is not found or invalid.
    """
    if not os.path.exists(session_file):
        logger.error("No active Kite session found. Please login via dashboard.")
        raise KiteAuthError("Kite session file not found.")
        
    try:
        with open(session_file, "r") as f:
            session = json.load(f)
            
        kite = KiteConnect(api_key=KITE_API_KEY)
        kite.set_access_token(session["access_token"])
        return kite
    except Exception as e:
        logger.error(f"Kite auth error during initialization: {e}")
        raise KiteAuthError(f"Failed to load Kite session: {e}")
