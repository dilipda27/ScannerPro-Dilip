import streamlit as st
import pandas as pd
import datetime
import os
import logging
from kiteconnect import KiteConnect
import config
import paper_trader

import bullish_vwap_rejection as logic

# Re-expose functions that app.py or local execution expects to import directly
calculate_vwap = logic.calculate_vwap
calculate_ema = logic.calculate_ema
calculate_rsi = logic.calculate_rsi
detect_bullish_reversals = logic.detect_bullish_reversals
generate_synthetic_bullish_setup = logic.generate_synthetic_bullish_setup
fetch_stock_data = logic.fetch_stock_data
run_rejection_scanner = logic.run_rejection_scanner
batch_pre_screen = logic.batch_pre_screen
scan_all_tickers_parallel = logic.scan_all_tickers_parallel
scan_bullish_vwap_rejections = logic.scan_bullish_vwap_rejections
BULLISH_CACHE_FILE = logic.BULLISH_CACHE_FILE

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
        
        div[data-testid="stMetric"] {
            background-color: #1e293b !important;
            padding: 18px 22px !important;
            border-radius: 12px !important;
            border: 1px solid #334155 !important;
        }
        div[data-testid="stMetric"] label {
            color: #94a3b8 !important;
            font-weight: 600 !important;
            font-size: 0.8rem !important;
            text-transform: uppercase !important;
        }
        div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
            color: #f8fafc !important;
            font-size: 1.6rem !important;
            font-weight: 700 !important;
        }
        
        .header-bar {
            background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
            padding: 20px 25px;
            border-radius: 16px;
            border: 1px solid #334155;
            margin-bottom: 25px;
        }
        
        .trigger-card {
            background: linear-gradient(135deg, #14532d 0%, #064e3b 100%);
            border: 1px solid #10b981;
            border-left: 8px solid #10b981;
            padding: 24px;
            border-radius: 14px;
            color: #d1fae5;
            margin-bottom: 25px;
        }
        .monitoring-card {
            background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
            border: 1px solid #334155;
            border-left: 8px solid #3b82f6;
            padding: 24px;
            border-radius: 14px;
            color: #94a3b8;
            margin-bottom: 25px;
        }
        </style>
    """, unsafe_allow_html=True)

    st.markdown("""
        <div class="header-bar">
            <h2 style='margin:0; font-weight:700; color: #10b981; display: flex; align-items: center;'>
                📈 Bullish VWAP Rejection Scanner
                <span style='margin-left:15px; font-weight:400; font-size:0.95rem; color: #94a3b8; background: rgba(16, 185, 129, 0.1); padding: 4px 12px; border-radius: 20px;'>
                    Intraday Long Pullback Setup Engine
                </span>
            </h2>
        </div>
    """, unsafe_allow_html=True)

    universe_df = load_universe()
    st.sidebar.markdown("### ⚙️ Scanner Control Panel")
    op_mode = st.sidebar.radio("Data Input Mode", ["⚡ Live Market / YFinance", "🎬 Interactive Simulator (Demo)"])
    use_demo = (op_mode == "🎬 Interactive Simulator (Demo)")
    capital = st.sidebar.number_input("Risk Capital Per Paper Trade (₹)", value=250000, step=10000)

    tickers = universe_df['Ticker'].tolist()
    tab1, tab2 = st.tabs(["🎯 Single Ticker Deep Dive", "📡 Real-Time Global Scanner"])

    with tab1:
        selected_ticker = st.selectbox("🎯 Target Ticker Analysis", tickers)
        row_info = universe_df[universe_df['Ticker'] == selected_ticker].iloc[0]
        pdc = row_info['Prev_Close']
        token = row_info.get('Token', None)
        yesterday_high = row_info.get('Yesterday_High', None)

        st.markdown(f"**Yesterday's Close (PDC)**: ₹{pdc:,.2f} | **Yesterday's High**: ₹{yesterday_high:,.2f}" if yesterday_high else f"**Yesterday's Close (PDC)**: ₹{pdc:,.2f}")
        st.markdown("---")

        with st.spinner(f"Acquiring data for {selected_ticker}..."):
            df_raw = fetch_stock_data(selected_ticker, token, pdc, use_demo=use_demo)

        if df_raw.empty:
            st.error(f"Failed to fetch market data for {selected_ticker}.")
        else:
            df_analyzed, alerts = run_rejection_scanner(df_raw, pdc, yesterday_high=yesterday_high)
            if not df_analyzed.empty:
                latest = df_analyzed.iloc[-1]
                m_col1, m_col2, m_col3, m_col4 = st.columns(4)
                m_col1.metric("LTP", f"₹{latest['close']:,.2f}", delta=f"{((latest['close'] - pdc)/pdc * 100):.2f}% vs PDC")
                m_col2.metric("VWAP", f"₹{latest['vwap']:,.2f}")
                m_col3.metric("9 EMA", f"₹{latest['ema_9']:,.2f}")
                avg_vol = latest['vol_ma20']
                vol_ratio = latest['volume'] / avg_vol if avg_vol > 0 else 1.0
                m_col4.metric("Volume", f"{int(latest['volume']):,}", delta=f"{vol_ratio:.1f}x of Avg")

            if alerts:
                latest_alert = alerts[-1]
                st.markdown(f"""
                    <div class="trigger-card">
                        <h3 style='margin-top:0; color:#d1fae5;'>🟢 BULLISH PULLBACK SETUP CONFIRMED</h3>
                        <p style='font-size:1.05rem; margin-bottom:15px;'>
                            A high-probability long trigger occurred at <b>{latest_alert['Timestamp'].strftime('%H:%M')}</b>! 
                            A bullish <b>{latest_alert['Pattern']}</b> candlestick formed directly at the <b>{latest_alert['Zone']} Rejection Zone</b>.
                        </p>
                        <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; background: rgba(0,0,0,0.2); padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                            <div>
                                <span style="font-size:0.8rem; color:#34d399; text-transform:uppercase; font-weight:600;">ENTRY BUY</span><br>
                                <span style="font-size:1.3rem; font-weight:700;">₹{latest_alert['Price']:.2f}</span>
                            </div>
                            <div>
                                <span style="font-size:0.8rem; color:#ef4444; text-transform:uppercase; font-weight:600;">STOP LOSS</span><br>
                                <span style="font-size:1.3rem; font-weight:700;">₹{latest_alert['SL']:.2f}</span>
                            </div>
                            <div>
                                <span style="font-size:0.8rem; color:#34d399; text-transform:uppercase; font-weight:600;">TARGET 1 (1.5R)</span><br>
                                <span style="font-size:1.3rem; font-weight:700;">₹{latest_alert['Target_1']:.2f}</span>
                            </div>
                            <div>
                                <span style="font-size:0.8rem; color:#34d399; text-transform:uppercase; font-weight:600;">TARGET 2 (3.0R)</span><br>
                                <span style="font-size:1.3rem; font-weight:700;">₹{latest_alert['Target_2']:.2f}</span>
                            </div>
                        </div>
                    </div>
                """, unsafe_allow_html=True)
                
                if st.button("⚡ Execute Paper Trade"):
                    qty = int(capital / latest_alert['Price'])
                    kite = get_kite_client()
                    success = paper_trader.execute_paper_trade(
                        ticker=selected_ticker,
                        trade_type="Bullish Rejection",
                        entry_price=latest_alert['Price'],
                        sl=latest_alert['SL'],
                        qty=qty,
                        token=token,
                        strategy="Bullish VWAP Rejection",
                        target=latest_alert['Target_1']
                    )
                    if success:
                        st.success("Trade executed successfully!")
                    else:
                        st.warning("Trade already active.")

    with tab2:
        st.subheader("📡 Global Live Rejection Screener")
        if st.button("Run Global Bullish Rejection Scan", type="primary"):
            kite = get_kite_client()
            triggered_df, monitored_df = scan_bullish_vwap_rejections(kite, use_demo=use_demo)
            if not triggered_df.empty:
                st.success(f"Found {len(triggered_df)} active setups!")
                st.dataframe(triggered_df)
            else:
                st.info("No active rejection setups found. Currently scanning and monitoring candidates...")
            if not monitored_df.empty:
                st.subheader("Monitoring Watchlist")
                st.dataframe(monitored_df)

if __name__ == "__main__":
    main()
