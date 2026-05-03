import pandas as pd
import pandas_ta as ta
import datetime
import time
import logging
import requests
import io
from kiteconnect import KiteConnect

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_nifty500_symbols():
    """Fetch Nifty 500 list from NSE."""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    url_500 = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
    try:
        r_500 = requests.get(url_500, headers=headers, timeout=10)
        df_500 = pd.read_csv(io.StringIO(r_500.text))
        nifty500_symbols = list(df_500['Symbol'].str.strip())
        return nifty500_symbols
    except Exception as e:
        logging.error(f"Error fetching Nifty 500: {e}")
        # Fallback small list for testing
        return ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK"]

def get_kite_instruments(kite, symbols):
    """
    Fetch all NSE instruments from Kite and filter out those that are in the symbols list.
    Returns a dict mapping trading symbol to instrument_token.
    """
    try:
        instruments = kite.instruments("NSE")
        df_instruments = pd.DataFrame(instruments)
        
        # Filter instruments for our required symbols
        df_filtered = df_instruments[df_instruments['tradingsymbol'].isin(symbols)]
        
        # Create a mapping of tradingsymbol -> instrument_token
        token_map = dict(zip(df_filtered['tradingsymbol'], df_filtered['instrument_token']))
        return token_map
    except Exception as e:
        logging.error(f"Error fetching instruments from Kite: {e}")
        return {}

def fetch_kite_data(kite, instrument_token, from_date, to_date, interval):
    """
    Fetch historical data from Kite with rate limit handling.
    Kite limit is typically 3 requests per second.
    """
    try:
        data = kite.historical_data(
            instrument_token=instrument_token,
            from_date=from_date,
            to_date=to_date,
            interval=interval,
            continuous=False,
            oi=False
        )
        time.sleep(0.35) # Ensure we don't breach 3 req/sec limit
        
        if data:
            df = pd.DataFrame(data)
            df['date'] = pd.to_datetime(df['date'])
            # Set date as index
            df.set_index('date', inplace=True)
            return df
        return pd.DataFrame()
    except Exception as e:
        logging.error(f"Error fetching data for token {instrument_token}: {e}")
        return pd.DataFrame()

