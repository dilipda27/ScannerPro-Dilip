import json
import os
import datetime

CACHE_FILE = os.path.join("data", "state", "notified_cache.json")

def get_notified_tickers(scan_name):
    """Load notified tickers for today and specific scan."""
    if not os.path.exists(CACHE_FILE):
        return set()
    
    try:
        with open(CACHE_FILE, 'r') as f:
            data = json.load(f)
        
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        if today in data:
            return set(data[today].get(scan_name, []))
    except:
        pass
    return set()

def mark_as_notified(scan_name, tickers):
    """Save newly notified tickers to persistent file."""
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    data = {}
    
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                data = json.load(f)
        except:
            data = {}
            
    if today not in data:
        data = {today: {}}
    
    if scan_name not in data[today]:
        data[today][scan_name] = []
        
    current_list = set(data[today][scan_name])
    current_list.update(tickers)
    data[today][scan_name] = list(current_list)
    
    with open(CACHE_FILE, 'w') as f:
        json.dump(data, f)

def filter_new_tickers(scan_name, tickers):
    """Filter out tickers that have already been notified today for this scan."""
    already_notified = get_notified_tickers(scan_name)
    return [t for t in tickers if t not in already_notified]
