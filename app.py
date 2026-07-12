import streamlit as st
import datetime
import scanner
import kite_scanner
import high52_scanner
import bullish_breakout_scanner
import long_trade_scanner
import minervini_vcp_scanner
import os
import pandas as pd
from kiteconnect import KiteConnect
from requests.adapters import HTTPAdapter

# Global patch to increase requests connection pool size for multi-threading stability
_original_kite_init = KiteConnect.__init__
def _patched_kite_init(self, *args, **kwargs):
    _original_kite_init(self, *args, **kwargs)
    if hasattr(self, "reqsession"):
        adapter = HTTPAdapter(pool_connections=100, pool_maxsize=100)
        self.reqsession.mount("https://", adapter)
        self.reqsession.mount("http://", adapter)
KiteConnect.__init__ = _patched_kite_init
import config
import json
import paper_trader

st.set_page_config(page_title="NSE Stock Scanner Dashboard", layout="wide")

# --- NOTIFICATION SYSTEM ---
if 'notifications' not in st.session_state:
    st.session_state.notifications = []

def consume_shared_notifications():
    import json
    import os
    import time
    SHARED_NOTIFICATIONS_FILE = os.path.join("data", "state", "shared_notifications.json")
    if not os.path.exists(SHARED_NOTIFICATIONS_FILE):
        return
    for _ in range(5):
        try:
            with open(SHARED_NOTIFICATIONS_FILE, "r") as f:
                data = json.load(f)
            if data:
                for item in data:
                    is_dup = any(n['ticker'] == item['ticker'] and n['msg'] == item['msg'] for n in st.session_state.notifications)
                    if not is_dup:
                        st.session_state.notifications.insert(0, {
                            "time": item.get("time", datetime.datetime.now().strftime("%H:%M")),
                            "ticker": item["ticker"],
                            "msg": item["msg"],
                            "category": item.get("category", "Breakout")
                        })
                if len(st.session_state.notifications) > 50:
                    st.session_state.notifications = st.session_state.notifications[:50]
                with open(SHARED_NOTIFICATIONS_FILE, "w") as f:
                    json.dump([], f)
            break
        except Exception:
            time.sleep(0.1)

consume_shared_notifications()

if 'processed_orb_tickers' not in st.session_state:
    st.session_state.processed_orb_tickers = set()
if 'view_options_log' not in st.session_state:
    st.session_state.view_options_log = False

# --- PROFESSIONAL UI STYLING ---
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    
    .main {
        background-color: #f8fafc;
    }
    
    /* Global Card Style */
    .stMetric {
        background-color: white;
        padding: 20px !important;
        border-radius: 12px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
        border: 1px solid #e2e8f0;
    }
    
    /* Header Styling */
    .header-container {
        background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
        padding: 30px;
        border-radius: 16px;
        color: white;
        margin-bottom: 30px;
        box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
    }
    
    /* Portfolio Card */
    .portfolio-card {
        background: white;
        padding: 25px;
        border-radius: 16px;
        border: 1px solid #e2e8f0;
        margin-bottom: 25px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.05);
    }
    
    /* Button Styling */
    .stButton>button {
        border-radius: 8px;
        font-weight: 600;
        transition: all 0.2s ease;
    }
    
    .stButton>button:hover {
        transform: translateY(-1px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.1);
    }
    
    /* Table Styling */
    .stDataFrame {
        border-radius: 12px;
        overflow: hidden;
        border: 1px solid #e2e8f0;
    }
    
    /* Restore default Streamlit header visibility for Sidebar/Settings access */
    [data-testid="stHeader"] {
        background-color: rgba(0,0,0,0);
    }

    /* Ultra-Compact Sticky Header - Theme Aware */
    [data-testid="stVerticalBlock"] > div:has(div.header-anchor) {
        position: sticky;
        top: 2.875rem; 
        z-index: 1000;
        background-color: var(--background-color); 
        padding: 5px 0;
        border-bottom: 1px solid rgba(128, 128, 128, 0.2);
    }
    
    .header-anchor {
        display: none;
    }
    
    /* Remove redundant margins for compact view */
    .header-container {
        background: transparent !important;
        padding: 0 !important;
        box-shadow: none !important;
        margin: 0 !important;
    }
    </style>