def scan_315_setups(kite, progress_callback=None):
    """
    Core scanning logic for 3:15 PM setup.
    """
    logging.info("Starting 3:15 PM Nifty 500 Scan...")
    
    symbols = get_nifty500_symbols()
    token_map = get_kite_instruments(kite, symbols)
    
    if not token_map:
        logging.error("Failed to retrieve instrument tokens. Aborting scan.")
        return pd.DataFrame()
        
    results = []
    
    # Timeframes
    to_date = datetime.datetime.now()
    from_date_daily = to_date - datetime.timedelta(days=300) # Fetch 300 days to ensure enough data for 200 EMA
    from_date_intraday = to_date - datetime.timedelta(days=5) # Fetch last 5 days to handle weekends/holidays
    
    total_symbols = len(token_map)
    processed = 0
    
    for symbol, token in token_map.items():
        processed += 1
        if processed % 50 == 0:
            logging.info(f"Processed {processed}/{total_symbols} stocks...")
            
        if progress_callback:
            progress_callback(processed, total_symbols, symbol)
            
        # 1. Fetch Daily Data
        df_daily = fetch_kite_data(kite, token, from_date_daily, to_date, "day")
        if df_daily.empty or len(df_daily) < 200:
            continue
            
        # Calculate Daily Indicators
        df_daily.ta.ema(length=50, append=True)
        df_daily.ta.ema(length=200, append=True)
        df_daily.ta.rsi(length=14, append=True)
        df_daily['Vol_SMA_20'] = df_daily['volume'].rolling(window=20).mean()
        
        # Get latest daily metrics (which includes today's partial data if fetched today)
        latest_daily = df_daily.iloc[-1]
        ltp_now = latest_daily['close']
        
        # Price Filter: Avoid penny stocks or illiquid heavyweights
        if not (100 <= ltp_now <= 5000):
            continue
        
        # Condition 1: Trend Filter (Daily)
        if latest_daily['close'] <= latest_daily['EMA_50'] or latest_daily['close'] <= latest_daily['EMA_200']:
            continue
            
        # Condition 2: Momentum Filter (Daily)
        rsi = latest_daily['RSI_14']
        if pd.isna(rsi) or not (60 <= rsi <= 80):
            continue
            
        # 2. Fetch Intraday Data (5-minute)
        df_intra = fetch_kite_data(kite, token, from_date_intraday, to_date, "5minute")
        if df_intra.empty:
            continue
            
        # Handle weekends/holidays by only looking at the last available trading day's intraday data
        last_trading_date = df_intra.index[-1].date()
        df_intra = df_intra[df_intra.index.date == last_trading_date]
        
        if df_intra.empty:
            continue
            
        # Ensure we are looking at data up to 3:15 PM roughly
        latest_intra = df_intra.iloc[-1]
        ltp = latest_intra['close']
        
        # Calculate Today's Day High (from intraday data)
        day_high = df_intra['high'].max()
        
        # Condition 3: Volume Anomaly
        # Today's total volume so far
        today_volume = df_intra['volume'].sum()
        avg_vol_20 = latest_daily['Vol_SMA_20']
        
        # Avoid division by zero
        if pd.isna(avg_vol_20) or avg_vol_20 == 0:
            continue
            
        vol_spike_ratio = today_volume / avg_vol_20
        if vol_spike_ratio <= 1.5:
            continue
            
        # Condition 4: Closing Conviction (Intraday)
        # LTP must be within 2% of the day's High
        if ltp < (day_high * 0.98):
            continue
            
        # All conditions met! Calculate Risk Management
        entry_price = ltp
        target_price = entry_price * 1.09 # Approx 9% target (middle of 8-10%)
        
        # SL: 4% below entry or previous day's low
        sl_fixed = entry_price * 0.96
        prev_day_low = df_daily.iloc[-2]['low']
        prev_close = df_daily.iloc[-2]['close']
        stop_loss = max(sl_fixed, prev_day_low) # "whichever is closer" means the higher value of the two below the price
        
        # Calculate % Gain for the day
        percent_gain = ((entry_price - prev_close) / prev_close) * 100
        
        results.append({
            "Ticker": symbol,
            "LTP": round(entry_price, 2),
            "% Gain": round(percent_gain, 2),
            "Day High": round(day_high, 2),
            "RSI (Daily)": round(rsi, 2),
            "Volume Spike Ratio": round(vol_spike_ratio, 2),
            "Target": round(target_price, 2),
            "Stop Loss": round(stop_loss, 2)
        })

    logging.info(f"Scan complete. Found {len(results)} candidates.")
    return pd.DataFrame(results)

