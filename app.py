import streamlit as st
import scanner
import kite_scanner
import os
import pandas as pd
from kiteconnect import KiteConnect
import config
import json

st.set_page_config(page_title="NSE Stock Scanner Dashboard", layout="wide")

st.title("📈 NSE Stock Scanner")
st.markdown("Scan Nifty 500 stocks for actionable trading setups based on technical indicators.")

if st.session_state.get('kite_access_token'):
    user_name = st.session_state.get('kite_user_name', 'User')
    user_id = st.session_state.get('kite_user_id', '')
    st.success(f"✅ Login Successful! Connected as **{user_name}** ({user_id})")

    # --- LIVE PORTFOLIO DASHBOARD ---
    st.markdown("---")
    st.markdown("## 📦 Live Paper Trading Portfolio")
    import paper_trader
    
    try:
        # Use existing kite object or create local one
        kite_pnl = KiteConnect(api_key=getattr(config, 'KITE_API_KEY', ''))
        kite_pnl.set_access_token(st.session_state.kite_access_token)
        
        portfolio_df = paper_trader.update_portfolio_pnl(kite_pnl)
        if not portfolio_df.empty:
            total_pnl = portfolio_df['Live P&L'].sum()
            st.metric("Total Live P&L", f"₹{total_pnl:,.2f}")
            st.dataframe(portfolio_df.style.format({"Live P&L": "₹{:.2f}", "EntryPrice": "₹{:.2f}", "Current Price": "₹{:.2f}"}), use_container_width=True)
        else:
            st.info("No open paper trades. Run an ORB scan to find opportunities!")
    except Exception as e:
        st.warning(f"Portfolio update paused: {e}")
    st.markdown("---")

st.sidebar.header("Scanner Settings")
strategy = st.sidebar.selectbox(
    "Select Scanning Strategy",
    ["Swing Trade Candidates", "Volume Breakout Stocks", "3:15 PM Swing Setup (Kite)", "15-Min ORB Breakout (Kite)"]
)

st.sidebar.markdown("---")

api_key = ""
api_secret = ""

if 'kite_access_token' not in st.session_state:
    st.session_state.kite_access_token = None

if strategy in ["3:15 PM Swing Setup (Kite)", "15-Min ORB Breakout (Kite)"]:
    st.sidebar.info("This scanner uses the Kite Connect API and requires authentication.")
    api_key = st.sidebar.text_input("Kite API Key", type="password", value=getattr(config, 'KITE_API_KEY', ''))
    api_secret = st.sidebar.text_input("Kite API Secret", type="password", value=getattr(config, 'KITE_API_SECRET', ''))
    
    if api_key and api_secret:
        kite = KiteConnect(api_key=api_key)
        
        if not st.session_state.kite_access_token:
            query_params = st.query_params
            if "request_token" in query_params:
                request_token = query_params["request_token"]
                try:
                    data = kite.generate_session(request_token, api_secret=api_secret)
                    st.session_state.kite_access_token = data["access_token"]
                    st.session_state.kite_user_name = data.get("user_name", "User")
                    st.session_state.kite_user_id = data.get("user_id", "ID")
                    
                    # Persist session for background scheduler
                    with open(".kite_session.json", "w") as f:
                        json.dump({
                            "access_token": data["access_token"],
                            "user_id": data.get("user_id", "ID"),
                            "user_name": data.get("user_name", "User")
                        }, f)
                        
                    st.query_params.clear()
                    st.rerun()
                except Exception as e:
                    st.sidebar.error(f"Error authenticating: {e}")
                    st.sidebar.markdown(f'<a href="{kite.login_url()}" target="_self" style="text-decoration: none; font-weight: bold;">👉 Click here to Login with Kite</a>', unsafe_allow_html=True)
            else:
                st.sidebar.markdown(f'<a href="{kite.login_url()}" target="_self" style="text-decoration: none; font-weight: bold;">👉 Click here to Login with Kite</a>', unsafe_allow_html=True)
        else:
            if st.sidebar.button("Logout"):
                st.session_state.kite_access_token = None
                st.session_state.kite_user_name = None
                st.session_state.kite_user_id = None
                st.rerun()
else:
    st.sidebar.info("This scanner evaluates the latest daily data from Yahoo Finance.")

