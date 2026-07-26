import os
import re

directories = ["ui", "strategies", "services", "api", "core", "."]

replacements = [
    (r'^import config$', 'from core import config'),
    (r'^import telegram_agent$', 'from services import telegram_agent'),
    (r'^import paper_trader$', 'from services import paper_trader'),
    (r'^from base_strategy import', 'from strategies.base_strategy import'),
    (r'^import base_strategy$', 'from strategies import base_strategy'),
    (r'^import scheduler_service$', 'from services import scheduler_service'),
    
    # Scanners in app.py or scheduler
    (r'^import kite_scanner$', 'from strategies import kite_scanner'),
    (r'^import high52_scanner$', 'from strategies import high52_scanner'),
    (r'^import bullish_breakout_scanner$', 'from strategies import bullish_breakout_scanner'),
    (r'^import bearish_breakdown_scanner$', 'from strategies import bearish_breakdown_scanner'),
    (r'^import long_trade_scanner$', 'from strategies import long_trade_scanner'),
    (r'^import minervini_vcp_scanner$', 'from strategies import minervini_vcp_scanner'),
    (r'^import morning_range_scanner$', 'from strategies import morning_range_scanner'),
    (r'^import top_gainers_losers_scanner$', 'from strategies import top_gainers_losers_scanner'),
    (r'^import failed_breakout_scanner$', 'from strategies import failed_breakout_scanner'),
    (r'^import bearish_vwap_rejection_scanner$', 'from strategies import bearish_vwap_rejection_scanner'),
    (r'^import bullish_vwap_rejection_scanner$', 'from strategies import bullish_vwap_rejection_scanner'),
    (r'^import orb_scanner$', 'from strategies import orb_scanner'),
    (r'^import volatility_contraction_scanner$', 'from strategies import volatility_contraction_scanner'),
    (r'^import multi_year_breakout_scanner$', 'from strategies import multi_year_breakout_scanner'),
    
    # Old API logic
    (r'^import scanner$', 'from api import market_data as scanner'),
]

for d in directories:
    if not os.path.exists(d): continue
    for root, _, files in os.walk(d):
        if "venv" in root or "__pycache__" in root: continue
        for file in files:
            if file.endswith(".py") and file != "update_imports.py":
                filepath = os.path.join(root, file)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                except Exception as e:
                    continue
                
                changed = False
                for i in range(len(lines)):
                    for pattern, repl in replacements:
                        new_line = re.sub(pattern, repl, lines[i])
                        if new_line != lines[i]:
                            lines[i] = new_line
                            changed = True
                            
                if changed:
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.writelines(lines)
                    print(f"Updated imports in {filepath}")
