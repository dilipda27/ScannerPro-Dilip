import requests
import logging
import pandas as pd

def send_message(text: str, bot_token: str, chat_id: str, parse_mode: str = None):
    """
    Sends a generic text message to a Telegram chat.
    """
    if not bot_token or not chat_id:
        logging.warning("Telegram credentials missing. Cannot send message.")
        return False
        
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        logging.info("Telegram message sent successfully.")
        return True
    except Exception as e:
        logging.error(f"Failed to send Telegram message: {e}")
        return False

def send_photo(photo_path: str, caption: str, bot_token: str, chat_id: str, parse_mode: str = None):
    """
    Uploads a photo to Telegram with an optional caption.
    """
    if not bot_token or not chat_id:
        logging.warning("Telegram credentials missing.")
        return False
        
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    data = {
        "chat_id": chat_id,
        "caption": caption
    }
    if parse_mode:
        data["parse_mode"] = parse_mode
        
    try:
        with open(photo_path, "rb") as photo_file:
            files = {"photo": photo_file}
            response = requests.post(url, data=data, files=files)
            response.raise_for_status()
            logging.info("Telegram photo sent successfully.")
            return True
    except Exception as e:
        logging.error(f"Failed to send Telegram photo: {e}")
        return False

def send_dataframe(df: pd.DataFrame, bot_token: str, chat_id: str, scan_name: str = "3:15 PM Nifty 500"):
    """
    Formats the scanner results DataFrame and sends it to Telegram.
    """
    if df.empty:
        return send_message(f"📉 *{scan_name} Scan Complete*: No stocks met the criteria today.", bot_token, chat_id, parse_mode="Markdown")
        
    message = f"🚀 *{scan_name} Scan Complete*\nFound {len(df)} candidates:\n\n"
    
    for _, row in df.iterrows():
        # Detect price column (LTP for Kite, Close for YFinance)
        price = row.get('LTP', row.get('Close', 'N/A'))
        gain = row.get('% Gain', 'N/A')
        rsi = row.get('RSI (Daily)', row.get('RSI', 'N/A'))
        vol_spike = row.get('Volume Spike Ratio', 'N/A')
        target = row.get('Target', 'N/A')
        sl = row.get('Stop Loss', 'N/A')
        
        message += f"▪️ *{row['Ticker']}*\n"
        message += f"Price: ₹{price} | Gain: {gain}%\n"
        
        if rsi != 'N/A' or vol_spike != 'N/A':
            message += f"RSI: {rsi} | Vol Spike: {vol_spike}x\n"
            
        if target != 'N/A' or sl != 'N/A':
            message += f"Target: ₹{target} | SL: ₹{sl}\n"
            
        message += "\n"
        
    return send_message(message, bot_token, chat_id, parse_mode="Markdown")
