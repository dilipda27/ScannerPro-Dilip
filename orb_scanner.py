import os
import json
import logging
import datetime
import pandas as pd
import pandas_ta as ta

from kite_scanner import (
    fetch_kite_data, 
    calculate_vwap, 
    get_trending_orb_list, 
    get_nifty500_fno_symbols, 
    get_kite_instruments
)

def scan_orb_setups(kite, progress_callback=None):
    """
    Opening Range Breakout (ORB) - 15 minute strategy with enhanced strength filters.
    Filters:
    1. LTP > 15-min ORB High (Bullish) or LTP < 15-min ORB Low (Bearish)
    2. Strength: ORB High > Prev Day High (Bullish) or ORB Low < Prev Day Low (Bearish)
    3. Daily Trend: Price > Daily 20 EMA (Bullish) or Price < Daily 20 EMA (Bearish)
    4. Daily Momentum: Daily RSI > 55 (Bullish) or Daily RSI < 50 (Bearish)
    5. Momentum: Breakout Candle Volume > 1.5x Average 5-min Volume
    6. Conviction: Breakout Candle Body >= 50% of its total range
    7. Cleanliness: ORB High/Low must be the current Day High/Low (no prior breakouts)
    """
    logging.info("Starting Refined 15-Min ORB Scan with First-Breakout filter...")
    
    # Try to get cached trending stocks first
    cached_df = get_trending_orb_list()
    
    if cached_df is not None and not cached_df.empty:
        logging.info(f"Using {len(cached_df)} cached trending stocks for ORB scan.")
        token_map = dict(zip(cached_df['Ticker'], cached_df['Token']))
        # Pre-populate indicators from cache
        cache_indicators = cached_df.set_index('Ticker').to_dict('index')
    else:
        logging.info("No valid cache found. Falling back to full FNO scan (Slow).")
        symbols = get_nifty500_fno_symbols()
        token_map = get_kite_instruments(kite, symbols)
        cache_indicators = {}
    
    # --- FETCH SECTORAL STATUSES ---
    sector_indices = [
        "NIFTY 50", "NIFTY BANK", "NIFTY IT", "NIFTY AUTO", "NIFTY METAL", 
        "NIFTY PHARMA", "NIFTY FMCG", "NIFTY ENERGY", "NIFTY REALTY", "NIFTY PSU BANK"
    ]
    sector_status = {}
    try:
        idx_quotes = kite.ohlc([f"NSE:{idx}" for idx in sector_indices])
        for idx in sector_indices:
            q = idx_quotes.get(f"NSE:{idx}")
            if q:
                open_val = q['ohlc']['open']
                ltp_val = q['last_price']
                sector_status[idx] = "Bullish" if ltp_val >= open_val else "Bearish"
    except Exception as e:
        logging.warning(f"Failed to fetch sectoral indices: {e}")

    # Load Sector Map
    sector_map = {}
    sector_map_path = os.path.join("data", "cache", "sector_map.json")
    if os.path.exists(sector_map_path):
        try:
            with open(sector_map_path, "r") as f:
                sector_map = json.load(f)
        except Exception:
            pass
    
    if not token_map:
        return pd.DataFrame(), 0
        
    results = []
    to_date = datetime.datetime.now()
    from_date_intra = to_date - datetime.timedelta(days=10) 
    from_date_daily = to_date - datetime.timedelta(days=300)
    
    total_symbols = len(token_map)
    processed = 0
    
    # Pre-scan with batch OHLC
    if cache_indicators: 
        logging.info(f"Pre-screening {total_symbols} stocks with batch quotes...")
        all_tickers = [f"NSE:{s}" for s in token_map.keys()]
        try:
            quotes = kite.ohlc(all_tickers)
            filtered_tokens = {}
            for s, t in token_map.items():
                q = quotes.get(f"NSE:{s}")
                if q:
                    ltp = q['last_price']
                    p_high = cache_indicators[s]['Prev_Day_High']
                    p_low = cache_indicators[s]['Prev_Day_Low']
                    if ltp > p_high or ltp < p_low:
                        filtered_tokens[s] = t
            
            token_map = filtered_tokens
            logging.info(f"Pre-screen complete. {len(token_map)}/{total_symbols} stocks are active candidates.")
            total_symbols = len(token_map)
        except Exception as e:
            logging.warning(f"Batch pre-screen failed: {e}")

    for symbol, token in token_map.items():
        processed += 1
        if progress_callback:
            progress_callback(processed, total_symbols, symbol)
            
        # 1. Get Daily Data for Trend & Momentum
        if symbol in cache_indicators:
            daily_ema_20 = cache_indicators[symbol].get('EMA_20', 0)
            daily_ema_200 = cache_indicators[symbol]['EMA_200']
            daily_rsi = cache_indicators[symbol]['RSI_14']
            prev_day_high = cache_indicators[symbol]['Prev_Day_High']
            prev_day_low = cache_indicators[symbol]['Prev_Day_Low']
            prev_close = cache_indicators[symbol].get('Prev_Day_Close', cache_indicators[symbol].get('Prev_Close', 0))
        else:
            df_daily = fetch_kite_data(kite, token, from_date_daily, to_date, "day")
            if df_daily.empty or len(df_daily) < 200:
                continue
                
            df_daily.ta.ema(length=20, append=True)
            df_daily.ta.ema(length=200, append=True)
            df_daily.ta.rsi(length=14, append=True)
            
            latest_daily = df_daily.iloc[-1]
            daily_ema_20 = latest_daily['EMA_20']
            daily_ema_200 = latest_daily['EMA_200']
            daily_rsi = latest_daily['RSI_14']
            prev_day_high = df_daily.iloc[-2]['high']
            prev_day_low = df_daily.iloc[-2]['low']
            prev_close = df_daily.iloc[-2]['close']
        
        # 2. Fetch 5-minute data
        df_intra = fetch_kite_data(kite, token, from_date_intra, to_date, "5minute")
        if df_intra.empty or len(df_intra) < 100:
            continue
            
        df_intra['Vol_Avg_5'] = df_intra['volume'].rolling(window=20).mean()
        
        unique_dates = sorted(pd.Series(df_intra.index.date).unique())
        if len(unique_dates) < 2:
            continue
            
        today = unique_dates[-1]
        df_today = df_intra[df_intra.index.date == today]
        
        if len(df_today) < 4:
            continue
            
        first_candle = df_today.iloc[0]
        if prev_close > 0:
            gap_pct = ((first_candle['open'] - prev_close) / prev_close) * 100
            if abs(gap_pct) > 3.0:
                continue
        else:
            gap_pct = 0

        if len(df_today) < 2:
            continue
            
        current_vwap = calculate_vwap(df_today)
        orb_candles = df_today.iloc[0:3]
        
        if not (100 <= orb_candles.iloc[0]['close'] <= 5000):
            continue
            
        orb_high = orb_candles['high'].max()
        orb_low = orb_candles['low'].min()
        
        subsequent_candles = df_today.iloc[3:]
        
        breakout_type = None
        breakout_price = None
        breakout_time = None
        vol_ratio = 1.0
        sl_price = 0
        
        for i in range(len(subsequent_candles)):
            row = subsequent_candles.iloc[i]
            prev_row = df_today.iloc[i]
            timestamp = subsequent_candles.index[i]
            
            candles_before = df_today.iloc[:i+3]
            max_close_so_far = candles_before['close'].max()
            min_close_so_far = candles_before['close'].min()
            
            vol_ok = row['volume'] > (row['Vol_Avg_5'] * 1.5)
            candle_range = row['high'] - row['low']
            body_size = abs(row['close'] - row['open'])
            strength_ok = (body_size >= 0.5 * candle_range) if candle_range > 0 else False
            
            is_valid_long_gap = gap_pct >= -0.5
            is_valid_short_gap = gap_pct <= 0.5
            
            preceding_candles = df_today.iloc[i:i+3]
            preceding_low = preceding_candles['low'].min()
            tight_range = (preceding_candles['high'].max() - preceding_low) / preceding_low * 100 if preceding_low > 0 else 99
            is_consolidating = tight_range <= 1.20

            nifty_trend = sector_status.get("NIFTY 50", "Neutral")

            if not (vol_ok and strength_ok):
                continue

            # BULLISH BREAKOUT
            if row['close'] > orb_high:
                if max_close_so_far <= orb_high:
                    dist_pct = (row['close'] - orb_high) / orb_high * 100
                    if orb_high > prev_day_high and row['close'] > daily_ema_20 and daily_rsi > 55 and row['close'] > current_vwap:
                        if is_valid_long_gap and 0.05 <= dist_pct <= 1.5:
                            if nifty_trend in ["Bullish", "Neutral"] and is_consolidating:
                                sl_price = prev_row['low']
                                target_sector = sector_map.get(symbol, "NIFTY 50")
                                sec_trend = sector_status.get(target_sector, "Neutral")
                                is_in_sync = sec_trend in ["Bullish", "Neutral"]
                                
                                if is_in_sync:
                                    breakout_type = "Bullish (Strong Trend)"
                                    breakout_price = orb_high if row['low'] <= orb_high else row['close']
                                    breakout_time = timestamp.strftime("%H:%M")
                                    vol_ratio = row['volume'] / row['Vol_Avg_5']
                                    sl_price = prev_row['low']
                                    break
            
            # BEARISH BREAKOUT
            elif row['close'] < orb_low:
                if min_close_so_far >= orb_low:
                    dist_pct = (orb_low - row['close']) / orb_low * 100
                    if orb_low < prev_close and row['close'] < daily_ema_20 and daily_rsi < 50 and row['close'] < current_vwap:
                        if is_valid_short_gap and 0.05 <= dist_pct <= 1.5:
                            if nifty_trend in ["Bearish", "Neutral"] and is_consolidating:
                                sl_price = prev_row['high']
                                target_sector = sector_map.get(symbol, "NIFTY 50")
                                sec_trend = sector_status.get(target_sector, "Neutral")
                                is_in_sync = sec_trend in ["Bearish", "Neutral"]
                                
                                if is_in_sync:
                                    breakout_type = "Bearish (Strong Trend)"
                                    breakout_price = orb_low if row['high'] >= orb_low else row['close']
                                    breakout_time = timestamp.strftime("%H:%M")
                                    vol_ratio = row['volume'] / row['Vol_Avg_5']
                                    sl_price = prev_row['high']
                                    break
        
        if breakout_type:
            risk = abs(breakout_price - sl_price)
            if risk > 0:
                capital_per_trade = 250000.0
                qty = max(1, int(capital_per_trade / breakout_price))
                
                results.append({
                    "Ticker": symbol,
                    "Token": token,
                    "Breakout": breakout_type,
                    "ORB High": round(orb_high, 2),
                    "ORB Low": round(orb_low, 2),
                    "Breakout Price": round(breakout_price, 2),
                    "Breakout Time": breakout_time,
                    "Paper SL": round(sl_price, 2),
                    "Paper Qty": qty,
                    "Risk (₹)": round(risk * qty, 2),
                    "Vol Spike": f"{vol_ratio:.1f}x",
                    "RSI": round(daily_rsi, 1),
                    "Sector": sector_map.get(symbol, "NIFTY 50")
                })
                
    if results:
        res_df = pd.DataFrame(results)
        return res_df.sort_values(by="Vol Spike", ascending=False), len(token_map)
    else:
        return pd.DataFrame(), len(token_map)
