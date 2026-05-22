import streamlit as st
import datetime
import scanner
import kite_scanner
import high52_scanner
import bullish_breakout_scanner
import long_trade_scanner
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

# --- OPTIONS BOT LIVE LOGIC (TOP PLACEMENT) ---
if st.session_state.get('kite_access_token'):
    if st.session_state.get("view_options_log"):
        st.markdown("---")
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
                
                if st.button("🛑 Emergency Stop Bot", type="primary", use_container_width=True):
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
                # Format OI in Lakhs for readability
                ce_lakhs = state['ce_oi'] / 100000
                pe_lakhs = state['pe_oi'] / 100000
                st.metric("Total Call OI", f"{ce_lakhs:.1f} L")
            with col4:
                st.markdown(f"**Latest Signal**<br><span style='color:{sig_color}; font-size:1.1rem; font-weight:bold;'>{sig}</span>", unsafe_allow_html=True)
                st.markdown(f"**Recommended Trade**<br><span style='color:#3b82f6; font-size:1.1rem; font-weight:bold;'>{state.get('recommended_trade', 'Wait')}</span>", unsafe_allow_html=True)

            # --- Execution Button ---
            st.markdown("<br>", unsafe_allow_html=True)
            if state.get("is_running") and state.get('recommended_trade') not in ["Wait", "None"]:
                if st.button("⚡ Execute Recommended Trade", type="primary", use_container_width=True):
                    kite = KiteConnect(api_key=api_key)
                    kite.set_access_token(st.session_state.kite_access_token)
                    success, msg = options_bot.execute_bot_recommendation(kite, state.get("index_name", "NIFTY")) # Assuming index_name is in state or default NIFTY
                    if success:
                        st.success(msg)
                        st.toast(msg, icon="🚀")
                    else:
                        st.error(msg)
            
            # --- Signal Log ---
            st.markdown("### 📜 Signal Log")
            import os
            import pandas as pd
            if os.path.exists("options_signals.csv"):
                log_df = pd.read_csv("options_signals.csv")
                if len(log_df.columns) == 6:
                    log_df.columns = ["Timestamp", "Index", "Signal", "Score", "PCR", "Spot"]
                elif len(log_df.columns) == 7:
                    log_df.columns = ["Timestamp", "Index", "Signal", "Score", "PCR", "Spot", "Recommendation"]
                
                st.dataframe(log_df.tail(20).sort_values("Timestamp", ascending=False), use_container_width=True)
                
                if st.button("🗑️ Clear Log", key="clear_opt_log"):
                    os.remove("options_signals.csv")
                    st.rerun()
            else:
                st.info("No signals generated yet. Ensure the bot is running during market hours.")
                
        except Exception as e:
            st.error(f"Could not load Options Bot state: {e}")

    # --- LIVE PORTFOLIO DASHBOARD ---
    st.markdown("---")
    st.markdown("## 📦 Live Paper Trading Portfolio")

    # Cache expensive Kite LTP calls for 60 s to avoid repeated fetches on every widget interaction
    @st.cache_data(ttl=60, show_spinner=False)
    def _cached_portfolio(access_token):
        _kite = KiteConnect(api_key=getattr(config, 'KITE_API_KEY', ''))
        _kite.set_access_token(access_token)
        return paper_trader.update_portfolio_pnl(_kite)

    try:
        portfolio_df = _cached_portfolio(st.session_state.kite_access_token)
        
        # --- PERIODIC TELEGRAM UPDATES (Every 10 Minutes) ---
        if 'last_telegram_update' not in st.session_state:
            st.session_state.last_telegram_update = datetime.datetime.now() - datetime.timedelta(minutes=11)
            
        if datetime.datetime.now() - st.session_state.last_telegram_update >= datetime.timedelta(minutes=10):
            now = datetime.datetime.now()
            current_time = now.time()
            start_time = datetime.time(9, 30)
            end_time = datetime.time(15, 25)
            
            # Check if there's at least one active trade
            has_open_positions = not portfolio_df.empty and (portfolio_df['Status'] == 'Active').any()
            
            if start_time <= current_time <= end_time and has_open_positions:
                import telegram_agent
                tel_token = getattr(config, 'TELEGRAM_BOT_TOKEN', '')
                tel_chat_id = getattr(config, 'TELEGRAM_CHAT_ID_INTRADAY', getattr(config, 'TELEGRAM_CHAT_ID', ''))
                
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
                
                st.markdown(f"""
                <div style="display: flex; gap: 15px; flex-wrap: wrap; margin-bottom: 25px;">
                    {"".join(cards_html)}
                </div>
                """, unsafe_allow_html=True)
                
                if st.button("🔄 Refresh Live P&L", use_container_width=False):
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
                    
                    st.markdown("---")
                    st.subheader("🧹 Clear Trades")
                    
                    strategies_in_portfolio = portfolio_df['Strategy'].unique().tolist() if 'Strategy' in portfolio_df.columns and not portfolio_df.empty else []
                    if strategies_in_portfolio:
                        strategy_to_clear = st.selectbox("Select Strategy to Clear:", strategies_in_portfolio)
                        if st.button(f"🧹 Clear '{strategy_to_clear}' Trades", use_container_width=True):
                            paper_trader.clear_portfolio_by_strategy(strategy_to_clear)
                            st.cache_data.clear()
                            st.success(f"Cleared {strategy_to_clear}!")
                            st.rerun()
                                
                    st.markdown("<br>", unsafe_allow_html=True)
                    if st.button("🚨 Clear Entire Portfolio", type="secondary"):
                        paper_trader.clear_portfolio()
                        st.cache_data.clear()
                        st.success("Cleared All!")
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
            
            if st.button("🗑️ Clear History"):
                if os.path.exists(paper_trader.HISTORY_FILE):
                    os.remove(paper_trader.HISTORY_FILE)
                    st.success("History cleared!")
                    st.rerun()
        else:
            st.info("No closed trades in history yet.")
    except Exception as e:
        st.warning(f"Portfolio update paused: {e}")
    
    # --- POSITIONAL SWING PORTFOLIO SECTION ---
    st.markdown("---")
    st.markdown("## 📊 Positional Swing Portfolio (3:15 PM)")

    @st.cache_data(ttl=60, show_spinner=False)
    def _cached_swing(access_token):
        _kite = KiteConnect(api_key=getattr(config, 'KITE_API_KEY', ''))
        _kite.set_access_token(access_token)
        return paper_trader.update_swing_portfolio(_kite)

    try:
        full_swing_df = _cached_swing(st.session_state.kite_access_token)
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

