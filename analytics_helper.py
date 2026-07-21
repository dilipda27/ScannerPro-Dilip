import os
import datetime
import logging
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

@st.cache_data(ttl=3600)
def get_vix_data_cached(start_date, end_date):
    try:
        import yfinance as yf
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = (end_date + datetime.timedelta(days=2)).strftime("%Y-%m-%d")
        vix_data = yf.download("^INDIAVIX", start=start_str, end=end_str, progress=False, timeout=10)
        if not vix_data.empty:
            if isinstance(vix_data.columns, pd.MultiIndex):
                vix_data.columns = vix_data.columns.get_level_values(0)
            vix_df = vix_data[['Close']].reset_index()
            vix_df['Date'] = pd.to_datetime(vix_df['Date']).dt.date
            return dict(zip(vix_df['Date'], vix_df['Close']))
    except Exception as e:
        logging.warning(f"Failed to fetch historical VIX: {e}")
    return {}

@st.cache_data(ttl=3600)
def get_bulk_gaps_cached(tickers_tuple, start_date, end_date):
    gaps_dict = {}
    if not tickers_tuple:
        return gaps_dict
    try:
        import yfinance as yf
        start_str = (start_date - datetime.timedelta(days=5)).strftime("%Y-%m-%d")
        end_str = (end_date + datetime.timedelta(days=2)).strftime("%Y-%m-%d")
        
        tickers_formatted = [f"{t}.NS" for t in tickers_tuple if not any(c.isdigit() for c in t)]
        if not tickers_formatted:
            return gaps_dict
            
        data = yf.download(tickers_formatted, start=start_str, end=end_str, group_by='ticker', progress=False, timeout=10)
        if not data.empty:
            for ticker in tickers_formatted:
                try:
                    symbol = ticker.replace('.NS', '')
                    if isinstance(data.columns, pd.MultiIndex) and ticker in data.columns.levels[0]:
                        df_stock = data[ticker].dropna().copy()
                    else:
                        df_stock = data.copy()
                        
                    if not df_stock.empty:
                        if isinstance(df_stock.columns, pd.MultiIndex):
                            df_stock.columns = df_stock.columns.get_level_values(0)
                        df_stock['Prev_Close'] = df_stock['Close'].shift(1)
                        df_stock['Gap_Pct'] = ((df_stock['Open'] - df_stock['Prev_Close']) / df_stock['Prev_Close']) * 100
                        
                        for date_val, gap_val in zip(df_stock.index, df_stock['Gap_Pct']):
                            if pd.notnull(gap_val):
                                gaps_dict[(symbol, date_val.date())] = gap_val
                except Exception:
                    pass
    except Exception as e:
        logging.warning(f"Failed to fetch gaps in bulk: {e}")
    return gaps_dict

