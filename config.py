# Configuration File
# Expose variables dynamically loaded from config/config.yaml for backwards compatibility.
import os

# Fallback defaults (checked against environment variables first)
KITE_API_KEY = os.environ.get("KITE_API_KEY", "cl7wqm54xlvsva52")
KITE_API_SECRET = os.environ.get("KITE_API_SECRET", "x4gfq71ek6twzdrr3fki3hpf71tvfahz")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AQ.Ab8RN6IyPAvq-0VscihThiwa7EIOFyZcMZJ5RaEO2ZfDpLvzSA")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8741631027:AAGHs2efvOeQg9mTVBE2Oe8HNKbj0H7P1oQ")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "@SwingTradeIdeaNse500")
TELEGRAM_CHAT_ID_INTRADAY = os.environ.get("TELEGRAM_CHAT_ID_INTRADAY", "@IntradayTrades_FinFundaIndia")
TELEGRAM_PERSONAL_CHAT_ID = os.environ.get("TELEGRAM_PERSONAL_CHAT_ID", "1082905661")
LOT_SIZE_NIFTY = int(os.environ.get("LOT_SIZE_NIFTY", 65))
LOT_SIZE_SENSEX = int(os.environ.get("LOT_SIZE_SENSEX", 20))

# Path to the config yaml
CONFIG_PATH = os.path.join("config", "config.yaml")

if os.path.exists(CONFIG_PATH):
    try:
        import yaml
        with open(CONFIG_PATH, "r") as f:
            _cfg = yaml.safe_load(f)
        if _cfg:
            KITE_API_KEY = _cfg.get("kite", {}).get("api_key", KITE_API_KEY)
            KITE_API_SECRET = _cfg.get("kite", {}).get("api_secret", KITE_API_SECRET)
            gemini_val = _cfg.get("gemini", {}).get("api_key")
            if gemini_val:
                GEMINI_API_KEY = gemini_val
            
            _tel = _cfg.get("telegram", {})
            TELEGRAM_BOT_TOKEN = _tel.get("bot_token", TELEGRAM_BOT_TOKEN)
            TELEGRAM_CHAT_ID = _tel.get("chat_id", TELEGRAM_CHAT_ID)
            TELEGRAM_CHAT_ID_INTRADAY = _tel.get("chat_id_intraday", TELEGRAM_CHAT_ID_INTRADAY)
            TELEGRAM_PERSONAL_CHAT_ID = _tel.get("personal_chat_id", TELEGRAM_PERSONAL_CHAT_ID)
            
            _opt = _cfg.get("options", {})
            LOT_SIZE_NIFTY = _opt.get("lot_size_nifty", LOT_SIZE_NIFTY)
            LOT_SIZE_SENSEX = _opt.get("lot_size_sensex", LOT_SIZE_SENSEX)
    except ModuleNotFoundError:
        import logging
        logging.warning("PyYAML not found in the current environment. Falling back to default credentials inside config.py.")
    except Exception as e:
        import logging
        logging.error(f"Error loading config.yaml: {e}")

# Environment variables take precedence over config.yaml (ideal for cloud/Railway deployments)
KITE_API_KEY = os.environ.get("KITE_API_KEY", KITE_API_KEY)
KITE_API_SECRET = os.environ.get("KITE_API_SECRET", KITE_API_SECRET)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", GEMINI_API_KEY)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)
TELEGRAM_CHAT_ID_INTRADAY = os.environ.get("TELEGRAM_CHAT_ID_INTRADAY", TELEGRAM_CHAT_ID_INTRADAY)
TELEGRAM_PERSONAL_CHAT_ID = os.environ.get("TELEGRAM_PERSONAL_CHAT_ID", TELEGRAM_PERSONAL_CHAT_ID)
try:
    LOT_SIZE_NIFTY = int(os.environ.get("LOT_SIZE_NIFTY", LOT_SIZE_NIFTY))
    LOT_SIZE_SENSEX = int(os.environ.get("LOT_SIZE_SENSEX", LOT_SIZE_SENSEX))
except Exception:
    pass

def save_gemini_key(key: str) -> bool:
    """Saves the Gemini API key back to config/config.yaml."""
    import yaml
    config_path = os.path.join("config", "config.yaml")
    cfg = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                cfg = yaml.safe_load(f) or {}
        except Exception:
            pass
    if "gemini" not in cfg:
        cfg["gemini"] = {}
    cfg["gemini"]["api_key"] = key
    try:
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False)
        return True
    except Exception as e:
        import logging
        logging.error(f"Failed to save Gemini key to config.yaml: {e}")
        return False
