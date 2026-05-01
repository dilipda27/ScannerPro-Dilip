import streamlit as st
import scanner

st.set_page_config(page_title="NSE Stock Scanner Dashboard", layout="wide")

st.title("📈 NSE Stock Scanner")
st.markdown("Scan Nifty 500 (F&O) stocks for actionable trading setups based on technical indicators.")

st.sidebar.header("Scanner Settings")
strategy = st.sidebar.selectbox(
    "Select Scanning Strategy",
    ["Swing Trade Candidates", "Volume Breakout Stocks"]
)

st.sidebar.markdown("---")
st.sidebar.info("This scanner evaluates the latest daily data from Yahoo Finance.")

# Allow users to run scan
if st.button(f"Run Scan: {strategy}", type="primary"):
    with st.spinner(f"Fetching Nifty 500 F&O components and running {strategy} scan... This may take a minute or two."):
        tickers = scanner.get_nifty500_fno_tickers()
        
        # Adding a progress bar to show scanning progress
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # We can pass a callback or just let it run.
        # Since our scanner functions do the loop internally, we won't do a real progress bar per ticker right now.
        # Let's just run it.
        if strategy == "Swing Trade Candidates":
            results_df = scanner.scan_swing_candidates(tickers)
        else:
            results_df = scanner.scan_breakout_stocks(tickers)
            
        progress_bar.progress(100)
        status_text.text("Scan complete!")
            
        if results_df.empty:
            st.warning("No stocks met the criteria today.")
        else:
            st.success(f"Found {len(results_df)} stocks matching the criteria!")
            st.dataframe(results_df, use_container_width=True)
