import os
import pandas as pd
from services import paper_trader

today_str = "2026-07-22"
invalid_mr_tickers = ["JUBLFOOD", "SUZLON", "UPL", "NATIONALUM", "ASHOKLEY", "COCHINSHIP"]

files_to_update = [
    os.path.join("data", "trades", "paper_portfolio.csv"),
    os.path.join("data", "trades", "paper_trade_history.csv"),
    os.path.join("data", "trades", "paper_trade_archive.csv")
]

print("=========================================================================")
print("  UPDATING TODAY'S PAPER PORTFOLIO & TRADE HISTORY WITH NEW GUARDRAILS   ")
print("=========================================================================\n")

for filepath in files_to_update:
    if os.path.exists(filepath):
        df = pd.read_csv(filepath)
        if df.empty or 'Ticker' not in df.columns or 'EntryTime' not in df.columns:
            continue
            
        mask_remove = (df['Ticker'].isin(invalid_mr_tickers)) & \
                      (df['Strategy'] == 'Morning Range Str/Wk') & \
                      (df['EntryTime'].astype(str).str.contains(today_str, na=False))
                      
        removed_count = mask_remove.sum()
        df_cleaned = df[~mask_remove]
        
        df_cleaned.to_csv(filepath, index=False)
        print(f"Purged {removed_count} non-qualifying Morning Range trades from '{filepath}'. Remaining rows: {len(df_cleaned)}")

# Re-export Excel workbook
paper_trader.export_history_to_excel()
print("\nSuccessfully updated and refreshed 'paper_trade_history.xlsx' workbook!")

# Display current active portfolio
df_port = pd.read_csv(os.path.join("data", "trades", "paper_portfolio.csv"))
print("\n--- UPDATED ACTIVE PAPER PORTFOLIO ---")
print(df_port[['Ticker', 'Type', 'EntryPrice', 'SL', 'EntryTime', 'Status', 'Strategy']].to_string(index=False))
