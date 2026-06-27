import streamlit as st
import pandas as pd
import datetime
import os
import logging
from kiteconnect import KiteConnect
import config
import paper_trader

import bearish_vwap_rejection as logic

# Re-expose functions that app.py or local execution expects to import directly
calculate_vwap = logic.calculate_vwap
calculate_ema = logic.calculate_ema
calculate_rsi = logic.calculate_rsi
detect_bearish_reversals = logic.detect_bearish_reversals
generate_synthetic_bearish_setup = logic.generate_synthetic_bearish_setup
fetch_stock_data = logic.fetch_stock_data
run_rejection_scanner = logic.run_rejection_scanner
batch_pre_screen = logic.batch_pre_screen
scan_all_tickers_parallel = logic.scan_all_tickers_parallel
scan_bearish_vwap_rejections = logic.scan_bearish_vwap_rejections
BEARISH_CACHE_FILE = logic.BEARISH_CACHE_FILE

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_kite_client():
    """Initializes and returns Kite client if active session is available."""
    try:
        if st.runtime.exists() and 'kite_access_token' in st.session_state:
            token = st.session_state.kite_access_token
            if token:
                api_key = getattr(config, 'KITE_API_KEY', '')
                kite = KiteConnect(api_key=api_key)
                kite.set_access_token(token)
                return kite
    except Exception as e:
        logging.error(f"Error initializing Kite client: {e}")
    return None

@st.cache_data(ttl=300)
def load_universe():
    return logic.load_universe_data()