def scan_orb_setups(kite, progress_callback=None):
    """
    Opening Range Breakout (ORB) - 15 minute strategy with strength filters.
    Filters:
    1. LTP > 15-min ORB High
    2. ORB High > Previous Day High (Strength)
    3. Breakout Candle Volume > Average 15-min Volume (Momentum)
    """
    logging.info("Starting 15-Min ORB Scan with filters...")
    
    symbols = get_nifty500_symbols()
    token_map = get_kite_instruments(kite, symbols)
    
    if not token_map:
        return pd.DataFrame()
        
    results = []
    to_date = datetime.datetime.now()
    # Fetch 10 days to ensure we have enough candles for volume average and previous day high
    from_date = to_date - datetime.timedelta(days=10) 
    
    total_symbols = len(token_map)
    processed = 0
    
    for symbol, token in token_map.items():
        processed += 1
        if progress_callback:
            progress_callback(processed, total_symbols, symbol)
            
        # Fetch 15-minute data
        df = fetch_kite_data(kite, token, from_date, to_date, "15minute")
        if df.empty or len(df) < 50: # Ensure enough data for moving averages
            continue
            
        # Calculate 20-period Average 15-min Volume
        df['Vol_Avg_15'] = df['volume'].rolling(window=20).mean()
        
        # Identify the most recent trading day and the day before it
        unique_dates = sorted(pd.Series(df.index.date).unique())
        if len(unique_dates) < 2:
            continue
            
        today = unique_dates[-1]
        prev_day = unique_dates[-2]
        
        df_today = df[df.index.date == today]
        df_prev = df[df.index.date == prev_day]
        
        if len(df_today) < 2:
            continue
            
        prev_day_high = df_prev['high'].max()
        prev_day_low = df_prev['low'].min()
        
        # ORB Candle (9:15-9:30)
        first_candle = df_today.iloc[0]
        
        # Price Filter: Avoid penny stocks or illiquid heavyweights
        if not (100 <= first_candle['close'] <= 5000):
            continue
            
        orb_high = first_candle['high']
        orb_low = first_candle['low']
        avg_vol = first_candle['Vol_Avg_15'] # Avg volume at the start of today
        
        subsequent_candles = df_today.iloc[1:]
        
        breakout_type = None
        breakout_price = None
        breakout_time = None
        vol_ratio = 1.0
        
        for i in range(len(subsequent_candles)):
            timestamp = subsequent_candles.index[i]
            row = subsequent_candles.iloc[i]
            
            # Previous candle is either the ORB candle (if i=0) or the candle before the current breakout
            prev_row = df_today.iloc[i] # Because df_today index 0 is ORB, and subsequent starts at 1
            
            # BULLISH FILTERS
            if row['close'] > orb_high:
                if orb_high > prev_day_high:
                    if row['volume'] > row['Vol_Avg_15']:
                        breakout_type = "Bullish (High Strength)"
                        breakout_price = row['close']
                        breakout_time = timestamp.strftime("%H:%M")
                        vol_ratio = row['volume'] / row['Vol_Avg_15']
                        # SL = Previous candle Low
                        sl_price = prev_row['low']
                        break
            
            # BEARISH FILTERS
            elif row['close'] < orb_low:
                if orb_low < prev_day_low:
                    if row['volume'] > row['Vol_Avg_15']:
                        breakout_type = "Bearish (High Strength)"
                        breakout_price = row['close']
                        breakout_time = timestamp.strftime("%H:%M")
                        vol_ratio = row['volume'] / row['Vol_Avg_15']
                        # SL = Previous candle High
                        sl_price = prev_row['high']
                        break
                
        if breakout_type:
            # Capital Allocation: 1,00,000 per trade
            entry_price = breakout_price
            qty = round(100000 / entry_price) if entry_price > 0 else 0
            
            results.append({
                "Ticker": symbol,
                "LTP": round(df_today.iloc[-1]['close'], 2),
                "ORB High": round(orb_high, 2),
                "ORB Low": round(orb_low, 2),
                "Breakout": breakout_type,
                "Breakout Price": round(breakout_price, 2),
                "Breakout Time": breakout_time,
                "Paper Qty": qty,
                "Paper SL": round(sl_price, 2),
                "Vol Spike": round(vol_ratio, 2),
                "% Gain": round(((df_today.iloc[-1]['close'] - df_today.iloc[0]['open']) / df_today.iloc[0]['open']) * 100, 2)
            })
            
    logging.info(f"Filtered ORB Scan complete. Found {len(results)} candidates.")
    return pd.DataFrame(results)

def execute_buy_order(kite, ticker, qty, price=None, order_type="MARKET"):
    """
    Placeholder for automated market/limit order execution logic.
    """
    logging.info(f"Placeholder: Executing {order_type} BUY order for {ticker}, Qty: {qty}")
    # Example Kite API call for placing order:
    # try:
    #     order_id = kite.place_order(
    #         tradingsymbol=ticker,
    #         exchange=kite.EXCHANGE_NSE,
    #         transaction_type=kite.TRANSACTION_TYPE_BUY,
    #         quantity=qty,
    #         variety=kite.VARIETY_REGULAR,
    #         order_type=kite.ORDER_TYPE_MARKET if order_type == "MARKET" else kite.ORDER_TYPE_LIMIT,
    #         product=kite.PRODUCT_CNC, # CNC for delivery/swing
    #         validity=kite.VALIDITY_DAY,
    #         price=price
    #     )
    #     logging.info(f"Order placed. ID is: {order_id}")
    #     return order_id
    # except Exception as e:
    #     logging.error(f"Order placement failed: {e}")
    #     return None
    pass