# Initialize session state for results
if 'results_df' not in st.session_state:
    st.session_state.results_df = pd.DataFrame()

# Allow users to run scan
if st.button(f"Run Scan: {strategy}", type="primary"):
    if strategy in ["3:15 PM Swing Setup (Kite)", "15-Min ORB Breakout (Kite)"] and not st.session_state.kite_access_token:
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

                    results_df = kite_scanner.scan_orb_setups(kite, progress_callback=update_progress)
                except Exception as e:
                    st.error(f"Failed to initialize Kite API: {e}")
                    results_df = pd.DataFrame()
            else:
                tickers = scanner.get_nifty500_fno_tickers()
                if strategy == "Swing Trade Candidates":
                    results_df = scanner.scan_swing_candidates(tickers)
                else:
                    results_df = scanner.scan_breakout_stocks(tickers)
                
            progress_bar.progress(100)
            status_text.text("Scan complete!")
                
            if results_df.empty:
                st.warning("No stocks met the criteria today.")
                st.session_state.results_df = pd.DataFrame()
            else:
                # Sort by Volume Spike Ratio descending if the column exists (primarily for 3:15 PM strategy)
                if 'Volume Spike Ratio' in results_df.columns:
                    results_df = results_df.sort_values(by='Volume Spike Ratio', ascending=False)
                st.session_state.results_df = results_df

            # Automatically send to Telegram
            import telegram_agent
            tel_token = getattr(config, 'TELEGRAM_BOT_TOKEN', '')
            tel_chat_id = getattr(config, 'TELEGRAM_CHAT_ID', '')
            if tel_token and tel_chat_id:
                success = telegram_agent.send_dataframe(st.session_state.results_df, tel_token, tel_chat_id, scan_name=strategy)
                if success:
                    st.success("📲 Results automatically sent to Telegram!")
                else:
                    st.error("⚠️ Failed to send results to Telegram.")

if not st.session_state.results_df.empty:
    st.success(f"Found {len(st.session_state.results_df)} stocks matching the criteria!")
    st.dataframe(st.session_state.results_df, use_container_width=True)
    
    if strategy == "15-Min ORB Breakout (Kite)":
        if st.button("🚀 Execute These as Paper Trades"):
            import paper_trader
            count = 0
            for _, row in st.session_state.results_df.iterrows():
                if paper_trader.execute_paper_trade(
                    ticker=row['Ticker'],
                    trade_type=row['Breakout'],
                    entry_price=row['Breakout Price'],
                    sl=row['Paper SL'],
                    qty=row['Paper Qty']
                ):
                    count += 1
            st.success(f"Executed {count} new paper trades!")
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
                analysis = ai_advisor.analyze_stocks(st.session_state.results_df, gemini_key)
                st.info("AI Analysis Complete")
                st.markdown(analysis)
                
                # Automatically send AI analysis to Telegram with dynamic infographic
                import telegram_agent
                import image_generator
                tel_token = getattr(config, 'TELEGRAM_BOT_TOKEN', '')
                tel_chat_id = getattr(config, 'TELEGRAM_CHAT_ID', '')
                if tel_token and tel_chat_id:
                    # Generate the graphic
                    img_path = image_generator.create_infographic(st.session_state.results_df, scan_name=strategy)
                    
                    if img_path:
                        # Caption limit is 1024. If longer, send photo then text.
                        caption = f"🤖 *AI Conviction Analysis: {strategy}*\n\n{analysis}"
                        if len(caption) < 1000:
                             success = telegram_agent.send_photo(img_path, caption, tel_token, tel_chat_id)
                        else:
                             # Send photo first with short caption
                             telegram_agent.send_photo(img_path, f"🚀 Top {strategy} Picks", tel_token, tel_chat_id)
                             # Then send full analysis
                             success = telegram_agent.send_message(caption, tel_token, tel_chat_id)
                    else:
                        # Fallback to just message
                        success = telegram_agent.send_message(f"🤖 *AI Conviction Analysis*\n\n{analysis}", tel_token, tel_chat_id)
                    
                    if success:
                        st.success("📲 Infographic & AI Analysis sent to Telegram!")
                    else:
                        st.error("⚠️ Failed to send to Telegram.")