@st.cache_data(ttl=300)
def load_and_normalize_archived_trades():
    trades = []
    
    # 1. Equity Intraday Archive
    file_eq = os.path.join("data", "trades", "paper_trade_archive.csv")
    if os.path.exists(file_eq):
        try:
            df_eq = pd.read_csv(file_eq)
            for _, row in df_eq.iterrows():
                try:
                    entry_time = pd.to_datetime(row['EntryTime'])
                    exit_time = pd.to_datetime(row['ExitTime']) if pd.notnull(row.get('ExitTime')) else entry_time
                    pnl = float(row.get('Final P&L', 0.0))
                    buy_val = float(row.get('EntryPrice', 0)) * float(row.get('Qty', 0))
                    sell_val = float(row.get('ExitPrice', 0)) * float(row.get('Qty', 0))
                    charges = 0.0006 * (buy_val + sell_val) + 40.0
                    net_pnl = pnl - charges
                    ticker_clean = str(row['Ticker']).replace('.NS', '').replace('.BO', '').upper()
                    
                    trades.append({
                        "Ticker": ticker_clean,
                        "Type": row['Type'],
                        "EntryPrice": float(row['EntryPrice']),
                        "ExitPrice": float(row['ExitPrice']) if pd.notnull(row.get('ExitPrice')) else float(row['EntryPrice']),
                        "Qty": int(row['Qty']),
                        "EntryTime": entry_time,
                        "ExitTime": exit_time,
                        "NetPnL": net_pnl,
                        "Strategy": row.get('Strategy', 'Intraday Equity'),
                        "AssetClass": "Equity Intraday",
                        "CapitalDeployed": buy_val
                    })
                except Exception:
                    pass
        except Exception as e:
            logging.error(f"Error loading equity archive: {e}")

    # 2. Options Archive
    file_opt = os.path.join("data", "trades", "options_trade_archive.csv")
    if os.path.exists(file_opt):
        try:
            df_opt = pd.read_csv(file_opt)
            for _, row in df_opt.iterrows():
                try:
                    entry_time = pd.to_datetime(row['EntryTime'])
                    exit_time = pd.to_datetime(row['ExitTime']) if pd.notnull(row.get('ExitTime')) else entry_time
                    pnl = float(row.get('Final P&L', 0.0))
                    buy_val = float(row.get('EntryPrice', 0)) * float(row.get('Qty', 0))
                    sell_val = float(row.get('ExitPrice', 0)) * float(row.get('Qty', 0))
                    charges = 40.0 + 0.000625 * buy_val + 0.00053 * (buy_val + sell_val)
                    net_pnl = pnl - charges
                    ticker_clean = str(row['Ticker']).replace('.NS', '').replace('.BO', '').upper()
                    
                    trades.append({
                        "Ticker": ticker_clean,
                        "Type": row['Type'],
                        "EntryPrice": float(row['EntryPrice']),
                        "ExitPrice": float(row['ExitPrice']) if pd.notnull(row.get('ExitPrice')) else float(row['EntryPrice']),
                        "Qty": int(row['Qty']),
                        "EntryTime": entry_time,
                        "ExitTime": exit_time,
                        "NetPnL": net_pnl,
                        "Strategy": row.get('Strategy', 'Option Desk'),
                        "AssetClass": "Options Selling",
                        "CapitalDeployed": buy_val
                    })
                except Exception:
                    pass
        except Exception as e:
            logging.error(f"Error loading options archive: {e}")

    # 3. Swing Archive
    file_swing = os.path.join("data", "trades", "swing_trades_archived.csv")
    if os.path.exists(file_swing):
        try:
            df_swing = pd.read_csv(file_swing)
            for _, row in df_swing.iterrows():
                try:
                    entry_time = pd.to_datetime(row['EntryDate'])
                    exit_time = pd.to_datetime(row['ExitDate']) if pd.notnull(row.get('ExitDate')) else entry_time
                    net_pnl = float(row.get('Net P&L', 0.0))
                    buy_val = float(row.get('EntryPrice', 0)) * float(row.get('Qty', 0))
                    ticker_clean = str(row['Ticker']).replace('.NS', '').replace('.BO', '').upper()
                    
                    trades.append({
                        "Ticker": ticker_clean,
                        "Type": "Swing Position",
                        "EntryPrice": float(row['EntryPrice']),
                        "ExitPrice": float(row.get('Current Price', row['EntryPrice'])),
                        "Qty": int(row['Qty']),
                        "EntryTime": entry_time,
                        "ExitTime": exit_time,
                        "NetPnL": net_pnl,
                        "Strategy": "3:15 PM Swing Setup",
                        "AssetClass": "Positional Swing",
                        "CapitalDeployed": buy_val
                    })
                except Exception:
                    pass
        except Exception as e:
            logging.error(f"Error loading swing archive: {e}")

    if not trades:
        return pd.DataFrame()
        
    return pd.DataFrame(trades).sort_values('ExitTime')