def main():
    # --- PROFESSIONAL UI STYLING & DIRECT CUSTOMIZATIONS ---
    st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
        
        html, body, [class*="css"] {
            font-family: 'Inter', sans-serif;
            background-color: #0f172a;
            color: #cbd5e1;
        }
        
        .main {
            background-color: #0f172a;
        }
        
        /* Metrics Styling */
        div[data-testid="stMetric"] {
            background-color: #1e293b !important;
            padding: 18px 22px !important;
            border-radius: 12px !important;
            border: 1px solid #334155 !important;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06) !important;
        }
        div[data-testid="stMetric"] label {
            color: #94a3b8 !important;
            font-weight: 600 !important;
            font-size: 0.8rem !important;
            text-transform: uppercase !important;
            letter-spacing: 0.5px;
        }
        div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
            color: #f8fafc !important;
            font-size: 1.6rem !important;
            font-weight: 700 !important;
        }
        
        /* Header Bar */
        .header-bar {
            background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
            padding: 20px 25px;
            border-radius: 16px;
            border: 1px solid #334155;
            margin-bottom: 25px;
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3);
        }
        
        /* Dynamic Signal Cards */
        .trigger-card {
            background: linear-gradient(135deg, #7f1d1d 0%, #450a0a 100%);
            border: 1px solid #ef4444;
            border-left: 8px solid #ef4444;
            padding: 24px;
            border-radius: 14px;
            color: #fecaca;
            margin-bottom: 25px;
            box-shadow: 0 10px 20px -5px rgba(239, 68, 68, 0.3);
        }
        .monitoring-card {
            background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
            border: 1px solid #334155;
            border-left: 8px solid #3b82f6;
            padding: 24px;
            border-radius: 14px;
            color: #94a3b8;
            margin-bottom: 25px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }
        
        /* Sidebar Details */
        .sidebar .sidebar-content {
            background-color: #1e293b;
        }
        
        /* Clean tables */
        .stDataFrame {
            border-radius: 12px;
            border: 1px solid #334155;
            background-color: #1e293b;
        }
        </style>
    """, unsafe_allow_html=True)

    # Header Bar
    st.markdown("""
        <div class="header-bar">
            <h2 style='margin:0; font-weight:700; color: #ef4444; display: flex; align-items: center;'>
                📉 Bearish VWAP Rejection Scanner
                <span style='margin-left:15px; font-weight:400; font-size:0.95rem; color: #94a3b8; background: rgba(239, 68, 68, 0.1); padding: 4px 12px; border-radius: 20px;'>
                    Intraday Short-Selling Setup Engine
                </span>
            </h2>
        </div>
    """, unsafe_allow_html=True)

    universe_df = load_universe()

    # --- SIDEBAR INTERACTION PANEL ---
    st.sidebar.markdown("### ⚙️ Scanner Control Panel")

    op_mode = st.sidebar.radio("Data Input Mode", ["⚡ Live Market / YFinance", "🎬 Interactive Simulator (Demo)"])
    use_demo = (op_mode == "🎬 Interactive Simulator (Demo)")

    pullback_pct = st.sidebar.slider("Rejection Resistance Buffer (%)", 0.02, 0.30, 0.15, 0.01, 
                                   help="Buffer zone percentage around VWAP or 9 EMA for valid rejection touches.")
    pullback_threshold = pullback_pct / 100.0

    capital = st.sidebar.number_input("Risk Capital Per Paper Trade (₹)", value=250000, step=10000)

    st.sidebar.toggle("🤖 Enable Auto-Paper Trading", value=False,
                     help="Automatically open paper trades when the scanner triggers bearish setups.")

    tickers = universe_df['Ticker'].tolist()

    if st.sidebar.button("🔄 Invalidate Cache & Refresh", type="primary", width="stretch"):
        st.cache_data.clear()
        st.toast("Refreshed data feeds!", icon="⚡")
        st.rerun()

    tab1, tab2 = st.tabs(["🎯 Single Ticker Deep Dive", "📡 Real-Time Global Scanner"])

    # --- TAB 1: SINGLE TICKER DEEP DIVE ---
    with tab1:
        selected_ticker = st.selectbox("🎯 Target Ticker Analysis", tickers)

        row_info = universe_df[universe_df['Ticker'] == selected_ticker].iloc[0]
        pdc = row_info['Prev_Close']
        token = row_info.get('Token', None)
        yesterday_low = row_info.get('Yesterday_Low', None)

        stat_col1, stat_col2, stat_col3 = st.columns(3)
        stat_col1.markdown(f"**Yesterday's Close (PDC)**: ₹{pdc:,.2f}")
        stat_col2.markdown(f"**Yesterday's Low**: ₹{yesterday_low:,.2f}" if yesterday_low else "**Yesterday's Low**: N/A")
        if 'RSI' in row_info:
            stat_col3.markdown(f"**Daily RSI (Pre-Filtered)**: `{row_info['RSI']}`")

        st.markdown("---")

        nifty_bullish = False
        try:
            if not use_demo:
                kite = get_kite_client()
                if kite:
                    import kite_scanner
                    nifty_token_map = kite_scanner.get_kite_instruments(kite, ["NIFTY 50"])
                    if nifty_token_map and "NIFTY 50" in nifty_token_map:
                        nifty_token = nifty_token_map["NIFTY 50"]
                        to_date = datetime.datetime.now()
                        nifty_from = to_date.replace(hour=9, minute=15, second=0, microsecond=0)
                        if nifty_from > to_date:
                            nifty_from = nifty_from - datetime.timedelta(days=1)
                        nifty_df = kite_scanner.fetch_kite_data(kite, nifty_token, nifty_from, to_date, "5minute")
                        if not nifty_df.empty:
                            nifty_open = nifty_df.iloc[0]['open']
                            nifty_ltp = nifty_df.iloc[-1]['close']
                            nifty_bullish = nifty_ltp > nifty_open
        except Exception as e:
            logging.warning(f"Failed to check Nifty trend in single ticker scan: {e}")

        with st.spinner(f"Acquiring 5-minute ticks for {selected_ticker}..."):
            df_raw = fetch_stock_data(selected_ticker, token, pdc, use_demo=use_demo)

        if df_raw.empty:
            st.error(f"Failed to fetch market data for {selected_ticker}. Please retry.")
        else:
            df_analyzed, alerts = run_rejection_scanner(df_raw, pdc, pullback_threshold, yesterday_low=yesterday_low, nifty_bullish=nifty_bullish)

            if not df_analyzed.empty:
                latest = df_analyzed.iloc[-1]
                m_col1, m_col2, m_col3, m_col4 = st.columns(4)
                with m_col1:
                    st.metric("Last Traded Price", f"₹{latest['close']:,.2f}", 
                              delta=f"{((latest['close'] - pdc)/pdc * 100):.2f}% vs PDC")
                with m_col2:
                    st.metric("Session VWAP", f"₹{latest['vwap']:,.2f}")
                with m_col3:
                    st.metric("9 Period EMA", f"₹{latest['ema_9']:,.2f}")
                with m_col4:
                    avg_vol = latest['vol_ma20']
                    vol_ratio = latest['volume'] / avg_vol if avg_vol > 0 else 1.0
                    st.metric("Intraday Volume", f"{int(latest['volume']):,}", 
                              delta=f"{vol_ratio:.1f}x of Avg", delta_color="inverse" if vol_ratio < 1.0 else "normal")

            st.markdown("<br>", unsafe_allow_html=True)

            if alerts:
                latest_alert = alerts[-1]
                with st.container():
                    st.markdown(f"""
                        <div class="trigger-card">
                            <h3 style='margin-top:0; color:#fecaca;'>🔴 BEARISH PULLBACK SETUP CONFIRMED</h3>
                            <p style='font-size:1.05rem; margin-bottom:15px;'>
                                A high-probability short-selling trigger occurred at <b>{latest_alert['Timestamp'].strftime('%H:%M')}</b>! 
                                A bearish <b>{latest_alert['Pattern']}</b> candlestick formed directly at the <b>{latest_alert['Zone']} Resistance Zone</b> 
                                on lower volume, signaling institutional rejection.
                            </p>
                            <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; background: rgba(0,0,0,0.2); padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                                <div>
                                    <span style="font-size:0.8rem; color:#f87171; text-transform:uppercase; font-weight:600;">ENTRY SHORT</span><br>
                                    <span style="font-size:1.3rem; font-weight:700;">₹{latest_alert['Price']:.2f}</span>
                                </div>
                                <div>
                                    <span style="font-size:0.8rem; color:#f87171; text-transform:uppercase; font-weight:600;">STOP LOSS (SL)</span><br>
                                    <span style="font-size:1.3rem; font-weight:700;">₹{latest_alert['SL']:.2f}</span>
                                </div>
                                <div>
                                    <span style="font-size:0.8rem; color:#f87171; text-transform:uppercase; font-weight:600;">TARGET 1 (1.5R)</span><br>
                                    <span style="font-size:1.3rem; font-weight:700;">₹{latest_alert['Target_1']:.2f}</span>
                                </div>
                                <div>
                                    <span style="font-size:0.8rem; color:#f87171; text-transform:uppercase; font-weight:600;">TARGET 2 (3.0R)</span><br>
                                    <span style="font-size:1.3rem; font-weight:700;">₹{latest_alert['Target_2']:.2f}</span>
                                </div>
                            </div>
                        </div>
                    """, unsafe_allow_html=True)
                    
                    if st.button("⚡ Execute Paper Short-Sale"):
                        qty = int(capital / latest_alert['Price'])
                        kite = get_kite_client()
                        success = paper_trader.execute_paper_trade(
                            ticker=selected_ticker,
                            trade_type="Bearish Rejection",
                            entry_price=latest_alert['Price'],
                            sl=latest_alert['SL'],
                            qty=qty,
                            token=token,
                            strategy="Bearish VWAP Rejection",
                            target=latest_alert['Target_1']
                        )
                        if success:
                            st.success("Short position opened successfully!")
                        else:
                            st.warning("Short trade already active.")

    # --- TAB 2: GLOBAL SCREENER ---
    with tab2:
        st.subheader("📡 Global Live Rejection Screener")
        if st.button("Run Global Bearish Rejection Scan", type="primary"):
            kite = get_kite_client()
            triggered_df, monitored_df = scan_bearish_vwap_rejections(kite, pullback_threshold, use_demo=use_demo)
            if not triggered_df.empty:
                st.success(f"Found {len(triggered_df)} active setup triggers!")
                st.dataframe(triggered_df)
            else:
                st.info("No active rejection triggers. Watching candidates...")
            if not monitored_df.empty:
                st.subheader("Watchlist candidates")
                st.dataframe(monitored_df)

if __name__ == "__main__":
    main()
