import os
import pandas as pd

portfolio_file = os.path.join("data", "trades", "paper_portfolio.csv")
history_file = os.path.join("data", "trades", "paper_trade_history.csv")
excel_file = "paper_trade_history.xlsx"

today_str = "2026-07-21"

def clean_file(filepath):
    if os.path.exists(filepath):
        df = pd.read_csv(filepath)
        if df.empty:
            return
            
        # Remove rows from today where Strategy is 'Failed Breakout Short'
        mask = (df['Strategy'].str.contains('Failed Breakout', case=False, na=False)) & \
               (df['EntryTime'].astype(str).str.contains(today_str, na=False))
               
        removed_tickers = df[mask]['Ticker'].tolist()
        df_cleaned = df[~mask]
        
        df_cleaned.to_csv(filepath, index=False)
        print(f"Removed {len(removed_tickers)} Failed Breakout trades from {filepath}: {removed_tickers}")

clean_file(portfolio_file)
clean_file(history_file)

if os.path.exists(excel_file):
    try:
        df_xl = pd.read_excel(excel_file)
        if not df_xl.empty:
            mask = (df_xl['Strategy'].str.contains('Failed Breakout', case=False, na=False)) & \
                   (df_xl['EntryTime'].astype(str).str.contains(today_str, na=False))
            df_xl_cleaned = df_xl[~mask]
            df_xl_cleaned.to_excel(excel_file, index=False)
            print(f"Removed Failed Breakout trades from {excel_file}")
    except Exception as e:
        print(f"Error cleaning excel file: {e}")
