import pandas as pd
import pandas_ta as ta
import datetime
import logging
import scanner
import kite_scanner

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def scan_long_setups(kite, progress_callback=None):
    """
    Scans for Long Swing Trading setups based on the following criteria:
    Market Context: Nifty 50 Close > 50 EMA, and 50 EMA > 200 EMA (Bypassed / Not strictly enforced).
    Trend Filter: Stock Close > 50 EMA AND 21 EMA > 50 EMA.
    Momentum Filter: ADX > 25 AND +DI > -DI AND (RSI >= 40 AND RSI <= 60).
    Price & Volume Filter: (Stock Low <= 21 EMA OR Stock Low <= 50 EMA) AND Close > (High + Low) / 2 AND Volume > (20-period Volume SMA * 1.5).
    """
    logging.info("Starting EOD Long Swing Scanner...")
    results = []
    
    to_date = datetime.datetime.now()
    # Fetch 400 days of data to accurately calculate 200 EMA
    from_date = to_date - datetime.timedelta(days=400)
    
    # 1. Market Context Check (Nifty 50)
    logging.info("Evaluating Market Context (Nifty 50)...")
    if progress_callback:
        progress_callback(0, 100, "Evaluating Market Context...")
        
    try:
        nifty_token_map = kite_scanner.get_kite_instruments(kite, ["NIFTY 50"])
        if nifty_token_map and "NIFTY 50" in nifty_token_map:
            nifty_token = nifty_token_map["NIFTY 50"]
            nifty_df = kite_scanner.fetch_kite_data(kite, nifty_token, from_date, to_date, "day")
            
            if not nifty_df.empty and len(nifty_df) >= 200:
                nifty_df.ta.ema(length=50, append=True)
                nifty_df.ta.ema(length=200, append=True)
                
                nifty_latest = nifty_df.iloc[-1]
                
                if not pd.isna(nifty_latest['EMA_50']) and not pd.isna(nifty_latest['EMA_200']):
                    # Nifty 50 Close > Nifty 50 EMA, and Nifty 50 EMA > Nifty 200 EMA
                    market_bullish = (nifty_latest['close'] > nifty_latest['EMA_50']) and (nifty_latest['EMA_50'] > nifty_latest['EMA_200'])
                    
                    if not market_bullish:
                        logging.warning("Market Context is NOT bullish (Nifty 50 Close <= 50 EMA or 50 EMA <= 200 EMA). Proceeding anyway as strict check is bypassed.")
                    else:
                        logging.info("Market Context is BULLISH. Proceeding with stock scan.")
                else:
                    logging.warning("Not enough data to calculate NIFTY 50 EMAs. Proceeding with stock scan.")
            else:
                logging.warning("Insufficient daily data for NIFTY 50. Proceeding with stock scan.")
        else:
            logging.warning("Could not find NIFTY 50 instrument token. Proceeding with stock scan.")
    except Exception as e:
        logging.warning(f"Error checking NIFTY 50 Market Context: {e}. Proceeding with stock scan.")
    
    if progress_callback:
        progress_callback(0, 100, "Fetching F&O Symbols...")
        
    # 2. Fetch F&O Symbols
    fno_tickers = scanner.get_nifty500_fno_tickers()
    # Remove '.NS' suffix for Kite format
    fno_symbols = [ticker.replace(".NS", "") for ticker in fno_tickers]
    
    logging.info(f"Fetching instrument tokens for {len(fno_symbols)} F&O stocks...")
    token_map = kite_scanner.get_kite_instruments(kite, fno_symbols)
    
    if not token_map:
        logging.error("Failed to retrieve instrument tokens for F&O stocks.")
        return pd.DataFrame()
        
    total_symbols = len(token_map)
    processed = 0
    
    # 3. Scan each stock
    for symbol, token in token_map.items():
        processed += 1
        if progress_callback:
            progress_callback(processed, total_symbols, symbol)
            
        try:
            df = kite_scanner.fetch_kite_data(kite, token, from_date, to_date, "day")
            if df.empty or len(df) < 200:
                continue
                
            # Calculate Indicators
            df.ta.ema(length=21, append=True)
            df.ta.ema(length=50, append=True)
            df.ta.ema(length=200, append=True)
            df.ta.adx(length=14, append=True) # Creates ADX_14, DMP_14, DMN_14
            df.ta.rsi(length=14, append=True)
            df.ta.atr(length=14, append=True)
            df['Vol_SMA_20'] = df['volume'].rolling(window=20).mean()
            
            latest = df.iloc[-1]
            
            # Check for NaN values in required indicators
            required_cols = ['EMA_21', 'EMA_50', 'ADX_14', 'DMP_14', 'DMN_14', 'RSI_14', 'ATRr_14', 'Vol_SMA_20']
            if latest[required_cols].isna().any():
                continue
                
            close = latest['close']
            high = latest['high']
            low = latest['low']
            vol = latest['volume']
            
            ema_21 = latest['EMA_21']
            ema_50 = latest['EMA_50']
            
            adx = latest['ADX_14']
            plus_di = latest['DMP_14']
            minus_di = latest['DMN_14']
            
            rsi = latest['RSI_14']
            atr = latest['ATRr_14']
            vol_sma = latest['Vol_SMA_20']
            
            # Filter 1: Trend Filter
            trend_ok = (close > ema_50) and (ema_21 > ema_50)
            
            # Filter 2: Momentum Filter
            momentum_ok = (adx > 25) and (plus_di > minus_di) and (40 <= rsi <= 60)
            
            # Filter 3: Price & Volume Filter
            price_ok = (low <= ema_21 or low <= ema_50) and (close > (high + low) / 2)
            vol_ok = (vol > vol_sma * 1.5)
            
            if trend_ok and momentum_ok and price_ok and vol_ok:
                entry = high
                sl = low - atr
                tp1 = entry + 2 * (entry - sl)
                
                results.append({
                    "Ticker": symbol,
                    "Close": round(close, 2),
                    "Entry Trigger": round(entry, 2),
                    "Stop Loss": round(sl, 2),
                    "Take Profit 1": round(tp1, 2),
                    "RSI": round(rsi, 2),
                    "ADX": round(adx, 2),
                    "Vol Spike": round(vol / vol_sma, 2) if vol_sma > 0 else 0,
                    "Token": token
                })
                
        except Exception as e:
            logging.error(f"Error processing {symbol}: {e}")
            continue
            
    logging.info(f"Scan complete. Found {len(results)} long swing candidates.")
    return pd.DataFrame(results)
