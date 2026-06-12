import pandas as pd
import pandas_ta as ta
import datetime
import logging
import scanner
import kite_scanner

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def scan_multi_year_breakouts(kite, progress_callback=None):
    """
    Scans F&O stocks for high-probability Multi-Year Breakouts that occurred
    within the last 5 trading days (current week) and are currently consolidating / holding up.
    
    Filters:
    1. Multi-Year High (MYH): Calculated over previous 500 trading days (~2 Years), excluding last 5 days.
    2. Recent Weekly Breakout: At least one daily Close in last 5 sessions > MYH.
    3. Holding Up (Consolidation): Latest Close >= MYH * 0.99 and Latest Close <= MYH * 1.05.
    4. Institutional Volume: Max daily volume in last 5 days >= 2.5x of 20 Volume SMA, OR 5-day average volume >= 1.5x.
    5. Trend Alignment: Close > 20 EMA > 50 EMA > 200 EMA.
    6. Trend Strength: ADX > 20 AND +DI > -DI.
    """
    logging.info("Starting EOD Multi-Year Breakout Scanner...")
    results = []
    
    to_date = datetime.datetime.now()
    # Fetch 1200 calendar days of data to guarantee 800+ trading days for stable 500 MYH and 200 EMA calculations
    from_date = to_date - datetime.timedelta(days=1200)
    
    # Market Context Check bypassed as requested. Proceeding directly with stock scan.
    logging.info("Market Context Check bypassed. Proceeding with stock scan.")
    
    if progress_callback:
        progress_callback(0, 100, "Fetching F&O Symbols...")
        
    # 2. Fetch Nifty F&O Tickers
    fno_tickers = scanner.get_nifty500_fno_tickers()
    fno_symbols = [ticker.replace(".NS", "") for ticker in fno_tickers]
    
    logging.info(f"Fetching instrument tokens for {len(fno_symbols)} F&O stocks...")
    token_map = kite_scanner.get_kite_instruments(kite, fno_symbols)
    
    if not token_map:
        logging.error("Failed to retrieve instrument tokens for F&O stocks.")
        return pd.DataFrame()
        
    total_symbols = len(token_map)
    processed = 0
    
    # 3. Scan stocks
    for symbol, token in token_map.items():
        processed += 1
        if progress_callback:
            progress_callback(processed, total_symbols, symbol)
            
        try:
            df = kite_scanner.fetch_kite_data(kite, token, from_date, to_date, "day")
            # We need at least 700 trading sessions to compute 500-day MYH + 200 EMA
            # We need at least 520 trading sessions to compute 500-day MYH + 200 EMA
            if df.empty or len(df) < 520:
                continue
                
            # A. Calculate technical indicators
            df.ta.ema(length=20, append=True)
            df.ta.ema(length=50, append=True)
            df.ta.ema(length=200, append=True)
            df.ta.adx(length=14, append=True)  # ADX_14, DMP_14, DMN_14
            df.ta.rsi(length=14, append=True)
            df.ta.atr(length=14, append=True)
            
            # Calculate Volume SMA prior to the breakout week (shift 5 days) to avoid self-inflation
            df['Vol_SMA_20'] = df['volume'].shift(5).rolling(window=20).mean()
            
            # B. Calculate Multi-Year High (500 trading days prior to the breakout week)
            # Shift by 5 to exclude the current week's candles
            df['MYH'] = df['high'].shift(5).rolling(window=500).max()
            
            latest = df.iloc[-1]
            required_cols = ['EMA_20', 'EMA_50', 'EMA_200', 'ADX_14', 'DMP_14', 'DMN_14', 'RSI_14', 'ATRr_14', 'Vol_SMA_20', 'MYH']
            if latest[required_cols].isna().any():
                continue
                
            close = latest['close']
            vol_sma = latest['Vol_SMA_20']
            atr = latest['ATRr_14']
            myh = latest['MYH']
            
            # C. Evaluation Filters
            
            # 1. Recent Weekly Breakout Check (last 5 sessions)
            # At least one daily Close or High crossed the MYH level
            recent_highs = df['high'].iloc[-5:]
            recent_closes = df['close'].iloc[-5:]
            recent_myhs = df['MYH'].iloc[-5:]
            
            broke_out = (recent_closes > recent_myhs).any() or (recent_highs > recent_myhs).any()
            if not broke_out:
                continue
                
            # 2. Holding Up / Proximity Check
            # Current Close is holding near or above MYH support, not overextended
            is_holding = (close >= myh * 0.99)
            not_extended = (close <= myh * 1.05)
            
            if not (is_holding and not_extended):
                continue
                
            # 3. Institutional Volume Spike Confirmation
            # Max volume in last 5 days is >= 2.5x SMA OR average is >= 1.5x SMA
            recent_vols = df['volume'].iloc[-5:]
            max_vol_last_5 = recent_vols.max()
            avg_vol_last_5 = recent_vols.mean()
            
            vol_ok = (max_vol_last_5 >= vol_sma * 2.5) or (avg_vol_last_5 >= vol_sma * 1.5)
            if not vol_ok:
                continue
                
            # 4. Long-Term Trend Alignment (Relaxed to close above 50 and 200 EMA)
            trend_ok = (close > latest['EMA_50']) and (close > latest['EMA_200'])
            if not trend_ok:
                continue
                
            # 5. Trend Strength (Relaxed ADX requirement to DMP > DMN only, since ADX lags fresh breakouts)
            momentum_ok = (latest['DMP_14'] > latest['DMN_14'])
            if not momentum_ok:
                continue
                
            # D. Safe Technical Stop Loss & Target Calculation
            # SL just below MYH support or 1.8 ATR, whichever is safer
            sl = max(myh * 0.965, close - 1.8 * atr)
            
            # Targets representing clean 1.5R and 3.0R swing ratios
            tp1 = close + 1.5 * (close - sl)
            tp2 = close + 3.0 * (close - sl)
            
            results.append({
                "Ticker": symbol,
                "Close": round(close, 2),
                "Breakout MYH": round(myh, 2),
                "Proximity %": round(((close - myh) / myh) * 100, 2),
                "Stop Loss": round(sl, 2),
                "Target 1 (1.5R)": round(tp1, 2),
                "Target 2 (3.0R)": round(tp2, 2),
                "Volume Spike (Peak)": round(max_vol_last_5 / vol_sma, 2) if vol_sma > 0 else 0,
                "Volume Spike (Avg)": round(avg_vol_last_5 / vol_sma, 2) if vol_sma > 0 else 0,
                "ADX": round(latest['ADX_14'], 2),
                "RSI": round(latest['RSI_14'], 2),
                "Token": token
            })
            
        except Exception as e:
            logging.error(f"Error processing {symbol}: {e}")
            continue
            
    logging.info(f"Multi-Year Breakout Scan complete. Found {len(results)} swing candidates.")
    return pd.DataFrame(results)
