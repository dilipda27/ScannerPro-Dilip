import os
import io
import pandas as pd
import requests
from typing import Set, Tuple
from core.logger import logger
from core.exceptions import DataFetchError

def get_nifty500_fno_tickers() -> Tuple[Set[str], Set[str]]:
    """
    Fetch Nifty 500 stocks and filter for those in the FNO segment.
    Returns:
        Tuple of (nifty500_symbols, fno_symbols)
    """
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    cache_dir = os.path.join("data", "cache")
    os.makedirs(cache_dir, exist_ok=True)
    
    cache_500 = os.path.join(cache_dir, "nifty500_local_cache.csv")
    cache_fno = os.path.join(cache_dir, "fo_mktlots_local_cache.csv")
    
    # 1. Fetch Nifty 500
    url_500 = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
    nifty500_symbols = set()
    try:
        r_500 = requests.get(url_500, headers=headers, timeout=10)
        r_500.raise_for_status()
        text_500 = r_500.text
        with open(cache_500, "w", encoding="utf-8") as f:
            f.write(text_500)
        df_500 = pd.read_csv(io.StringIO(text_500))
        nifty500_symbols = set(df_500['Symbol'].str.strip())
    except Exception as e:
        logger.warning(f"Error fetching Nifty 500 from NSE: {e}. Trying local cache...")
        if os.path.exists(cache_500):
            try:
                df_500 = pd.read_csv(cache_500)
                nifty500_symbols = set(df_500['Symbol'].str.strip())
                logger.info("Loaded Nifty 500 from local cache.")
            except Exception as ce:
                logger.error(f"Failed to read Nifty 500 cache: {ce}")
        
        if not nifty500_symbols:
            # Absolute fallback list (approx 50 major stocks)
            nifty500_symbols = {
                "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "BHARTIARTL", "SBI", "LICI",
                "ITC", "HINDUNILVR", "LT", "BAJFINANCE", "HCLTECH", "MARUTI", "SUNPHARMA",
                "ADANIENT", "KOTAKBANK", "AXISBANK", "TITAN", "ULTRACEMCO", "NTPC", "TATAMOTORS",
                "ONGC", "POWERGRID", "ASIANPAINT", "COALINDIA", "JSWSTEEL", "M&M", "TRENT",
                "NESTLEIND", "TATACHEM", "HINDALCO", "BPCL", "Grasim", "WIPRO", "TECHM",
                "HDFCLIFE", "SBILIFE", "DRREDDY", "IOC", "CIPLA", "EICHERMOT", "DIVISLAB",
                "INDUSINDBK", "SBICARD", "MUTHOOTFIN", "APOLLOHOSP", "HEROMOTOCO", "SHRIRAMFIN"
            }

    # 2. Fetch FNO List
    url_fno = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"
    fno_symbols = set()
    try:
        r_fno = requests.get(url_fno, headers=headers, timeout=10)
        r_fno.raise_for_status()
        text_fno = r_fno.text
        with open(cache_fno, "w", encoding="utf-8") as f:
            f.write(text_fno)
        for line in text_fno.split('\n'):
            parts = line.split(',')
            if len(parts) > 2:
                sym = parts[1].strip()
                if sym and sym != "SYMBOL":
                    fno_symbols.add(sym)
    except Exception as e:
        logger.warning(f"Error fetching FNO list from NSE: {e}. Trying local cache...")
        loaded_fno_cache = False
        if os.path.exists(cache_fno):
            try:
                with open(cache_fno, "r", encoding="utf-8") as f:
                    for line in f.read().split('\n'):
                        parts = line.split(',')
                        if len(parts) > 2:
                            sym = parts[1].strip()
                            if sym and sym != "SYMBOL":
                                fno_symbols.add(sym)
                if fno_symbols:
                    logger.info("Loaded FNO list from local cache.")
                    loaded_fno_cache = True
            except Exception as ce:
                logger.error(f"Failed to read FNO cache: {ce}")
                
        if not loaded_fno_cache:
            kite_inst_file = os.path.join(cache_dir, "kite_instruments_nfo.csv")
            if os.path.exists(kite_inst_file):
                try:
                    df_inst = pd.read_csv(kite_inst_file)
                    nfo_fno_syms = df_inst[df_inst['segment'] == 'NFO-FUT']['name'].dropna().unique()
                    for sym in nfo_fno_syms:
                        fno_symbols.add(sym.strip())
                    if fno_symbols:
                        logger.info(f"Loaded {len(fno_symbols)} FNO symbols from {kite_inst_file}")
                except Exception as ie:
                    logger.error(f"Failed to load FNO symbols from kite instruments: {ie}")
                    
    final_symbols = nifty500_symbols.intersection(fno_symbols)
    tickers = [f"{sym}.NS" for sym in final_symbols]
    return sorted(tickers)