# --- NOTIFICATION CENTER (SIDEBAR) ---
st.sidebar.markdown("### 🔔 Activity Feed")
n_count = len(st.session_state.notifications)

with st.sidebar.expander(f"Recent Alerts ({n_count})", expanded=True):
    if not st.session_state.notifications:
        st.caption("No recent activity.")
    else:
        if st.button("Clear All Feed", use_container_width=True):
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
    
    if 'mon_orb' not in st.session_state: st.session_state.mon_orb = False
    if 'mon_52w' not in st.session_state: st.session_state.mon_52w = False
    if 'mon_bearish' not in st.session_state: st.session_state.mon_bearish = False
    if 'mon_vwap_rejection' not in st.session_state: st.session_state.mon_vwap_rejection = False
    if 'mon_bullish' not in st.session_state: st.session_state.mon_bullish = False
    if 'mon_failed_breakout' not in st.session_state: st.session_state.mon_failed_breakout = False
    
    st.session_state.mon_orb = st.sidebar.toggle("15-Min ORB Monitor", value=st.session_state.mon_orb)
    st.session_state.mon_52w = st.sidebar.toggle("52-Week High Monitor", value=st.session_state.mon_52w)
    st.session_state.mon_bearish = st.sidebar.toggle("Bearish Breakdown Monitor", value=st.session_state.mon_bearish)
    st.session_state.mon_vwap_rejection = st.sidebar.toggle("Bearish VWAP Rejection Monitor", value=st.session_state.mon_vwap_rejection)
    st.session_state.mon_bullish = st.sidebar.toggle("Bullish Breakout Monitor", value=st.session_state.mon_bullish)
    st.session_state.mon_failed_breakout = st.sidebar.toggle("Failed Breakout Short Monitor", value=st.session_state.mon_failed_breakout)
    
    # Persistent Toggle for AI Active Positions Advisor
    import ai_advisor
    ai_advisor_state = ai_advisor.is_ai_advisor_enabled()
    ai_advisor_toggle = st.sidebar.toggle("🤖 AI Position Advisor", value=ai_advisor_state)
    if ai_advisor_toggle != ai_advisor_state:
        ai_advisor.set_ai_advisor_enabled(ai_advisor_toggle)
        st.session_state.last_ai_advisor_run = None # Reset so it triggers immediately when enabled
        st.toast(f"🤖 AI Position Advisor {'Enabled' if ai_advisor_toggle else 'Disabled'}!", icon="🔔")
        
    if ai_advisor_toggle:
        # Check active hour window
        now = datetime.datetime.now()
        current_time = now.time()
        start_time = datetime.time(9, 45)
        end_time = datetime.time(15, 25)
        is_within_window = (start_time <= current_time <= end_time) and (now.weekday() <= 4)
        if not is_within_window:
            st.sidebar.warning("⚠️ AI Advisor is active but currently outside market hours (9:45 AM - 3:25 PM Weekdays). It will analyze your positions once active.")
    
    if st.session_state.mon_orb or st.session_state.mon_52w or st.session_state.mon_bearish or st.session_state.mon_bullish or st.session_state.mon_vwap_rejection or st.session_state.mon_failed_breakout or ai_advisor_toggle:
        st.sidebar.success("Live Monitoring ACTIVE")
        if st.sidebar.button("⏹️ Stop All Monitors"):
            st.session_state.mon_orb = False
            st.session_state.mon_52w = False
            st.session_state.mon_bearish = False
            st.session_state.mon_vwap_rejection = False
            st.session_state.mon_bullish = False
            st.session_state.mon_failed_breakout = False
            ai_advisor.set_ai_advisor_enabled(False)
            st.session_state.last_ai_advisor_run = None
            st.rerun()


