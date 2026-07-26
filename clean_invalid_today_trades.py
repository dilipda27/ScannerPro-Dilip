import os
import pandas as pd
from services import paper_trader

today_str = "2026-07-22"
invalid_tickers = ["JUBLFOOD"]

files_to_clean = [
    os.path.join("data", "trades", "paper_portfolio.csv"),
    os.path.join("data", "trades", "paper_trade_history.csv"),
    os.path.join("data", "trades", "paper_trade_archive.csv")
]

for filepath in files_to_clean:
    if os.path.exists(filepath):
        df = pd.read_csv(filepath)
        if df.empty or 'Ticker' not in df.columns or 'EntryTime' not in df.columns:
            continue
            
        mask_remove = (df['Ticker'].isin(invalid_tickers)) & \
                      (df['EntryTime'].astype(str).str.contains(today_str, na=False)) & \
                      (df['Strategy'] == 'Morning Range Str/Wk')
                      
        removed_count = mask_remove.sum()
        df_cleaned = df[~mask_remove]
        
        df_cleaned.to_csv(filepath, index=False)
        print(f"Purged {removed_count} invalid JUBLFOOD trades from '{filepath}'. Remaining rows: {len(df_cleaned)}")

# Re-export Excel workbook
paper_trader.export_history_to_excel()
print("Successfully refreshed paper_trade_history.xlsx!")
