"""
utils/indicators.py — Shared technical indicator functions for all scanners.

Centralizes VWAP calculations that were previously duplicated across 8+ scanner files
with inconsistent implementations.  Import from here instead of defining locally.
"""

import pandas as pd


# ---------------------------------------------------------------------------
# VWAP helpers
# ---------------------------------------------------------------------------

def calculate_vwap_scalar(df: pd.DataFrame) -> float:
    """
    Compute a **single scalar VWAP** value for the given DataFrame.

    Formula: VWAP = Σ(Typical Price × Volume) / Σ(Volume)

    Use this for **end-of-session or single-candle checks** where you need one
    number representing the session's volume-weighted average price, e.g.:
        vwap = calculate_vwap_scalar(df_today)
        if close > vwap: ...

    Args:
        df: DataFrame with columns ['high', 'low', 'close', 'volume'].
            Assumed to contain data for a single trading session.

    Returns:
        VWAP as a float, or 0.0 if df is empty or volume is zero.
    """
    if df.empty:
        return 0.0
    total_volume = df['volume'].sum()
    if total_volume == 0:
        return 0.0
    tp = (df['high'] + df['low'] + df['close']) / 3
    return float((tp * df['volume']).sum() / total_volume)


def calculate_vwap_cumulative(df: pd.DataFrame) -> pd.Series:
    """
    Compute a **running (cumulative) VWAP** that resets at the start of each
    trading session.

    This is the correct VWAP for per-candle intraday checks.  The running VWAP
    at candle N reflects only the volume traded up to that point in the session,
    not the final end-of-day VWAP.

    Use this when checking whether a stock is trading above/below VWAP at each
    intermediate candle, e.g.:
        df['vwap'] = calculate_vwap_cumulative(df)
        if latest_candle['close'] > latest_candle['vwap']: ...

    Args:
        df: DataFrame with columns ['high', 'low', 'close', 'volume'] and a
            DatetimeIndex.  Can span multiple trading days — the calculation
            resets at midnight boundaries automatically.

    Returns:
        A pd.Series of per-candle cumulative VWAP values, aligned with df.index.
    """
    if df.empty:
        return pd.Series(dtype=float)

    df_calc = df.copy()
    tp = (df_calc['high'] + df_calc['low'] + df_calc['close']) / 3
    tpv = tp * df_calc['volume']

    # Group by calendar date so VWAP resets at session open each day
    date_groups = df_calc.index.date
    cum_tpv = tpv.groupby(date_groups).cumsum()
    cum_vol = df_calc['volume'].groupby(date_groups).cumsum()

    # Guard against zero-volume candles
    return cum_tpv / cum_vol.replace(0, float('nan'))