# --- OPTIONS SELLING BOT (SIDEBAR) ---
st.sidebar.markdown("---")
st.sidebar.header("🤖 Options Selling Bot")
if st.session_state.get('kite_access_token'):
    import options_bot
    bot_state = options_bot.get_state()
    
    bot_index = st.sidebar.radio("Select Index", ["NIFTY", "SENSEX"], horizontal=True)
    
    if bot_state["is_running"]:
        st.sidebar.success(f"🟢 Bot is Running ({bot_index})")
        if st.sidebar.button("⏹️ Stop Bot", use_container_width=True):
            options_bot.stop_bot()
            st.session_state.view_options_log = True
            st.rerun()
    else:
        st.sidebar.info("🔴 Bot is Stopped")
        if st.sidebar.button("▶️ Start Bot", type="primary", use_container_width=True):
            kite = KiteConnect(api_key=api_key)
            kite.set_access_token(st.session_state.kite_access_token)
            success, msg = options_bot.start_bot(kite, bot_index)
            st.session_state.view_options_log = True
            if success:
                st.sidebar.success(msg)
            else:
                st.sidebar.error(msg)
            st.rerun()
            
    if st.sidebar.button("📜 View Signal Log", use_container_width=True):
        st.session_state.view_options_log = not st.session_state.view_options_log
else:
    st.sidebar.warning("🔒 Login required for Options Bot")

st.sidebar.markdown("---")
st.sidebar.header("🎯 Strategy Control Center")

# Strategy Categories
KITE_STRATEGIES = [
    "15-Min ORB Breakout (Kite)", 
    "52-Week High Breakout (Kite)", 
    "15-Min Bearish Breakdown (Kite)", 
    "15-Min Bullish Breakout (Kite)",
    "Failed Breakout Short (Kite)",
    "3:15 PM Swing Setup (Kite)",
    "EOD Long Swing Setup (Kite)",
    "Multi-Year Breakout (Kite)"
]
YF_STRATEGIES = ["Swing Trade Candidates", "Volume Breakout Stocks"]

selected_strategies = st.sidebar.multiselect(
    "Select Active Strategies",
    options=YF_STRATEGIES + KITE_STRATEGIES,
    default=["15-Min ORB Breakout (Kite)"]
)

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
    "15-Min ORB Breakout (Kite)": "orb_trending_cache.csv",
    "52-Week High Breakout (Kite)": "high52_cache.csv",
    "15-Min Bearish Breakdown (Kite)": "bearish_breakdown_cache.csv",
    "15-Min Bullish Breakout (Kite)": "bullish_breakout_cache.csv",
    "Failed Breakout Short (Kite)": "failed_breakout_cache.csv"
}

for s in selected_strategies:
    if s in cache_files:
        count = get_cache_count(cache_files[s])
        st.sidebar.markdown(f"**{s}**: `{count}` stocks cached")

st.sidebar.markdown("---")
st.sidebar.subheader("⚡ Bulk Operations")

