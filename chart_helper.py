import mplfinance as mpf
import pandas as pd
import os
import datetime

def generate_intraday_chart(df, ticker, scan_name, output_path="temp_chart.png"):
    """
    Generates a professional candlestick chart using mplfinance.
    Looks like a real charting platform.
    """
    if df.empty:
        return None
        
    try:
        # Ensure column names are correct for mplfinance
        df.columns = [c.lower() for c in df.columns]
        
        # Define the style
        # 'charles' is a professional candlestick style (green/red)
        mc = mpf.make_marketcolors(up='g', down='r',
                                 edge='inherit',
                                 wick='inherit',
                                 volume='in',
                                 ohlc='inherit')
        
        s = mpf.make_mpf_style(marketcolors=mc, gridstyle='--', 
                             base_mpf_style='nightclouds',
                             facecolor='#0d1117', # Dark GitHub-style background
                             edgecolor='#30363d')

        # Add VWAP as an overlay if available
        addplots = []
        if 'vwap' in df.columns:
            # Bold, distinguished yellow line for VWAP
            addplots.append(mpf.make_addplot(df['vwap'], color='#ffdf00', linestyle='-', width=2.0, alpha=0.9))

        # Save to file
        mpf.plot(df, 
                 type='candle', 
                 style=s,
                 title=f"\n{ticker} - {scan_name}",
                 addplot=addplots,
                 figsize=(10, 6),
                 savefig=dict(fname=output_path, dpi=120, bbox_inches='tight'),
                 volume=True,
                 tight_layout=True,
                 show_nontrading=False, # This hides the gaps between days and pre/post market
                 datetime_format='%H:%M') # Force HH:MM format
                 
        return output_path
    except Exception as e:
        print(f"Error generating chart with mplfinance: {e}")
        # Fallback to a very basic chart if needed, but mplfinance should work
        return None

def resample_to_15m(df_5m):
    """Resample 5-minute data to 15-minute for the chart."""
    if df_5m.empty:
        return df_5m
    
    # Resample OHLC and sum Volume
    # Ensure index is datetime and localized to IST if not already
    if not isinstance(df_5m.index, pd.DatetimeIndex):
        df_5m.index = pd.to_datetime(df_5m.index)
    
    # If it's UTC or naive, convert/localize to IST (Asia/Kolkata)
    if df_5m.index.tz is None:
        df_5m.index = df_5m.index.tz_localize('UTC').tz_convert('Asia/Kolkata')
    else:
        df_5m.index = df_5m.index.tz_convert('Asia/Kolkata')
    
    # STRIP TIMEZONE (Make it naive IST so mplfinance doesn't shift it)
    df_5m.index = df_5m.index.tz_localize(None)
        
    resampled = df_5m.resample('15min').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()
    
    # --- CORRECT INTRADAY VWAP CALCULATION (Resets Daily) ---
    resampled['tp'] = (resampled['high'] + resampled['low'] + resampled['close']) / 3
    resampled['tpv'] = resampled['tp'] * resampled['volume']
    
    # Reset cumsum per day
    grouped = resampled.groupby(resampled.index.date)
    resampled['vwap'] = grouped['tpv'].cumsum() / grouped['volume'].cumsum()
    
    # Cleanup temp columns
    resampled.drop(columns=['tp', 'tpv'], inplace=True)
    
    return resampled
