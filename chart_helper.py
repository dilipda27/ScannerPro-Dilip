import mplfinance as mpf
import pandas as pd
import os
import datetime

def generate_intraday_chart(df, ticker, scan_name, output_path="temp_chart.png", 
                            entry_price=None, sl_price=None, signal_type=None, trigger_time=None):
    """
    Generates a professional candlestick chart using mplfinance.
    Looks like a real charting platform with trendlines and signal arrows.
    """
    if df.empty:
        return None
        
    try:
        # Ensure column names are correct for mplfinance
        df.columns = [c.lower() for c in df.columns]
        
        # Define the style
        mc = mpf.make_marketcolors(up='g', down='r',
                                 edge='inherit',
                                 wick='inherit',
                                 volume='in',
                                 ohlc='inherit')
        
        s = mpf.make_mpf_style(marketcolors=mc, gridstyle='--', 
                             base_mpf_style='nightclouds',
                             facecolor='#0d1117', # Dark GitHub-style background
                             edgecolor='#30363d')

        # Add overlays (VWAP + Signal arrow)
        addplots = []
        if 'vwap' in df.columns:
            addplots.append(mpf.make_addplot(df['vwap'], color='#ffdf00', linestyle='-', width=2.0, alpha=0.9))

        # Add Buy/Sell signal arrow marker if signal details are provided
        if signal_type and trigger_time is not None:
            trigger_idx = None
            if isinstance(trigger_time, str):
                # Try to match timezone-stripped date/time string format
                for idx in df.index:
                    if trigger_time in str(idx) or idx.strftime('%H:%M') == trigger_time:
                        trigger_idx = idx
                        break
            else:
                try:
                    # Safely convert to a tz-naive Timestamp
                    t_time = pd.Timestamp(trigger_time)
                    if t_time.tz is not None:
                        t_time = t_time.tz_convert('Asia/Kolkata').tz_localize(None)
                    
                    # Snap to the nearest datetime index point (completely immune to non-monotonic indexes)
                    time_diffs = abs(df.index - t_time)
                    nearest_pos = time_diffs.argmin()
                    trigger_idx = df.index[nearest_pos]
                except Exception as ex:
                    print(f"Error snapping trigger time: {ex}")
            
            # Default to the most recent candle if not matched
            if trigger_idx is None:
                trigger_idx = df.index[-1]
                
            # Construct marker series with NaN everywhere except at trigger index
            marker_series = pd.Series(index=df.index, dtype=float)
            if signal_type == "BUY":
                # Draw green up arrow below the candle low
                marker_series.loc[trigger_idx] = df.loc[trigger_idx, 'low'] * 0.997
                addplots.append(mpf.make_addplot(marker_series, type='scatter', marker='^', markersize=180, color='#10b981'))
            elif signal_type == "SELL":
                # Draw red down arrow above the candle high
                marker_series.loc[trigger_idx] = df.loc[trigger_idx, 'high'] * 1.003
                addplots.append(mpf.make_addplot(marker_series, type='scatter', marker='v', markersize=180, color='#ef4444'))

        # Prepare horizontal lines (Entry and SL)
        hlines_list = []
        colors_list = []
        if entry_price is not None:
            try:
                hlines_list.append(float(entry_price))
                colors_list.append('#3b82f6') # Blue for Entry Price
            except: pass
        if sl_price is not None:
            try:
                hlines_list.append(float(sl_price))
                colors_list.append('#ef4444') # Red for Stop Loss (SL)
            except: pass
            
        hlines_spec = None
        if hlines_list:
            hlines_spec = dict(hlines=hlines_list,
                               colors=colors_list,
                               linestyle='--',
                               linewidths=[1.5] * len(hlines_list))

        # Save to file
        mpf.plot(df, 
                 type='candle', 
                 style=s,
                 title=f"\n{ticker} - {scan_name}",
                 addplot=addplots,
                 hlines=hlines_spec,
                 figsize=(10, 6),
                 savefig=dict(fname=output_path, dpi=120, bbox_inches='tight'),
                 volume=True,
                 tight_layout=True,
                 show_nontrading=False,
                 datetime_format='%d-%b %H:%M')
                 
        return output_path
    except Exception as e:
        print(f"Error generating chart with mplfinance: {e}")
        # Fallback to a very basic chart if needed, but mplfinance should work
        return None

def resample_to_15m(df_5m):
    """Resample 5-minute data to 15-minute for the chart."""
    if df_5m.empty:
        return df_5m
    
    # Prevent mutating the original dataframe
    df_5m = df_5m.copy()
    
    # Ensure index is sorted chronologically
    df_5m = df_5m.sort_index()
    
    # Ensure index is datetime and localized to IST if not already
    if not isinstance(df_5m.index, pd.DatetimeIndex):
        df_5m.index = pd.to_datetime(df_5m.index)
    
    # If it's naive, localize directly to Asia/Kolkata (do not assume UTC and shift by +5.5 hrs!)
    if df_5m.index.tz is None:
        df_5m.index = df_5m.index.tz_localize('Asia/Kolkata')
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