if any(s in KITE_STRATEGIES for s in selected_strategies):
    if not st.session_state.kite_access_token:
        st.sidebar.warning("🔒 Login required for Kite strategies")
    else:
        refresh_orb = st.sidebar.checkbox("Refresh ORB Only", value=False, help="Only updates today's momentum for ORB")
        refresh_bullish = st.sidebar.checkbox("Refresh Bullish Only", value=False)
        refresh_bearish = st.sidebar.checkbox("Refresh Bearish Only", value=False)
        refresh_failed = st.sidebar.checkbox("Refresh Failed Breakout Only", value=False)
        
        if st.sidebar.button("🚀 Run Sequential Cache", use_container_width=True):
            kite = KiteConnect(api_key=api_key)
            kite.set_access_token(st.session_state.kite_access_token)
            
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
                elif s == "15-Min Bullish Breakout (Kite)":
                    st.info(f"🔄 Caching Bullish...")
                    p_bar = st.progress(0)
                    bullish_breakout_scanner.cache_bullish_candidates(kite, progress_callback=lambda p, t, sym: p_bar.progress(p/t), refresh_only=refresh_bullish)
                    p_bar.empty()
                elif s == "Failed Breakout Short (Kite)":
                    st.info(f"🔄 Caching Failed Breakout...")
                    p_bar = st.progress(0)
                    import failed_breakout_scanner
                    failed_breakout_scanner.cache_failed_candidates(kite, progress_callback=lambda p, t, sym: p_bar.progress(p/t), refresh_only=refresh_failed)
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
if st.sidebar.button("❓ Help & Documentation", use_container_width=True):
    show_help_dialog()

# Initialize session state for multi-strategy results
if 'all_results' not in st.session_state:
    st.session_state.all_results = {}

# Relocated Options Bot Live Logic to the top


# Allow users to run scan
if st.button(f"Run Scan: {strategy}", type="primary"):
    if strategy in ["3:15 PM Swing Setup (Kite)", "15-Min ORB Breakout (Kite)", "EOD Long Swing Setup (Kite)", "Multi-Year Breakout (Kite)"] and not st.session_state.kite_access_token:
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
            if strategy in ["15-Min Bearish Breakdown (Kite)", "15-Min Bullish Breakout (Kite)"]:
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
any_active = (
    st.session_state.get('mon_orb') or 
    st.session_state.get('mon_52w') or 
    st.session_state.get('mon_bearish') or 
    st.session_state.get('mon_bullish') or 
    st.session_state.get('mon_vwap_rejection') or
    ai_advisor.is_ai_advisor_enabled()
)


