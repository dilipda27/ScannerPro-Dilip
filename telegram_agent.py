import requests
import logging
import pandas as pd
import chart_helper
import os
import datetime

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
        
    # Calculate Net Live P&L of active positions
    net_live_pnl = active_df['Net P&L'].sum() if 'Net P&L' in active_df.columns else active_df['Live P&L'].sum()
    
    # Calculate Total Realized P&L of closed positions from today's portfolio to match Streamlit
    closed_df = portfolio_df[portfolio_df['Status'] == 'Closed'].copy()
    total_realized_pnl = closed_df['Net P&L'].sum() if 'Net P&L' in closed_df.columns else closed_df['Live P&L'].sum()
    
    # Total P&L is the sum of active net P&L and realized net P&L (matches dashboard's total net P&L)
    total_pnl = net_live_pnl + total_realized_pnl
    if 'Margin Required' in portfolio_df.columns:
        total_capital = portfolio_df.apply(
            lambda r: r['Margin Required'] if r['Status'] == 'Active' else (r['EntryPrice'] * r['Qty']),
            axis=1
        ).sum()
    else:
        total_capital = (portfolio_df['EntryPrice'] * portfolio_df['Qty']).sum()
    total_roi = (total_pnl / total_capital * 100) if total_capital > 0 else 0
    
    header = "📊 *Current Portfolio Summary*\n"
    header += f"Net Live P&L: ₹{net_live_pnl:,.2f}\n"
    header += f"Realized P&L: ₹{total_realized_pnl:,.2f}\n"
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


def format_signal_message(row, scan_name):
    """
    Formats a single row of scan result into a nice Markdown message.
    """
    ticker = row['Ticker']
    price = row.get('LTP', row.get('Close', row.get('Entry Price', row.get('Breakout Price', 'N/A'))))
    gain = row.get('% Gain', 'N/A')
    rsi = row.get('RSI (Daily)', row.get('RSI', 'N/A'))
    vol_spike = row.get('Vol Spike', row.get('Volume Spike Ratio', 'N/A'))
    target = row.get('Paper Target', row.get('Target', 'N/A'))
    sl = row.get('Paper SL', row.get('Stop Loss', 'N/A'))
    
    trade_type = ""
    breakout = str(row.get('Breakout', ''))
    if "Bullish" in breakout or "Bullish" in scan_name:
        trade_type = "🟩 *LONG ENTRY TRIGGERED*\n"
    elif "Bearish" in breakout or "Bearish" in scan_name:
        trade_type = "🟥 *SHORT ENTRY TRIGGERED*\n"
        
    msg = f"🚀 *{scan_name} Signal*\n"
    msg += f"🔥 *{ticker}*\n"
    msg += trade_type
    msg += f"Entry: ₹{price}\n"
    if gain != 'N/A': msg += f"Day Gain: {gain}%\n"
    if target != 'N/A': msg += f"Target: ₹{target} | SL: ₹{sl}\n"
    if vol_spike != 'N/A': msg += f"Volume Spike: {vol_spike}x\n"
    
    # Add direct link to Kite chart for professional utility
    token = row.get('Token')
    if token:
        msg += f"📈 [Open Full Chart](https://kite.zerodha.com/markets/ext/chart/web/ciq/NSE/{ticker}/{int(token)})\n"
    
    msg += f"\n_Generated at {datetime.datetime.now().strftime('%H:%M')}_"
    return msg

def send_signal_with_chart(ticker: str, message: str, df_5m: pd.DataFrame, bot_token: str, chat_id: str, scan_name: str, row_data=None):
    """
    Generates a chart from 5m data (resampled to 15m) and sends it with the signal message.
    """
    chart_path = f"chart_{ticker}.png"
    try:
        # Resample to 15m as requested by user
        df_15m = chart_helper.resample_to_15m(df_5m)
        
        # Extract signal details from row_data if available
        entry_price = None
        sl_price = None
        signal_type = None
        trigger_time = None
        
        if row_data is not None:
            if hasattr(row_data, 'get'):
                entry_price = row_data.get('Price') or row_data.get('Breakout Price') or row_data.get('LTP') or row_data.get('Entry Price')
                sl_price = row_data.get('SL') or row_data.get('Paper SL') or row_data.get('Stop Loss')
                trigger_time = row_data.get('Timestamp') or row_data.get('Breakout Time')
                
                # Detect signal type (BUY / SELL)
                breakout = str(row_data.get('Breakout', '')).lower()
                if "bearish" in breakout or "bearish" in scan_name.lower() or "short" in breakout:
                    signal_type = "SELL"
                elif "bullish" in breakout or "bullish" in scan_name.lower() or "long" in breakout:
                    signal_type = "BUY"
        
        # Generate chart with custom overlays
        generated_path = chart_helper.generate_intraday_chart(
            df_15m, ticker, scan_name, output_path=chart_path,
            entry_price=entry_price, sl_price=sl_price,
            signal_type=signal_type, trigger_time=trigger_time
        )
        
        if generated_path and os.path.exists(generated_path):
            success = send_photo(generated_path, message, bot_token, chat_id, parse_mode="Markdown")
            # Cleanup
            try: os.remove(generated_path)
            except: pass
            return success
        else:
            # Fallback to plain text if chart generation fails
            return send_message(message, bot_token, chat_id, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Error in send_signal_with_chart: {e}")
        return send_message(message, bot_token, chat_id, parse_mode="Markdown")
