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
        price = row.get('LTP', row.get('Close', row.get('Breakout Price', 'N/A')))
        gain = row.get('% Gain', 'N/A')
        rsi = row.get('RSI (Daily)', row.get('RSI', 'N/A'))
        vol_spike = row.get('Vol Spike', row.get('Volume Spike Ratio', 'N/A'))
        
        # Priority for SL and Target (Paper versions first)
        target = row.get('Paper Target', row.get('Target', 'N/A'))
        sl = row.get('Paper SL', row.get('Stop Loss', 'N/A'))
        
        # Trade Type Marker
        trade_type = ""
        breakout = str(row.get('Breakout', ''))
        if "Bullish" in breakout:
            trade_type = "🟩 *LONG TRADE*\n"
        elif "Bearish" in breakout:
            trade_type = "🟥 *SHORT TRADE*\n"
        
        message += f"▪️ *{row['Ticker']}*\n"
        message += trade_type
        message += f"Price: ₹{price} | Gain: {gain}%\n"
        
        if rsi != 'N/A' or vol_spike != 'N/A':
            message += f"RSI: {rsi} | Vol: {vol_spike}x\n"
            
        if target != 'N/A' or sl != 'N/A':
            message += f"Target: ₹{target} | SL: ₹{sl}\n"
            
        message += "\n"
        
    return send_message(message, bot_token, chat_id, parse_mode="Markdown")

def send_portfolio_report(portfolio_df: pd.DataFrame, bot_token: str, chat_id: str):
    """
    Sends a summary of the current active portfolio to Telegram.
    """
    if portfolio_df.empty:
        return # Don't bother if empty
        
    active_df = portfolio_df[portfolio_df['Status'] == 'Active'].copy()
    if active_df.empty:
        return
        
    total_pnl = active_df['Live P&L'].sum()
    total_capital = (active_df['EntryPrice'] * active_df['Qty']).sum()
    total_roi = (total_pnl / total_capital * 100) if total_capital > 0 else 0
    
    header = "📊 *Current Portfolio Summary*\n"
    header += f"Total P&L: *₹{total_pnl:,.2f}* ({total_roi:.2f}%)\n"
    header += f"Active Positions: {len(active_df)}\n\n"
    
    message = header
    for _, row in active_df.iterrows():
        pnl = row['Live P&L']
        pnl_pct = (pnl / (row['EntryPrice'] * row['Qty']) * 100) if row['EntryPrice'] > 0 else 0
        icon = "🟢" if pnl >= 0 else "🔴"
        
        message += f"{icon} *{row['Ticker']}*: ₹{pnl:,.2f} ({pnl_pct:.2f}%)\n"
        message += f"   LTP: ₹{row['Current Price']} | Entry: ₹{row['EntryPrice']}\n"
    
    return send_message(message, bot_token, chat_id, parse_mode="Markdown")