if any_active and st.session_state.get('kite_access_token'):
    st.info(f"🔄 Live Monitoring ACTIVE: Next cycle in 1 minute... (Last: {datetime.datetime.now().strftime('%H:%M:%S')})")
    
    monitor_progress = st.progress(0)
    monitor_status = st.empty()
    
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(st.session_state.kite_access_token)
    
    def update_mon_progress(processed, total, symbol, scan_name):
        progress = min(processed / total, 1.0)
        monitor_progress.progress(progress)
        monitor_status.text(f"Monitoring {scan_name}: {symbol} ({processed}/{total})")

    # 1. RUN ORB MONITOR
    if st.session_state.mon_orb:
        with st.spinner("Live Scanning ORB Breakouts..."):
            res_orb, pre_screen_count = kite_scanner.scan_orb_setups(kite, progress_callback=lambda p, t, s: update_mon_progress(p, t, s, "ORB"))
            if pre_screen_count > 0:
                monitor_status.text(f"ORB Monitor: {pre_screen_count} candidates active.")
            
            if not res_orb.empty:
                import notification_helper
                new_tickers = notification_helper.filter_new_tickers("ORB", res_orb['Ticker'].tolist())
                
                if new_tickers:
                    new_orb = res_orb[res_orb['Ticker'].isin(new_tickers)]
                    # Check active portfolio and filter out existing tickers
                    import paper_trader
                    p_df = paper_trader.get_portfolio()
                    active_tickers = p_df[p_df['Status'] == 'Active']['Ticker'].tolist() if not p_df.empty else []
                    new_orb = new_orb[~new_orb['Ticker'].isin(active_tickers)]
                    
                    if not new_orb.empty:
                        st.session_state.results_df = new_orb # Update display
                        import telegram_agent
                        for _, row in new_orb.iterrows():
                            msg = telegram_agent.format_signal_message(row, "ORB Breakout")
                            # Fetch 2 days of 5m data for chart (resampled to 15m later)
                            df_chart = kite_scanner.fetch_kite_data(kite, row['Token'], datetime.datetime.now() - datetime.timedelta(days=2), datetime.datetime.now(), "5minute")
                            telegram_agent.send_signal_with_chart(row['Ticker'], msg, df_chart, config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID, "ORB Breakout", row_data=row)
                        st.toast(f"🔥 {len(new_orb)} New ORB Breakouts!", icon="🚀")
                        
                        for _, row in new_orb.iterrows():
                            add_notification(row['Ticker'], f"New ORB Breakout: {row['Breakout']} at ₹{row['Breakout Price']}")
                        
                        notification_helper.mark_as_notified("ORB", new_orb['Ticker'].tolist())
                        
                        # --- AUTO-EXECUTE PAPER TRADES ---
                        p_count = 0
                        for _, row in new_orb.iterrows():
                            if paper_trader.execute_paper_trade(
                                ticker=row['Ticker'],
                                trade_type=row['Breakout'],
                                entry_price=row['Breakout Price'],
                                sl=row['Paper SL'],
                                qty=row['Paper Qty'],
                                token=row.get('Token'),
                                strategy="15-Min ORB"
                            ):
                                p_count += 1
                        if p_count > 0:
                            st.toast(f"✅ Executed {p_count} paper trades for ORB", icon="📈")

    # 2. RUN 52W HIGH MONITOR
    if st.session_state.mon_52w:
        with st.spinner("Live Scanning 52W High Breakouts..."):
            res_52w, pre_screen_count = high52_scanner.scan_52w_breakouts(kite, progress_callback=lambda p, t, s: update_mon_progress(p, t, s, "52W High"))
            if pre_screen_count > 0:
                monitor_status.text(f"52W Monitor: {pre_screen_count} candidates active.")
            
            if not res_52w.empty:
                import notification_helper
                new_tickers = notification_helper.filter_new_tickers("52W", res_52w['Ticker'].tolist())
                
                if new_tickers:
                    new_52w = res_52w[res_52w['Ticker'].isin(new_tickers)]
                    st.session_state.results_df = new_52w # Update display
                    import telegram_agent
                    telegram_agent.send_dataframe(new_52w, config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID, scan_name="LIVE: 52W High")
                    st.toast(f"🔥 {len(new_52w)} New 52W High Breakouts!", icon="💥")
                    
                    for _, row in new_52w.iterrows():
                        add_notification(row['Ticker'], f"New 52W High Breakout at ₹{row['LTP']}")
                    
                    notification_helper.mark_as_notified("52W", new_tickers)
                    
                    # --- AUTO-EXECUTE PAPER TRADES ---
                    import paper_trader
                    p_count = 0
                    for _, row in new_52w.iterrows():
                        # Calculate default Qty for 52W (e.g. 250,000 per trade)
                        qty = round(250000 / row['LTP']) if row['LTP'] > 0 else 0

                        if paper_trader.execute_paper_trade(
                            ticker=row['Ticker'],
                            trade_type="Bullish (52W High)",
                            entry_price=row['LTP'],
                            sl=row['LTP'] * 0.97, # 3% SL for 52W
                            qty=qty,
                            token=row.get('Token'),
                            strategy="52W High"
                        ):
                            p_count += 1
                    if p_count > 0:
                        st.toast(f"✅ Executed {p_count} paper trades for 52W High", icon="📈")

    # 3. RUN BEARISH BREAKDOWN MONITOR
    if st.session_state.mon_bearish:
        with st.spinner("Live Scanning Bearish Breakdowns..."):
            import bearish_breakdown_scanner
            res_bear = bearish_breakdown_scanner.scan_bearish_breakdowns(kite, progress_callback=lambda p, t, s: update_mon_progress(p, t, s, "Bearish"))
            
            if not res_bear.empty:
                # Filter for only triggered signals
                triggered_bear = res_bear[res_bear['Status'] == 'Triggered']
                
                if not triggered_bear.empty:
                    import notification_helper
                    new_tickers = notification_helper.filter_new_tickers("BEARISH", triggered_bear['Ticker'].tolist())
                    
                    if new_tickers:
                        new_bear = triggered_bear[triggered_bear['Ticker'].isin(new_tickers)]
                        # Check active portfolio and filter out existing tickers
                        import paper_trader
                        p_df = paper_trader.get_portfolio()
                        active_tickers = p_df[p_df['Status'] == 'Active']['Ticker'].tolist() if not p_df.empty else []
                        new_bear = new_bear[~new_bear['Ticker'].isin(active_tickers)]
                        
                        if not new_bear.empty:
                            st.session_state.results_df = new_bear # Update display
                            import telegram_agent
                            # Route to Intraday Channel
                            tel_chat_id_intraday = getattr(config, 'TELEGRAM_CHAT_ID_INTRADAY', config.TELEGRAM_CHAT_ID)
                            
                            for _, row in new_bear.iterrows():
                                msg = telegram_agent.format_signal_message(row, "Bearish Breakdown")
                                df_chart = kite_scanner.fetch_kite_data(kite, row['Token'], datetime.datetime.now() - datetime.timedelta(days=2), datetime.datetime.now(), "5minute")
                                telegram_agent.send_signal_with_chart(row['Ticker'], msg, df_chart, config.TELEGRAM_BOT_TOKEN, tel_chat_id_intraday, "Bearish Breakdown", row_data=row)

                            st.toast(f"🔥 {len(new_bear)} Bearish Breakdowns Triggered!", icon="🔴")
                            
                            for _, row in new_bear.iterrows():
                                add_notification(row['Ticker'], f"🔴 Bearish Breakdown Entry @ {row['Entry Price']}", category="Bearish")
                            
                            notification_helper.mark_as_notified("BEARISH", new_bear['Ticker'].tolist())
                            
                            # --- AUTO-EXECUTE PAPER TRADES ---
                            p_count = 0
                            for _, row in new_bear.iterrows():
                                if paper_trader.execute_paper_trade(
                                    ticker=row['Ticker'],
                                    trade_type="Bearish Breakdown",
                                    entry_price=row['Entry Price'],
                                    sl=row['Stop Loss'],
                                    qty=row['Qty'],
                                    token=row.get('Token'),
                                    strategy="Bearish Breakdown"
                                ):
                                    p_count += 1
                            if p_count > 0:
                                st.toast(f"✅ Executed {p_count} paper trades for Bearish Breakdown", icon="📉")

    # 3.5. RUN BEARISH VWAP REJECTION MONITOR
    if st.session_state.mon_vwap_rejection:
        with st.spinner("Live Scanning Bearish VWAP Rejections..."):
            import bearish_vwap_rejection_scanner
            res_vwap, monitored_vwap = bearish_vwap_rejection_scanner.scan_bearish_vwap_rejections(kite)
            
            if not res_vwap.empty:
                import notification_helper
                new_tickers = notification_helper.filter_new_tickers("BEARISH_VWAP_REJECTION", res_vwap['Ticker'].tolist())
                
                if new_tickers:
                    new_vwap = res_vwap[res_vwap['Ticker'].isin(new_tickers)]
                    # Check active portfolio and filter out existing tickers
                    import paper_trader
                    p_df = paper_trader.get_portfolio()
                    active_tickers = p_df[p_df['Status'] == 'Active']['Ticker'].tolist() if not p_df.empty else []
                    new_vwap = new_vwap[~new_vwap['Ticker'].isin(active_tickers)]
                    
                    if not new_vwap.empty:
                        st.session_state.results_df = new_vwap  # Update display
                        import telegram_agent
                        # Route to Intraday Channel
                        tel_chat_id_intraday = getattr(config, 'TELEGRAM_CHAT_ID_INTRADAY', config.TELEGRAM_CHAT_ID)
                        
                        for _, row in new_vwap.iterrows():
                            msg = (
                                f"📉 *Bearish VWAP Rejection Alert* 📉\\n\\n"
                                f"🎯 *Ticker*: {row['Ticker']}\\n"
                                f"🔴 *Entry (Short)*: ₹{row['Price']}\\n"
                                f"🛡️ *Stop Loss*: ₹{row['SL']}\\n"
                                f"🟢 *Target 1 (1.5R)*: ₹{row['Target_1']}\\n"
                                f"🟢 *Target 2 (3.0R)*: ₹{row['Target_2']}\\n"
                                f"📊 *Pattern*: {row['Pattern']}\\n"
                                f"🛡️ *Zone*: {row['Zone']} Rejection\\n"
                                f"📈 *Risk/Reward*: {row['Risk_Reward']}\\n"
                            )
                            df_chart = kite_scanner.fetch_kite_data(kite, int(row['Token']), datetime.datetime.now() - datetime.timedelta(days=2), datetime.datetime.now(), "5minute")
                            telegram_agent.send_signal_with_chart(row['Ticker'], msg, df_chart, config.TELEGRAM_BOT_TOKEN, tel_chat_id_intraday, "Bearish VWAP Rejection", row_data=row)

                        st.toast(f"🔥 {len(new_vwap)} Bearish VWAP Rejections Triggered!", icon="🔴")
                        
                        for _, row in new_vwap.iterrows():
                            add_notification(row['Ticker'], f"🔴 Bearish VWAP Rejection Entry @ {row['Price']}", category="Bearish")
                        
                        notification_helper.mark_as_notified("BEARISH_VWAP_REJECTION", new_vwap['Ticker'].tolist())
                        
                        # --- AUTO-EXECUTE PAPER TRADES ---
                        p_count = 0
                        for _, row in new_vwap.iterrows():
                            capital = 250000 # Default capital per trade
                            qty = int(capital / row['Price'])
                            if paper_trader.execute_paper_trade(
                                ticker=row['Ticker'],
                                trade_type="Bearish Pullback",
                                entry_price=row['Price'],
                                sl=row['SL'],
                                qty=qty,
                                token=int(row['Token']),
                                strategy="Bearish VWAP Rejection"
                            ):
                                p_count += 1
                        if p_count > 0:
                            st.toast(f"✅ Executed {p_count} paper trades for Bearish VWAP Rejection", icon="📉")

    # 4. RUN BULLISH BREAKOUT MONITOR
    if st.session_state.mon_bullish:
        with st.spinner("Live Scanning Bullish Breakouts..."):
            res_bull = bullish_breakout_scanner.scan_bullish_breakouts(kite, progress_callback=lambda p, t, s: update_mon_progress(p, t, s, "Bullish"))
            
            if not res_bull.empty:
                triggered_bull = res_bull[res_bull['Status'] == 'Triggered']
                
                if not triggered_bull.empty:
                    import notification_helper
                    new_tickers = notification_helper.filter_new_tickers("BULLISH", triggered_bull['Ticker'].tolist())
                    
                    if new_tickers:
                        new_bull = triggered_bull[triggered_bull['Ticker'].isin(new_tickers)]
                        # Check active portfolio and filter out existing tickers
                        import paper_trader
                        p_df = paper_trader.get_portfolio()
                        active_tickers = p_df[p_df['Status'] == 'Active']['Ticker'].tolist() if not p_df.empty else []
                        new_bull = new_bull[~new_bull['Ticker'].isin(active_tickers)]
                        
                        if not new_bull.empty:
                            st.session_state.results_df = new_bull
                            import telegram_agent
                            tel_chat_id_intraday = getattr(config, 'TELEGRAM_CHAT_ID_INTRADAY', config.TELEGRAM_CHAT_ID)
                            
                            for _, row in new_bull.iterrows():
                                msg = telegram_agent.format_signal_message(row, "Bullish Breakout")
                                df_chart = kite_scanner.fetch_kite_data(kite, row['Token'], datetime.datetime.now() - datetime.timedelta(days=2), datetime.datetime.now(), "5minute")
                                telegram_agent.send_signal_with_chart(row['Ticker'], msg, df_chart, config.TELEGRAM_BOT_TOKEN, tel_chat_id_intraday, "Bullish Breakout", row_data=row)

                            st.toast(f"🔥 {len(new_bull)} Bullish Breakouts Triggered!", icon="🟢")
                            
                            for _, row in new_bull.iterrows():
                                add_notification(row['Ticker'], f"🟢 Bullish Breakout Entry @ {row['Entry Price']}", category="Bullish")
                            
                            notification_helper.mark_as_notified("BULLISH", new_bull['Ticker'].tolist())
                            
                            p_count = 0
                            for _, row in new_bull.iterrows():
                                if paper_trader.execute_paper_trade(
                                    ticker=row['Ticker'],
                                    trade_type="Bullish Breakout",
                                    entry_price=row['Entry Price'],
                                    sl=row['Stop Loss'],
                                    qty=row['Qty'],
                                    token=row.get('Token'),
                                    strategy="Bullish Breakout"
                                ):
                                    p_count += 1
                            if p_count > 0:
                                st.toast(f"✅ Executed {p_count} paper trades for Bullish Breakout", icon="📈")

        # 5. RUN FAILED BREAKOUT SHORT MONITOR
        if st.session_state.mon_failed_breakout:
            with st.spinner("Live Scanning Failed Breakouts..."):
                import failed_breakout_scanner
                res_failed = failed_breakout_scanner.scan_failed_breakouts(kite, progress_callback=lambda p, t, s: update_mon_progress(p, t, s, "Failed Breakout"))
                
                if not res_failed.empty:
                    triggered_failed = res_failed[res_failed['Status'] == 'Triggered']
                    
                    if not triggered_failed.empty:
                        import notification_helper
                        new_tickers = notification_helper.filter_new_tickers("FAILED_BREAKOUT", triggered_failed['Ticker'].tolist())
                        
                        if new_tickers:
                            new_fail = triggered_failed[triggered_failed['Ticker'].isin(new_tickers)]
                            # Check active portfolio and filter out existing tickers
                            import paper_trader
                            p_df = paper_trader.get_portfolio()
                            active_tickers = p_df[p_df['Status'] == 'Active']['Ticker'].tolist() if not p_df.empty else []
                            new_fail = new_fail[~new_fail['Ticker'].isin(active_tickers)]
                            
                            if not new_fail.empty:
                                st.session_state.results_df = new_fail
                                import telegram_agent
                                tel_chat_id_intraday = getattr(config, 'TELEGRAM_CHAT_ID_INTRADAY', config.TELEGRAM_CHAT_ID)
                                
                                for _, row in new_fail.iterrows():
                                    msg = telegram_agent.format_signal_message(row, "Failed Breakout Short")
                                    df_chart = kite_scanner.fetch_kite_data(kite, row['Token'], datetime.datetime.now() - datetime.timedelta(days=2), datetime.datetime.now(), "5minute")
                                    telegram_agent.send_signal_with_chart(row['Ticker'], msg, df_chart, config.TELEGRAM_BOT_TOKEN, tel_chat_id_intraday, "Failed Breakout Short", row_data=row)

                                st.toast(f"🔥 {len(new_fail)} Failed Breakouts Triggered!", icon="🔴")
                                
                                for _, row in new_fail.iterrows():
                                    add_notification(row['Ticker'], f"🔴 Failed Breakout Entry @ {row['Entry Price']}", category="Bearish")
                                
                                notification_helper.mark_as_notified("FAILED_BREAKOUT", new_fail['Ticker'].tolist())
                                
                                p_count = 0
                                for _, row in new_fail.iterrows():
                                    if paper_trader.execute_paper_trade(
                                        ticker=row['Ticker'],
                                        trade_type="Failed Breakout",
                                        entry_price=row['Entry Price'],
                                        sl=row['Stop Loss'],
                                        qty=row['Qty'],
                                        token=row.get('Token'),
                                        strategy="Failed Breakout Short"
                                    ):
                                        p_count += 1
                                if p_count > 0:
                                    st.toast(f"✅ Executed {p_count} paper trades for Failed Breakout Short", icon="📈")

        # 6. RUN AI ACTIVE POSITIONS ADVISOR MONITOR
        if ai_advisor.is_ai_advisor_enabled():
            # Check if 10 minutes have passed since last run
            now = datetime.datetime.now()
            should_run = False
            if 'last_ai_advisor_run' not in st.session_state or st.session_state.last_ai_advisor_run is None:
                should_run = True
            else:
                elapsed = (now - st.session_state.last_ai_advisor_run).total_seconds()
                if elapsed >= 600: # 10 minutes
                    should_run = True
                    
            # Time window check: 9:45 AM to 3:25 PM, weekday check
            current_time = now.time()
            start_time = datetime.time(9, 45)
            end_time = datetime.time(15, 25)
            is_within_window = (start_time <= current_time <= end_time) and (now.weekday() <= 4)
            
            if should_run and is_within_window:
                with st.spinner("AI Active Positions Advisor: Running technical conviction analysis..."):
                    try:
                        import scheduler_service
                        scheduler_service.run_ai_position_advisor()
                        st.session_state.last_ai_advisor_run = now
                        st.success("🤖 AI Advisor recommendations successfully analyzed and dispatched to Telegram!")
                    except Exception as ai_err:
                        st.error(f"Failed to run AI Position Advisor: {ai_err}")

    import time
    time.sleep(60) # Reduced to 60 seconds for more realistic intraday monitoring
    st.rerun()

# --- PERSISTENT STRATEGY-SPECIFIC RESULTS DISPLAY ---
current_results = st.session_state.all_results.get(strategy, pd.DataFrame())

if not current_results.empty:
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
                if strategy in ["15-Min Bearish Breakdown (Kite)", "15-Min Bullish Breakout (Kite)", "Failed Breakout Short (Kite)"]:
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
