# 🔍 ScannerPro-Dilip: Trading Logic & Optimization Report

This document outlines the core mathematical logic, indicators, and filters used in each scanner of the ScannerPro-Dilip suite. It also identifies areas for future improvement.

---

## 1. ⚡ 15-Minute ORB (Opening Range Breakout)
The ORB scanner is designed to capture the first major expansion of price after the initial morning volatility.

### Core Logic:
- **Range Definition**: Calculates the High and Low of the first three 5-minute candles (9:15 AM - 9:30 AM).
- **Trend Filters**:
    - **Bullish**: Daily Price > EMA 20, Daily RSI > 55, and ORB High > Previous Day High.
    - **Bearish**: Daily Price < EMA 20, Daily RSI < 50, and ORB Low < Previous Day Close.
- **Intraday Confirmation**:
    - **Volume Spike**: Breakout candle volume must be > 1.5x the average volume of the previous 5 candles.
    - **VWAP**: Price must be above VWAP for Longs and below VWAP for Shorts.
- **Slippage Control (The 0.8% Buffer)**:
    - **Confirmation**: Breakout candle must close at least 0.1% beyond the level.
    - **No-Chase**: Discards signals if the candle closes > 0.8% away from the level.

### Improvised Suggestions:
- **Multi-Timeframe Sync**: Confirm the 5-min breakout with 15-min RSI momentum.
- **Auto-Stop Loss**: Move SL to VWAP once the trade moves 1% in favor.

---

## 2. 🚀 52-Week High Breakout
This scanner identifies stocks showing multi-month structural strength and breaking into "blue sky" territory.

### Core Logic:
- **Daily Pre-Screen**: Filters Nifty 500 stocks that are within 3% of their 52-week highs.
- **Indicator Stack**:
    - **Trend**: LTP > EMA 20 > EMA 50 > EMA 200 (Full Bullish Stack).
    - **Relative Volume (RVOL)**: Breakout volume must be > 2.5x the 20-day average.
- **Execution Filters**:
    - **ATR %**: Requires a minimum ATR of 1.5% to ensure the stock has enough "juice" to move.
    - **VWAP**: Ensures intraday momentum is aligned with the long-term breakout.

### Improvised Suggestions:
- **Sector Rotation**: Score stocks higher if their sector (e.g., NIFTY BANK) is also hitting a 52-week high.
- **Consolidation Filter**: Only alert if the stock has stayed within a 5% range for the last 10 days before the breakout.

---

## 3. 📉 3:15 PM Swing Setup
A strategy designed to find stocks with strong closing momentum for overnight or multi-day trades.

### Core Logic:
- **Timing**: Scans between 3:10 PM and 3:25 PM.
- **Conditions**:
    - **Trend**: Price > EMA 20 > EMA 50 (Strong short-term bullishness).
    - **Closing Strength**: Stock must be trading near its daily high (LTP within 0.5% of Day High).
    - **Momentum**: RSI (Daily) must be between 60 and 75 (trending but not exhausted).

### Improvised Suggestions:
- **Next-Day Gap Prediction**: Use the Index (Nifty/BankNifty) trend to filter out swing trades if the market is expected to open weak.

---

## 4. 📈 General Volume Breakout
A broader scanner for detecting abnormal buying/selling interest.

### Core Logic:
- **Volume Ratio**: Current volume vs. 20-day average.
- **Price Action**: Price movement > 2% accompanied by volume > 300% of average.
- **Indicators**: RSI > 60 for bullish, < 40 for bearish.

---

## 🚀 Potential Improvisations (Global Roadmap)

1.  **Dynamic ATR-Based Targets**:
    Instead of a fixed 1:2 Risk-Reward, set targets based on 2x ATR. High-volatility stocks get wider targets, low-volatility stocks get tighter ones.
2.  **Sectoral Strength Filter**:
    Integrate a live "Sector Heatmap." A breakout in an IT stock is significantly more reliable if the NIFTY IT index is also bullish.
3.  **Automated Position Sizing**:
    Implement a logic that calculates `Quantity = Risk Amount / (Entry - SL)`. This ensures you always lose a fixed amount (e.g., ₹1000) if an SL is hit, regardless of the stock price.
4.  **Trailing SL for Swing**:
    Automate the movement of SL to breakeven once a 5% profit is reached.

---

## 5. 🔴 15-Min Bearish Breakdown
Designed to catch intraday weakness in structurally weak stocks.

### Core Logic:
- **Phase 1 (Pre-Market)**: Daily Price < EMA 50 OR RSI < 55. Shortlists F&O stocks with structural weakness.
- **Phase 2 (Opening Range)**: Identifies OR Low of first 15 mins (9:15-9:30).
- **Trigger**: 5-min candle close below both OR Low and Yesterday's Low.
- **Filters**: Volume spike (>1.2x avg) and Price below VWAP.
- **Risk Management**: SL at VWAP + 0.2% buffer (Min 0.5%, Max 2.5% risk).

---

## 🟢 6. 15-Min Bullish Breakout
The reverse of the Bearish Breakdown, optimized for "Bullish Days."

### Core Logic:
- **Phase 1 (Pre-Market)**: Daily Price > EMA 50 AND RSI > 50. Shortlists F&O stocks with structural strength.
- **Refresh (9:20-9:30)**: Further narrows the list based on today's opening strength (Price > Open and near Yesterday's High).
- **Trigger**: 5-min candle close above both OR High and Yesterday's High.
- **Filters**: Volume spike (>1.2x avg) and Price above VWAP.
- **Risk Management**: SL at VWAP - 0.2% buffer (Min 0.5%, Max 2.5% risk).

---
*Created for: Dilip*
*Last Updated: 2026-05-15*
