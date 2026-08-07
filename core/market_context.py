"""
core/market_context.py — Standardized Broad Market Context & Nifty Trend Provider.

Consolidates Nifty 50 trend determination across all scanners into a single,
consistent definition:
- Requires 5-minute candle data for NIFTY 50
- Calculates 20 EMA and change % vs session Open
- Applies a ±0.10% buffer zone:
    - BULLISH: LTP > 20 EMA and Change % > +0.10%
    - BEARISH: LTP < 20 EMA and Change % < -0.10%
    - NEUTRAL: Inside buffer zone or mixed signals
"""

import datetime
import logging
from strategies import kite_scanner


class MarketContext:
    """Encapsulates broad market trend state and sector statuses."""

    def __init__(self, kite=None):
        self.kite = kite
        self._nifty_trend = None
        self._nifty_details = {}

    def check_nifty_trend(self, kite=None) -> str:
        """
        Returns 'BULLISH', 'BEARISH', or 'NEUTRAL' based on Nifty 50 5m price, 20 EMA, & 0.10% buffer alignment.
        Caches the result per instance to avoid redundant API calls during a single scan pass.
        """
        k = kite or self.kite
        if not k:
            logging.warning("No Kite instance provided for Nifty trend check.")
            return "NEUTRAL"

        if self._nifty_trend is not None:
            return self._nifty_trend

        try:
            nifty_token_map = kite_scanner.get_kite_instruments(k, ["NIFTY 50"])
            if nifty_token_map and "NIFTY 50" in nifty_token_map:
                nifty_token = nifty_token_map["NIFTY 50"]
                today = datetime.datetime.now()
                # Fetch last 3 days of 5-minute data to ensure EMA-20 is populated cleanly
                nifty_from = today - datetime.timedelta(days=3)
                nifty_df = kite_scanner.fetch_kite_data(k, nifty_token, nifty_from, today, "5minute")

                if not nifty_df.empty and len(nifty_df) >= 20:
                    nifty_df.columns = [c.lower() for c in nifty_df.columns]

                    import pandas_ta as ta
                    nifty_df.ta.ema(length=20, append=True)

                    latest = nifty_df.iloc[-1]
                    nifty_ltp = latest['close']
                    nifty_ema = latest['EMA_20']

                    # Determine Today's Open and Yesterday's Close to correctly capture gaps and intraday direction
                    today_rows = nifty_df[nifty_df.index.date == today.date()]
                    nifty_open = today_rows.iloc[0]['open'] if not today_rows.empty else nifty_ltp

                    unique_dates = sorted(list(set(nifty_df.index.date)))
                    if len(unique_dates) >= 2:
                        prev_day_rows = nifty_df[nifty_df.index.date == unique_dates[-2]]
                        nifty_prev_close = prev_day_rows.iloc[-1]['close']
                    else:
                        nifty_prev_close = nifty_open

                    # Introduce a 0.10% buffer zone around Yesterday's Close
                    nifty_change_pct = (nifty_ltp - nifty_prev_close) / nifty_prev_close
                    buffer = 0.0010

                    # Bullish requires: above EMA-20, daily return > +0.10%, AND price >= today's open
                    is_bullish = nifty_ltp > nifty_ema and nifty_change_pct > buffer and nifty_ltp >= nifty_open

                    # Bearish if: below EMA-20 and daily return < -0.10%, OR price is below both today's open and EMA-20 (intraday sell-off)
                    is_bearish = (nifty_ltp < nifty_ema and nifty_change_pct < -buffer) or (nifty_ltp < nifty_open and nifty_ltp < nifty_ema)

                    trend = "BULLISH" if is_bullish else ("BEARISH" if is_bearish else "NEUTRAL")

                    logging.info(
                        f"Broad Market Check -> Nifty LTP: {nifty_ltp:.2f} | Open: {nifty_open:.2f} | Prev Close: {nifty_prev_close:.2f} | "
                        f"20 EMA: {nifty_ema:.2f} | Change %: {nifty_change_pct*100:.3f}% | Trend: {trend}"
                    )

                    self._nifty_trend = trend
                    self._nifty_details = {
                        "ltp": nifty_ltp,
                        "open": nifty_open,
                        "prev_close": nifty_prev_close,
                        "ema_20": nifty_ema,
                        "change_pct": nifty_change_pct,
                        "trend": trend,
                    }
                    return trend
        except Exception as e:
            logging.error(f"Error checking Nifty trend in MarketContext: {e}")

        self._nifty_trend = "NEUTRAL"
        return "NEUTRAL"

    def is_bullish(self, kite=None) -> bool:
        return self.check_nifty_trend(kite) == "BULLISH"

    def is_bearish(self, kite=None) -> bool:
        return self.check_nifty_trend(kite) == "BEARISH"


def check_nifty_trend(kite) -> str:
    """Standalone module function for checking Nifty trend."""
    ctx = MarketContext(kite)
    return ctx.check_nifty_trend()


def is_nifty_bullish(kite) -> bool:
    """Standalone module function returning True if Nifty trend is BULLISH."""
    return check_nifty_trend(kite) == "BULLISH"


def is_nifty_bearish(kite) -> bool:
    """Standalone module function returning True if Nifty trend is BEARISH."""
    return check_nifty_trend(kite) == "BEARISH"
