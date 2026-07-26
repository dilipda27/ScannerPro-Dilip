import os
import re

scanners = [
    "kite_scanner", "high52_scanner", "bullish_breakout_scanner",
    "bearish_breakdown_scanner", "long_trade_scanner", "minervini_vcp_scanner",
    "morning_range_scanner", "top_gainers_losers_scanner", "failed_breakout_scanner",
    "bearish_vwap_rejection_scanner", "bullish_vwap_rejection_scanner",
    "orb_scanner", "volatility_contraction_scanner", "multi_year_breakout_scanner",
    "bearish_vwap_rejection", "bullish_vwap_rejection"
]

services = [
    "scheduler_service", "paper_trader", "telegram_agent", "intraday_cache_service"
]

def process_file(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    changed = False
    for i in range(len(lines)):
        line = lines[i]
        for scanner in scanners:
            pattern = r'^(\s+)import ' + scanner + r'\b'
            repl = r'\1from strategies import ' + scanner
            new_line = re.sub(pattern, repl, line)
            if new_line != line:
                lines[i] = new_line
                changed = True
                line = new_line
                
        for service in services:
            pattern = r'^(\s+)import ' + service + r'\b'
            repl = r'\1from services import ' + service
            new_line = re.sub(pattern, repl, line)
            if new_line != line:
                lines[i] = new_line
                changed = True
                line = new_line

    if changed:
        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        print(f"Fixed inline imports in {file_path}")

def process_all_files():
    root_dir = r"f:\MyFinance\ScannerPro-Dilip"
    for subdir, dirs, files in os.walk(root_dir):
        if "venv" in subdir or ".git" in subdir:
            continue
        for file in files:
            if file.endswith('.py'):
                process_file(os.path.join(subdir, file))

process_all_files()
