# 🔍 ScannerPro-Dilip: Trading Logic & Technical Specifications Guide

This document serves as the complete technical reference and mathematical guide for the trading scanners in the ScannerPro-Dilip suite. It outlines the indicators, filters, entry/exit criteria, and risk parameters used by each strategy.

---

## Table of Contents
1. [15-Minute ORB (Opening Range Breakout)](#1-15-minute-orb-opening-range-breakout)
2. [52-Week High Breakout](#2-52-week-high-breakout)
3. [Morning Range Strength & Weakness Scanner](#3-morning-range-strength--weakness-scanner)
4. [15-Min Bullish Breakout](#4-15-min-bullish-breakout)
5. [15-Min Bearish Breakdown](#5-15-min-bearish-breakdown)
6. [Failed Breakout (Bull Trap Short)](#6-failed-breakout-bull-trap-short)
7. [Bullish VWAP Rejection (Intraday Long Pullback)](#7-bullish-vwap-rejection-intraday-long-pullback)
8. [Bearish VWAP Rejection (Intraday Short Pullback)](#8-bearish-vwap-rejection-intraday-short-pullback)
9. [3:15 PM Swing Setup](#9-315-pm-swing-setup)
10. [Minervini VCP Breakout](#10-minervini-vcp-breakout)
11. [Multi-Year Breakout Swing](#11-multi-year-breakout-swing)
12. [Volatility Contraction (3-Stage VCP)](#12-volatility-contraction-3-stage-vcp)
13. [General Swing & 20-Day Breakout Scanners](#13-general-swing--20-day-breakout-scanners)

---

## 1. 15-Minute ORB (Opening Range Breakout)
* **File Location**: [kite_scanner.py:scan_orb_setups](file:///f:/MyFinance/ScannerPro-Dilip/kite_scanner.py#L659)
* **Timeframe**: 5-minute candles (Intraday)

### Core Logic
* **Range Definition**: Computes the High and Low of the first three 5-minute candles (9:15 AM - 9:30 AM).
* **Pre-Market Filtering (`cache_orb_stocks`)**:
  * Price range: ₹100 to ₹5,000.
  * EMA Trend Stack: Close > 20 EMA > 50 EMA > 200 EMA (Bullish) or Close < 20 EMA < 50 EMA < 200 EMA (Bearish).
  * Volatility Check: Daily 14-period ATR >= 2.0% of the price.
  * Daily RSI: RSI_14 > 55 (Bullish) or RSI_14 < 45 (Bearish).
* **Trigger Conditions**:
  * **Bullish Breakout (Long)**:
    * 5-min Close > ORB High AND ORB High > Previous Day High (PDH).
    * Price > Daily 20 EMA AND Daily RSI > 55 AND Price > current VWAP.
    * Gap Filter: Today's Open is flat or positive vs Previous Day's Close (`gap_pct >= -0.5%` and `abs(gap_pct) <= 3.0%`).
    * Index & Sector Check: Nifty 50 trend is "Bullish" or "Neutral" (LTP >= Open). Target sector trend must not be Bearish.
  * **Bearish Breakout (Short)**:
    * 5-min Close < ORB Low AND ORB Low < Previous Day Close (PDC).
    * Price < Daily 20 EMA AND Daily RSI < 50 AND Price < current VWAP.
    * Gap Filter: Today's Open is flat or negative vs Previous Day's Close (`gap_pct <= 0.5%` and `abs(gap_pct) <= 3.0%`).
    * Index & Sector Check: Nifty 50 trend is "Bearish" or "Neutral" (LTP < Open). Target sector trend must not be Bullish.
* **Volume Spike & Candlestick Confirmation**:
  * Volume > 1.5x of the 20-candle 5-min Volume SMA.
  * Candle body size must be >= 50% of the total candle range (high - low).
  * **Cleanliness**: Must be the *first* candle of the day closing outside the ORB range.
  * **Slippage Buffer (No-Chase)**: Close price must be between 0.05% and 1.5% away from the breakout level.

### Execution & Risk Parameters
* **Entry**: Limit entry at the breakout level (`orb_high` / `orb_low`) if touched by the trigger candle's low/high; otherwise, market entry on close.
* **Stop Loss (SL)**: Low of the preceding candle (for Longs) or High of the preceding candle (for Shorts).
* **Target**: 1:2 Risk-to-Reward ratio.

---

## 2. 52-Week High Breakout
* **File Location**: [high52_scanner.py:scan_52w_breakouts](file:///f:/MyFinance/ScannerPro-Dilip/high52_scanner.py#L134)
* **Timeframe**: Daily (Caching) / 5-minute candles (Intraday Execution)

### Core Logic
* **Pre-Market Filter (Phase 1)**:
  * LTP between ₹100 and ₹5,000.
  * Trend: LTP > 20 EMA > 50 EMA > 200 EMA.
  * Proximity: Close is within 3.0% of the 52-week High.
  * Consolidation: Daily range of the last 10 completed sessions prior to today is <= 5.0%.
* **Intraday Execution (Phase 2 & 3)**:
  * Time Window: 9:45 AM - 2:45 PM.
  * Batch Pre-Screen: Streams quotes and only processes stocks where live price is >= 99.5% of their 52W High.
  * Trigger: 5-minute Close breaks the 52-Week High with a confirmation buffer of 0.1% and a max chase of 0.8% (`0.1% <= (close - 52W_High) / 52W_High * 100 <= 0.8%`).
  * Volume Spike: Volume > 2.5x of the 20-candle 5-min Volume SMA.
  * VWAP: Close > VWAP.
  * ATR Check: Daily 14-day ATR is >= 1.5% of the close price.
  * Sector Alignment: Target sector index must be Bullish (LTP >= Open).

### Execution & Risk Parameters
* **Capital**: ₹250,000 per trade.
* **Stop Loss (SL)**: Set via standard portfolio risk metrics (typically below breakout level or at VWAP).

---

## 3. Morning Range Strength & Weakness Scanner
* **File Location**: [morning_range_scanner.py:scan_morning_range](file:///f:/MyFinance/ScannerPro-Dilip/morning_range_scanner.py#L169)
* **Timeframe**: 5-minute candles (Intraday)

### Core Logic
* **Watchlist building (at 9:45 AM)**:
  * Scans the morning range (9:15 AM - 9:45 AM) for Nifty 500 F&O stocks.
  * Excludes stocks with low volatility (range width `High - Low < 0.5%` of price).
  * Classifications:
    * **STRONG**: 9:45 Close > 9:15 Open AND `(high_945 - close) / range_width <= 0.15` (Close is in the top 15% of the range).
    * **WEAK**: 9:45 Close < 9:15 Open AND `(close - low_945) / range_width <= 0.15` (Close is in the bottom 15% of the range).
* **Intraday Execution (Post-9:45 AM)**:
  * **Long Trigger (STRONG)**:
    * Nifty 50 Trend is Bullish today (LTP > Open).
    * 5-minute close > `high_945` AND price > current VWAP.
    * Volume Ratio: Trigger candle volume >= 1.5x of the average volume of the previous 5 candles.
  * **Short Trigger (WEAK)**:
    * Nifty 50 Trend is Bearish today (LTP <= Open).
    * 5-minute close < `low_945` AND price < current VWAP.
    * Volume Ratio: Trigger candle volume >= 1.5x of the average volume of the previous 5 candles.

### Execution & Risk Parameters
* **Entry**: Current price on breakout close.
* **Stop Loss (SL)**:
  * Long: `max(current_vwap, mid_point)` where `mid_point = (high_945 + low_945) / 2`.
  * Short: `min(current_vwap, mid_point)`.
* **Target**: Risk-Reward 1:2 (`entry + 2 * (entry - SL)` for Longs).
* **Position Size**: Fixed capital of ₹250,000 per trade (`Quantity = capital / entry`).

---

## 4. 15-Min Bullish Breakout
* **File Location**: [bullish_breakout_scanner.py:scan_bullish_breakouts](file:///f:/MyFinance/ScannerPro-Dilip/bullish_breakout_scanner.py#L129)
* **Timeframe**: 5-minute candles (Intraday)

### Core Logic
* **Pre-Market Filter (9:00 AM - 9:15 AM)**:
  * Shortlists stocks with Daily Close > 50 EMA and Daily RSI > 50.
  * Establishes volume baseline: calculates the 5-day average 9:15 AM (first 15 minutes) volume (`Avg_15m_Vol`).
* **Early Momentum Refresh (9:20 AM - 9:30 AM)**:
  * Discards weak opens: skips stocks if today's LTP < today's Open OR today's LTP < Yesterday's High * 0.995.
* **Intraday Execution (9:30 AM - 2:45 PM)**:
  * Range: Defines OR High/Low of first 15 mins (9:15 - 9:30 AM).
  * Pre-Screen: LTP >= `breakout_level * 0.995` where `breakout_level = max(or_high, Yesterday_High)`.
  * Broad Market Check: Nifty 50 trend is Bullish.
  * **Trigger Conditions**:
    1. **Breakout Type**:
       * *Fresh Breakout*: Current candle is the first close above the breakout level.
       * *Retest Recovery*: Price broke out earlier, pulled back to touch the breakout level (`low <= breakout_level`), and recovered back above it within the last 2 candles.
    2. **Volume Spike**: Volume of the first 15 minutes > `vol_spike_multiplier * Avg_15m_Vol` (multiplier = 1.2 if Nifty is bullish, 1.8 if volatile/bearish).
    3. **VWAP Alignment**: Close > VWAP.
    4. **Consolidation**: Preceding 3 candles must be tight (range of high/low is <= 0.50%).
    5. **No-Chase**: Discards signals if price has already moved > 0.8% above the breakout level.

### Execution & Risk Parameters
* **Entry**: Retest limit entry at `breakout_level` if the candle low touched it; otherwise, market entry on close.
* **Stop Loss (SL)**: Structural SL placed at `vwap * 0.998`. Enforces a minimum risk of 0.5% and a maximum risk of 2.5%.
* **Target**: Risk-Reward 1:2.

---

## 5. 15-Min Bearish Breakdown
* **File Location**: [bearish_breakdown_scanner.py:scan_bearish_breakdowns](file:///f:/MyFinance/ScannerPro-Dilip/bearish_breakdown_scanner.py#L133)
* **Timeframe**: 5-minute candles (Intraday)

### Core Logic
* **Pre-Market Filter (9:00 AM - 9:15 AM)**:
  * Shortlists stocks with Daily Close < 50 EMA OR Daily RSI < 55.
  * Establishes volume baseline: calculates the 5-day average 9:15 AM volume (`Avg_15m_Vol`).
* **Early Momentum Refresh (9:20 AM - 9:30 AM)**:
  * Price must be below Today's Open AND near/below Yesterday's Low (`today_ltp <= today_open` and `today_ltp <= Yesterday_Low * 1.002`).
* **Intraday Execution (9:30 AM - 2:45 PM)**:
  * Range: Defines OR Low/High of first 15 mins (9:15 - 9:30 AM).
  * Pre-Screen: LTP <= `breakdown_level * 1.005` where `breakdown_level = min(or_low, Yesterday_Low)`.
  * Broad Market Check: Nifty 50 trend is Bearish (LTP <= Open).
  * **Trigger Conditions**:
    1. **Breakdown Type**:
       * *Fresh Breakdown*: Current candle is the first close below the breakdown level.
       * *Retest Recovery*: Price broke down earlier, pulled back to touch the breakdown level (`high >= breakdown_level`), and recovered back below it within the last 2 candles.
    2. **Volume Spike**: Volume of first 15 minutes > `vol_spike_threshold * Avg_15m_Vol` (threshold = 1.8 if Nifty is bullish, 1.2 if Nifty is bearish/neutral).
    3. **VWAP Alignment**: Close < VWAP.
    4. **Intraday Oversold**: 5-minute RSI must be >= 30 (prevents selling the absolute bottom).
    5. **Daily Extension**: Price must NOT be down > 3.0% from yesterday's close.
    6. **Candle Shape**: Rejection candle must close in the lower half of its range (`close < (high + low) / 2`).
    7. **Consolidation**: Preceding 3 candles must be tight (range <= 0.50%).
    8. **No-Chase**: Discards signals if price has already dropped > 0.4% from the breakdown level.

### Execution & Risk Parameters
* **Entry**: Retest limit entry at `breakdown_level` if the candle high touched it; otherwise, market entry on close.
* **Stop Loss (SL)**: Structural SL placed at `vwap * 1.002`. Enforces a minimum risk of 0.5% and a maximum risk of 2.5%.
* **Target**: Risk-Reward 1:2.

---

## 6. Failed Breakout (Bull Trap Short)
* **File Location**: [failed_breakout_scanner.py:scan_failed_breakouts](file:///f:/MyFinance/ScannerPro-Dilip/failed_breakout_scanner.py#L130)
* **Timeframe**: 5-minute candles (Intraday)

### Core Logic
* **Pre-Market Strength Filter**:
  * Targets structurally strong stocks (Close > 50 EMA and RSI > 50) because they are the ones likely to attempt breakout levels, setting up potential Bull Traps if they fail.
* **Early Momentum Refresh (9:20 AM - 9:30 AM)**:
  * Price must show strength: Open positive (`LTP > Open`) and trade near Yesterday's High (`LTP >= Yesterday_High * 0.99`).
* **Intraday Execution (9:30 AM - 2:45 PM)**:
  * Resistance Level: `R = max(Yesterday_High, OR_High)`.
  * **Breakout Attempt Check**: At least one candle after 9:30 AM must have touched or closed above R (`high.max() > R`). Skips if no breakout attempt occurred.
  * **Trigger Conditions**:
    1. **Trap Trigger**: Confirmed candle close falls back *below* the resistance level R (`close < R`).
    2. **Bearish Rejection Shape**: Rejection candle close must be in the lower half of its range (`close < (high + low) / 2`) AND it must be a red candle OR have a long upper shadow (`upper_shadow > 1.5 * body_size`).
    3. **Breakout Duration Constraint**: The price must not have hovered above R for too long; consecutive candles closing above R before the trigger must be <= 4.
    4. **Volume Spike**: Rejection candle volume must be >= 1.5x of the 20-candle 5-min Volume SMA.
    5. **VWAP Alignment**: Price is below VWAP.
    6. **RSI Buffer**: 5-min RSI > 40 (room to fall, not oversold).
    7. **No-Chase**: Discards signals if price is already down > 0.4% below R.

### Execution & Risk Parameters
* **Entry**: Market price on the trap confirmation close.
* **Stop Loss (SL)**: Set at `failed_swing_high * 1.001` (where swing high is the peak of the breakout attempt). Risk is strictly bounded between a minimum of 0.5% and a maximum of 2.0%.
* **Target**: Risk-Reward 1:2.

---

## 7. Bullish VWAP Rejection (Intraday Long Pullback)
* **File Location**: [bullish_vwap_rejection.py:run_rejection_scanner](file:///f:/MyFinance/ScannerPro-Dilip/bullish_vwap_rejection.py#L195)
* **Timeframe**: 5-minute candles (Intraday)

### Core Logic
* **Trend Conditions**:
  * Stock is in a clear intraday uptrend: Close > VWAP.
  * VWAP > Previous Day's Close (PDC).
  * VWAP is sloping upward (higher than it was 3 candles ago).
* **Uptrend Validation**:
  * Stock must have previously broken above the 15-minute Opening Range High, OR crossed above VWAP on high volume (`prev_close < prev_vwap` and `close > vwap` and `volume > vol_ma20 * 1.2`).
* **Trigger Conditions**:
  * **Pullback Rejection Touch**: Low must pull back to touch either VWAP or the 9 EMA within a 0.2 * ATR buffer (`low <= vwap + 0.2 * atr_5m` and `high >= vwap - 0.2 * atr_5m`).
  * **Bullish Candlestick Pattern**: A Hammer, Bullish Engulfing, or Bullish Pin Bar pattern must form on the rejection candle.
  * **Safety Confirmation**:
    * Price is above both PDC and Today's Open (positive intraday trend).
    * RSI_5m <= 70 (not overbought).
    * Daily Gain < 3.0% from PDC (not overextended).
    * Close is in the upper 40% of the candle's range (`close > low + 0.6 * range`).
    * Volume > 0.8x of the 20-candle Volume SMA (institutional confirmation).
    * No-chase: Entry Close is within 0.4% of the rejection level (VWAP or 9 EMA).

### Execution & Risk Parameters
* **Stop Loss (SL)**: `min(swing_low_last_5_candles, vwap - 1.5 * atr_5m)`. The SL risk is mathematically bounded between 0.4% and 1.5% of the entry price.
* **Target**: Target 1 at 1.5R, Target 2 at 3.0R.

---

## 8. Bearish VWAP Rejection (Intraday Short Pullback)
* **File Location**: [bearish_vwap_rejection.py:run_rejection_scanner](file:///f:/MyFinance/ScannerPro-Dilip/bearish_vwap_rejection.py#L195)
* **Timeframe**: 5-minute candles (Intraday)

### Core Logic
* **Trend Conditions**:
  * Stock is in a clear intraday downtrend: Close < VWAP.
  * VWAP < Previous Day's Close (PDC).
  * VWAP is sloping downward (lower than it was 3 candles ago).
* **Downtrend Validation**:
  * Stock must have previously broken below the 15-minute Opening Range Low, OR crossed below VWAP on high volume (`prev_close > prev_vwap` and `close < vwap` and `volume > vol_ma20 * 1.2`).
* **Trigger Conditions**:
  * **Pullback Rejection Touch**: High must pull back to touch either VWAP or the 9 EMA within a 0.2 * ATR buffer (`high >= vwap - 0.2 * atr_5m` and `low <= vwap + 0.2 * atr_5m`).
  * **Bearish Candlestick Pattern**: A Shooting Star, Bearish Engulfing, or Bearish Pin Bar pattern must form on the rejection candle.
  * **Safety Confirmation**:
    * Price is below both PDC and Today's Open (negative intraday trend).
    * RSI_5m >= 30 (not oversold).
    * Daily Loss > -3.0% from PDC (not overextended).
    * Close is in the lower 40% of the candle's range (`close < high - 0.6 * range`).
    * Volume > 0.8x of the 20-candle Volume SMA.
    * No-chase: Entry Close is within 0.4% of the rejection level (VWAP or 9 EMA).

### Execution & Risk Parameters
* **Stop Loss (SL)**: `max(swing_high_last_5_candles, vwap + 1.5 * atr_5m)`. The SL risk is mathematically bounded between 0.4% and 1.5% of the entry price.
* **Target**: Target 1 at 1.5R, Target 2 at 3.0R.

---

## 9. 3:15 PM Swing Setup
* **File Location**: [kite_scanner.py:scan_315_setups](file:///f:/MyFinance/ScannerPro-Dilip/kite_scanner.py#L325)
* **Timeframe**: Daily + Intraday (Execution)

### Core Logic
* **Scan Timing**: Executed between 3:10 PM and 3:25 PM daily.
* **Batch Pre-Screen**:
  * Price range: ₹100 to ₹5,000.
  * Positive Day: Price > Open.
  * Proximity: Close is within 1.0% of the Daily High.
* **Refined Trend & Momentum Filters**:
  * **EMA Alignment**: Close > 50 EMA and Close > 200 EMA (Long-term structural uptrend).
  * **Daily RSI**: RSI_14 is between 60 and 80 (strong but not fully exhausted momentum).
  * **Volume Anomaly**: Today's total volume > 1.5x of the 20-day Volume SMA.
  * **Closing Conviction**: Latest price must be within 2.0% of today's high (`ltp >= day_high * 0.98`).

### Execution & Risk Parameters
* **Entry**: Market price on setup confirmation.
* **Stop Loss (SL)**: `max(Entry * 0.96, Yesterday's Low)`.
* **Target**: Fixed swing target of 9.0% (`Entry * 1.09`).

---

## 10. Minervini VCP Breakout
* **File Location**: [minervini_vcp_scanner.py:scan_minervini_vcp](file:///f:/MyFinance/ScannerPro-Dilip/minervini_vcp_scanner.py#L49)
* **Timeframe**: Daily (End-of-Day Swing)

### Core Logic
* **Trend Template Rules (Mark Minervini)**:
  1. Price > 150-day SMA AND Price > 200-day SMA.
  2. 150-day SMA > 200-day SMA.
  3. 200-day SMA is trending upward: `SMA_200 > SMA_200_20d_ago` (20 trading days ago).
  4. Price > 50-day SMA.
  5. Price is at least 30% above the 52-week Low.
  6. Price is within 25% of the 52-week High.
* **Volatility Contraction Pattern (VCP) Proxy**:
  * **Volatility Contraction**: Average daily range (High - Low) of the last 10 days is tighter than the trading range 30 days ago.
  * **Volume Contraction**: Average volume of the last 10 days is lower than the 50-day average volume (volume dry-up / quiet accumulation).
* **The Breakout Trigger**:
  * Price Action: Close breaks above the highest high of the last 20 days (pivot level, excluding today).
  * Volume Surge: Today's volume >= 150% of the 50-day average volume.

### Execution & Risk Parameters
* **Stop Loss (SL)**: `max(pivot_level * 0.965, lowest_low_last_20_days)`.
* **Targets**: Target 1 at 1.5R, Target 2 at 3.0R.

---

## 11. Multi-Year Breakout Swing
* **File Location**: [multi_year_breakout_scanner.py:scan_multi_year_breakouts](file:///f:/MyFinance/ScannerPro-Dilip/multi_year_breakout_scanner.py#L11)
* **Timeframe**: Daily (End-of-Day Swing)

### Core Logic
* **Multi-Year High (MYH)**:
  * Calculated as the maximum high over the previous 500 trading days (~2 Years), shifted by 5 days to exclude the current week's price action.
* **Weekly Breakout & Holding Up**:
  * **Recent Breakout**: At least one daily Close or High in the last 5 sessions must be > `MYH`.
  * **Proximity Check**: Price is consolidating and holding near the breakout level: `MYH * 0.99 <= Close <= MYH * 1.05`.
  * **Institutional Volume Spike**: Peak volume in the last 5 days >= 2.5x of the shifted 20-day Volume SMA OR 5-day average volume >= 1.5x of the shifted SMA.
* **Trend Strength & Alignment**:
  * Trend: Close > 50 EMA and Close > 200 EMA.
  * Momentum: DMP_14 > DMN_14 (Positive DI is above Negative DI; ADX filter is relaxed since ADX lags fresh breakouts).

### Execution & Risk Parameters
* **Stop Loss (SL)**: `max(MYH * 0.965, Close - 1.8 * ATR_14)`.
* **Targets**: Target 1 at 1.5R, Target 2 at 3.0R.

---

## 12. Volatility Contraction (3-Stage VCP)
* **File Location**: [volatility_contraction_scanner.py](file:///f:/MyFinance/ScannerPro-Dilip/volatility_contraction_scanner.py)
* **Timeframe**: Daily (Caching) + WebSocket ticks (Intraday Live Monitor)

### Stage 1: End-of-Day (EOD) Proximity Filter
* **Liquidity Filter**: 20-day Average Volume > 500,000 shares.
* **Range Tightness**: The entire 20-day rolling High-to-Low range must be tight: `(resistance - support) / support <= 12%`.
* **Proximity**: Price must be within 3% of the 20-day resistance (High) or support (Low).
* **EMA & RSI Trend Confirmation**:
  * Price > 50 EMA and RSI_14 > 50 (for Resistance/Breakout setups).
  * Price < 50 EMA and RSI_14 < 50 (for Support/Breakdown setups).

### Stage 2: Setup Validation (Volatility Contraction)
* **ATR Contraction**: 5-day Wilder's smoothed ATR < 14-day Wilder's smoothed ATR.
* **Squeeze Tightness**: 5-day ATR represents <= 3.5% of the stock price (`(atr_5 / close) * 100 <= 3.5%`).
* **Volume Dry-Up**: 5-day Volume SMA < 20-day Volume SMA.
* **Horizontal Price Consolidation**: 5-day closing price range is tight (`(max_close - min_close) / min_close * 100 <= 4.0%`).
* **Pivot Contraction Waves**: A peak/trough wave analysis over the last 40 days must show decreasing peak-to-trough depths (`latest_depth < previous_depth`).

### Stage 3: Intraday WebSocket Monitor
* Streams real-time prices during market hours (9:30 AM - 2:45 PM).
* **Bullish Breakout (Buy)**:
  * Last price >= 20-day High (resistance).
  * Filter: Price > Today's Open.
  * Range Filter: Today's High-to-Low range <= 3.0% (skips if it's already too volatile today).
  * Extension Filter: Daily gain from Previous Close and Open <= 5.5%.
  * SL: `last_price - 1.0 * atr_5`, capped at max 2.0% loss (`max(atr_sl, last_price * 0.98)`).
  * Target: Fixed 2.0% profit target.
* **Bearish Breakdown (Sell)**:
  * Last price <= 20-day Low (support).
  * Filter: Price < Today's Open.
  * Range Filter: Today's High-to-Low range <= 3.0%.
  * Extension Filter: Daily loss from Previous Close and Open <= 5.5%.
  * SL: `last_price + 1.0 * atr_5`, capped at max 2.0% loss (`min(atr_sl, last_price * 1.02)`).
  * Target: Fixed 2.0% profit target.

---

## 13. General Swing & 20-Day Breakout Scanners
* **File Location**: [scanner.py](file:///f:/MyFinance/ScannerPro-Dilip/scanner.py)
* **Timeframe**: Daily (End-of-Day)

### Pullback & MACD Swing setups (`scan_swing_candidates`)
* **Price Range**: ₹100 to ₹5,000.
* **Uptrend Filter**: Close > 50 SMA and Close > 200 SMA.
* **Pullback Setup**: RSI_14 < 50 AND Bullish Reversal (Close > Previous High).
* **Momentum Setup**: MACD line crosses above Signal line AND Volume > 20-day Volume SMA AND Bullish Reversal (Close > Previous High).
* **Risk Management**: Target and Stop Loss are evaluated based on individual chart supports.

### 20-Day High Breakout (`scan_breakout_stocks`)
* **Price Range**: ₹100 to ₹5,000.
* **Price Breakout**: Close > Previous 20-day High.
* **Volume Breakout**: Volume > 1.5x of the 20-day Volume SMA.

---
*Created for: Dilip*  
*Last Updated: 2026-07-14*
