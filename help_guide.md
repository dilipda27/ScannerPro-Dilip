# ScannerPro - Trading Strategies & Documentation

Welcome to ScannerPro! This dashboard is designed to automate the discovery of high-probability intraday and swing trading setups using real-time data from Zerodha Kite. 

Below is a detailed guide on the active scanners, the underlying trading logic, and how the paper trading system works.

---

## 1. 15-Min ORB Breakout (Intraday Bullish)
**Objective:** Catch explosive morning momentum on stocks breaking their opening range.
- **Universe:** Nifty 500 / F&O Stocks
- **Timeframe:** 15-minute candles.
- **Trigger Condition:** The Last Traded Price (LTP) crosses above the High of the first 15-minute candle (9:15 AM - 9:30 AM).
- **Confirmation:** Requires strong relative volume.
- **Risk Management:** 
  - Capital per trade: ₹250,000.
  - Stop Loss: Below the 15-min OR Low or a maximum structural risk limit.

## 2. 15-Min Bearish Breakdown (Intraday Short)
**Objective:** Identify weak stocks that are breaking key support levels for intraday shorting.
- **Universe:** F&O Stocks only (for high liquidity and shorting capability).
- **Time Window:** 9:30 AM to 3:00 PM. No fresh entries after 3:00 PM.
- **Pre-Conditions (Phase 1 & 2):** 
  - Price is below the 50 EMA.
  - RSI (14) is below 45 (indicating weakness).
  - Current price is below the intraday VWAP.
  - Volume spike detected compared to the 5-day average.
- **Trigger Condition (Phase 3):** A 5-minute candle **closes** below BOTH the 15-min Opening Range Low AND the Previous Day's Low (PDL).
- **Risk Management:**
  - Capital per trade: ₹250,000.
  - Stop Loss: Dynamic VWAP-based SL (VWAP + 0.2%), capped between 0.5% and 2.5% risk.

## 3. 52-Week High Breakout (Momentum)
**Objective:** Ride extreme momentum on stocks breaking multi-month resistance.
- **Universe:** F&O Stocks.
- **Trigger Condition:** LTP crosses above the 52-Week High.
- **Risk Management:**
  - Capital per trade: ₹250,000.
  - Initial SL: 3% below the entry price.

## 4. 3:15 PM Swing Setup (Positional Bullish)
**Objective:** Capture overnight gap-ups and multi-day trends by entering strong stocks right before market close.
- **Universe:** Nifty 500.
- **Time Window:** Scans specifically around 3:15 PM.
- **Trigger Conditions:**
  - Strong daily trend (e.g., Price > 50 EMA).
  - RSI (14) > 60 (indicating strong momentum).
  - Closing near the Day's High.
- **Risk Management:**
  - Capital per trade: ₹100,000.
  - Stop Loss: Based on recent daily swing lows.

---

## 🤖 AI Advisor (Gemini Integration)
The app features an integrated AI Advisor powered by Google Gemini.
- **How it works:** When you run a scan, you can click the "Ask AI for Conviction Picks" button.
- **Strategy Aware:** The AI knows which scanner you ran. If you run the Bearish scanner, it acts as an Expert Short-Seller. If you run a Bullish scan, it acts as a Momentum trader.
- **Output:** It analyzes the raw technical data (RSI, EMAs, Volume) of the shortlisted stocks and returns the top 3 highest-conviction setups.

---

## 🛡️ Paper Trading & Risk Management Engine
The app includes a fully automated paper trading engine to track performance without risking real capital.

### Structural Trailing Stop-Loss (The "75% Rule")
To avoid getting "chopped out" prematurely during normal pullbacks, the app uses a patient trailing SL:
- **Initial Target:** Set at 2R (Risk:Reward of 1:2).
- **Trailing Trigger:** The Stop Loss is ONLY moved to break-even once the stock achieves **75% of the target move** (i.e., 1.5R).
- **Why?** This prevents you from being stopped out at break-even during standard retests of the breakout/breakdown levels.

### Notifications & Telegram
- All live scan triggers and automated paper trade executions are logged in the app.
- Alerts and AI Conviction graphics are automatically routed to your configured Telegram channels (e.g., Intraday alerts go to your dedicated intraday channel).

---
*ScannerPro v2.0 - Developed for Automated Alpha Generation.*