def render_analytics_tab(portfolio_df=None):
    st.markdown("<h2 style='color:#3b82f6; font-family:\"Plus Jakarta Sans\", sans-serif; font-weight:700;'>📊 Performance & Portfolio Analytics</h2>", unsafe_allow_html=True)
    st.markdown("<p style='color:var(--text-color); opacity:0.7; font-size:0.95rem; margin-top:-10px; margin-bottom:25px;'>Professional-grade quantitative analytics and trade visualization desk.</p>", unsafe_allow_html=True)
    
    # Load normalized trades
    all_trades_df = load_and_normalize_archived_trades()
    if all_trades_df.empty:
        st.info("No archived trades found to analyze.")
        return
        
    # Drop rows with null timestamps to avoid NaT errors
    all_trades_df = all_trades_df.dropna(subset=['ExitTime', 'EntryTime'])
    if all_trades_df.empty:
        st.info("No archived trades with valid timestamps found to analyze.")
        return
        
    # Get absolute min/max dates from history
    min_date = all_trades_df['ExitTime'].min().date()
    max_date = all_trades_df['ExitTime'].max().date()
    
    # Initialize session state for date range
    if 'date_range_val' not in st.session_state:
        st.session_state.date_range_val = (min_date, max_date)
    if 'preset_date_choice' not in st.session_state:
        st.session_state.preset_date_choice = "All Time"
        
    # CSS injection for premium card styling
    st.markdown("""
        <style>
            .metric-card {
                background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
                border: 1px solid #334155;
                border-radius: 12px;
                padding: 18px 22px;
                box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
                transition: transform 0.2s ease-in-out, border-color 0.2s ease-in-out;
                margin-bottom: 20px;
                min-height: 120px;
            }
            .metric-card:hover {
                transform: translateY(-2px);
                border-color: #3b82f6;
            }
            .metric-lbl {
                color: #94a3b8;
                font-size: 0.75rem;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 0.05em;
                margin: 0 0 6px 0;
            }
            .metric-val-green {
                color: #10b981;
                font-size: 1.65rem;
                font-weight: 700;
                margin: 0;
            }
            .metric-val-red {
                color: #ef4444;
                font-size: 1.65rem;
                font-weight: 700;
                margin: 0;
            }
            .metric-val-blue {
                color: #3b82f6;
                font-size: 1.65rem;
                font-weight: 700;
                margin: 0;
            }
            .metric-subtext {
                color: #64748b;
                font-size: 0.75rem;
                margin-top: 4px;
                font-weight: 500;
            }
        </style>
    """, unsafe_allow_html=True)
    
    # ------------------ FILTER PANEL ------------------
    with st.expander("🎛️ Advanced Control Desk & Filters", expanded=True):
        col_pres, col_cal = st.columns([3, 2])
        
        with col_pres:
            st.markdown("<p style='font-size:0.85rem; font-weight:600; color:#475569; margin-bottom:5px;'>📅 TIMEFRAME PRESETS</p>", unsafe_allow_html=True)
            presets = ["Today", "Last 7 Days", "Last 30 Days", "Month to Date", "Year to Date", "All Time"]
            p_cols = st.columns(len(presets))
            for idx, p_name in enumerate(presets):
                if p_cols[idx].button(p_name, key=f"p_btn_{p_name}", use_container_width=True,
                                      type="primary" if st.session_state.preset_date_choice == p_name else "secondary"):
                    st.session_state.preset_date_choice = p_name
                    today = datetime.date.today()
                    
                    if p_name == "Today":
                        start_val, end_val = today, today
                    elif p_name == "Last 7 Days":
                        start_val, end_val = today - datetime.timedelta(days=7), today
                    elif p_name == "Last 30 Days":
                        start_val, end_val = today - datetime.timedelta(days=30), today
                    elif p_name == "Month to Date":
                        start_val, end_val = today.replace(day=1), today
                    elif p_name == "Year to Date":
                        start_val, end_val = today.replace(month=1, day=1), today
                    elif p_name == "All Time":
                        start_val, end_val = min_date, max_date
                        
                    # Cap dates to historical bounds to prevent Streamlit date_input bounds exception
                    start_val = max(min_date, start_val)
                    end_val = min(max_date, end_val)
                    if start_val > end_val:
                        start_val = end_val
                        
                    st.session_state.date_range_val = (start_val, end_val)
                    st.rerun()
                    
        with col_cal:
            st.markdown("<p style='font-size:0.85rem; font-weight:600; color:#475569; margin-bottom:5px;'>📆 CUSTOM DATE RANGE</p>", unsafe_allow_html=True)
            # Safe calendar picker check
            val_in = st.session_state.date_range_val
            if not isinstance(val_in, tuple) or len(val_in) != 2:
                val_in = (min_date, max_date)
            # Omit min_value and max_value to prevent Bounds Exceptions when presets are selected
            selected_range = st.date_input("Filter Window", value=val_in, key="cal_range_picker", label_visibility="collapsed")
            if isinstance(selected_range, tuple) and len(selected_range) == 2:
                if selected_range != st.session_state.date_range_val:
                    st.session_state.date_range_val = selected_range
                    st.session_state.preset_date_choice = "Custom"
                    st.rerun()
                    
        st.markdown("<hr style='margin:10px 0; border:0.5px solid #f1f5f9;' />", unsafe_allow_html=True)
        col_fil1, col_fil2, col_fil3 = st.columns(3)
        
        with col_fil1:
            all_classes = sorted(all_trades_df['AssetClass'].unique().tolist())
            selected_classes = st.multiselect("Asset Class Selection", options=all_classes, default=all_classes, key="mult_assets")
            
        with col_fil2:
            all_strats = sorted(all_trades_df['Strategy'].unique().tolist())
            selected_strats = st.multiselect("Strategy Filter", options=all_strats, default=all_strats, key="mult_strats")
            
        with col_fil3:
            capital_base = st.number_input("Capital Base (₹) for ROI & Drawdown %", min_value=10000.0, value=500000.0, step=50000.0, key="cap_base_input")

    # Filter Data based on selections
    filtered_df = all_trades_df.copy()
    if selected_classes:
        filtered_df = filtered_df[filtered_df['AssetClass'].isin(selected_classes)]
    else:
        filtered_df = pd.DataFrame(columns=filtered_df.columns)
        
    if not filtered_df.empty and selected_strats:
        filtered_df = filtered_df[filtered_df['Strategy'].isin(selected_strats)]
    elif not filtered_df.empty:
        filtered_df = pd.DataFrame(columns=filtered_df.columns)
        
    # Handle date range filtering
    if not filtered_df.empty:
        start_dt = pd.to_datetime(st.session_state.date_range_val[0]).tz_localize(None)
        end_dt = pd.to_datetime(st.session_state.date_range_val[1]).tz_localize(None) + datetime.timedelta(days=1)
        filtered_df = filtered_df[(filtered_df['ExitTime'] >= start_dt) & (filtered_df['ExitTime'] < end_dt)]
        
    if filtered_df.empty:
        st.warning("⚠️ No trades matched the current filter configuration. Change dates or select different classes/strategies.")
        return
        
    # ------------------ COMPUTE QUANT METRICS ------------------
    total_trades = len(filtered_df)
    wins_df = filtered_df[filtered_df['NetPnL'] > 0]
    losses_df = filtered_df[filtered_df['NetPnL'] <= 0]
    total_wins = len(wins_df)
    total_losses = len(losses_df)
    win_rate = (total_wins / total_trades) * 100 if total_trades > 0 else 0.0
    
    total_pnl = filtered_df['NetPnL'].sum()
    roi_pct = (total_pnl / capital_base) * 100
    
    gross_wins = wins_df['NetPnL'].sum()
    gross_losses = abs(losses_df['NetPnL'].sum())
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float('inf')
    avg_trade_pnl = filtered_df['NetPnL'].mean()
    
    # Sharpe Ratio
    filtered_df['ExitDateStr'] = filtered_df['ExitTime'].dt.date.astype(str)
    daily_grouped = filtered_df.groupby('ExitDateStr').agg({'NetPnL': 'sum'}).reset_index()
    if len(daily_grouped) > 1:
        std_pnl = daily_grouped['NetPnL'].astype(float).std()
        sharpe = (daily_grouped['NetPnL'].mean() / std_pnl) * (252 ** 0.5) if std_pnl > 0 else 0.0
    else:
        sharpe = 0.0
        
    # Max Drawdown
    filtered_df['CumPnL'] = filtered_df['NetPnL'].cumsum()
    peak = -float('inf')
    max_dd = 0.0
    for _, row in filtered_df.iterrows():
        pnl_val = row['CumPnL']
        if pnl_val > peak:
            peak = pnl_val
        dd = peak - pnl_val
        if dd > max_dd:
            max_dd = dd
    max_dd_pct = (max_dd / capital_base) * 100
    
    # ------------------ METRIC CARDS ------------------
    st.markdown("<div style='margin-top:10px;'></div>", unsafe_allow_html=True)
    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
    
    # Net PnL Card
    pnl_class = "metric-val-green" if total_pnl >= 0 else "metric-val-red"
    pnl_sign = "+" if total_pnl >= 0 else ""
    m_col1.markdown(f"""
        <div class="metric-card">
            <p class="metric-lbl">Net Performance</p>
            <p class="{pnl_class}">{pnl_sign}₹{total_pnl:,.2f}</p>
            <p class="metric-subtext">Return on Capital: <b>{pnl_sign}{roi_pct:.2f}%</b></p>
        </div>
    """, unsafe_allow_html=True)
    
    # Win Rate Card
    m_col2.markdown(f"""
        <div class="metric-card">
            <p class="metric-lbl">Win Ratio</p>
            <p class="metric-val-blue">{win_rate:.1f}%</p>
            <p class="metric-subtext"><b>{total_wins}</b> Wins / <b>{total_losses}</b> Losses</p>
        </div>
    """, unsafe_allow_html=True)
    
    # Profit Factor Card
    pf_val = f"{profit_factor:.2f}" if profit_factor != float('inf') else "∞"
    m_col3.markdown(f"""
        <div class="metric-card">
            <p class="metric-lbl">Profit Factor</p>
            <p class="metric-val-blue">{pf_val}</p>
            <p class="metric-subtext">Avg PnL/Trade: <b>₹{avg_trade_pnl:,.1f}</b></p>
        </div>
    """, unsafe_allow_html=True)
    
    # Drawdown Card
    m_col4.markdown(f"""
        <div class="metric-card">
            <p class="metric-lbl">Max Drawdown & Sharpe</p>
            <p class="metric-val-red">₹{max_dd:,.2f}</p>
            <p class="metric-subtext">Ann. Sharpe: <b>{sharpe:.2f}</b> | DD: <b>{max_dd_pct:.2f}%</b></p>
        </div>
    """, unsafe_allow_html=True)

    # ------------------ SUB TABS ------------------
    tab_equity, tab_stats, tab_regimes = st.tabs(["📈 Premium Equity Curve", "📊 Performance Distribution", "⏱️ Market Regime Analytics"])
    
    with tab_equity:
        st.markdown("<p style='font-size:1.05rem; font-weight:600; color:#1e293b; margin-bottom:10px;'>📊 Interactive Cumulative Net Equity Curve</p>", unsafe_allow_html=True)
        
        # Calculate daily cumulative PnL
        daily_cum = filtered_df.groupby(filtered_df['ExitTime'].dt.date).agg({'NetPnL': 'sum'}).reset_index().sort_values('ExitTime')
        daily_cum['Cumulative PnL'] = daily_cum['NetPnL'].cumsum()
        
        fig_eq = go.Figure()
        fig_eq.add_trace(go.Scatter(
            x=daily_cum['ExitTime'],
            y=daily_cum['Cumulative PnL'],
            mode='lines+markers',
            name='Equity P&L',
            line=dict(color='#3b82f6', width=3, shape='spline'),
            fill='tozeroy',
            fillcolor='rgba(59, 130, 246, 0.08)',
            marker=dict(size=5, color='#2563eb', line=dict(width=1, color='white')),
            hovertemplate='<b>Date</b>: %{x}<br><b>Net PnL</b>: ₹%{y:,.2f}<extra></extra>'
        ))
        
        fig_eq.update_layout(
            hovermode='x unified',
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)',
            xaxis=dict(
                showgrid=True, gridcolor='rgba(148, 163, 184, 0.12)',
                title=dict(text="Timeline", font=dict(size=11, color="#64748b"))
            ),
            yaxis=dict(
                showgrid=True, gridcolor='rgba(148, 163, 184, 0.12)',
                title=dict(text="Cumulative Gain/Loss (₹)", font=dict(size=11, color="#64748b"))
            ),
            height=400,
            margin=dict(l=10, r=10, t=10, b=10)
        )
        st.plotly_chart(fig_eq, width='stretch', config={'displayModeBar': False})

    with tab_stats:
        col_dist1, col_dist2 = st.columns(2)
        
        with col_dist1:
            st.markdown("<p style='font-size:1.05rem; font-weight:600; color:#1e293b; margin-bottom:10px;'>📊 PnL Distribution Density (Trade Frequencies)</p>", unsafe_allow_html=True)
            fig_hist = go.Figure()
            
            # Group into wins and losses for colored overlays
            fig_hist.add_trace(go.Histogram(
                x=wins_df['NetPnL'],
                name='Profitable Trades',
                xbins=dict(start=0, size=max(100, int(filtered_df['NetPnL'].max()/15))),
                marker_color='#10b981',
                opacity=0.75,
                hovertemplate='Wins Range: %{x}<br>Count: %{y}<extra></extra>'
            ))
            fig_hist.add_trace(go.Histogram(
                x=losses_df['NetPnL'],
                name='Losing Trades',
                xbins=dict(end=0, size=max(100, int(abs(filtered_df['NetPnL'].min())/15))),
                marker_color='#ef4444',
                opacity=0.75,
                hovertemplate='Losses Range: %{x}<br>Count: %{y}<extra></extra>'
            ))
            
            fig_hist.update_layout(
                barmode='overlay',
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)',
                xaxis=dict(showgrid=True, gridcolor='rgba(148, 163, 184, 0.12)', title="Trade Net PnL (₹)"),
                yaxis=dict(showgrid=True, gridcolor='rgba(148, 163, 184, 0.12)', title="Frequency"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                height=350,
                margin=dict(l=10, r=10, t=10, b=10)
            )
            st.plotly_chart(fig_hist, width='stretch', config={'displayModeBar': False})
            
        with col_dist2:
            st.markdown("<p style='font-size:1.05rem; font-weight:600; color:#1e293b; margin-bottom:10px;'>📊 Net PnL Contribution by Strategy</p>", unsafe_allow_html=True)
            strat_perf = filtered_df.groupby('Strategy').agg({
                'NetPnL': ['sum', 'count'],
                'Ticker': lambda x: len(x[filtered_df.loc[x.index, 'NetPnL'] > 0])
            }).reset_index()
            strat_perf.columns = ['Strategy', 'Net P&L', 'Total Trades', 'Wins']
            strat_perf['Win Rate (%)'] = (strat_perf['Wins'] / strat_perf['Total Trades']) * 100
            strat_perf = strat_perf.sort_values(by='Net P&L', ascending=False)
            
            colors_strat = ['#10b981' if p >= 0 else '#ef4444' for p in strat_perf['Net P&L']]
            fig_strat = go.Figure()
            fig_strat.add_trace(go.Bar(
                x=strat_perf['Strategy'],
                y=strat_perf['Net P&L'],
                marker_color=colors_strat,
                hovertemplate='Strategy: %{x}<br>Net P&L: <b>₹%{y:,.2f}</b><extra></extra>'
            ))
            fig_strat.update_layout(
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)',
                xaxis=dict(showgrid=False, title="Strategy Desk"),
                yaxis=dict(showgrid=True, gridcolor='rgba(148, 163, 184, 0.12)', title="Total Net PnL (₹)"),
                height=350,
                margin=dict(l=10, r=10, t=10, b=10)
            )
            st.plotly_chart(fig_strat, width='stretch', config={'displayModeBar': False})
            
        st.markdown("<hr style='margin:15px 0; border:0.5px solid #e2e8f0;' />", unsafe_allow_html=True)
        st.markdown("##### 🔍 Strategy Performance Overview Table")
        st.dataframe(
            strat_perf[['Strategy', 'Total Trades', 'Win Rate (%)', 'Net P&L']].style.format({
                "Win Rate (%)": "{:.1f}%",
                "Net P&L": "₹{:,.2f}"
            }).map(style_pnl, subset=['Net P&L']),
            width='stretch'
        )

    with tab_regimes:
        st.markdown("<p style='font-size:1.05rem; font-weight:600; color:#1e293b; margin-bottom:10px;'>⏱️ Time-of-Day and Day-of-Week Efficiency</p>", unsafe_allow_html=True)
        
        filtered_df['EntryHour'] = filtered_df['EntryTime'].dt.hour
        filtered_df['DayName'] = filtered_df['EntryTime'].dt.day_name()
        
        c_time1, c_time2 = st.columns(2)
        with c_time1:
            hour_pnl = filtered_df.groupby('EntryHour').agg({'NetPnL': 'sum'}).reset_index().sort_values('EntryHour')
            colors_hour = ['#10b981' if v >= 0 else '#ef4444' for v in hour_pnl['NetPnL']]
            fig_hr = go.Figure()
            fig_hr.add_trace(go.Bar(
                x=hour_pnl['EntryHour'].astype(str) + ":00",
                y=hour_pnl['NetPnL'],
                marker_color=colors_hour,
                hovertemplate='Hour: %{x}<br>Net PnL: <b>₹%{y:,.2f}</b><extra></extra>'
            ))
            fig_hr.update_layout(
                title=dict(text="PnL by Trade Entry Hour", font=dict(size=12, color="#475569")),
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)',
                xaxis=dict(title="Hour of Entry"),
                yaxis=dict(showgrid=True, gridcolor='rgba(148, 163, 184, 0.12)', title="Net Return (₹)"),
                height=300,
                margin=dict(l=10, r=10, t=30, b=10)
            )
            st.plotly_chart(fig_hr, width='stretch', config={'displayModeBar': False})
            
        with c_time2:
            days_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
            day_pnl = filtered_df.groupby('DayName').agg({'NetPnL': 'sum'}).reindex(days_order).dropna().reset_index()
            colors_day = ['#10b981' if v >= 0 else '#ef4444' for v in day_pnl['NetPnL']]
            fig_dy = go.Figure()
            fig_dy.add_trace(go.Bar(
                x=day_pnl['DayName'],
                y=day_pnl['NetPnL'],
                marker_color=colors_day,
                hovertemplate='Day: %{x}<br>Net PnL: <b>₹%{y:,.2f}</b><extra></extra>'
            ))
            fig_dy.update_layout(
                title=dict(text="PnL by Day of Week", font=dict(size=12, color="#475569")),
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)',
                xaxis=dict(title="Weekday"),
                yaxis=dict(showgrid=True, gridcolor='rgba(148, 163, 184, 0.12)', title="Net Return (₹)"),
                height=300,
                margin=dict(l=10, r=10, t=30, b=10)
            )
            st.plotly_chart(fig_dy, width='stretch', config={'displayModeBar': False})
            
        st.markdown("<hr style='margin:15px 0; border:0.5px solid #e2e8f0;' />", unsafe_allow_html=True)
        st.markdown("<p style='font-size:1.05rem; font-weight:600; color:#1e293b; margin-bottom:10px;'>📊 India VIX & Stock Gap Correlation Desks</p>", unsafe_allow_html=True)
        
        c_cond1, c_cond2 = st.columns(2)
        with c_cond1:
            st.markdown("##### India VIX Regime Breakdown")
            with st.spinner("Analyzing VIX regime correlations..."):
                vix_dict = get_vix_data_cached(st.session_state.date_range_val[0], st.session_state.date_range_val[1])
                
            if not vix_dict:
                st.info("Historical VIX correlation data currently offline.")
            else:
                filtered_df['EntryDateOnly'] = filtered_df['EntryTime'].dt.date
                filtered_df['VIX'] = filtered_df['EntryDateOnly'].map(vix_dict)
                vix_clean = filtered_df.dropna(subset=['VIX']).copy()
                
                if vix_clean.empty:
                    st.info("No trades matched historical VIX dates.")
                else:
                    def get_vix_class(vix):
                        if vix < 13: return "Low Volatility (<13 VIX)"
                        elif vix <= 18: return "Normal Volatility (13-18 VIX)"
                        else: return "High Volatility (>18 VIX)"
                        
                    vix_clean['VIX_Regime'] = vix_clean['VIX'].apply(get_vix_class)
                    vix_table = vix_clean.groupby('VIX_Regime').agg({
                        'NetPnL': ['sum', 'count'],
                        'Ticker': lambda x: len(x[vix_clean.loc[x.index, 'NetPnL'] > 0])
                    }).reset_index()
                    vix_table.columns = ['Regime', 'Net P&L', 'Total Trades', 'Wins']
                    vix_table['Win Rate (%)'] = (vix_table['Wins'] / vix_table['Total Trades']) * 100
                    
                    st.dataframe(
                        vix_table[['Regime', 'Total Trades', 'Win Rate (%)', 'Net P&L']].style.format({
                            "Win Rate (%)": "{:.1f}%",
                            "Net P&L": "₹{:,.2f}"
                        }).map(style_pnl, subset=['Net P&L']),
                        width='stretch'
                    )
                    
        with c_cond2:
            st.markdown("##### Equity Intraday Gap Correlation")
            gap_trades = filtered_df[filtered_df['AssetClass'] == "Equity Intraday"].copy()
            if gap_trades.empty:
                st.info("Gap correlation is only available for Equity Intraday trades.")
            else:
                with st.spinner("Downloading gap tickers data..."):
                    tickers_tup = tuple(sorted(gap_trades['Ticker'].unique().tolist()))
                    gaps_dict = get_bulk_gaps_cached(tickers_tup, st.session_state.date_range_val[0], st.session_state.date_range_val[1])
                    
                if not gaps_dict:
                    st.info("Stock gap database correlation offline.")
                else:
                    gap_trades['EntryDateOnly'] = gap_trades['EntryTime'].dt.date
                    gap_trades['Gap_Pct'] = gap_trades.apply(
                        lambda row: gaps_dict.get((row['Ticker'], row['EntryDateOnly'])), axis=1
                    )
                    gap_clean = gap_trades.dropna(subset=['Gap_Pct']).copy()
                    
                    if gap_clean.empty:
                        st.info("No trades matched historical gap boundaries.")
                    else:
                        def get_gap_class(pct):
                            if pct > 0.3: return "Gap Up (>0.3%)"
                            elif pct < -0.3: return "Gap Down (<-0.3%)"
                            else: return "Flat"
                            
                        gap_clean['Gap_Regime'] = gap_clean['Gap_Pct'].apply(get_gap_class)
                        gap_table = gap_clean.groupby('Gap_Regime').agg({
                            'NetPnL': ['sum', 'count'],
                            'Ticker': lambda x: len(x[gap_clean.loc[x.index, 'NetPnL'] > 0])
                        }).reset_index()
                        gap_table.columns = ['Gap Scenario', 'Net P&L', 'Total Trades', 'Wins']
                        gap_table['Win Rate (%)'] = (gap_table['Wins'] / gap_table['Total Trades']) * 100
                        
                        st.dataframe(
                            gap_table[['Gap Scenario', 'Total Trades', 'Win Rate (%)', 'Net P&L']].style.format({
                                "Win Rate (%)": "{:.1f}%",
                                "Net P&L": "₹{:,.2f}"
                            }).map(style_pnl, subset=['Net P&L']),
                            width='stretch'
                        )
                        
    # --- BEST AND WORST TRADES TABLE (Moved to Bottom as a detailed table section) ---
    st.markdown("<hr style='margin:25px 0; border:0.5px solid #cbd5e1;' />", unsafe_allow_html=True)
    st.markdown("<h4 style='color:#1e293b; font-weight:600;'>🏆 Individual Trade Explorer (Hall of Fame / Shame)</h4>", unsafe_allow_html=True)
    
    col_t1, col_t2 = st.columns(2)
    with col_t1:
        st.markdown("🟢 **Top 5 Most Profitable Trades**")
        best_trades = filtered_df.sort_values(by='NetPnL', ascending=False).head(5).copy()
        if not best_trades.empty:
            best_trades['ROI (%)'] = (best_trades['NetPnL'] / best_trades['CapitalDeployed']) * 100
            st.dataframe(
                best_trades[['Ticker', 'Strategy', 'EntryPrice', 'ExitPrice', 'NetPnL', 'ROI (%)']].style.format({
                    "EntryPrice": "₹{:.2f}",
                    "ExitPrice": "₹{:.2f}",
                    "NetPnL": "₹{:,.2f}",
                    "ROI (%)": "{:.2f}%"
                }).map(style_pnl, subset=['NetPnL']),
                width='stretch'
            )
        else:
            st.info("No trades to display.")
            
    with col_t2:
        st.markdown("🔴 **Top 5 Deepest Loss Trades**")
        worst_trades = filtered_df.sort_values(by='NetPnL', ascending=True).head(5).copy()
        if not worst_trades.empty:
            worst_trades['ROI (%)'] = (worst_trades['NetPnL'] / worst_trades['CapitalDeployed']) * 100
            st.dataframe(
                worst_trades[['Ticker', 'Strategy', 'EntryPrice', 'ExitPrice', 'NetPnL', 'ROI (%)']].style.format({
                    "EntryPrice": "₹{:.2f}",
                    "ExitPrice": "₹{:.2f}",
                    "NetPnL": "₹{:,.2f}",
                    "ROI (%)": "{:.2f}%"
                }).map(style_pnl, subset=['NetPnL']),
                width='stretch'
            )
        else:
            st.info("No trades to display.")


# Helper to style PnL numbers color-coded
def style_pnl(val):
    try:
        f_val = float(str(val).replace('₹', '').replace(',', '').replace('%', '').strip())
        if f_val > 0:
            return 'color: #10b981; font-weight: 600;'
        elif f_val < 0:
            return 'color: #ef4444; font-weight: 600;'
    except ValueError:
        pass
    return ''

