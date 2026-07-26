import os
import pandas as pd
from services import paper_trader

portfolio_file = os.path.join("data", "trades", "paper_portfolio.csv")
history_file = os.path.join("data", "trades", "paper_trade_history.csv")

jublfood_row = {
    "Ticker": "JUBLFOOD",
    "Type": "Bearish Breakdown",
    "EntryPrice": 418.5,
    "SL": 421.18,
    "InitialSL": 421.18,
    "Target": 413.14,
    "Qty": 597,
    "Token": 4632577,
    "EntryTime": "2026-07-22 09:49",
    "Status": "Closed",
    "Strategy": "Morning Range Str/Wk",
    "Delta": None,
    "Current Price": 421.18
}

for fpath in [portfolio_file, history_file]:
    if os.path.exists(fpath):
        df = pd.read_csv(fpath)
        # Check if JUBLFOOD already exists
        exists = not df[(df['Ticker'] == 'JUBLFOOD') & (df['EntryTime'] == '2026-07-22 09:49')].empty
        if not exists:
            df = pd.concat([df, pd.DataFrame([jublfood_row])], ignore_index=True)
            df.to_csv(fpath, index=False)
            print(f"Restored JUBLFOOD trade to '{fpath}'.")

paper_trader.export_history_to_excel()
print("Refreshed paper_trade_history.xlsx.")