""", unsafe_allow_html=True)

def add_notification(ticker, msg, category="Breakout"):
    st.session_state.notifications.insert(0, {
        "time": datetime.datetime.now().strftime("%H:%M"),
        "ticker": ticker,
        "msg": msg,
        "category": category
    })
    if len(st.session_state.notifications) > 50:
        st.session_state.notifications = st.session_state.notifications[:50]

# Global access to Kite credentials
api_key = getattr(config, 'KITE_API_KEY', '')
api_secret = getattr(config, 'KITE_API_SECRET', '')

if 'kite_access_token' not in st.session_state:
    st.session_state.kite_access_token = None

# --- STICKY HEADER & LOGIN SECTION ---
st.markdown('<div class="header-anchor"></div>', unsafe_allow_html=True)

header_col1, header_col2 = st.columns([2, 1])

with header_col1:
    st.markdown(f"""
        <div class="header-container">
            <h2 style='margin:0; font-weight:700; color: #3b82f6;'>🚀 ScannerPro-Dilip <span style='font-weight:400; font-size:0.9rem; opacity:0.8; color: #3b82f6;'>| NSE Portfolio Manager</span></h2>
        </div>
    """, unsafe_allow_html=True)

with header_col2:
    if st.session_state.get('kite_access_token'):
        user_name = st.session_state.get('kite_user_name', 'User')
        # Handle logout query param
        if st.query_params.get("logout"):
            st.session_state.kite_access_token = None
            st.session_state.kite_user_name = None
            st.session_state.kite_user_id = None
            if os.path.exists(".kite_session.json"):
                os.remove(".kite_session.json")
            st.query_params.clear()
            st.rerun()
            
        # Ultra compact status with logout
        st.markdown(f"""
            <div style='display:flex; justify-content:flex-end; align-items:center; gap:12px; height: 100%;'>
                <span style='color:#10b981; font-weight:bold; font-size:0.95rem; background: rgba(16, 185, 129, 0.1); padding: 4px 10px; border-radius: 20px;'>✅ {user_name}</span>
                <a href='?logout=true' target='_self' style='text-decoration:none; font-size:1.1rem; filter: grayscale(1); transition: 0.2s;' title='Logout'>🔓</a>
            </div>
        """, unsafe_allow_html=True)
    else:
        if api_key and api_secret:
            kite_login = KiteConnect(api_key=api_key)
            query_params = st.query_params
            if "request_token" in query_params:
                request_token = query_params["request_token"]
                try:
                    data = kite_login.generate_session(request_token, api_secret=api_secret)
                    st.session_state.kite_access_token = data["access_token"]
                    st.session_state.kite_user_name = data.get("user_name", "User")
                    st.session_state.kite_user_id = data.get("user_id", "ID")
                    
                    with open(".kite_session.json", "w") as f:
                        json.dump({
                            "access_token": data["access_token"],
                            "user_id": data.get("user_id", "ID"),
                            "user_name": data.get("user_name", "User")
                        }, f)
                    st.query_params.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Auth Error: {e}")
            st.markdown(f'<a href="{kite_login.login_url()}" target="_self" style="text-decoration:none;"><button style="background:#007bff; color:white; border:none; padding:5px 15px; border-radius:5px; font-weight:bold; cursor:pointer; font-size:0.8rem; float:right;">Login with Kite</button></a>', unsafe_allow_html=True)

# --- UTILITY STYLING FUNCTIONS (GLOBAL) ---
def style_pnl(val):
    try:
        val = float(val)
        color = '#28a745' if val >= 0 else '#dc3545'
        return f'color: {color}; font-weight: bold'
    except:
        return ''

def style_status(val):
    val_str = str(val)
    if "HIT" in val_str:
        return 'background-color: #f8d7da; color: #721c24; font-weight: bold'
    if "Closed" in val_str:
        return 'background-color: #e9ecef; color: #495057'
    if "Holding" in val_str or "Active" in val_str:
        return 'background-color: #d4edda; color: #155724'
    return ''

@st.dialog("📖 ScannerPro Help & Documentation", width="large")
def show_help_dialog():
    import os
    if os.path.exists("help_guide.md"):
        with open("help_guide.md", "r", encoding="utf-8") as f:
            st.markdown(f.read())
    else:
        st.error("Documentation file missing.")

# --- MAIN APP TABS ---
tab_scanners, tab_intraday, tab_swing, tab_option_desk, tab_analytics, tab_backtest = st.tabs(["🔍 Scanners", "💼 Intraday Paper Trades", "📊 Swing Trades", "📈 Option Desk", "📊 Performance Analytics", "📉 Backtesting"], key="main_tabs")


# Cache expensive Kite LTP calls for 60 s to avoid repeated fetches on every widget interaction
@st.cache_data(ttl=60, show_spinner=False)
def _cached_portfolio(access_token):
    _kite = KiteConnect(api_key=getattr(config, 'KITE_API_KEY', ''))
    _kite.set_access_token(access_token)
    try:
        import option_desk_manager
        option_desk_manager.update_desk_portfolio_and_roll(_kite)
    except Exception as ode:
        logging.error(f"Error updating option desk roll: {ode}")
    res = paper_trader.update_portfolio_pnl(_kite)
    try:
        paper_trader.log_intraday_pnl_snapshot(_kite)
    except Exception as le:
        logging.error(f"Failed to log intraday PnL snapshot on page refresh: {le}")
    return res

with tab_option_desk:
    if st.session_state.get("main_tabs", 0) == 3:
        st.markdown("## 📈 Option Desk")

        if not st.session_state.get('kite_access_token'):
            st.warning("🔒 Please authenticate with Kite Connect in the sidebar to access the Option Desk.")
        else:
            kite = KiteConnect(api_key=api_key)
            kite.set_access_token(st.session_state.kite_access_token)

            # Auto-restart rolling straddle if it was running
            import rolling_straddle_manager
            import option_desk_manager
            try:
                rolling_straddle_manager.init_rolling_straddle_on_startup(kite)
            except Exception as startup_err:
                logging.error(f"Failed to auto-restart rolling straddle: {startup_err}")

            try:
                option_desk_manager.init_option_desk_on_startup(kite)
            except Exception as startup_err:
                logging.error(f"Failed to auto-restart option desk: {startup_err}")

            sub_tab1, sub_tab2, sub_tab3 = st.tabs(["⚙️ Delta Option Strategy", "🔄 Intraday Rolling Straddle", "📊 Option Desk Portfolio"])

            portfolio_df = _cached_portfolio(st.session_state.kite_access_token)

            with sub_tab1:
                st.markdown("### ⚙️ Delta Weekly Option Selling Strategy")

                # Load option desk state
                state_od = option_desk_manager.load_state()
                is_running_od = state_od.get("is_running", False)

                col_desk1, col_desk2, col_desk3 = st.columns(3)
                with col_desk1:
                    desk_index = st.selectbox("Select Expiry Index", ["NIFTY", "SENSEX"], index=0 if state_od.get("index_name") == "NIFTY" else 1, disabled=is_running_od, key="desk_idx_sel")
                    desk_lots = st.selectbox("Number of Lots", list(range(1, 11)), index=list(range(1, 11)).index(int(state_od.get("lots", 3))), disabled=is_running_od, key="desk_lots_sel")
                    desk_capital = st.number_input("Risk Capital (₹)", value=int(state_od.get("risk_capital", 250000)), step=50000, disabled=is_running_od, key="desk_capital_sel")
                with col_desk2:
                    desk_entry_delta = st.number_input("Target Entry Delta", min_value=0.05, max_value=0.45, value=float(state_od.get("target_delta", 0.20)), step=0.01, format="%.2f", disabled=is_running_od, key="desk_entry_delta_sel")
                    desk_sl_delta = st.number_input("Stoploss Delta", min_value=0.10, max_value=0.90, value=float(state_od.get("stoploss_delta", 0.50)), step=0.01, format="%.2f", disabled=is_running_od, key="desk_sl_delta_sel")
                with col_desk3:
                    desk_start = st.text_input("Entry Time (HH:MM)", value=state_od.get("entry_time", "09:20"), disabled=is_running_od, key="desk_start_sel")
                    desk_end = st.text_input("Exit/Square-off Time (HH:MM)", value=state_od.get("exit_time", "15:20"), disabled=is_running_od, key="desk_end_sel")

                desk_sl_action = st.selectbox("Stoploss Action", ["Roll", "Close"], index=0 if state_od.get("stoploss_action") == "Roll" else 1, disabled=is_running_od, key="desk_sl_action_sel")

                # Start / Stop Buttons
                btn_desk1, btn_desk2 = st.columns(2)
                with btn_desk1:
                    if not is_running_od:
                        if st.button("⚡ Start Option Desk Strategy", type="primary", width='stretch', key="btn_start_od"):
                            try:
                                datetime.datetime.strptime(desk_start, "%H:%M")
                                datetime.datetime.strptime(desk_end, "%H:%M")
                                success, msg = option_desk_manager.start_strategy(
                                    kite, desk_index, desk_capital, desk_entry_delta, desk_sl_delta, desk_start, desk_end, desk_sl_action, desk_lots
                                )
                                if success:
                                    st.success(msg)
                                    st.rerun()
                                else:
                                    st.error(msg)
                            except ValueError:
                                st.error("Invalid time format. Please use HH:MM (e.g., 09:20).")
                    else:
                        st.button("⚡ Start Option Desk Strategy", type="primary", width='stretch', disabled=True, key="btn_start_od_disabled")

                with btn_desk2:
                    if is_running_od:
                        if st.button("🛑 Stop & Square-off Strategy", type="primary", width='stretch', key="btn_stop_od"):
                            success, msg = option_desk_manager.stop_strategy(kite)
                            if success:
                                st.success(msg)
                                st.rerun()
                            else:
                                st.error(msg)
                    else:
                        st.button("🛑 Stop & Square-off Strategy", type="primary", width='stretch', disabled=True, key="btn_stop_od_disabled")

                # Live Status Panel
                st.markdown("#### 📊 Strategy Live Monitor")
                status_color_od = "#10b981" if is_running_od else "#64748b"
                status_text_od = "RUNNING" if is_running_od else "STOPPED"

                st.markdown(f"""
                <div style="background: white; padding: 15px; border-radius: 8px; border: 1px solid #e2e8f0; border-left: 5px solid {status_color_od}; margin-bottom: 20px;">
                    <div style="font-weight: 600; font-size: 0.9rem; color: #64748b;">Strategy Status: <span style="color: {status_color_od}; font-weight: 700;">{status_text_od}</span></div>
                    <div style="font-size: 1.1rem; font-weight: bold; color: #1e293b; margin-top: 5px;">{state_od.get('status_message', 'Not running')}</div>
                    <div style="font-size: 0.75rem; color: #94a3b8; margin-top: 5px;">Last Update: {state_od.get('last_update', 'N/A')}</div>
                </div>
                """, unsafe_allow_html=True)

                if is_running_od:
                    desk_active = portfolio_df[(portfolio_df['Strategy'] == 'Option Desk') & (portfolio_df['Status'] == 'Active')].copy() if not portfolio_df.empty else pd.DataFrame()
                    unrealized_pnl = desk_active['Live P&L'].sum() if not desk_active.empty else 0.0
                    total_pnl = state_od.get("realized_pnl", 0.0) + unrealized_pnl

                    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
                    m_col1.metric("Index & Lots", f"{state_od.get('index_name')} ({state_od.get('lots', 3)} Lots)")
                    m_col2.metric("MTM P&L (₹)", f"₹{total_pnl:,.2f}", delta=f"₹{unrealized_pnl:,.2f} Unrel.", delta_color="normal" if total_pnl >= 0 else "inverse")
                    m_col3.metric("Delta Settings", f"Entry: {state_od.get('target_delta')} | SL: {state_od.get('stoploss_delta')}")
                    m_col4.metric("Realized P&L (₹)", f"₹{state_od.get('realized_pnl', 0.0):,.2f}")

                    # Active Trades table for Option Desk
                    st.subheader("📍 Active Option Desk Contracts")
                    if not desk_active.empty:
                        if 'Delta' not in desk_active.columns:
                            desk_active['Delta'] = None
                        st.dataframe(
                            desk_active[["Ticker", "Type", "EntryPrice", "Current Price", "Delta", "Qty", "SL", "SL Status", "Live P&L", "Est. Charges", "Net P&L", "EntryTime"]].style.format({
                                "EntryPrice": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-",
                                "Current Price": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-",
                                "Delta": lambda x: f"{x:.2f}" if pd.notnull(x) else "-",
                                "SL": lambda x: f"{x:.2f} Delta" if pd.notnull(x) else "-",
                                "Live P&L": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-",
                                "Est. Charges": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-",
                                "Net P&L": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-"
                            }).map(style_pnl, subset=['Live P&L', 'Net P&L']),
                            width="stretch"
                        )
                    else:
                        st.info("Waiting for time execution / Fetching active option contracts...")

            with sub_tab2:
                st.markdown("### 🔄 Intraday Rolling Straddle Strategy")

                # Load current state
                state = rolling_straddle_manager.load_state()
                is_running = state.get("is_running", False)

                # Form to set/update configuration
                st.markdown("#### ⚙️ Strategy Parameters")
                col_rs1, col_rs2, col_rs3 = st.columns(3)
                with col_rs1:
                    idx_sel = st.selectbox("Index Selection", ["NIFTY", "SENSEX"], index=0 if state.get("index_name") == "NIFTY" else 1, disabled=is_running, key="rs_idx")
                    lots_sel = st.number_input("Number of Lots", min_value=1, value=int(state.get("lots", 1)), step=1, disabled=is_running, key="rs_lots")
                with col_rs2:
                    start_sel = st.text_input("Start Time (HH:MM)", value=state.get("start_time", "09:20"), disabled=is_running, key="rs_start")
                    end_sel = st.text_input("End/Square-off Time (HH:MM)", value=state.get("end_time", "15:20"), disabled=is_running, key="rs_end")
                with col_rs3:
                    threshold_sel = st.number_input("Rolling Threshold (%)", min_value=0.1, max_value=5.0, value=float(state.get("rolling_threshold_pct", 0.5)), step=0.1, disabled=is_running, key="rs_threshold")
                    max_sl_sel = st.number_input("Daily Max MTM SL (₹)", min_value=100.0, value=float(state.get("max_sl", 5000.0)), step=500.0, disabled=is_running, key="rs_max_sl")

                max_rolls_sel = st.number_input("Max Adjustment Rolls", min_value=1, max_value=20, value=int(state.get("max_rolls", 5)), step=1, disabled=is_running, key="rs_max_rolls")

                st.markdown("---")

                # Start / Stop Buttons
                btn_col1, btn_col2 = st.columns(2)
                with btn_col1:
                    if not is_running:
                        if st.button("⚡ Start Straddle Strategy", type="primary", width='stretch', key="btn_start_rs"):
                            try:
                                datetime.datetime.strptime(start_sel, "%H:%M")
                                datetime.datetime.strptime(end_sel, "%H:%M")
                                success, msg = rolling_straddle_manager.start_strategy(
                                    kite, idx_sel, lots_sel, threshold_sel, max_sl_sel, max_rolls_sel, start_sel, end_sel
                                )
                                if success:
                                    st.success(msg)
                                    st.rerun()
                                else:
                                    st.error(msg)
                            except ValueError:
                                st.error("Invalid time format. Please use HH:MM (e.g., 09:20).")
                    else:
                        st.button("⚡ Start Straddle Strategy", type="primary", width='stretch', disabled=True, key="btn_start_rs_disabled")

                with btn_col2:
                    if is_running:
                        if st.button("🛑 Stop & Square-off Strategy", type="primary", width='stretch', key="btn_stop_rs"):
                            success, msg = rolling_straddle_manager.stop_strategy(kite)
                            if success:
                                st.success(msg)
                                st.rerun()
                            else:
                                st.error(msg)
                    else:
                        st.button("🛑 Stop & Square-off Strategy", type="primary", width='stretch', disabled=True, key="btn_stop_rs_disabled")

                # Live Status Panel
                st.markdown("#### 📊 Strategy Live Monitor")
                status_color = "#10b981" if is_running else "#64748b"
                status_text = "RUNNING" if is_running else "STOPPED"

                st.markdown(f"""
                <div style="background: white; padding: 15px; border-radius: 8px; border: 1px solid #e2e8f0; border-left: 5px solid {status_color}; margin-bottom: 20px;">
                    <div style="font-weight: 600; font-size: 0.9rem; color: #64748b;">Strategy Status: <span style="color: {status_color}; font-weight: 700;">{status_text}</span></div>
                    <div style="font-size: 1.1rem; font-weight: bold; color: #1e293b; margin-top: 5px;">{state.get('status_message', 'Not running')}</div>
                    <div style="font-size: 0.75rem; color: #94a3b8; margin-top: 5px;">Last Update: {state.get('last_update', 'N/A')}</div>
                </div>
                """, unsafe_allow_html=True)

                # Detailed metrics
                if is_running:
                    straddle_active = portfolio_df[(portfolio_df['Strategy'] == 'Rolling Straddle') & (portfolio_df['Status'] == 'Active')].copy() if not portfolio_df.empty else pd.DataFrame()
                    unrealized_pnl = straddle_active['Live P&L'].sum() if not straddle_active.empty else 0.0
                    total_pnl = state.get("realized_pnl", 0.0) + unrealized_pnl

                    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
                    m_col1.metric("Index & Lots", f"{state.get('index_name')} ({state.get('lots')} Lots)")
                    m_col2.metric("MTM P&L (₹)", f"₹{total_pnl:,.2f}", delta=f"₹{unrealized_pnl:,.2f} Unrel.", delta_color="normal" if total_pnl >= 0 else "inverse")
                    m_col3.metric("Adjustment Rolls", f"{state.get('current_rolls')} / {state.get('max_rolls')}")
                    m_col4.metric("Realized P&L (₹)", f"₹{state.get('realized_pnl', 0.0):,.2f}")

                    # Active Trades table for straddle
                    st.subheader("📍 Active Straddle Contracts")
                    if not straddle_active.empty:
                        st.dataframe(
                            straddle_active[["Ticker", "Type", "EntryPrice", "Current Price", "Qty", "Live P&L", "Est. Charges", "Net P&L", "EntryTime"]].style.format({
                                "EntryPrice": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-",
                                "Current Price": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-",
                                "Live P&L": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-",
                                "Est. Charges": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-",
                                "Net P&L": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-"
                            }).map(style_pnl, subset=['Live P&L', 'Net P&L']),
                            width="stretch"
                        )

                        ex_str_col1, ex_str_col2 = st.columns([2, 3])
                        with ex_str_col1:
                            str_exit_ticker = st.selectbox("Select Straddle Option", straddle_active['Ticker'].tolist(), key="str_exit_ticker_tab2")
                            btn_close_single, btn_close_both = st.columns(2)
                            with btn_close_single:
                                if st.button("🚪 Close Selected Leg", type="secondary", width="stretch", key="btn_close_str_tab2"):
                                    price_now = straddle_active[straddle_active['Ticker'] == str_exit_ticker]['Current Price'].values[0]
                                    paper_trader.exit_trade(str_exit_ticker, kite, override_price=price_now)
                                    st.cache_data.clear()
                                    st.success(f"Closed leg {str_exit_ticker}!")
                                    st.rerun()
                            with btn_close_both:
                                if st.button("🚪 Close Both Legs", type="primary", width="stretch", key="btn_close_both_tab2"):
                                    for _, row in straddle_active.iterrows():
                                        paper_trader.exit_trade(row['Ticker'], kite, override_price=row['Current Price'])
                                    st.cache_data.clear()
                                    st.success("Both Straddle legs closed!")
                                    st.rerun()
                    else:
                        st.info("Waiting for time execution / Fetching active straddle contracts...")

            with sub_tab3:
                st.markdown("### 📊 Option Desk Portfolios Dashboard")

                # Filter for Option Desk strategy
                desk_active = portfolio_df[(portfolio_df['Strategy'] == 'Option Desk') & (portfolio_df['Status'] == 'Active')].copy() if not portfolio_df.empty else pd.DataFrame()
                desk_closed = portfolio_df[(portfolio_df['Strategy'] == 'Option Desk') & (portfolio_df['Status'] == 'Closed')].copy() if not portfolio_df.empty else pd.DataFrame()

                # Filter for Rolling Straddle strategy
                str_active = portfolio_df[(portfolio_df['Strategy'] == 'Rolling Straddle') & (portfolio_df['Status'] == 'Active')].copy() if not portfolio_df.empty else pd.DataFrame()
                str_closed = portfolio_df[(portfolio_df['Strategy'] == 'Rolling Straddle') & (portfolio_df['Status'] == 'Closed')].copy() if not portfolio_df.empty else pd.DataFrame()

                # Also check history
                history_df_all = paper_trader.get_options_history()
                desk_history = history_df_all[history_df_all['Strategy'] == 'Option Desk'].copy() if not history_df_all.empty else pd.DataFrame()
                str_history = history_df_all[history_df_all['Strategy'] == 'Rolling Straddle'].copy() if not history_df_all.empty else pd.DataFrame()

                # Deduplicate and sync fields for Delta 0.2
                if not desk_closed.empty:
                    if 'ExitPrice' not in desk_closed.columns:
                        desk_closed['ExitPrice'] = desk_closed['Current Price']
                    else:
                        desk_closed['ExitPrice'] = desk_closed['ExitPrice'].fillna(desk_closed['Current Price'])
                    if 'ExitTime' not in desk_closed.columns:
                        desk_closed['ExitTime'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                    if 'Final P&L' not in desk_closed.columns:
                        desk_closed['Final P&L'] = desk_closed['Live P&L']
                    else:
                        desk_closed['Final P&L'] = desk_closed['Final P&L'].fillna(desk_closed['Live P&L'])
                    if not desk_history.empty:
                        history_keys = set(zip(desk_history['Ticker'], desk_history['EntryTime']))
                        desk_closed = desk_closed[~desk_closed.apply(lambda r: (r['Ticker'], r['EntryTime']) in history_keys, axis=1)]

                all_closed_desk = pd.concat([desk_closed, desk_history], ignore_index=True) if (not desk_closed.empty or not desk_history.empty) else pd.DataFrame()

                # Deduplicate and sync fields for Rolling Straddle
                if not str_closed.empty:
                    if 'ExitPrice' not in str_closed.columns:
                        str_closed['ExitPrice'] = str_closed['Current Price']
                    else:
                        str_closed['ExitPrice'] = str_closed['ExitPrice'].fillna(str_closed['Current Price'])
                    if 'ExitTime' not in str_closed.columns:
                        str_closed['ExitTime'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                    if 'Final P&L' not in str_closed.columns:
                        str_closed['Final P&L'] = str_closed['Live P&L']
                    else:
                        str_closed['Final P&L'] = str_closed['Final P&L'].fillna(str_closed['Live P&L'])
                    if not str_history.empty:
                        history_keys = set(zip(str_history['Ticker'], str_history['EntryTime']))
                        str_closed = str_closed[~str_closed.apply(lambda r: (r['Ticker'], r['EntryTime']) in history_keys, axis=1)]

                all_closed_str = pd.concat([str_closed, str_history], ignore_index=True) if (not str_closed.empty or not str_history.empty) else pd.DataFrame()

                # Metrics Calculations
                total_active_pnl = desk_active['Live P&L'].sum() if not desk_active.empty else 0.0
                total_str_pnl = str_active['Live P&L'].sum() if not str_active.empty else 0.0

                # Total capital estimation
                total_cap_desk = desk_active['Margin Required'].sum() if (not desk_active.empty and 'Margin Required' in desk_active.columns) else (desk_active['EntryPrice'] * desk_active['Qty']).sum() if not desk_active.empty else 0.0
                total_cap_str = str_active['Margin Required'].sum() if (not str_active.empty and 'Margin Required' in str_active.columns) else (str_active['EntryPrice'] * str_active['Qty']).sum() if not str_active.empty else 0.0

                col_metric1, col_metric2, col_metric3 = st.columns(3)
                with col_metric1:
                    st.metric("Delta 0.2 Active MTM", f"₹{total_active_pnl:,.2f}", delta=f"₹{total_cap_desk:,.2f} Margin")
                with col_metric2:
                    # Load realized pnl from straddle state to get full MTM of straddle today
                    rs_state = rolling_straddle_manager.load_state()
                    rs_full_pnl = rs_state.get("realized_pnl", 0.0) + total_str_pnl if rs_state.get("is_running") else total_str_pnl
                    st.metric("Rolling Straddle Daily MTM", f"₹{rs_full_pnl:,.2f}", delta=f"₹{total_cap_str:,.2f} Margin")
                with col_metric3:
                    # Refresh Option Desk PnL Button
                    if st.button("🔄 Refresh Option Desk", key="refresh_desk_pnl_sub"):
                        st.cache_data.clear()
                        st.toast("Refreshed option desk portfolios!", icon="⚡")
                        st.rerun()

                st.markdown("---")
                st.subheader("📍 Delta 0.2 Active Positions")
                if not desk_active.empty:
                    # Ensure Delta column exists in desk_active
                    if 'Delta' not in desk_active.columns:
                        desk_active['Delta'] = None
                    st.dataframe(
                        desk_active[["Ticker", "Type", "EntryPrice", "Current Price", "Delta", "Qty", "SL", "SL Status", "Live P&L", "Est. Charges", "Net P&L", "EntryTime"]].style.format({
                            "EntryPrice": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-",
                            "Current Price": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-",
                            "Delta": lambda x: f"{x:.2f}" if pd.notnull(x) else "-",
                            "SL": lambda x: f"{x:.2f} Delta" if pd.notnull(x) else "-",
                            "Live P&L": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-",
                            "Est. Charges": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-",
                            "Net P&L": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-"
                        }).map(style_pnl, subset=['Live P&L', 'Net P&L']),
                        width="stretch"
                    )

                    # Action button to exit active desk positions
                    ex_col1, ex_col2 = st.columns([1, 4])
                    with ex_col1:
                        desk_exit_ticker = st.selectbox("Select Delta 0.2 Option to Close", desk_active['Ticker'].tolist(), key="desk_exit_ticker_sub")
                        if st.button("🚪 Close Delta 0.2 Trade", type="secondary", width="stretch", key="btn_close_delta"):
                            price_now = desk_active[desk_active['Ticker'] == desk_exit_ticker]['Current Price'].values[0]
                            paper_trader.exit_trade(desk_exit_ticker, kite, override_price=price_now)
                            st.cache_data.clear()
                            st.success(f"Position closed for {desk_exit_ticker}!")
                            st.rerun()
                else:
                    st.info("No active Delta 0.2 Weekly positions.")

                st.subheader("📍 Rolling Straddle Active Positions")
                if not str_active.empty:
                    st.dataframe(
                        str_active[["Ticker", "Type", "EntryPrice", "Current Price", "Qty", "Live P&L", "Est. Charges", "Net P&L", "EntryTime"]].style.format({
                            "EntryPrice": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-",
                            "Current Price": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-",
                            "Live P&L": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-",
                            "Est. Charges": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-",
                            "Net P&L": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-"
                        }).map(style_pnl, subset=['Live P&L', 'Net P&L']),
                        width="stretch"
                    )

                    # Action button to exit active straddle positions manually
                    ex_str_col1, ex_str_col2 = st.columns([2, 3])
                    with ex_str_col1:
                        str_exit_ticker = st.selectbox("Select Straddle Option", str_active['Ticker'].tolist(), key="str_exit_ticker_tab3")
                        btn_close_single, btn_close_both = st.columns(2)
                        with btn_close_single:
                            if st.button("🚪 Close Selected Leg", type="secondary", width="stretch", key="btn_close_str_tab3"):
                                price_now = str_active[str_active['Ticker'] == str_exit_ticker]['Current Price'].values[0]
                                paper_trader.exit_trade(str_exit_ticker, kite, override_price=price_now)
                                st.cache_data.clear()
                                st.success(f"Closed leg {str_exit_ticker}!")
                                st.rerun()
                        with btn_close_both:
                            if st.button("🚪 Close Both Legs", type="primary", width="stretch", key="btn_close_both_tab3"):
                                for _, row in str_active.iterrows():
                                    paper_trader.exit_trade(row['Ticker'], kite, override_price=row['Current Price'])
                                st.cache_data.clear()
                                st.success("Both Straddle legs closed!")
                                st.rerun()
                else:
                    st.info("No active Rolling Straddle positions.")

                st.subheader("📜 Realized Delta 0.2 History")
                if not all_closed_desk.empty:
                    st.dataframe(
                        all_closed_desk[["Ticker", "Type", "EntryPrice", "ExitPrice", "Qty", "Final P&L", "EntryTime", "ExitTime"]].style.format({
                            "EntryPrice": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-",
                            "ExitPrice": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-",
                            "Final P&L": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-"
                        }).map(style_pnl, subset=['Final P&L']),
                        width="stretch"
                    )
                else:
                    st.info("No closed Delta 0.2 history yet.")

                st.subheader("📜 Realized Rolling Straddle History")
                if not all_closed_str.empty:
                    st.dataframe(
                        all_closed_str[["Ticker", "Type", "EntryPrice", "ExitPrice", "Qty", "Final P&L", "EntryTime", "ExitTime"]].style.format({
                            "EntryPrice": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-",
                            "ExitPrice": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-",
                            "Final P&L": lambda x: f"₹{x:.2f}" if pd.notnull(x) else "-"
                        }).map(style_pnl, subset=['Final P&L']),
                        width="stretch"
                    )
                else:
                    st.info("No closed Rolling Straddle history yet.")


            st.markdown("---")

            # 3. Options Bot (Moved here)
            st.markdown("## 🤖 Options Selling Bot - Live Dashboard")
            try:
                import options_bot
                state = options_bot.get_state()

                # --- Animated Status Indicator ---
                if state.get("is_running"):
                    with st.status("Bot is actively scanning option chains...", expanded=False, state="running"):
                        st.write("WebSocket Feeder: Connected")
                        st.write("Snapshot Engine: Running (3m cycle)")
                        st.write("Database Logger: Queue active")

                    if st.button("🛑 Emergency Stop Bot", type="primary", width="stretch", key="stop_opt_bot_desk"):
                        options_bot.stop_bot()
                        st.rerun()
                else:
                    if state.get('latest_signal') not in ["Neutral", "Initializing...", "Wait"] and "🏖️" not in state.get('latest_signal', ''):
                        st.success(f"✅ Bot completed its task. Signal found: **{state['latest_signal']}**")
                    else:
                        st.warning("Bot is currently stopped.")


                # --- Rich Metrics ---
                st.markdown("### 📊 Live Analytics")
                col1, col2, col3, col4 = st.columns(4)

                sig = state['latest_signal']
                sig_color = "gray"
                if "Bull" in sig: sig_color = "#10b981"
                elif "Bear" in sig: sig_color = "#ef4444"

                with col1:
                    st.metric("Probability Score", f"{state['prob_score']}/100", 
                              delta="Bullish" if state['prob_score'] > 50 else "Bearish", 
                              delta_color="normal" if state['prob_score'] > 50 else "inverse")
                    st.metric("India VIX Filter", f"{state['vix']}")
                with col2:
                    st.metric("Put-Call Ratio (PCR)", f"{state['pcr']}")
                    st.metric("ATM Strike", f"{state['current_atm']:,.0f}")
                with col3:
                    st.metric("Spot Price", f"{state['spot_price']:,.2f}")
                    ce_lakhs = state['ce_oi'] / 100000
                    pe_lakhs = state['pe_oi'] / 100000
                    st.metric("Total Call OI", f"{ce_lakhs:.1f} L")
                with col4:
                    st.markdown(f"**Latest Signal**<br><span style='color:{sig_color}; font-size:1.1rem; font-weight:bold;'>{sig}</span>", unsafe_allow_html=True)
                    st.markdown(f"**Recommended Trade**<br><span style='color:#3b82f6; font-size:1.1rem; font-weight:bold;'>{state.get('recommended_trade', 'Wait')}</span>", unsafe_allow_html=True)

                # --- Execution Button ---
                st.markdown("<br>", unsafe_allow_html=True)
                if state.get("is_running") and state.get('recommended_trade') not in ["Wait", "None"]:
                    if st.button("⚡ Execute Recommended Trade", type="primary", width="stretch", key="exec_rec_trade_desk"):
                        kite = KiteConnect(api_key=api_key)
                        kite.set_access_token(st.session_state.kite_access_token)
                        success, msg = options_bot.execute_bot_recommendation(kite, state.get("index_name", "NIFTY"))
                        if success:
                            st.success(msg)
                            st.toast(msg, icon="🚀")
                        else:
                            st.error(msg)

                # --- Signal Log ---
                st.markdown("### 📜 Signal Log")
                import os
                import pandas as pd
                if os.path.exists(os.path.join("data", "trades", "options_signals.csv")):
                    import csv
                    rows = []
                    try:
                        with open(os.path.join("data", "trades", "options_signals.csv"), "r", encoding="utf-8") as f:
                            reader = csv.reader(f)
                            header = next(reader, None)
                            if header:
                                for row in reader:
                                    if not row:
                                        continue
                                    while len(row) < 7:
                                        row.append("")
                                    rows.append(row[:7])
                        if rows:
                            log_df = pd.DataFrame(rows, columns=["Timestamp", "Index", "Signal", "Score", "PCR", "Spot", "Recommendation"])
                            st.dataframe(log_df.tail(20).sort_values("Timestamp", ascending=False), width='stretch')
                        else:
                            st.info("No signal logs found.")
                    except Exception as parse_err:
                        st.error(f"Error reading options_signals.csv: {parse_err}")

                    if st.button("🗑️ Clear Log", key="clear_opt_log_desk"):
                        try:
                            os.remove(os.path.join("data", "trades", "options_signals.csv"))
                        except:
                            pass
                        st.rerun()
                else:
                    st.info("No signals generated yet. Ensure the bot is running during market hours.")

            except Exception as e:
                st.error(f"Could not load Options Bot state: {e}")

    with tab_analytics:
        if st.session_state.get("main_tabs", 0) == 4:
            import analytics_helper
            analytics_helper.render_analytics_tab()

    with tab_intraday:
        if st.session_state.get("main_tabs", 0) == 1:
            st.markdown("## 📦 Live Paper Trading Portfolio")
            try:
                if not st.session_state.get('kite_access_token'):
                    st.warning("🔒 Please authenticate with Kite Connect in the sidebar to access the Live Paper Trading Portfolio.")
                    portfolio_df_all = pd.DataFrame()
                else:
                    portfolio_df_all = _cached_portfolio(st.session_state.kite_access_token)
                portfolio_df = portfolio_df_all[~portfolio_df_all['Strategy'].isin(['Option Desk', 'Rolling Straddle'])].copy() if not portfolio_df_all.empty else pd.DataFrame()

                # --- PERIODIC TELEGRAM UPDATES (Every 10 Minutes) ---
                if 'last_telegram_update' not in st.session_state:
                    st.session_state.last_telegram_update = datetime.datetime.now() - datetime.timedelta(minutes=11)

                if datetime.datetime.now() - st.session_state.last_telegram_update >= datetime.timedelta(minutes=10):
                    now = datetime.datetime.now()
                    current_time = now.time()
                    start_time = datetime.time(9, 30)
                    end_time = datetime.time(14, 45)

                    # Check if there's at least one active trade
                    has_open_positions = not portfolio_df.empty and (portfolio_df['Status'] == 'Active').any()

                    if start_time <= current_time <= end_time and has_open_positions:
                        import telegram_agent
                        tel_token = getattr(config, 'TELEGRAM_BOT_TOKEN', '')
                        # Prioritize personal private chat ID for P&L reports if configured
                        tel_chat_id = getattr(config, 'TELEGRAM_PERSONAL_CHAT_ID', '') or getattr(config, 'TELEGRAM_CHAT_ID_INTRADAY', getattr(config, 'TELEGRAM_CHAT_ID', ''))

                        if tel_token and tel_chat_id:
                            if telegram_agent.send_portfolio_report(portfolio_df, tel_token, tel_chat_id):
                                st.session_state.last_telegram_update = datetime.datetime.now()
                                st.toast("📲 Sent periodic portfolio update to Telegram", icon="📊")
                    else:
                        # Reset the 10-minute timer if skipped to prevent redundant checks on every page interaction
                        st.session_state.last_telegram_update = datetime.datetime.now()

                if not portfolio_df.empty:
                    col1, col2 = st.columns([4, 1])
                    with col1:
                        # Calculate advanced metrics
                        total_pnl = portfolio_df['Live P&L'].sum()
                        if 'Margin Required' in portfolio_df.columns:
                            total_capital = portfolio_df.apply(
                                lambda r: r['Margin Required'] if r['Status'] == 'Active' else (r['EntryPrice'] * r['Qty']),
                                axis=1
                            ).sum()
                        else:
                            total_capital = (portfolio_df['EntryPrice'] * portfolio_df['Qty']).sum()
                        pl_percent = (total_pnl / total_capital * 100) if total_capital > 0 else 0
                        wins = (portfolio_df['Live P&L'] > 0).sum()
                        losses = (portfolio_df['Live P&L'] <= 0).sum()
                        win_ratio = f"{wins}W / {losses}L"

                        # --- Custom Styled Metrics ---
                        total_net_pnl = portfolio_df['Net P&L'].sum()
                        total_charges = portfolio_df['Est. Charges'].sum()
                        pnl_color = "#10b981" if total_net_pnl >= 0 else "#ef4444" # Vibrant Green/Red

                        st.markdown(f"""
                        <div style="display: flex; gap: 20px; margin-bottom: 25px;">
                            <div style="flex: 1; background: white; padding: 20px; border-radius: 12px; border: 1px solid #e2e8f0; border-left: 6px solid {pnl_color}; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
                                <div style="font-size: 0.85rem; color: #64748b; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Net Live P&L</div>
                                <div style="font-size: 1.5rem; font-weight: 700; color: {pnl_color}; margin: 5px 0;">₹{total_net_pnl:,.2f}</div>
                                <div style="font-size: 0.75rem; color: #94a3b8;">Incl. Charges: ₹{total_charges:,.2f}</div>
                            </div>
                            <div style="flex: 1; background: white; padding: 20px; border-radius: 12px; border: 1px solid #e2e8f0; border-left: 6px solid #3b82f6; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
                                <div style="font-size: 0.85rem; color: #64748b; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Capital Deployed</div>
                                <div style="font-size: 1.5rem; font-weight: 700; color: #1e293b; margin: 5px 0;">₹{total_capital:,.2f}</div>
                                <div style="font-size: 0.75rem; color: #94a3b8;">Active Positions: {len(portfolio_df[portfolio_df['Status']=='Active'])}</div>
                            </div>
                            <div style="flex: 1; background: white; padding: 20px; border-radius: 12px; border: 1px solid #e2e8f0; border-left: 6px solid {pnl_color}; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
                                <div style="font-size: 0.85rem; color: #64748b; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Return Rate</div>
                                <div style="font-size: 1.5rem; font-weight: 700; color: {pnl_color}; margin: 5px 0;">{(total_net_pnl/total_capital*100 if total_capital>0 else 0):.2f}%</div>
                                <div style="font-size: 0.75rem; color: #94a3b8;">Win/Loss: {win_ratio}</div>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)

                        # --- STRATEGY BREAKDOWN METRICS (HTML) ---
                        strat_df = portfolio_df.groupby('Strategy').agg(
                            Net_PL=('Net P&L', 'sum'),
                            Charges=('Est. Charges', 'sum'),
                            Capital=('EntryPrice', lambda x: (x * portfolio_df.loc[x.index, 'Qty']).sum()),
                            Count=('Ticker', 'count')
                        ).reset_index()

                        cards_html = []
                        for _, s_row in strat_df.iterrows():
                            strat_name = s_row['Strategy']
                            s_net_pl = s_row['Net_PL']
                            s_charges = s_row['Charges']
                            s_cap = s_row['Capital']
                            s_count = s_row['Count']
                            s_ret = (s_net_pl / s_cap * 100) if s_cap > 0 else 0

                            s_color = "#10b981" if s_net_pl >= 0 else "#ef4444"

                            card = f'<div style="flex: 1; min-width: 180px; background: #f8fafc; padding: 15px; border-radius: 10px; border: 1px solid #e2e8f0; border-left: 5px solid {s_color}; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">' \
                                   f'<div style="font-size: 0.8rem; color: #64748b; font-weight: 600; text-transform: uppercase; letter-spacing: 0.3px;">🎯 {strat_name}</div>' \
                                   f'<div style="font-size: 1.25rem; font-weight: 700; color: {s_color}; margin: 3px 0;">₹{s_net_pl:,.2f}</div>' \
                                   f'<div style="font-size: 0.7rem; color: #94a3b8;">Pos: {s_count} | Ret: {s_ret:.2f}%</div>' \
                                   f'</div>'
                            cards_html.append(card)

                        # Render strategies and intraday equity curve only if toggled
                        show_intra_curve = st.toggle("📈 Show Today's Equity Curve", value=False, key="toggle_intra_curve")

                        if show_intra_curve:
                            col_strat, col_curve = st.columns([1, 1])
                            with col_strat:
                                st.markdown(f"""
                                <div style="display: flex; flex-direction: column; gap: 12px; margin-bottom: 25px;">
                                    {"".join([c.replace('flex: 1; min-width: 180px;', 'width: 100%;') for c in cards_html])}
                                </div>
                                """, unsafe_allow_html=True)

                            with col_curve:
                                try:
                                    _kite_curve = KiteConnect(api_key=getattr(config, 'KITE_API_KEY', ''))
                                    _kite_curve.set_access_token(st.session_state.kite_access_token)
                                    intra_curve = paper_trader.get_intraday_equity_curve(_kite_curve)

                                    if not intra_curve.empty and len(intra_curve) > 1:
                                        import plotly.graph_objects as go

                                        fig = go.Figure()
                                        fig.add_trace(go.Scatter(
                                            x=intra_curve['Time'],
                                            y=intra_curve['Cumulative P&L'],
                                            mode='lines+markers',
                                            line=dict(color='#3b82f6', width=3, shape='spline'),
                                            marker=dict(size=6, color='#2563eb'),
                                            fill='tozeroy',
                                            fillcolor='rgba(59, 130, 246, 0.1)',
                                            name='Intraday P&L',
                                            text=intra_curve.apply(lambda r: f"Ticker: {r['Ticker']}<br>Trade P&L: ₹{r['P&L']:.2f}<br>Cumulative: ₹{r['Cumulative P&L']:.2f}", axis=1),
                                            hoverinfo='text'
                                        ))

                                        fig.update_layout(
                                            title=dict(text="📈 Today's Intraday Equity Curve", font=dict(size=14, color="#1e293b", weight="bold")),
                                            margin=dict(l=20, r=20, t=35, b=20),
                                            height=230,
                                            plot_bgcolor='rgba(0,0,0,0)',
                                            paper_bgcolor='rgba(0,0,0,0)',
                                            xaxis=dict(
                                                showgrid=True,
                                                gridcolor='#e2e8f0',
                                                tickfont=dict(color="#64748b", size=9)
                                            ),
                                            yaxis=dict(
                                                showgrid=True,
                                                gridcolor='#e2e8f0',
                                                tickfont=dict(color="#64748b", size=9)
                                            ),
                                            hovermode="x unified"
                                        )
                                        st.plotly_chart(fig, width="stretch", config={'displayModeBar': False})
                                    else:
                                        st.info("Insufficient data for today's intraday equity curve.")
                                except Exception as curve_err:
                                    st.caption(f"Could not load intraday equity curve: {curve_err}")
                        else:
                            # Render strategy cards horizontally when chart is hidden
                            st.markdown(f"""
                            <div style="display: flex; gap: 15px; flex-wrap: wrap; margin-bottom: 25px;">
                                {"".join(cards_html)}
                            </div>
                            """, unsafe_allow_html=True)

                        if st.button("🔄 Refresh Live P&L", width="content"):
                            st.rerun()


                        # Generate Chart Links for Active Trades
                        # Generate Chart Links for Active Trades
                        if not portfolio_df.empty:
                            def make_chart_link(row):
                                if 'Token' in row and pd.notna(row['Token']) and row['Status'] == 'Active':
                                    return f"https://kite.zerodha.com/markets/ext/chart/web/ciq/NSE/{row['Ticker']}/{int(row['Token'])}"
                                return None
                            portfolio_df['Chart'] = portfolio_df.apply(make_chart_link, axis=1)

                        styled_df = portfolio_df.style.format({
                            "Live P&L": "₹{:.2f}", 
                            "EntryPrice": "₹{:.2f}", 
                            "Current Price": "₹{:.2f}",
                            "SL": "₹{:.2f}",
                            "Est. Charges": "₹{:.2f}",
                            "Net P&L": "₹{:.2f}"
                        }).map(style_pnl, subset=['Live P&L', 'Net P&L'])\
                          .map(style_status, subset=['SL Status'])

                        # Column Ordering: Ticker, Chart, then others
                        cols = list(portfolio_df.columns)
                        if 'Chart' in cols:
                            cols.remove('Chart')
                            cols.insert(1, 'Chart')
                        display_cols = [c for c in cols if c != 'Token']

                        st.dataframe(
                            styled_df, 
                            width='stretch',
                            column_config={
                                "Chart": st.column_config.LinkColumn("Chart 📈", display_text="View Chart")
                            },
                            column_order=display_cols
                        )

                    with col2:
                        with st.expander("🛠️ Manage", expanded=False):
                            st.subheader("Exit Trades")
                            tickers_to_exit = st.multiselect("Tickers", portfolio_df['Ticker'].tolist())
                            if st.button("🚪 Exit"):
                                if tickers_to_exit:
                                    count = 0
                                    # Build a fresh kite object for mutation operations (not cached)
                                    _kite_exit = KiteConnect(api_key=getattr(config, 'KITE_API_KEY', ''))
                                    _kite_exit.set_access_token(st.session_state.kite_access_token)
                                    for ticker in tickers_to_exit:
                                        if paper_trader.exit_trade(ticker, _kite_exit):
                                            count += 1
                                    st.cache_data.clear()  # Force portfolio refresh after mutation
                                    st.success(f"Closed {count} trades.")
                                    st.rerun()
                                else:
                                    st.warning("Select tickers.")

                            if st.button("🚪 Exit All Active Trades", type="primary", width="stretch"):
                                # Build a fresh kite object for mutation operations
                                _kite_exit = KiteConnect(api_key=getattr(config, 'KITE_API_KEY', ''))
                                _kite_exit.set_access_token(st.session_state.kite_access_token)

                                # Exit only the active equity positions (excluding Option Desk and Rolling Straddle)
                                active_equity = portfolio_df[portfolio_df['Status'] == 'Active']
                                count = 0
                                for _, row in active_equity.iterrows():
                                    if paper_trader.exit_trade(row['Ticker'], _kite_exit):
                                        count += 1
                                paper_trader.export_history_to_excel()
                                st.cache_data.clear() # Force portfolio refresh
                                if count > 0:
                                    st.success(f"Closed all {count} active equity trades and archived to Excel.")
                                else:
                                    st.info("No active equity trades to close.")
                                st.rerun()

                            st.markdown("---")
                            st.subheader("🧹 Clear Trades")

                            strategies_in_portfolio = portfolio_df['Strategy'].unique().tolist() if 'Strategy' in portfolio_df.columns and not portfolio_df.empty else []
                            if strategies_in_portfolio:
                                strategy_to_clear = st.selectbox("Select Strategy to Clear:", strategies_in_portfolio)
                                if st.button(f"🧹 Clear '{strategy_to_clear}' Trades", width="stretch"):
                                    paper_trader.clear_portfolio_by_strategy(strategy_to_clear)
                                    st.cache_data.clear()
                                    st.success(f"Cleared {strategy_to_clear}!")
                                    st.rerun()

                            st.markdown("<br>", unsafe_allow_html=True)
                            if st.button("🚨 Clear Entire Portfolio", type="secondary"):
                                # Clear only equity trades from the portfolio file to leave Option Desk / Rolling Straddle untouched
                                for strat in strategies_in_portfolio:
                                    paper_trader.clear_portfolio_by_strategy(strat)
                                st.cache_data.clear()
                                st.success("Cleared All Intraday Equity Trades!")
                                st.rerun()

                else:
                    st.info("No open paper trades. Run an ORB scan to find opportunities!")

                # --- TRADE HISTORY (Always Visible) ---
                st.markdown("---")
                st.subheader("📜 Paper Trade History")
                history_df = paper_trader.get_history()
                if not history_df.empty:
                    if 'Strategy' not in history_df.columns:
                        history_df['Strategy'] = "15-Min ORB"

                    # Summary Stats
                    total_realized_pnl = history_df['Final P&L'].sum()
                    total_capital = history_df['Capital Deployed'].sum()
                    overall_perf = (total_realized_pnl / total_capital * 100) if total_capital > 0 else 0

                    h_col1, h_col2, h_col3 = st.columns(3)
                    h_col1.metric("Total Realized P&L", f"₹{total_realized_pnl:,.2f}")
                    h_col2.metric("Total Capital Deployed", f"₹{total_capital:,.2f}")
                    h_col3.metric("Overall Performance", f"{overall_perf:,.2f}%")

                    # --- STRATEGY-WISE REALIZED P&L BREAKDOWN ---
                    st.markdown("##### 🏁 Realized Performance by Strategy")
                    hist_strat = history_df.groupby('Strategy').agg(
                        Realized_PL=('Final P&L', 'sum'),
                        Capital=('Capital Deployed', 'sum'),
                        Trades=('Ticker', 'count')
                    ).reset_index()

                    h_cards_html = []
                    for _, hs_row in hist_strat.iterrows():
                        h_strat_name = hs_row['Strategy']
                        hs_pl = hs_row['Realized_PL']
                        hs_cap = hs_row['Capital']
                        hs_trades = hs_row['Trades']
                        hs_ret = (hs_pl / hs_cap * 100) if hs_cap > 0 else 0

                        hs_color = "#10b981" if hs_pl >= 0 else "#ef4444"

                        card = f'<div style="flex: 1; min-width: 180px; background: #f8fafc; padding: 15px; border-radius: 10px; border: 1px solid #e2e8f0; border-left: 5px solid {hs_color}; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">' \
                               f'<div style="font-size: 0.8rem; color: #64748b; font-weight: 600; text-transform: uppercase; letter-spacing: 0.3px;">🏁 {h_strat_name}</div>' \
                               f'<div style="font-size: 1.25rem; font-weight: 700; color: {hs_color}; margin: 3px 0;">₹{hs_pl:,.2f}</div>' \
                               f'<div style="font-size: 0.7rem; color: #94a3b8;">Trades: {hs_trades} | Return: {hs_ret:.2f}%</div>' \
                               f'</div>'
                        h_cards_html.append(card)

                    st.markdown(f"""
                    <div style="display: flex; gap: 15px; flex-wrap: wrap; margin-bottom: 25px;">
                        {"".join(h_cards_html)}
                    </div>
                    """, unsafe_allow_html=True)

                    st.dataframe(history_df.style.format({
                        "EntryPrice": "₹{:.2f}",
                        "ExitPrice": "₹{:.2f}",
                        "Capital Deployed": "₹{:.2f}",
                        "Final P&L": "₹{:.2f}",
                        "P&L %": "{:.2f}%"
                    }), width='stretch')

                    col_export, col_clear = st.columns(2)
                    with col_export:
                        import io
                        buffer = io.BytesIO()
                        paper_trader.export_history_to_excel(buffer)
                        buffer.seek(0)
                        st.download_button(
                            label="📥 Export Trade History to Excel",
                            data=buffer,
                            file_name="paper_trade_history.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            width="stretch"
                        )
                    with col_clear:
                        if st.button("🗑️ Archive & Clear History", width="stretch"):
                            paper_trader.archive_history()
                            # Regenerate Excel file to include archived records
                            paper_trader.export_history_to_excel()
                            st.success("History archived to permanent records and cleared!")
                            st.rerun()
                else:
                    st.info("No closed trades in history yet.")
            except Exception as e:
                st.warning(f"Portfolio update paused: {e}")


        # Cache expensive Kite LTP calls for 60 s to avoid repeated fetches on every widget interaction
        @st.cache_data(ttl=60, show_spinner=False)
        def _cached_swing(access_token):
            _kite = KiteConnect(api_key=getattr(config, 'KITE_API_KEY', ''))
            _kite.set_access_token(access_token)
            return paper_trader.update_swing_portfolio(_kite)

    with tab_swing:
        if st.session_state.get("main_tabs", 0) == 2:
            st.markdown("## 📊 Positional Swing Portfolio (3:15 PM)")
            try:
                if not st.session_state.get('kite_access_token'):
                    st.warning("🔒 Please authenticate with Kite Connect in the sidebar to access the Swing Portfolio.")
                    full_swing_df = pd.DataFrame()
                else:
                    full_swing_df = _cached_swing(st.session_state.kite_access_token)

                # --- SWING LIFETIME PERSISTENT EQUITY CURVE ---
                show_swing_curve = st.toggle("📊 Show Lifetime Swing Equity Curve", value=False, key="toggle_swing_curve")
                if show_swing_curve:
                    try:
                        _kite_swing = KiteConnect(api_key=getattr(config, 'KITE_API_KEY', ''))
                        _kite_swing.set_access_token(st.session_state.kite_access_token)
                        swing_curve = paper_trader.get_swing_equity_curve(_kite_swing)

                        if not swing_curve.empty and len(swing_curve) > 1:
                            import plotly.graph_objects as go

                            fig_swing = go.Figure()
                            fig_swing.add_trace(go.Scatter(
                                x=swing_curve['Date'],
                                y=swing_curve['Cumulative P&L'],
                                mode='lines+markers',
                                line=dict(color='#10b981', width=3, shape='spline'),
                                marker=dict(size=6, color='#059669'),
                                fill='tozeroy',
                                fillcolor='rgba(16, 185, 129, 0.08)',
                                name='Swing Lifetime P&L',
                                text=swing_curve.apply(lambda r: f"Date: {r['Date']}<br>Realized Net: ₹{r['P&L']:.2f}<br>Cumulative: ₹{r['Cumulative P&L']:.2f}<br>Tickers: {r['Ticker']}", axis=1),
                                hoverinfo='text'
                            ))

                            fig_swing.update_layout(
                                title=dict(text="📈 Lifetime Swing Equity Curve (Persistent)", font=dict(size=14, color="#1e293b", weight="bold")),
                                margin=dict(l=20, r=20, t=35, b=20),
                                height=250,
                                plot_bgcolor='rgba(0,0,0,0)',
                                paper_bgcolor='rgba(0,0,0,0)',
                                xaxis=dict(
                                    showgrid=True,
                                    gridcolor='#e2e8f0',
                                    tickfont=dict(color="#64748b", size=9)
                                ),
                                yaxis=dict(
                                    showgrid=True,
                                    gridcolor='#e2e8f0',
                                    tickfont=dict(color="#64748b", size=9)
                                ),
                                hovermode="x unified"
                            )
                            st.plotly_chart(fig_swing, width="stretch", config={'displayModeBar': False})
                        else:
                            st.info("Insufficient data for lifetime swing equity curve.")
                    except Exception as swing_curve_err:
                        st.caption(f"Could not load swing equity curve: {swing_curve_err}")

                if not full_swing_df.empty:
                    # Split into Active and Closed
                    active_swing = full_swing_df[full_swing_df['Status'] == 'OPEN'].copy()
                    # Status could be 'SL HIT' or 'TARGET HIT'
                    closed_swing = full_swing_df[full_swing_df['Status'].str.contains('HIT', na=False)].copy()

                    # Calculate Swing Summary Metrics (Only for Active)
                    if not active_swing.empty:
                        s_total_investment = (active_swing['EntryPrice'] * active_swing['Qty']).sum()
                        s_current_value = (active_swing['Current Price'] * active_swing['Qty']).sum()
                        s_day_pnl = active_swing['Day P&L'].sum()
                        s_total_pnl = active_swing['Live P&L'].sum()
                        s_pnl_pct = (s_total_pnl / s_total_investment * 100) if s_total_investment > 0 else 0

                        s_pnl_color = "#28a745" if s_total_pnl >= 0 else "#dc3545"
                        s_day_color = "#28a745" if s_day_pnl >= 0 else "#dc3545"

                        # Premium Summary Bar for Swing
                        st.markdown(f"""
                        <div style="display: flex; justify-content: space-between; align-items: center; background-color: #f0f2f6; padding: 15px; border-radius: 10px; border-left: 5px solid {s_pnl_color}; margin-bottom: 20px;">
                            <div style="flex: 1; text-align: center;">
                                <div style="font-size: 0.8rem; color: #6c757d; text-transform: uppercase;">Active Investment</div>
                                <div style="font-size: 1.1rem; font-weight: bold; color: #212529;">₹{s_total_investment:,.2f}</div>
                            </div>
                            <div style="flex: 1; text-align: center; border-left: 1px solid #dee2e6;">
                                <div style="font-size: 0.8rem; color: #6c757d; text-transform: uppercase;">Current Value</div>
                                <div style="font-size: 1.1rem; font-weight: bold; color: #212529;">₹{s_current_value:,.2f}</div>
                            </div>
                            <div style="flex: 1; text-align: center; border-left: 1px solid #dee2e6;">
                                <div style="font-size: 0.8rem; color: #6c757d; text-transform: uppercase;">Active Day's P&L</div>
                                <div style="font-size: 1.1rem; font-weight: bold; color: {s_day_color};">₹{s_day_pnl:,.2f}</div>
                            </div>
                            <div style="flex: 1; text-align: center; border-left: 1px solid #dee2e6;">
                                <div style="font-size: 0.8rem; color: #6c757d; text-transform: uppercase;">Total Active P&L</div>
                                <div style="font-size: 1.1rem; font-weight: bold; color: {s_pnl_color};">₹{s_total_pnl:,.2f} ({s_pnl_pct:.2f}%)</div>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)

                    if st.button("🔄 Refresh Swing P&L"):
                        st.rerun()

                    # --- ACTIVE POSITIONS ---
                    st.subheader("📍 Active Swing Positions")
                    if not active_swing.empty:
                        # Generate Chart Links
                        def make_swing_chart_link(row):
                            if 'Token' in row and pd.notna(row['Token']):
                                return f"https://kite.zerodha.com/markets/ext/chart/web/ciq/NSE/{row['Ticker']}/{int(row['Token'])}"
                            return None
                        active_swing['Chart'] = active_swing.apply(make_swing_chart_link, axis=1)

                        # Column Ordering
                        cols = list(active_swing.columns)
                        if 'Chart' in cols:
                            cols.remove('Chart')
                            cols.insert(1, 'Chart')
                        display_cols = [c for c in cols if c not in ['Token', 'Prev Close', 'Status']]

                        st.dataframe(
                            active_swing.style.format({
                                "EntryPrice": "₹{:.2f}",
                                "Current Price": "₹{:.2f}",
                                "Target": "₹{:.2f}",
                                "SL": "₹{:.2f}",
                                "Live P&L": "₹{:.2f}",
                                "Day P&L": "₹{:.2f}",
                                "Est. Charges": "₹{:.2f}",
                                "Net P&L": "₹{:.2f}",
                                "Return %": "{:.2f}%"
                            }).map(style_pnl, subset=['Live P&L', 'Day P&L', 'Net P&L', 'Return %'])
                              .map(style_status, subset=['Status']), 
                            width='stretch',

                            column_config={
                                "Chart": st.column_config.LinkColumn("Chart 📈", display_text="View Chart")
                            },
                            column_order=display_cols
                        )
                    else:
                        st.info("No active swing positions.")

                    # --- ARCHIVED SWING POSITIONS ---
                    st.markdown("---")
                    st.subheader("📚 Swing Trade Archive (Historical)")

                    # Read from the permanent archive file
                    if os.path.exists(paper_trader.SWING_ARCHIVE_FILE):
                        archive_df = pd.read_csv(paper_trader.SWING_ARCHIVE_FILE)
                        if not archive_df.empty:
                            # Calculate aggregate metrics
                            total_realized_swing = archive_df['Net P&L'].sum()
                            total_invested_archive = (archive_df['EntryPrice'] * archive_df['Qty']).sum()
                            archive_roi_pct = (total_realized_swing / total_invested_archive * 100) if total_invested_archive > 0 else 0

                            arc_color = "#10b981" if total_realized_swing >= 0 else "#ef4444"

                            st.markdown(f"""
                            <div style="background: white; padding: 25px; border-radius: 16px; border: 1px solid #e2e8f0; border-left: 8px solid {arc_color}; box-shadow: 0 4px 12px rgba(0,0,0,0.05); margin-bottom: 20px;">
                                <div style="display: flex; justify-content: space-between; align-items: center;">
                                    <div>
                                        <div style="color: #64748b; font-size: 0.85rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Total Realized Archive P&L</div>
                                        <div style="color: {arc_color}; font-weight: 700; font-size: 2.2rem; margin: 5px 0;">₹{total_realized_swing:,.2f}</div>
                                        <div style="color: #94a3b8; font-size: 0.85rem;">Overall Strategy ROI: <span style="color: {arc_color}; font-weight: bold;">{archive_roi_pct:.2f}%</span></div>
                                    </div>
                                    <div style="text-align: right; border-left: 1px solid #e2e8f0; padding-left: 20px;">
                                        <div style="color: #64748b; font-size: 0.85rem; font-weight: 600; text-transform: uppercase;">Total Invested</div>
                                        <div style="color: #1e293b; font-weight: 700; font-size: 1.5rem; margin: 5px 0;">₹{total_invested_archive:,.2f}</div>
                                        <div style="color: #94a3b8; font-size: 0.75rem;">Across {len(archive_df)} closed trades</div>
                                    </div>
                                </div>
                            </div>
                            """, unsafe_allow_html=True)

                            # Display the archive
                            st.dataframe(archive_df.style.format({
                                "EntryPrice": "₹{:.2f}",
                                "Current Price": "₹{:.2f}",
                                "Target": "₹{:.2f}",
                                "SL": "₹{:.2f}",
                                "Live P&L": "₹{:.2f}",
                                "Est. Charges": "₹{:.2f}",
                                "Net P&L": "₹{:.2f}",
                                "Return %": "{:.2f}%"
                            }).map(style_pnl, subset=['Live P&L', 'Net P&L', 'Return %'])
                              .map(style_status, subset=['Status']), width='stretch')

                            if st.button("🗑️ Clear Archive"):
                                os.remove(paper_trader.SWING_ARCHIVE_FILE)
                                st.success("Swing archive cleared!")
                                st.rerun()
                        else:
                            st.info("Archive is empty.")
                    else:
                        st.info("No archived swing trades yet.")

                    if st.button("🧹 Clear Swing Portfolio", type="secondary"):
                        if os.path.exists(paper_trader.SWING_FILE):
                            os.remove(paper_trader.SWING_FILE)
                            st.success("Swing Portfolio cleared!")
                            st.rerun()
                else:
                    st.info("No active swing trades. Run the 3:15 PM scan to find opportunities!")
            except Exception as e:
                st.warning(f"Swing Portfolio update paused: {e}")
            st.markdown("---")

    with tab_backtest:
        if st.session_state.get("main_tabs", 0) == 5:
            st.markdown("## 📉 Historical Strategy Backtester")
            st.markdown("Upload historical CSV data (minute or daily candles) and run backtesting simulations to evaluate performance.")

            import backtester

            # 1. Backtesting Strategy Selector
            bt_strategies = [
                "Bullish Breakout (Intraday)",
                "Bearish Breakdown (Intraday)",
                "Bullish VWAP Rejection (Intraday)",
                "Bearish VWAP Rejection (Intraday)",
                "52-Week High Breakout",
                "15-Min ORB Breakout (Intraday)"
            ]

            col_bt1, col_bt2 = st.columns([1, 1])
            with col_bt1:
                bt_strat = st.selectbox("Select Backtesting Strategy", bt_strategies, key="bt_strat_sel")
                uploaded_files = st.file_uploader("Upload Historical Data CSV(s) (columns: datetime/date, open, high, low, close, volume)", type=["csv"], accept_multiple_files=True, key="bt_file_uploader")
                bt_ticker = st.text_input("Ticker Name (Optional Filter)", value="", key="bt_ticker_input")

            with col_bt2:
                bt_capital = st.number_input("Starting Capital (₹)", value=250000, step=50000, key="bt_capital_input")
                bt_risk = st.number_input("Risk Per Trade (%)", value=1.0, step=0.5, key="bt_risk_input")
                bt_slippage = st.number_input("Slippage Per Trade (%)", value=0.05, step=0.01, format="%.3f", key="bt_slippage_input")

            if uploaded_files:
                # Parse dates to show range selector
                try:
                    valid_files = []
                    overall_min_date = None
                    overall_max_date = None

                    for u_file in uploaded_files:
                        try:
                            u_file.seek(0)
                            raw_df = pd.read_csv(u_file)

                            # Find datetime column dynamically
                            dt_col = None
                            for col in raw_df.columns:
                                if str(col).lower().strip() in ['datetime', 'date', 'time', 'timestamp']:
                                    dt_col = col
                                    break

                            if dt_col:
                                raw_df[dt_col] = pd.to_datetime(raw_df[dt_col], errors='coerce')
                                # Drop any unparseable rows
                                raw_df = raw_df.dropna(subset=[dt_col])

                                if not raw_df.empty:
                                    min_d = raw_df[dt_col].min().date()
                                    max_d = raw_df[dt_col].max().date()
                                    if overall_min_date is None or min_d < overall_min_date:
                                        overall_min_date = min_d
                                    if overall_max_date is None or max_d > overall_max_date:
                                        overall_max_date = max_d
                                    valid_files.append((u_file, dt_col))
                        except Exception as e:
                            st.error(f"Error parsing date columns from {u_file.name}: {e}")

                    if valid_files and overall_min_date and overall_max_date:
                        st.markdown("#### 📅 Filter Backtest Timeframe")
                        col_date1, col_date2 = st.columns(2)
                        with col_date1:
                            bt_start_date = st.date_input(
                                "Start Date", 
                                value=overall_min_date, 
                                min_value=overall_min_date, 
                                max_value=overall_max_date,
                                key="bt_start_date_input"
                            )
                        with col_date2:
                            bt_end_date = st.date_input(
                                "End Date", 
                                value=overall_max_date, 
                                min_value=overall_min_date, 
                                max_value=overall_max_date,
                                key="bt_end_date_input"
                            )
                    else:
                        bt_start_date = None
                        bt_end_date = None
                        if uploaded_files:
                            st.warning("⚠️ No valid date columns were found or parsed in the uploaded files.")
                except Exception as parse_err:
                    valid_files = []
                    bt_start_date = None
                    bt_end_date = None
                    st.error(f"Error initializing backtest files: {parse_err}")

                if valid_files:
                    if st.button("🚀 Run Backtest Simulation", type="primary", key="btn_run_backtest"):
                        with st.spinner("Processing historical data and running simulation..."):
                            try:
                                all_trades_list = []
                                total_files = len(valid_files)

                                # Add a callback to show progress in Streamlit
                                progress_bar_bt = st.progress(0)
                                status_text_bt = st.empty()

                                for idx, (u_file, dt_col) in enumerate(valid_files):
                                    ticker_name = u_file.name.split(".")[0].upper()
                                    status_text_bt.text(f"Processing ({idx+1}/{total_files}): {ticker_name}...")
                                    progress_bar_bt.progress(idx / total_files)

                                    u_file.seek(0)
                                    raw_df = pd.read_csv(u_file)
                                    raw_df[dt_col] = pd.to_datetime(raw_df[dt_col], errors='coerce')
                                    raw_df = raw_df.dropna(subset=[dt_col])

                                    # Apply date range filtering if column and range exist
                                    if dt_col and bt_start_date and bt_end_date:
                                        start_dt = pd.to_datetime(bt_start_date).tz_localize(None)
                                        end_dt = pd.to_datetime(bt_end_date).tz_localize(None) + datetime.timedelta(days=1)
                                        raw_df = raw_df[(raw_df[dt_col] >= start_dt) & (raw_df[dt_col] < end_dt)]

                                    if raw_df.empty:
                                        continue

                                    # Run backtest for this file
                                    trades_df, _, _ = backtester.run_backtest(
                                        raw_df,
                                        strategy=bt_strat,
                                        capital=bt_capital,
                                        risk_pct=bt_risk,
                                        slippage_pct=bt_slippage,
                                        ticker=bt_ticker if bt_ticker.strip() != "" else ticker_name,
                                        progress_callback=None
                                    )

                                    if not trades_df.empty:
                                        all_trades_list.append(trades_df)

                                progress_bar_bt.empty()
                                status_text_bt.empty()

                                # Process aggregated trades
                                if not all_trades_list:
                                    st.error("No trades were generated across all files in the selected timeframe.")
                                    trades_df = pd.DataFrame()
                                    equity_df = pd.DataFrame()
                                    stats = {}
                                else:
                                    trades_df = pd.concat(all_trades_list, ignore_index=True)
                                    st.success(f"✅ Backtest simulation completed successfully for {len(valid_files)} stock(s)!")

                                    # Recalculate portfolio stats
                                    stats = {}
                                    stats['Total Trades'] = len(trades_df)
                                    wins = trades_df[trades_df['PnL'] > 0]
                                    losses = trades_df[trades_df['PnL'] <= 0]
                                    stats['Wins'] = len(wins)
                                    stats['Losses'] = len(losses)
                                    stats['Win Rate'] = (len(wins) / len(trades_df)) * 100
                                    stats['Total Profit'] = trades_df['PnL'].sum()
                                    stats['Profit Factor'] = abs(wins['PnL'].sum() / losses['PnL'].sum()) if not losses.empty and losses['PnL'].sum() != 0 else float('inf')

                                    # Portfolio Equity Curve
                                    trades_df['ExitDateOnly'] = pd.to_datetime(trades_df['ExitTime']).dt.date
                                    daily_pnl = trades_df.groupby('ExitDateOnly')['PnL'].sum().reset_index()
                                    daily_pnl.sort_values('ExitDateOnly', inplace=True)
                                    daily_pnl['Cum_PnL'] = daily_pnl['PnL'].cumsum()
                                    daily_pnl['Equity'] = bt_capital + daily_pnl['Cum_PnL']

                                    # Portfolio Max Drawdown
                                    equity_series = daily_pnl['Equity'].tolist()
                                    peak = bt_capital
                                    max_dd = 0.0
                                    for eq in equity_series:
                                        if eq > peak:
                                            peak = eq
                                        dd = peak - eq
                                        if dd > max_dd:
                                            max_dd = dd
                                    stats['Max Drawdown'] = max_dd
                                    stats['Max Drawdown %'] = (max_dd / bt_capital) * 100

                                    # Portfolio Sharpe Ratio
                                    if len(daily_pnl) > 1:
                                        daily_returns = daily_pnl['PnL'] / bt_capital
                                        mean_ret = daily_returns.mean()
                                        std_ret = daily_returns.std()
                                        stats['Sharpe Ratio'] = (mean_ret / std_ret) * (252 ** 0.5) if std_ret > 0 else 0.0
                                    else:
                                        stats['Sharpe Ratio'] = 0.0

                                    equity_df = daily_pnl
                                    status_text_bt.empty()

                                    st.success("✅ Backtest simulation completed successfully!")

                                    # Display Stats
                                    st.markdown("### 📊 Simulation Summary Statistics")
                                    stat_col1, stat_col2, stat_col3, stat_col4 = st.columns(4)
                                    stat_col1.metric("Total Trades", f"{stats.get('Total Trades', 0)}")
                                    stat_col1.metric("Win Rate (%)", f"{stats.get('Win Rate', 0.0):.2f}%")
                                    stat_col2.metric("Total Profit (₹)", f"₹{stats.get('Total Profit', 0.0):,.2f}")
                                    stat_col2.metric("Max Drawdown (₹)", f"₹{stats.get('Max Drawdown', 0.0):,.2f}")
                                    stat_col3.metric("Profit Factor", f"{stats.get('Profit Factor', 0.0):.2f}" if stats.get('Profit Factor') != float('inf') else "∞")
                                    stat_col3.metric("Max Drawdown (%)", f"{stats.get('Max Drawdown %', 0.0):.2f}%")
                                    stat_col4.metric("Sharpe Ratio", f"{stats.get('Sharpe Ratio', 0.0):.2f}")

                                    # Plot Cumulative PnL / Equity Curve
                                    if not equity_df.empty:
                                        st.markdown("### 📈 Equity Curve")
                                        import plotly.graph_objects as go
                                        fig_bt = go.Figure()
                                        fig_bt.add_trace(go.Scatter(
                                            x=equity_df['ExitDateOnly'],
                                            y=equity_df['Equity'],
                                            mode='lines+markers',
                                            line=dict(color='#3b82f6', width=3),
                                            fill='tozeroy',
                                            fillcolor='rgba(59, 130, 246, 0.08)',
                                            name='Equity (₹)'
                                        ))
                                        fig_bt.update_layout(
                                            margin=dict(l=20, r=20, t=35, b=20),
                                            height=350,
                                            plot_bgcolor='rgba(0,0,0,0)',
                                            paper_bgcolor='rgba(0,0,0,0)',
                                            xaxis=dict(showgrid=True, gridcolor='#e2e8f0'),
                                            yaxis=dict(showgrid=True, gridcolor='#e2e8f0')
                                        )
                                        st.plotly_chart(fig_bt, width='stretch')

                                    # Show Detailed Trades table
                                    st.markdown("### 📜 Executed Transactions List")
                                    if not trades_df.empty:
                                        # Re-order columns for display
                                        cols_to_disp = [c for c in ['Ticker', 'Type', 'EntryTime', 'EntryPrice', 'Qty', 'SL', 'Target', 'ExitTime', 'ExitPrice', 'Status', 'PnL'] if c in trades_df.columns]
                                        st.dataframe(
                                            trades_df[cols_to_disp].style.format({
                                                "EntryPrice": "₹{:.2f}",
                                                "ExitPrice": "₹{:.2f}",
                                                "SL": "₹{:.2f}",
                                                "Target": "₹{:.2f}",
                                                "PnL": "₹{:.2f}"
                                            }).map(style_pnl, subset=['PnL']),
                                            width='stretch'
                                        )
                                    else:
                                        st.info("No trades were executed during this backtest timeframe.")

                            except Exception as bt_err:
                                st.error(f"Error executing backtest simulation: {bt_err}")
                                import traceback
                                st.code(traceback.format_exc())
                else:
                    st.info("ℹ️ Please upload a historical CSV data file to begin.")

            st.markdown("---")


    # --- NOTIFICATION CENTER (SIDEBAR) ---
    st.sidebar.markdown("### 🔔 Activity Feed")
    n_count = len(st.session_state.notifications)

    with st.sidebar.expander(f"Recent Alerts ({n_count})", expanded=True):
        if not st.session_state.notifications:
            st.caption("No recent activity.")
        else:
            if st.button("Clear All Feed", width="stretch"):
                st.session_state.notifications = []
                st.rerun()

            for n in st.session_state.notifications:
                # Create a more professional, minimal one-liner
                # Strip redundant words
                m = n['msg'].replace("New ORB Breakout: ", "").replace("New 52W High Breakout at ", "52W High @ ")
                m = m.replace("Bullish (Strong Trend)", "Bullish ORB")
                m = m.replace("Bearish (Strong Trend)", "Bearish ORB")

                color = "#28a745" if "Bullish" in n['msg'] or "Target" in n['msg'] else "#dc3545"
                st.markdown(f"""
                <div style="font-size: 0.85rem; border-bottom: 1px solid #f0f2f6; padding: 4px 0;">
                    <span style="color: #6c757d;">{n['time']}</span> | 
                    <span style="font-weight: bold; color: {color};">{n['ticker']}</span> | 
                    <span>{m}</span>
                </div>
                """, unsafe_allow_html=True)

    # --- GLOBAL LIVE MONITORING SECTION (SIDEBAR) ---
    if st.session_state.get('kite_access_token'):
        st.sidebar.markdown("---")
        st.sidebar.header("📡 Live Automation")

        import json
        import os
        SCHEDULER_SETTINGS_FILE = os.path.join("data", "state", ".scheduler_settings.json")
        os.makedirs(os.path.dirname(SCHEDULER_SETTINGS_FILE), exist_ok=True)

        def load_scheduler_settings():
            if os.path.exists(SCHEDULER_SETTINGS_FILE):
                try:
                    with open(SCHEDULER_SETTINGS_FILE, "r") as f:
                        return json.load(f)
                except:
                    pass
            return {
                "orb": False,
                "high_52w": False,
                "bearish": False,
                "vwap_rejection": False,
                "bullish_vwap_rejection": False,
                "bullish": False,
                "failed_breakout": False,
                "vcp": False,
                "ai_advisor": False
            }

        def save_scheduler_settings(settings):
            try:
                with open(SCHEDULER_SETTINGS_FILE, "w") as f:
                    json.dump(settings, f, indent=4)
            except Exception as e:
                logging.error(f"Failed to save scheduler settings: {e}")

        settings = load_scheduler_settings()

        if 'mon_orb' not in st.session_state: st.session_state.mon_orb = settings.get("orb", False)
        if 'mon_52w' not in st.session_state: st.session_state.mon_52w = settings.get("high_52w", False)
        if 'mon_bearish' not in st.session_state: st.session_state.mon_bearish = settings.get("bearish", False)
        if 'mon_vwap_rejection' not in st.session_state: st.session_state.mon_vwap_rejection = settings.get("vwap_rejection", False)
        if 'mon_bullish_vwap_rejection' not in st.session_state: st.session_state.mon_bullish_vwap_rejection = settings.get("bullish_vwap_rejection", False)
        if 'mon_bullish' not in st.session_state: st.session_state.mon_bullish = settings.get("bullish", False)
        if 'mon_failed_breakout' not in st.session_state: st.session_state.mon_failed_breakout = settings.get("failed_breakout", False)

        t_orb = st.sidebar.toggle("15-Min ORB Monitor", value=st.session_state.mon_orb)
        t_52w = st.sidebar.toggle("52-Week High Monitor", value=st.session_state.mon_52w)
        t_bearish = st.sidebar.toggle("Bearish Breakdown Monitor", value=st.session_state.mon_bearish)
        t_vwap = st.sidebar.toggle("Bearish VWAP Rejection Monitor", value=st.session_state.mon_vwap_rejection)
        t_bullish_vwap = st.sidebar.toggle("Bullish VWAP Rejection Monitor", value=st.session_state.mon_bullish_vwap_rejection)
        t_bullish = st.sidebar.toggle("Bullish Breakout Monitor", value=st.session_state.mon_bullish)
        t_failed = st.sidebar.toggle("Failed Breakout Short Monitor", value=st.session_state.mon_failed_breakout)

        # Save if modified
        if (t_orb != st.session_state.mon_orb or t_52w != st.session_state.mon_52w or 
            t_bearish != st.session_state.mon_bearish or t_vwap != st.session_state.mon_vwap_rejection or 
            t_bullish_vwap != st.session_state.mon_bullish_vwap_rejection or t_bullish != st.session_state.mon_bullish or 
            t_failed != st.session_state.mon_failed_breakout):

            st.session_state.mon_orb = t_orb
            st.session_state.mon_52w = t_52w
            st.session_state.mon_bearish = t_bearish
            st.session_state.mon_vwap_rejection = t_vwap
            st.session_state.mon_bullish_vwap_rejection = t_bullish_vwap
            st.session_state.mon_bullish = t_bullish
            st.session_state.mon_failed_breakout = t_failed

            settings.update({
                "orb": t_orb,
                "high_52w": t_52w,
                "bearish": t_bearish,
                "vwap_rejection": t_vwap,
                "bullish_vwap_rejection": t_bullish_vwap,
                "bullish": t_bullish,
                "failed_breakout": t_failed
            })
            save_scheduler_settings(settings)
            st.rerun()

        # Persistent VCP (Volatility Contraction) Sidebar Toggle
        import volatility_contraction_scanner
        vcp_monitor_state = volatility_contraction_scanner.is_live_monitor_running()
        mon_vcp_toggle = st.sidebar.toggle("Volatility Contraction Monitor", value=vcp_monitor_state)

        if mon_vcp_toggle != vcp_monitor_state:
            if mon_vcp_toggle:
                watchlist = {}
                if os.path.exists("volatility_contraction_watchlist.json"):
                    try:
                        with open("volatility_contraction_watchlist.json", "r") as f:
                            raw_watchlist = json.load(f)
                        watchlist = {int(k): v for k, v in raw_watchlist.items()}
                    except Exception as json_err:
                        st.sidebar.error(f"Error loading VCP watchlist: {json_err}")

                if not watchlist:
                    st.sidebar.error("⚠️ Watchlist is empty. Run Volatility Contraction Stage 1 & 2 first.")
                else:
                    kite = KiteConnect(api_key=api_key)
                    kite.set_access_token(st.session_state.kite_access_token)
                    success, msg = volatility_contraction_scanner.start_live_monitor(kite, watchlist)
                    if success:
                        st.toast("📡 Volatility Contraction Monitor started successfully!", icon="🟢")
                        settings.update({"vcp": True})
                        save_scheduler_settings(settings)
                        st.rerun()
                    else:
                        st.sidebar.error(msg)
            else:
                volatility_contraction_scanner.stop_live_monitor()
                settings.update({"vcp": False})
                save_scheduler_settings(settings)
                st.toast("Stopped background Volatility Contraction monitor.", icon="🛑")
                st.rerun()

        # Persistent Toggle for AI Active Positions Advisor
        import ai_advisor
        ai_advisor_state = ai_advisor.is_ai_advisor_enabled()
        ai_advisor_toggle = st.sidebar.toggle("🤖 AI Position Advisor", value=ai_advisor_state)
        if ai_advisor_toggle != ai_advisor_state:
            ai_advisor.set_ai_advisor_enabled(ai_advisor_toggle)
            settings.update({"ai_advisor": ai_advisor_toggle})
            save_scheduler_settings(settings)
            st.session_state.last_ai_advisor_run = None
            st.toast(f"🤖 AI Position Advisor {'Enabled' if ai_advisor_toggle else 'Disabled'}!", icon="🔔")
            st.rerun()

        if ai_advisor_toggle:
            now = datetime.datetime.now()
            current_time = now.time()
            start_time = datetime.time(9, 45)
            end_time = datetime.time(14, 45)
            is_within_window = (start_time <= current_time <= end_time) and (now.weekday() <= 4)
            if not is_within_window:
                st.sidebar.warning("⚠️ AI Advisor is active but currently outside market hours (9:45 AM - 2:45 PM Weekdays).")

        vcp_active = volatility_contraction_scanner.is_live_monitor_running()
        if st.session_state.mon_orb or st.session_state.mon_52w or st.session_state.mon_bearish or st.session_state.mon_bullish or st.session_state.mon_vwap_rejection or st.session_state.mon_failed_breakout or vcp_active or ai_advisor_toggle:
            st.sidebar.success("Live Automation ACTIVE")
            if st.sidebar.button("⏹️ Stop All Monitors"):
                st.session_state.mon_orb = False
                st.session_state.mon_52w = False
                st.session_state.mon_bearish = False
                st.session_state.mon_vwap_rejection = False
                st.session_state.mon_bullish = False
                st.session_state.mon_failed_breakout = False
                volatility_contraction_scanner.stop_live_monitor()
                ai_advisor.set_ai_advisor_enabled(False)
                st.session_state.last_ai_advisor_run = None

                settings.update({
                    "orb": False,
                    "high_52w": False,
                    "bearish": False,
                    "vwap_rejection": False,
                    "bullish_vwap_rejection": False,
                    "bullish": False,
                    "failed_breakout": False,
                    "vcp": False,
                    "ai_advisor": False
                })
                save_scheduler_settings(settings)
                st.rerun()

        # --- SCHEDULER SERVICE MANAGEMENT ---
        st.sidebar.markdown("---")
        st.sidebar.header("🕰️ Scheduler Daemon")

        def is_scheduler_running():
            import os
            pid_file = os.path.join("data", "state", ".scheduler.pid")
            if not os.path.exists(pid_file):
                return False
            try:
                with open(pid_file, "r") as f:
                    pid = int(f.read().strip())

                if os.name == 'nt':
                    import subprocess
                    # 0x08000000 is CREATE_NO_WINDOW to prevent cmd flashing
                    output = subprocess.check_output(
                        f'tasklist /FI "PID eq {pid}"', 
                        shell=True, 
                        creationflags=0x08000000
                    ).decode(errors='ignore')
                    return str(pid) in output
                else:
                    os.kill(pid, 0)
                    return True
            except PermissionError:
                return True
            except Exception:
                return False

        sched_running = is_scheduler_running()
        if sched_running:
            st.sidebar.success("🟢 Scheduler is RUNNING")
            if st.sidebar.button("⏹️ Stop Scheduler"):
                try:
                    pid_file = os.path.join("data", "state", ".scheduler.pid")
                    with open(pid_file, "r") as f:
                        pid = int(f.read().strip())
                    import signal
                    os.kill(pid, signal.SIGTERM)
                    st.toast("Scheduler process terminated.", icon="🛑")
                    if os.path.exists(pid_file):
                        try:
                            os.remove(pid_file)
                        except:
                            pass
                    st.rerun()
                except Exception as ex:
                    st.sidebar.error(f"Failed to stop scheduler: {ex}")
        else:
            st.sidebar.error("🔴 Scheduler is STOPPED")
            if st.sidebar.button("🚀 Start Scheduler"):
                import subprocess
                import sys
                try:
                    # Start scheduler service in a new detached process console on Windows
                    subprocess.Popen(
                        [sys.executable, "scheduler_service.py"],
                        creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0
                    )
                    st.toast("Scheduler started in background!", icon="🚀")
                    import time
                    time.sleep(0.5)
                    st.rerun()
                except Exception as ex:
                    st.sidebar.error(f"Failed to start scheduler: {ex}")



    # --- OPTIONS SELLING BOT (SIDEBAR) ---
    st.sidebar.markdown("---")
    st.sidebar.header("🤖 Options Selling Bot")
    if st.session_state.get('kite_access_token'):
        import options_bot
        bot_state = options_bot.get_state()

        bot_index = st.sidebar.radio("Select Index", ["NIFTY", "SENSEX"], horizontal=True)

        if bot_state["is_running"]:
            st.sidebar.success(f"🟢 Bot is Running ({bot_index})")
            if st.sidebar.button("⏹️ Stop Bot", width="stretch"):
                options_bot.stop_bot()
                st.session_state.view_options_log = True
                st.rerun()
        else:
            st.sidebar.info("🔴 Bot is Stopped")
            if st.sidebar.button("▶️ Start Bot", type="primary", width="stretch"):
                kite = KiteConnect(api_key=api_key)
                kite.set_access_token(st.session_state.kite_access_token)
                success, msg = options_bot.start_bot(kite, bot_index)
                st.session_state.view_options_log = True
                if success:
                    st.sidebar.success(msg)
                else:
                    st.sidebar.error(msg)
                st.rerun()

        if st.sidebar.button("📜 View Signal Log", width="stretch"):
            st.session_state.view_options_log = not st.session_state.view_options_log
    else:
        st.sidebar.warning("🔒 Login required for Options Bot")

    st.sidebar.markdown("---")
    st.sidebar.header("🎯 Strategy Control Center")

    KITE_STRATEGIES = [
        "15-Min ORB Breakout (Kite)", 
        "52-Week High Breakout (Kite)", 
        "15-Min Bearish Breakdown (Kite)", 
        "15-Min Bullish Breakout (Kite)",
        "Failed Breakout Short (Kite)",
        "3:15 PM Swing Setup (Kite)",
        "EOD Long Swing Setup (Kite)",
        "Multi-Year Breakout (Kite)",
        "Volatility Contraction Scanner (Kite)",
        "Minervini VCP Breakout (Kite)",
        "Bearish VWAP Rejection (Kite)",
        "Bullish VWAP Rejection (Kite)"
    ]

    YF_STRATEGIES = ["Swing Trade Candidates", "Volume Breakout Stocks"]

    INTRADAY_STRATEGIES = [
        "15-Min ORB Breakout (Kite)",
        "15-Min Bearish Breakdown (Kite)",
        "15-Min Bullish Breakout (Kite)",
        "Failed Breakout Short (Kite)",
        "Bearish VWAP Rejection (Kite)",
        "Bullish VWAP Rejection (Kite)"
    ]

    SWING_STRATEGIES = [
        "52-Week High Breakout (Kite)",
        "3:15 PM Swing Setup (Kite)",
        "EOD Long Swing Setup (Kite)",
        "Multi-Year Breakout (Kite)",
        "Volatility Contraction Scanner (Kite)",
        "Minervini VCP Breakout (Kite)",
        "Swing Trade Candidates",
        "Volume Breakout Stocks"
    ]

    selected_intraday = st.sidebar.multiselect(
        "🕒 Active Intraday Strategies",
        options=INTRADAY_STRATEGIES,
        default=["15-Min ORB Breakout (Kite)"]
    )

    selected_swing = st.sidebar.multiselect(
        "📈 Active Swing Strategies",
        options=SWING_STRATEGIES,
        default=[]
    )

    selected_strategies = selected_intraday + selected_swing

    # Helper to get cache counts
    def get_cache_count(file_path):
        if os.path.exists(file_path):
            try:
                df = pd.read_csv(file_path)
                return len(df)
            except: return 0
        return 0

    # Display Cache Status
    st.sidebar.markdown("### 📊 Cache Status")
    cache_files = {
        "15-Min ORB Breakout (Kite)": os.path.join("data", "cache", "orb_trending_cache.csv"),
        "52-Week High Breakout (Kite)": os.path.join("data", "cache", "high52_cache.csv"),
        "15-Min Bearish Breakdown (Kite)": os.path.join("data", "cache", "bearish_breakdown_cache.csv"),
        "15-Min Bullish Breakout (Kite)": os.path.join("data", "cache", "fno_strength_cache.csv"),
        "Failed Breakout Short (Kite)": os.path.join("data", "cache", "fno_strength_cache.csv")
    }

    for s in selected_strategies:
        if s in cache_files:
            count = get_cache_count(cache_files[s])
            st.sidebar.markdown(f"**{s}**: `{count}` stocks cached")

    st.sidebar.markdown("---")
    st.sidebar.subheader("📅 Scheduled Cache Service")

    import intraday_cache_service
    service_state = intraday_cache_service.get_service_status()

    if service_state["running"]:
        st.sidebar.success(f"🟢 {service_state['status']}")

        # Render tasks progress
        for task in service_state["task_list"]:
            t_id = task["id"]
            t_name = task["name"]
            t_time = task["scheduled_time"]
            if t_id in service_state["completed_tasks"]:
                st.sidebar.markdown(f"✅ ~~{t_name}~~ ({t_time})")
            elif service_state["current_task"] == t_id:
                st.sidebar.markdown(f"🔄 **{t_name}** ({t_time})")
            else:
                st.sidebar.markdown(f"⏳ {t_name} ({t_time})")

        # Stop button
        if st.sidebar.button("🛑 Stop Caching Service", width="stretch"):
            intraday_cache_service.stop_service()
            st.toast("Stopped background Caching Service.", icon="🛑")
            st.rerun()
    else:
        st.sidebar.info(f"⚪ Status: {service_state['status']}")
        if st.sidebar.button("🚀 Start Caching Service", width="stretch", type="primary"):
            if not st.session_state.kite_access_token:
                st.sidebar.error("🔒 Authenticate with Kite first.")
            else:
                success, msg = intraday_cache_service.start_service()
                if success:
                    st.toast("📡 Background Caching Service Started!", icon="🟢")
                    st.rerun()
                else:
                    st.sidebar.error(msg)

    st.sidebar.markdown("---")
    st.sidebar.subheader("🕰️ Automated Scheduler Service")

    # Helper functions for managing scheduler process
    PID_FILE = ".scheduler_pid.json"

    def get_scheduler_pid():
        if os.path.exists(PID_FILE):
            try:
                with open(PID_FILE, "r") as f:
                    data = json.load(f)
                    return data.get("pid")
            except Exception:
                pass
        return None

    def set_scheduler_pid(pid):
        try:
            with open(PID_FILE, "w") as f:
                json.dump({"pid": pid}, f)
        except Exception:
            pass

    def clear_scheduler_pid():
        if os.path.exists(PID_FILE):
            try:
                os.remove(PID_FILE)
            except Exception:
                pass

    def is_scheduler_running():
        pid = get_scheduler_pid()
        if pid is None:
            return False
        try:
            import subprocess
            output = subprocess.check_output(f'tasklist /FI "PID eq {pid}"', shell=True).decode()
            return str(pid) in output
        except Exception:
            return False

    scheduler_running = is_scheduler_running()

    if scheduler_running:
        st.sidebar.success(f"🟢 Running (PID: {get_scheduler_pid()})")
        st.sidebar.caption("Runs daily at 3:15 PM, runs AI advisor, executes trades, and stops.")
        if st.sidebar.button("🛑 Stop Scheduler Service", width="stretch", key="stop_sched_btn"):
            pid = get_scheduler_pid()
            if pid is not None:
                import subprocess
                import sys
                try:
                    if sys.platform == "win32":
                        subprocess.run(f"taskkill /F /PID {pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    else:
                        os.kill(pid, 9)
                    st.toast("Scheduler Service stopped successfully.", icon="🛑")
                except Exception as e:
                    st.sidebar.error(f"Error stopping: {e}")
                clear_scheduler_pid()
                st.rerun()
    else:
        st.sidebar.info("⚪ Status: Stopped")
        if st.sidebar.button("🚀 Start Scheduler Service", width="stretch", type="primary", key="start_sched_btn"):
            if not st.session_state.kite_access_token:
                st.sidebar.error("🔒 Authenticate with Kite first.")
            else:
                import subprocess
                import sys
                try:
                    creationflags = 0
                    if sys.platform == "win32":
                        creationflags = 0x00000008 # DETACHED_PROCESS
                    p = subprocess.Popen(
                        [sys.executable, "scheduler_service.py"],
                        creationflags=creationflags,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    set_scheduler_pid(p.pid)
                    st.toast(f"Scheduler Service started (PID: {p.pid})!", icon="🟢")
                    st.rerun()
                except Exception as e:
                    st.sidebar.error(f"Failed to start: {e}")

    st.sidebar.markdown("---")
    st.sidebar.subheader("⚡ Bulk Operations")

    if any(s in KITE_STRATEGIES for s in selected_strategies):
        if not st.session_state.kite_access_token:
            st.sidebar.warning("🔒 Login required for Kite strategies")
        else:
            refresh_all_cache = st.sidebar.checkbox("Refresh All on Previous Cache", value=False, help="Runs all selected strategy scans using previous cached candidates instead of a full market scan.")

            refresh_orb = refresh_all_cache or st.sidebar.checkbox("Refresh ORB Only", value=False, help="Only updates today's momentum for ORB", disabled=refresh_all_cache)
            refresh_bullish = refresh_all_cache or st.sidebar.checkbox("Refresh Bullish Only", value=False, disabled=refresh_all_cache)
            refresh_bearish = refresh_all_cache or st.sidebar.checkbox("Refresh Bearish Only", value=False, disabled=refresh_all_cache)
            refresh_failed = refresh_all_cache or st.sidebar.checkbox("Refresh Failed Breakout Only", value=False, disabled=refresh_all_cache)

            if st.sidebar.button("🚀 Run Sequential Cache", width="stretch"):
                kite = KiteConnect(api_key=api_key)
                kite.set_access_token(st.session_state.kite_access_token)

                fno_strength_cached = False

                for s in selected_strategies:
                    if s == "15-Min ORB Breakout (Kite)":
                        st.info(f"🔄 Caching ORB...")
                        p_bar = st.progress(0)
                        kite_scanner.cache_orb_stocks(kite, progress_callback=lambda p, t, sym: p_bar.progress(p/t), refresh_shortlist_only=refresh_orb)
                        p_bar.empty()
                    elif s == "52-Week High Breakout (Kite)":
                        st.info(f"🔄 Caching 52W High...")
                        p_bar = st.progress(0)
                        high52_scanner.cache_daily_data(kite, progress_callback=lambda p, t, sym: p_bar.progress(p/t))
                        p_bar.empty()
                    elif s == "15-Min Bearish Breakdown (Kite)":
                        st.info(f"🔄 Caching Bearish...")
                        p_bar = st.progress(0)
                        import bearish_breakdown_scanner
                        bearish_breakdown_scanner.cache_bearish_candidates(kite, progress_callback=lambda p, t, sym: p_bar.progress(p/t), refresh_only=refresh_bearish)
                        p_bar.empty()
                    elif s in ["15-Min Bullish Breakout (Kite)", "Failed Breakout Short (Kite)"]:
                        p_bar = st.progress(0)
                        # Case 1: Full Scan (neither refresh checkbox is checked)
                        if not refresh_bullish and not refresh_failed:
                            if not fno_strength_cached:
                                st.info(f"🔄 Running Full F&O Strength Cache Scan...")
                                bullish_breakout_scanner.cache_bullish_candidates(
                                    kite, 
                                    progress_callback=lambda p, t, sym: p_bar.progress(p/t), 
                                    refresh_only=False
                                )
                                fno_strength_cached = True
                            else:
                                st.info(f"🔄 F&O Strength Cache already built (shared). Skipping duplicate full scan.")
                        # Case 2: Refresh Scan (at least one refresh checkbox is checked)
                        else:
                            if s == "15-Min Bullish Breakout (Kite)" and refresh_bullish:
                                st.info(f"🔄 Refreshing Cache for Bullish Breakout...")
                                bullish_breakout_scanner.cache_bullish_candidates(
                                    kite, 
                                    progress_callback=lambda p, t, sym: p_bar.progress(p/t), 
                                    refresh_only=True
                                )
                            elif s == "Failed Breakout Short (Kite)" and refresh_failed:
                                st.info(f"🔄 Refreshing Cache for Failed Breakout Short...")
                                import failed_breakout_scanner
                                failed_breakout_scanner.cache_failed_candidates(
                                    kite, 
                                    progress_callback=lambda p, t, sym: p_bar.progress(p/t), 
                                    refresh_only=True
                                )
                        p_bar.empty()

                st.success("✅ Bulk Caching Complete!")
                st.rerun()

    st.sidebar.markdown("---")
    # Select strategy to view/run from the active list
    if selected_strategies:
        strategy = st.sidebar.selectbox("Active View / Manual Scan", selected_strategies)
    else:
        strategy = None
        st.sidebar.info("Select at least one strategy above.")


    # Data Source Information
    if strategy in YF_STRATEGIES:
        st.sidebar.info("This scanner evaluates the latest daily data from Yahoo Finance.")

    st.sidebar.markdown("---")
    if st.sidebar.button("❓ Help & Documentation", width="stretch"):
        show_help_dialog()

    # Initialize session state for multi-strategy results
    if 'all_results' not in st.session_state:
        st.session_state.all_results = {}

    # Relocated Options Bot Live Logic to the top


with tab_scanners:
    if st.session_state.get("main_tabs", 0) == 0:
        # Allow users to run scan
        if st.button(f"Run Scan: {strategy}", type="primary"):
            if strategy.endswith("(Kite)") and not st.session_state.kite_access_token:
                st.error("Please authenticate with Kite Connect in the sidebar first.")
            else:
                with st.spinner(f"Running {strategy} scan... This may take a few minutes depending on the strategy."):
                    progress_bar = st.progress(0)
                    status_text = st.empty()

                    if strategy == "3:15 PM Swing Setup (Kite)":
                        try:
                            kite = KiteConnect(api_key=api_key)
                            kite.set_access_token(st.session_state.kite_access_token)

                            def update_progress(processed, total, symbol):
                                progress = min(processed / total, 1.0)
                                progress_bar.progress(progress)
                                status_text.text(f"Processing: {symbol} ({processed}/{total})")

                            results_df = kite_scanner.scan_315_setups(kite, progress_callback=update_progress)
                        except Exception as e:
                            st.error(f"Failed to initialize Kite API: {e}")
                            results_df = pd.DataFrame()
                    elif strategy == "15-Min ORB Breakout (Kite)":
                        try:
                            kite = KiteConnect(api_key=api_key)
                            kite.set_access_token(st.session_state.kite_access_token)

                            def update_progress(processed, total, symbol):
                                progress = min(processed / total, 1.0)
                                progress_bar.progress(progress)
                                status_text.text(f"Processing: {symbol} ({processed}/{total})")

                            results_df, pre_screen_count = kite_scanner.scan_orb_setups(kite, progress_callback=update_progress)
                            st.info(f"📊 ORB Pre-check: {pre_screen_count} candidates matched initial criteria.")
                        except Exception as e:
                            st.error(f"Failed to initialize Kite API: {e}")
                            results_df = pd.DataFrame()
                    elif strategy == "52-Week High Breakout (Kite)":
                        try:
                            kite = KiteConnect(api_key=api_key)
                            kite.set_access_token(st.session_state.kite_access_token)

                            def update_progress(processed, total, symbol):
                                progress = min(processed / total, 1.0)
                                progress_bar.progress(progress)
                                status_text.text(f"Processing: {symbol} ({processed}/{total})")

                            results_df, pre_screen_count = high52_scanner.scan_52w_breakouts(kite, progress_callback=update_progress, only_closed_candles=True)
                            st.info(f"📊 52W High Pre-check: {pre_screen_count} candidates matched initial criteria.")
                        except Exception as e:
                            st.error(f"Failed to initialize Kite API: {e}")
                            results_df = pd.DataFrame()
                    elif strategy == "15-Min Bearish Breakdown (Kite)":
                        try:
                            import bearish_breakdown_scanner
                            kite = KiteConnect(api_key=api_key)
                            kite.set_access_token(st.session_state.kite_access_token)

                            def update_progress(processed, total, symbol):
                                progress = min(processed / total, 1.0)
                                progress_bar.progress(progress)
                                status_text.text(f"Processing: {symbol} ({processed}/{total})")

                            results_df = bearish_breakdown_scanner.scan_bearish_breakdowns(kite, progress_callback=update_progress)

                            # --- AUTO-TRADE LOGIC FOR BEARISH BREAKDOWN ---
                            if not results_df.empty:
                                triggered = results_df[results_df['Status'] == 'Triggered']
                                if not triggered.empty:
                                    for _, row in triggered.iterrows():
                                        # Execute paper trade
                                        paper_trader.execute_paper_trade(
                                            ticker=row['Ticker'],
                                            trade_type="Bearish Breakdown",
                                            entry_price=row['Entry Price'],
                                            sl=row['Stop Loss'],
                                            qty=row['Qty'],
                                            token=row['Token'],
                                            strategy="Bearish Breakdown"
                                        )
                                        add_notification(row['Ticker'], f"🔴 Bearish Breakdown Entry @ {row['Entry Price']}", category="Bearish")
                        except Exception as e:
                            st.error(f"Failed to initialize Bearish Scanner: {e}")
                            results_df = pd.DataFrame()
                    elif strategy == "15-Min Bullish Breakout (Kite)":
                        try:
                            kite = KiteConnect(api_key=api_key)
                            kite.set_access_token(st.session_state.kite_access_token)

                            def update_progress(processed, total, symbol):
                                progress = min(processed / total, 1.0)
                                progress_bar.progress(progress)
                                status_text.text(f"Processing: {symbol} ({processed}/{total})")

                            results_df = bullish_breakout_scanner.scan_bullish_breakouts(kite, progress_callback=update_progress)

                            # --- AUTO-TRADE LOGIC FOR BULLISH BREAKOUT ---
                            if not results_df.empty:
                                triggered = results_df[results_df['Status'] == 'Triggered']
                                if not triggered.empty:
                                    for _, row in triggered.iterrows():
                                        paper_trader.execute_paper_trade(
                                            ticker=row['Ticker'],
                                            trade_type="Bullish Breakout",
                                            entry_price=row['Entry Price'],
                                            sl=row['Stop Loss'],
                                            qty=row['Qty'],
                                            token=row['Token'],
                                            strategy="Bullish Breakout"
                                        )
                                        add_notification(row['Ticker'], f"🟢 Bullish Breakout Entry @ {row['Entry Price']}", category="Bullish")
                        except Exception as e:
                            st.error(f"Failed to initialize Bullish Scanner: {e}")
                            results_df = pd.DataFrame()
                    elif strategy == "Failed Breakout Short (Kite)":
                        try:
                            import failed_breakout_scanner
                            kite = KiteConnect(api_key=api_key)
                            kite.set_access_token(st.session_state.kite_access_token)

                            def update_progress(processed, total, symbol):
                                progress = min(processed / total, 1.0)
                                progress_bar.progress(progress)
                                status_text.text(f"Processing: {symbol} ({processed}/{total})")

                            results_df = failed_breakout_scanner.scan_failed_breakouts(kite, progress_callback=update_progress)

                            # --- AUTO-TRADE LOGIC FOR FAILED BREAKOUT SHORT ---
                            if not results_df.empty:
                                triggered = results_df[results_df['Status'] == 'Triggered']
                                if not triggered.empty:
                                    for _, row in triggered.iterrows():
                                        paper_trader.execute_paper_trade(
                                            ticker=row['Ticker'],
                                            trade_type="Failed Breakout",
                                            entry_price=row['Entry Price'],
                                            sl=row['Stop Loss'],
                                            qty=row['Qty'],
                                            token=row['Token'],
                                            strategy="Failed Breakout Short"
                                        )
                                        add_notification(row['Ticker'], f"🔴 Failed Breakout Entry @ {row['Entry Price']}", category="Bearish")
                        except Exception as e:
                            st.error(f"Failed to initialize Failed Breakout Scanner: {e}")
                            results_df = pd.DataFrame()
                    elif strategy == "EOD Long Swing Setup (Kite)":
                        try:
                            kite = KiteConnect(api_key=api_key)
                            kite.set_access_token(st.session_state.kite_access_token)

                            def update_progress(processed, total, symbol):
                                progress = min(processed / total, 1.0) if total > 0 else 0
                                progress_bar.progress(progress)
                                status_text.text(f"Processing: {symbol} ({processed}/{total})")

                            results_df = long_trade_scanner.scan_long_setups(kite, progress_callback=update_progress)
                            if results_df.empty:
                                st.info("Market Context may not be bullish, or no stocks matched criteria.")
                        except Exception as e:
                            st.error(f"Failed to run EOD Long Swing Scanner: {e}")
                            results_df = pd.DataFrame()
                    elif strategy == "Multi-Year Breakout (Kite)":
                        try:
                            kite = KiteConnect(api_key=api_key)
                            kite.set_access_token(st.session_state.kite_access_token)

                            def update_progress(processed, total, symbol):
                                progress = min(processed / total, 1.0) if total > 0 else 0
                                progress_bar.progress(progress)
                                status_text.text(f"Processing: {symbol} ({processed}/{total})")

                            import multi_year_breakout_scanner
                            results_df = multi_year_breakout_scanner.scan_multi_year_breakouts(kite, progress_callback=update_progress)
                            if results_df.empty:
                                st.info("Market Context may not be bullish, or no stocks matched criteria.")
                        except Exception as e:
                            st.error(f"Failed to run Multi-Year Breakout Scanner: {e}")
                            results_df = pd.DataFrame()
                    elif strategy == "Minervini VCP Breakout (Kite)":
                        try:
                            kite = KiteConnect(api_key=api_key)
                            kite.set_access_token(st.session_state.kite_access_token)

                            def update_progress(processed, total, symbol):
                                progress = min(processed / total, 1.0) if total > 0 else 0
                                progress_bar.progress(progress)
                                status_text.text(f"Processing: {symbol} ({processed}/{total})")

                            import minervini_vcp_scanner
                            results_df = minervini_vcp_scanner.scan_minervini_vcp(kite, progress_callback=update_progress)
                            if results_df.empty:
                                st.info("No stocks matched the Minervini VCP breakout criteria.")
                        except Exception as e:
                            st.error(f"Failed to run Minervini VCP Breakout Scanner: {e}")
                            results_df = pd.DataFrame()
                    elif strategy == "Bearish VWAP Rejection (Kite)":
                        try:
                            import bearish_vwap_rejection_scanner
                            kite = KiteConnect(api_key=api_key)
                            kite.set_access_token(st.session_state.kite_access_token)
                            progress_bar.progress(50)
                            status_text.text("Running Bearish VWAP Rejection Scan...")
                            results_df, monitored = bearish_vwap_rejection_scanner.scan_bearish_vwap_rejections(kite)
                        except Exception as e:
                            st.error(f"Failed to run Bearish VWAP Rejection: {e}")
                            results_df = pd.DataFrame()
                    elif strategy == "Bullish VWAP Rejection (Kite)":
                        try:
                            import bullish_vwap_rejection_scanner
                            kite = KiteConnect(api_key=api_key)
                            kite.set_access_token(st.session_state.kite_access_token)
                            progress_bar.progress(50)
                            status_text.text("Running Bullish VWAP Rejection Scan...")
                            results_df, monitored = bullish_vwap_rejection_scanner.scan_bullish_vwap_rejections(kite)
                        except Exception as e:
                            st.error(f"Failed to run Bullish VWAP Rejection: {e}")
                            results_df = pd.DataFrame()
                    else:
                        tickers = scanner.get_nifty500_fno_tickers()

                        def update_yf_progress(processed, total, symbol):
                            progress = min(processed / total, 1.0)
                            progress_bar.progress(progress)
                            status_text.text(f"Processing: {symbol} ({processed}/{total})")

                        if strategy == "Swing Trade Candidates":
                            results_df = scanner.scan_swing_candidates(tickers, progress_callback=update_yf_progress)
                        else:
                            results_df = scanner.scan_breakout_stocks(tickers, progress_callback=update_yf_progress)

                    progress_bar.progress(100)
                    status_text.text("Scan complete!")

                    if results_df.empty:
                        st.warning("No stocks met the criteria today.")
                        st.session_state.all_results[strategy] = pd.DataFrame()
                    else:
                        # Sort by Volume Spike Ratio descending if the column exists (primarily for 3:15 PM strategy)
                        if 'Volume Spike Ratio' in results_df.columns:
                            results_df = results_df.sort_values(by='Volume Spike Ratio', ascending=False)
                        st.session_state.all_results[strategy] = results_df

                    # --- MANUAL EXECUTION UI BELOW ---
                    st.info("💡 You can now manually select and execute trades from the results table below.")

                    # Automatically send NEW results to Telegram
                    import telegram_agent
                    import notification_helper
                    tel_token = getattr(config, 'TELEGRAM_BOT_TOKEN', '')

                    # Determine appropriate chat ID based on strategy
                    if strategy in ["15-Min Bearish Breakdown (Kite)", "15-Min Bullish Breakout (Kite)", "15-Min ORB Breakout (Kite)", "Volatility Contraction Scanner (Kite)"]:
                        tel_chat_id = getattr(config, 'TELEGRAM_CHAT_ID_INTRADAY', '')
                    else:
                        tel_chat_id = getattr(config, 'TELEGRAM_CHAT_ID', '')

                    if tel_token and tel_chat_id and not results_df.empty:
                        new_tickers = notification_helper.filter_new_tickers(strategy, results_df['Ticker'].tolist())
                        if new_tickers:
                            new_results = results_df[results_df['Ticker'].isin(new_tickers)]
                            success = telegram_agent.send_dataframe(new_results, tel_token, tel_chat_id, scan_name=f"NEW: {strategy}")
                            if success:
                                notification_helper.mark_as_notified(strategy, new_tickers)
                                st.success(f"📲 {len(new_tickers)} new results sent to Telegram!")
                        else:
                            st.info("ℹ️ All identified stocks have already been notified today.")

        # --- MULTI-STRATEGY LIVE MONITOR LOOP ---
        import ai_advisor
        import volatility_contraction_scanner
        has_opt_desk = False
        if os.path.exists(os.path.join("data", "trades", "paper_portfolio.csv")):
            try:
                pdf_temp = pd.read_csv(os.path.join("data", "trades", "paper_portfolio.csv"))
                has_opt_desk = ((pdf_temp['Strategy'] == 'Option Desk') & (pdf_temp['Status'] == 'Active')).any()
            except:
                pass

        any_active = (
            st.session_state.get('mon_orb') or 
            st.session_state.get('mon_52w') or 
            st.session_state.get('mon_bearish') or 
            st.session_state.get('mon_bullish') or 
            st.session_state.get('mon_vwap_rejection') or
            st.session_state.get('mon_bullish_vwap_rejection') or
            st.session_state.get('mon_failed_breakout') or
            volatility_contraction_scanner.is_live_monitor_running() or
            ai_advisor.is_ai_advisor_enabled() or
            has_opt_desk
        )

        if any_active and st.session_state.get('kite_access_token'):
            st.info("🔄 **Live Background Automation Active**\n\nThe background Scheduler Service (`scheduler_service.py`) is running your enabled strategy scanners and sending Telegram alerts/executing paper trades. You can safely close or minimize this tab.")
        # --- PERSISTENT STRATEGY-SPECIFIC RESULTS DISPLAY ---
        current_results = st.session_state.all_results.get(strategy, pd.DataFrame()) if strategy else pd.DataFrame()

        # --- VOLATILITY CONTRACTION SCANNER CUSTOM DASHBOARD PAGE ---
        if strategy == "Volatility Contraction Scanner (Kite)":
            st.markdown("---")
            st.markdown("<h2 style='color:#3b82f6; font-weight:700;'>📡 Volatility Contraction Stock Scanner</h2>", unsafe_allow_html=True)
            st.markdown("""
                This strategy screens liquid Nifty 500 stocks trading within 3% of their 20-day high/low boundaries, 
                confirms volatility contraction (consolidation phase) via 5-day vs 14-day ATR, 
                and monitors breakouts/breakdowns in real-time via WebSocket.
            """)

            # 3-Column Control Panel with clearly marked ideal times
            col1, col2, col3 = st.columns(3)

            import volatility_contraction_scanner

            with col1:
                st.markdown("""
                <div style='background: white; padding: 20px; border-radius: 12px; border: 1px solid #e2e8f0; min-height: 250px; box-shadow: 0 4px 6px rgba(0,0,0,0.02);'>
                    <h4 style='color:#1e293b; font-weight:600; margin-top:0;'>1️⃣ Stage 1: Proximity Screen</h4>
                    <p style='font-size:0.8rem; color:#f59e0b; font-weight:bold; margin: 4px 0;'>⚠️ Ideal Time: After Market Close or Pre-Market (9:00 - 9:15 AM)</p>
                    <p style='font-size:0.8rem; color:#64748b;'>Filters the Nifty 500 universe for liquid stocks trading near their 20-day high (Resistance) or 20-day low (Support).</p>
                </div>
                """, unsafe_allow_html=True)

                st.markdown("<div style='margin-top: -50px; padding: 0 20px 20px 20px;'>", unsafe_allow_html=True)
                if st.button("🚀 Run Stage 1 Scan", width="stretch", key="run_stage_1_btn"):
                    if not st.session_state.kite_access_token:
                        st.error("🔒 Please log in first.")
                    else:
                        with st.spinner("Screening Nifty 500 universe..."):
                            try:
                                kite = KiteConnect(api_key=api_key)
                                kite.set_access_token(st.session_state.kite_access_token)
                                symbols = volatility_contraction_scanner.fetch_nifty500_symbols()
                                shortlist = volatility_contraction_scanner.run_stage1_proximity_filter(kite, symbols)
                                st.success(f"Stage 1 Complete! Cached {len(shortlist)} stocks.")
                                st.toast("Stage 1 screening finished!", icon="✅")
                            except Exception as e:
                                st.error(f"Stage 1 Error: {e}")
                st.markdown("</div>", unsafe_allow_html=True)

            with col2:
                st.markdown("""
                <div style='background: white; padding: 20px; border-radius: 12px; border: 1px solid #e2e8f0; min-height: 250px; box-shadow: 0 4px 6px rgba(0,0,0,0.02);'>
                    <h4 style='color:#1e293b; font-weight:600; margin-top:0;'>2️⃣ Stage 2: Volatility Check</h4>
                    <p style='font-size:0.8rem; color:#f59e0b; font-weight:bold; margin: 4px 0;'>⚠️ Ideal Time: Pre-Market (9:10 - 9:15 AM) after caching</p>
                    <p style='font-size:0.8rem; color:#64748b;'>Validates EOD candidates for Volatility Contraction phase (5-day Wilder's ATR is less than 14-day ATR).</p>
                </div>
                """, unsafe_allow_html=True)

                st.markdown("<div style='margin-top: -50px; padding: 0 20px 20px 20px;'>", unsafe_allow_html=True)
                if st.button("🔥 Run Stage 2 Validation", width="stretch", key="run_stage_2_btn"):
                    if not st.session_state.kite_access_token:
                        st.error("🔒 Please log in first.")
                    else:
                        with st.spinner("Analyzing volatility contraction setups..."):
                            try:
                                kite = KiteConnect(api_key=api_key)
                                kite.set_access_token(st.session_state.kite_access_token)
                                watchlist = volatility_contraction_scanner.run_stage2_setup_validation(kite)
                                st.success(f"Stage 2 Complete! Validated {len(watchlist)} stocks in contraction phase.")
                                st.toast("Stage 2 validation finished!", icon="🔥")
                            except Exception as e:
                                st.error(f"Stage 2 Error: {e}")
                st.markdown("</div>", unsafe_allow_html=True)

            with col3:
                is_running = volatility_contraction_scanner.is_live_monitor_running()
                status_label = "<span style='color:#10b981; font-weight:bold;'>🟢 Streaming (Connected)</span>" if is_running else "<span style='color:#ef4444; font-weight:bold;'>🔴 Offline (Stopped)</span>"

                st.markdown(f"""
                <div style='background: white; padding: 20px; border-radius: 12px; border: 1px solid #e2e8f0; min-height: 250px; box-shadow: 0 4px 6px rgba(0,0,0,0.02);'>
                    <h4 style='color:#1e293b; font-weight:600; margin-top:0;'>3️⃣ Stage 3: Live Monitor</h4>
                    <p style='font-size:0.8rem; color:#f59e0b; font-weight:bold; margin: 4px 0;'>⚠️ Ideal Time: Market Hours (9:15 AM - 3:30 PM)</p>
                    <p style='font-size:0.8rem; color:#64748b; margin-bottom:12px;'>Streams ticks in real-time and logs paper trades in your portfolio on triggers.</p>
                    <p style='font-size:0.85rem; color:#1e293b;'><b>Status:</b> {status_label}</p>
                </div>
                """, unsafe_allow_html=True)

                st.markdown("<div style='margin-top: -50px; padding: 0 20px 20px 20px;'>", unsafe_allow_html=True)
                if is_running:
                    if st.button("🛑 Stop Live Monitor", type="primary", width="stretch", key="stop_stage_3_btn"):
                        volatility_contraction_scanner.stop_live_monitor()
                        st.toast("Stopped background WebSocket monitor.", icon="⏹️")
                        st.rerun()
                else:
                    if st.button("▶️ Start Live Monitor", type="primary", width="stretch", key="start_stage_3_btn"):
                        if not st.session_state.kite_access_token:
                            st.error("🔒 Please log in first.")
                        else:
                            with st.spinner("Starting WebSocket connection..."):
                                try:
                                    kite = KiteConnect(api_key=api_key)
                                    kite.set_access_token(st.session_state.kite_access_token)

                                    watchlist = {}
                                    if os.path.exists("volatility_contraction_watchlist.json"):
                                        with open("volatility_contraction_watchlist.json", "r") as f:
                                            raw_watchlist = json.load(f)
                                        # Convert keys back to integers for WebSocket/instrument token compatibility
                                        watchlist = {int(k): v for k, v in raw_watchlist.items()}

                                    if not watchlist:
                                        st.warning("⚠️ Watchlist is empty or not found. Please click 'Run Stage 2 Validation' first.")
                                    else:
                                        success, msg = volatility_contraction_scanner.start_live_monitor(kite, watchlist)
                                        if success:
                                            st.success(msg)
                                            st.toast("WebSocket monitor started successfully!", icon="📡")
                                        else:
                                            st.error(msg)
                                        st.rerun()
                                except Exception as e:
                                    st.error(f"Stage 3 Error: {e}")
                st.markdown("</div>", unsafe_allow_html=True)

            # --- Render Results Stage-by-Stage ---
            st.markdown("---")
            res_col1, res_col2 = st.columns(2)

            with res_col1:
                st.markdown("<h3 style='color:#1e293b; font-weight:600;'>📊 Stage 1 Filter Results</h3>", unsafe_allow_html=True)
                if os.path.exists("proximity_filter_cache.json"):
                    try:
                        with open("proximity_filter_cache.json", "r") as f:
                            cache_data = json.load(f)
                        if cache_data:
                            df1 = pd.DataFrame(cache_data.values())
                            # Display essential columns
                            df1_disp = df1[["symbol", "latest_close", "resistance", "support", "volume_sma", "near_level"]].copy()
                            df1_disp.columns = ["Ticker", "Latest Close", "20D High (R)", "20D Low (S)", "Volume SMA", "Near Level"]
                            st.dataframe(df1_disp.style.format({
                                "Latest Close": "₹{:.2f}",
                                "20D High (R)": "₹{:.2f}",
                                "20D Low (S)": "₹{:.2f}",
                                "Volume SMA": "{:,.0f}"
                            }), width="stretch")
                        else:
                            st.info("Stage 1 cache is currently empty.")
                    except Exception as e:
                        st.error(f"Could not load Stage 1 results: {e}")
                else:
                    st.info("No Stage 1 results cached yet. Click 'Run Stage 1 Scan' above to evaluate stocks.")

            with res_col2:
                st.markdown("<h3 style='color:#1e293b; font-weight:600;'>📊 Stage 2 Watchlist Results</h3>", unsafe_allow_html=True)
                # Load from Stage 2 validation watchlist JSON file
                if os.path.exists("volatility_contraction_watchlist.json"):
                    try:
                        with open("volatility_contraction_watchlist.json", "r") as f:
                            watchlist = json.load(f)
                        if watchlist:
                            df2_data = []
                            for tok_str, val in watchlist.items():
                                df2_data.append({
                                    "Ticker": val["symbol"],
                                    "Trigger Buy (20D High)": val["trigger_buy"],
                                    "Trigger Sell (20D Low)": val["trigger_sell"]
                                })
                            df2 = pd.DataFrame(df2_data)
                            st.dataframe(df2.style.format({
                                "Trigger Buy (20D High)": "₹{:.2f}",
                                "Trigger Sell (20D Low)": "₹{:.2f}"
                            }), width="stretch")
                        else:
                            st.info("No candidates are currently contracting in volatility (ATR).")
                    except Exception as e:
                        st.error(f"Could not load Stage 2 results: {e}")
                else:
                    st.info("No Stage 2 setups validated yet. Click 'Run Stage 2 Validation' above.")

        if strategy != "Volatility Contraction Scanner (Kite)" and not current_results.empty:
            st.success(f"Found {len(current_results)} stocks matching the {strategy} criteria!")

            # Add Chart Links and Sparklines to scan results
            if 'Token' in current_results.columns:
                def make_scan_chart_link(row):
                    if pd.notna(row['Token']):
                        return f"https://kite.zerodha.com/markets/ext/chart/web/ciq/NSE/{row['Ticker']}/{int(row['Token'])}"
                    return None
                current_results['Chart'] = current_results.apply(make_scan_chart_link, axis=1)

                cols = list(current_results.columns)
                if 'Chart' in cols: cols.remove('Chart')
                if 'Price History' in cols: cols.remove('Price History')

                cols.insert(1, 'Chart')
                if 'Price History' in current_results.columns:
                    cols.insert(2, 'Price History')

                display_cols = [c for c in cols if c != 'Token']

                st.dataframe(
                    current_results, 
                    width='stretch',
                    column_config={
                        "Chart": st.column_config.LinkColumn("Chart 📈", display_text="View Chart"),
                        "Price History": st.column_config.LineChartColumn("Trend (20D) 📊", y_min=None, y_max=None)
                    },
                    column_order=display_cols
                )
            else:
                st.dataframe(current_results, width='stretch')

            if strategy == "15-Min ORB Breakout (Kite)":
                if st.button("🚀 Execute All as Intraday Paper Trades"):
                    import paper_trader
                    count = 0
                    for _, row in current_results.iterrows():
                        if paper_trader.execute_paper_trade(
                            ticker=row['Ticker'],
                            trade_type=row['Breakout'],
                            entry_price=row['Breakout Price'],
                            sl=row['Paper SL'],
                            qty=row['Paper Qty'],
                            token=row.get('Token'),
                            strategy="15-Min ORB"
                        ):
                            count += 1
                    st.success(f"Executed {count} new paper trades!")
                    st.rerun()

            if strategy == "3:15 PM Swing Setup (Kite)":
                st.markdown("### 🎯 Selective Swing Execution")
                selected_tickers = st.multiselect("Choose tickers to paper trade:", current_results['Ticker'].tolist())

                if st.button("📝 Execute Selected as Swing Trades"):
                    if not selected_tickers:
                        st.warning("Please select at least one stock.")
                    else:
                        import paper_trader
                        count = 0
                        for ticker in selected_tickers:
                            row = current_results[current_results['Ticker'] == ticker].iloc[0]
                            # Default capital 1L per trade
                            qty = round(100000 / row['LTP']) if row['LTP'] > 0 else 0
                            if paper_trader.execute_swing_trade(
                                ticker=row['Ticker'],
                                entry_price=row['LTP'],
                                target=row['Target'],
                                sl=row['Stop Loss'],
                                qty=qty,
                                token=row.get('Token')
                            ):
                                count += 1
                        st.success(f"Successfully added {count} trades to Swing Portfolio!")
                        st.rerun()

            if strategy == "Multi-Year Breakout (Kite)":
                st.markdown("### 🎯 Selective Swing Execution")
                selected_tickers = st.multiselect("Choose tickers to paper trade:", current_results['Ticker'].tolist(), key="myb_multiselect")

                if st.button("📝 Execute Selected as Swing Trades", key="myb_execute_button"):
                    if not selected_tickers:
                        st.warning("Please select at least one stock.")
                    else:
                        import paper_trader
                        count = 0
                        for ticker in selected_tickers:
                            row = current_results[current_results['Ticker'] == ticker].iloc[0]
                            # Default capital 1L per trade
                            qty = round(100000 / row['Close']) if row['Close'] > 0 else 0
                            if paper_trader.execute_swing_trade(
                                ticker=row['Ticker'],
                                entry_price=row['Close'],
                                target=row['Target 1 (1.5R)'],
                                sl=row['Stop Loss'],
                                qty=qty,
                                token=row.get('Token')
                            ):
                                count += 1
                        st.success(f"Successfully added {count} trades to Swing Portfolio!")
                        st.rerun()

            if strategy == "Failed Breakout Short (Kite)":
                st.markdown("### 🎯 Selective Intraday Short Execution")
                selected_tickers = st.multiselect("Choose tickers to paper trade:", current_results['Ticker'].tolist(), key="failed_multiselect")

                if st.button("📝 Execute Selected as Failed Breakout Short Trades", key="failed_execute_button"):
                    if not selected_tickers:
                        st.warning("Please select at least one stock.")
                    else:
                        import paper_trader
                        count = 0
                        for ticker in selected_tickers:
                            row = current_results[current_results['Ticker'] == ticker].iloc[0]
                            # Since entry price can be a string "Wait for trigger < X", check if it is floatable
                            try:
                                entry_price = float(row['Entry Price'])
                                sl = float(row['Stop Loss'])
                                qty = int(row['Qty'])
                            except ValueError:
                                st.error(f"Cannot execute trade for {ticker}: Entry Price '{row['Entry Price']}' is not triggered yet.")
                                continue

                            if paper_trader.execute_paper_trade(
                                ticker=row['Ticker'],
                                trade_type="Failed Breakout",
                                entry_price=entry_price,
                                sl=sl,
                                qty=qty,
                                token=int(row.get('Token', 0)),
                                strategy="Failed Breakout Short"
                            ):
                                count += 1
                        if count > 0:
                            st.success(f"Successfully executed {count} paper trades!")
                            st.rerun()

            st.markdown("### AI Conviction Analysis")
            gemini_key = getattr(config, 'GEMINI_API_KEY', '')
            if not gemini_key:
                gemini_key = st.text_input("Gemini API Key", type="password")

            if st.button("🤖 Ask AI for Conviction Picks"):
                if not gemini_key:
                    st.error("Please provide a Gemini API Key in config.py or above to use this feature.")
                else:
                    with st.spinner("Gemini is analyzing the shortlisted stocks..."):
                        import ai_advisor
                        analysis = ai_advisor.analyze_stocks(current_results, gemini_key, strategy_name=strategy)

                        st.info("AI Analysis Complete")
                        st.markdown(analysis)

                        # Automatically send AI analysis to Telegram with dynamic infographic
                        import telegram_agent
                        import image_generator
                        tel_token = getattr(config, 'TELEGRAM_BOT_TOKEN', '')

                        # Use strategy-specific chat ID for AI analysis too
                        if strategy in ["15-Min Bearish Breakdown (Kite)", "15-Min Bullish Breakout (Kite)", "Failed Breakout Short (Kite)", "15-Min ORB Breakout (Kite)", "Volatility Contraction Scanner (Kite)"]:
                            tel_chat_id = getattr(config, 'TELEGRAM_CHAT_ID_INTRADAY', '')
                        else:
                            tel_chat_id = getattr(config, 'TELEGRAM_CHAT_ID', '')


                        # Only send if there's no error in the analysis
                        if tel_token and tel_chat_id and analysis and "Error:" not in analysis and "temporarily busy" not in analysis:
                            # Generate the graphic
                            img_path = image_generator.create_infographic(current_results, scan_name=strategy)

                            if img_path:
                                caption = f"🤖 *AI Conviction Analysis: {strategy}*\n\n{analysis}"
                                if len(caption) < 1000:
                                     success = telegram_agent.send_photo(img_path, caption, tel_token, tel_chat_id)
                                else:
                                     telegram_agent.send_photo(img_path, f"🚀 Top {strategy} Picks", tel_token, tel_chat_id)
                                     success = telegram_agent.send_message(caption, tel_token, tel_chat_id)
                            else:
                                success = telegram_agent.send_message(f"🤖 *AI Conviction Analysis*\n\n{analysis}", tel_token, tel_chat_id)

                            if success:
                                st.success("📲 Infographic & AI Analysis sent to Telegram!")
                            else:
                                st.error("⚠️ Failed to send to Telegram.")
                        elif "Error:" in analysis:
                            st.warning("⚠️ AI analysis failed. Skipping Telegram infographic.")

        st.markdown("---")
        st.markdown("### 🗄️ Today's Strategy Caches (Scanned Stocks)")
        st.caption("These lists show all candidates cached/scanned for the day. Caches naturally reset on the next market day morning.")

        cache_details = {
            "15-Min ORB Breakout (Kite)": os.path.join("data", "cache", "orb_trending_cache.csv"),
            "52-Week High Breakout (Kite)": os.path.join("data", "cache", "high52_cache.csv"),
            "15-Min Bearish Breakdown (Kite)": os.path.join("data", "cache", "bearish_breakdown_cache.csv"),
            "15-Min Bullish Breakout / Failed Breakout (Kite)": os.path.join("data", "cache", "fno_strength_cache.csv")
        }

        for label, filename in cache_details.items():
            if os.path.exists(filename):
                try:
                    cache_df = pd.read_csv(filename)
                    if not cache_df.empty:
                        with st.expander(f"📁 {label} ({len(cache_df)} stocks cached)", expanded=False):
                            st.dataframe(cache_df, width="stretch")
                    else:
                        with st.expander(f"📁 {label} (Empty)", expanded=False):
                            st.info("Cache is currently empty.")
                except Exception as e:
                    st.caption(f"Error reading {filename}: {e}")
            else:
                with st.expander(f"📁 {label} (Not Found)", expanded=False):
                    st.info(f"Cache file {filename} does not exist yet. Run a scan to generate it.")

        # --- GLOBAL AUTO-REFRESH TIMER FOR ACTIVE MONITORS ---
        if any_active and bool(st.session_state.get('kite_access_token')):
            st.markdown(
                """
                /* Hide the auto-refresh button container from the UI */
                div.element-container:has(button[aria-label="AutoRefreshTriggerBtn"]) {
                    display: none !important;
                }
                button[aria-label="AutoRefreshTriggerBtn"] {
                    display: none !important;
                }
                /* Hide the auto-refresh iframe safely without using display:none so browser JS execution is not suspended */
                div.element-container:has(iframe) {
                    position: absolute !important;
                    left: -9999px !important;
                    top: -9999px !important;
                    width: 10px !important;
                    height: 10px !important;
                    opacity: 0 !important;
                    pointer-events: none !important;
                }
                </style>
                """, 
                unsafe_allow_html=True
            )
            if st.button("AutoRefreshTriggerBtn", key="auto_refresh_trigger_btn"):
                st.rerun()
            st.iframe(
                """
                <script>
                setTimeout(function() {
                    const doc = window.parent.document;
                    const buttons = Array.from(doc.querySelectorAll("button"));
                    const refreshBtn = buttons.find(b => b.innerText && b.innerText.trim() === "AutoRefreshTriggerBtn");
                    if (refreshBtn) {
                        refreshBtn.click();
                    }
                }, 60000);
                </script>
                """,
                height=1
            )
