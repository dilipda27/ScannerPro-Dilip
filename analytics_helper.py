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
        vix_data = yf.download("^INDIAVIX", start=start_str, end=end_str, progress=False)
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
            
        data = yf.download(tickers_formatted, start=start_str, end=end_str, group_by='ticker', progress=False)
        if not data.empty:
            for ticker in tickers_formatted:
                try:
                    symbol = ticker.replace('.NS', '')
                    if ticker in data.columns.levels[0]:
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

def render_analytics_tab(portfolio_df):
    st.markdown("### 📈 Analytics & Performance Module")
    
    # Load normalized trades
    all_trades_df = load_and_normalize_archived_trades()
    if all_trades_df.empty:
        st.info("No archived trades found to analyze.")
        return
        
    # Filter selection
    col_lf1, col_lf2 = st.columns([1, 2])
    with col_lf1:
        lens_choice = st.selectbox("Select Analytical Lens", ["ALL (Combined)", "Equity Intraday", "Options Selling", "Positional Swing"], key="perf_lens_sel")
    with col_lf2:
        min_date = all_trades_df['ExitTime'].min().date()
        max_date = all_trades_df['ExitTime'].max().date()
        selected_range = st.date_input("Filter Date Range", value=(min_date, max_date), min_value=min_date, max_value=max_date, key="perf_date_range")
    
    # Filter Data
    filtered_df = all_trades_df.copy()
    if lens_choice != "ALL (Combined)":
        filtered_df = filtered_df[filtered_df['AssetClass'] == lens_choice]
        
    if len(selected_range) == 2:
        start_dt = pd.to_datetime(selected_range[0]).tz_localize(None)
        end_dt = pd.to_datetime(selected_range[1]).tz_localize(None) + datetime.timedelta(days=1)
        filtered_df = filtered_df[(filtered_df['ExitTime'] >= start_dt) & (filtered_df['ExitTime'] < end_dt)]
        
    if filtered_df.empty:
        st.warning("No trades found matching the selected filters.")
        return
        
    # Tabs
    tab_metrics, tab_explorer, tab_conditions = st.tabs(["📊 Performance & Quant Metrics", "🏆 Trade Explorer (Best/Worst)", "⏱️ Time & Condition Analytics"])
    
    # Compute Core Metrics
    total_trades = len(filtered_df)
    wins_df = filtered_df[filtered_df['NetPnL'] > 0]
    losses_df = filtered_df[filtered_df['NetPnL'] <= 0]
    total_wins = len(wins_df)
    total_losses = len(losses_df)
    win_rate = (total_wins / total_trades) * 100 if total_trades > 0 else 0
    
    total_pnl = filtered_df['NetPnL'].sum()
    gross_wins = wins_df['NetPnL'].sum()
    gross_losses = abs(losses_df['NetPnL'].sum())
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float('inf')
    expectancy = filtered_df['NetPnL'].mean()
    
    # Sharpe Ratio
    filtered_df['ExitDateStr'] = filtered_df['ExitTime'].dt.date.astype(str)
    daily_grouped = filtered_df.groupby('ExitDateStr').agg({'NetPnL': 'sum'}).reset_index()
    if len(daily_grouped) > 1:
        sharpe = (daily_grouped['NetPnL'].mean() / daily_grouped['NetPnL'].astype(float).std()) * (252 ** 0.5) if daily_grouped['NetPnL'].astype(float).std() > 0 else 0.0
    else:
        sharpe = 0.0
        
    # Max Drawdown & Time Underwater
    filtered_df['CumPnL'] = filtered_df['NetPnL'].cumsum()
    peak = -float('inf')
    max_dd = 0.0
    peak_time = None
    max_underwater_seconds = 0
    underwater_durations = []
    
    for _, row in filtered_df.iterrows():
        pnl_val = row['CumPnL']
        curr_time = row['ExitTime']
        
        if pnl_val > peak:
            if peak_time is not None:
                dur = (curr_time - peak_time).total_seconds()
                underwater_durations.append(dur)
            peak = pnl_val
            peak_time = curr_time
        else:
            dd = peak - pnl_val
            if dd > max_dd:
                max_dd = dd
                
        if peak_time is not None and pnl_val < peak:
            curr_underwater = (curr_time - peak_time).total_seconds()
            if curr_underwater > max_underwater_seconds:
                max_underwater_seconds = curr_underwater
                
    max_underwater_val = max(underwater_durations) if underwater_durations else max_underwater_seconds
    underwater_days = max_underwater_val / 86400.0
    
    with tab_metrics:
        col_m1, col_m2, col_m3, col_m4 = st.columns(4)
        col_m1.metric("Net Profit (₹)", f"₹{total_pnl:,.2f}", delta=f"{total_wins}W / {total_losses}L", delta_color="normal" if total_pnl >= 0 else "inverse")
        col_m2.metric("Win Rate (%)", f"{win_rate:.2f}%", delta=f"Total: {total_trades} Trades")
        col_m3.metric("Profit Factor", f"{profit_factor:.2f}" if profit_factor != float('inf') else "∞")
        col_m4.metric("Expectancy", f"₹{expectancy:,.2f}", delta="Avg Return per Trade", delta_color="off")
        
        col_m5, col_m6, col_m7, col_m8 = st.columns(4)
        col_m5.metric("Max Drawdown (₹)", f"₹{max_dd:,.2f}", delta_color="inverse")
        col_m6.metric("Time Underwater", f"{underwater_days:.1f} Days" if underwater_days >= 1.0 else f"{(underwater_days * 24.0):.1f} Hours")
        col_m7.metric("Sharpe Ratio (Ann.)", f"{sharpe:.2f}")
        col_m8.write("")
        
        # Plotly Equity Curve
        fig_eq = go.Figure()
        daily_grouped_cum = filtered_df.groupby(filtered_df['ExitTime'].dt.date).agg({'NetPnL': 'sum'}).reset_index().sort_values('ExitTime')
        daily_grouped_cum['CumNet'] = daily_grouped_cum['NetPnL'].cumsum()
        
        fig_eq.add_trace(go.Scatter(
            x=daily_grouped_cum['ExitTime'],
            y=daily_grouped_cum['CumNet'],
            mode='lines+markers',
            name='Net Cumulative P&L',
            line=dict(color='#3b82f6', width=3),
            fill='tozeroy',
            fillcolor='rgba(59, 130, 246, 0.1)',
            hovertemplate='Date: %{x}<br>Net P&L: <b>₹%{y:,.2f}</b><extra></extra>'
        ))
        fig_eq.update_layout(
            title=dict(text=f"Cumulative Performance Equity Curve ({lens_choice})", font=dict(size=16, color="#3b82f6", weight='bold')),
            xaxis=dict(title="Date", showgrid=True, gridcolor='rgba(128,128,128,0.1)'),
            yaxis=dict(title="Cumulative P&L (₹)", showgrid=True, gridcolor='rgba(128,128,128,0.1)'),
            hovermode='x unified',
            plot_bgcolor='white',
            paper_bgcolor='white',
            height=400,
            margin=dict(l=40, r=40, t=55, b=40)
        )
        st.plotly_chart(fig_eq, width='stretch', config={'displayModeBar': False})
        
        # Strategy breakdowns
        st.subheader("📊 Performance by Strategy")
        strat_grouped = filtered_df.groupby('Strategy').agg({
            'NetPnL': ['sum', 'count'],
            'Ticker': lambda x: len(x[filtered_df.loc[x.index, 'NetPnL'] > 0])
        }).reset_index()
        strat_grouped.columns = ['Strategy', 'Net P&L', 'Total Trades', 'Wins']
        strat_grouped['Win Rate (%)'] = (strat_grouped['Wins'] / strat_grouped['Total Trades']) * 100
        strat_grouped = strat_grouped.sort_values(by='Net P&L', ascending=False)
        
        fig_strat = go.Figure()
        bar_colors = ['#10b981' if val >= 0 else '#ef4444' for val in strat_grouped['Net P&L']]
        fig_strat.add_trace(go.Bar(
            x=strat_grouped['Strategy'],
            y=strat_grouped['Net P&L'],
            marker_color=bar_colors,
            hovertemplate='Strategy: %{x}<br>Net P&L: <b>₹%{y:,.2f}</b><extra></extra>'
        ))
        fig_strat.update_layout(
            title=dict(text="Net P&L by Strategy", font=dict(size=14, color="#1e293b", weight='bold')),
            xaxis=dict(title="Strategy"),
            yaxis=dict(title="Net P&L (₹)"),
            plot_bgcolor='white',
            paper_bgcolor='white',
            height=350,
            margin=dict(l=40, r=40, t=40, b=40)
        )
        
        col_s1, col_s2 = st.columns([2, 1])
        with col_s1:
            st.plotly_chart(fig_strat, width='stretch', config={'displayModeBar': False})
        with col_s2:
            st.markdown("##### Strategy Stats")
            st.dataframe(
                strat_grouped[['Strategy', 'Total Trades', 'Win Rate (%)', 'Net P&L']].style.format({
                    "Win Rate (%)": "{:.1f}%",
                    "Net P&L": "₹{:,.2f}"
                }),
                use_container_width=True
            )
            
    with tab_explorer:
        st.subheader("🏆 Best Individual Trades")
        best_trades = filtered_df.sort_values(by='NetPnL', ascending=False).head(5).copy()
        best_trades['ROI (%)'] = (best_trades['NetPnL'] / best_trades['CapitalDeployed']) * 100
        st.dataframe(
            best_trades[['Ticker', 'Strategy', 'EntryPrice', 'ExitPrice', 'EntryTime', 'ExitTime', 'NetPnL', 'ROI (%)']].style.format({
                "EntryPrice": "₹{:.2f}",
                "ExitPrice": "₹{:.2f}",
                "NetPnL": "₹{:,.2f}",
                "ROI (%)": "{:.2f}%"
            }),
            use_container_width=True
        )
        
        st.subheader("⚠️ Worst Individual Trades")
        worst_trades = filtered_df.sort_values(by='NetPnL', ascending=True).head(5).copy()
        worst_trades['ROI (%)'] = (worst_trades['NetPnL'] / worst_trades['CapitalDeployed']) * 100
        st.dataframe(
            worst_trades[['Ticker', 'Strategy', 'EntryPrice', 'ExitPrice', 'EntryTime', 'ExitTime', 'NetPnL', 'ROI (%)']].style.format({
                "EntryPrice": "₹{:.2f}",
                "ExitPrice": "₹{:.2f}",
                "NetPnL": "₹{:,.2f}",
                "ROI (%)": "{:.2f}%"
            }),
            use_container_width=True
        )
        
    with tab_conditions:
        filtered_df['EntryHour'] = filtered_df['EntryTime'].dt.hour
        filtered_df['DayName'] = filtered_df['EntryTime'].dt.day_name()
        
        col_time1, col_time2 = st.columns(2)
        with col_time1:
            hour_grouped = filtered_df.groupby('EntryHour').agg({'NetPnL': 'sum'}).reset_index().sort_values('EntryHour')
            hour_colors = ['#10b981' if val >= 0 else '#ef4444' for val in hour_grouped['NetPnL']]
            fig_hour = go.Figure()
            fig_hour.add_trace(go.Bar(
                x=hour_grouped['EntryHour'].astype(str) + ":00",
                y=hour_grouped['NetPnL'],
                marker_color=hour_colors,
                hovertemplate='Entry Hour: %{x}<br>Net P&L: <b>₹%{y:,.2f}</b><extra></extra>'
            ))
            fig_hour.update_layout(
                title=dict(text="Net P&L by Time of Day (Entry Hour)", font=dict(size=14, color="#1e293b", weight='bold')),
                xaxis=dict(title="Hour of Day"),
                yaxis=dict(title="Net P&L (₹)"),
                plot_bgcolor='white',
                paper_bgcolor='white',
                height=350,
                margin=dict(l=40, r=40, t=40, b=40)
            )
            st.plotly_chart(fig_hour, width='stretch', config={'displayModeBar': False})
            
        with col_time2:
            days_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
            day_grouped = filtered_df.groupby('DayName').agg({'NetPnL': 'sum'}).reindex(days_order).dropna().reset_index()
            day_colors = ['#10b981' if val >= 0 else '#ef4444' for val in day_grouped['NetPnL']]
            fig_day = go.Figure()
            fig_day.add_trace(go.Bar(
                x=day_grouped['DayName'],
                y=day_grouped['NetPnL'],
                marker_color=day_colors,
                hovertemplate='Day: %{x}<br>Net P&L: <b>₹%{y:,.2f}</b><extra></extra>'
            ))
            fig_day.update_layout(
                title=dict(text="Net P&L by Day of Week", font=dict(size=14, color="#1e293b", weight='bold')),
                xaxis=dict(title="Day of Week"),
                yaxis=dict(title="Net P&L (₹)"),
                plot_bgcolor='white',
                paper_bgcolor='white',
                height=350,
                margin=dict(l=40, r=40, t=40, b=40)
            )
            st.plotly_chart(fig_day, width='stretch', config={'displayModeBar': False})
            
        # India VIX correlation & Gap up/down
        st.subheader("📈 Market Conditions Correlation")
        col_cond1, col_cond2 = st.columns(2)
        
        with col_cond1:
            st.markdown("##### India VIX Regime Performance")
            with st.spinner("Downloading VIX data for correlation..."):
                vix_dict = get_vix_data_cached(selected_range[0], selected_range[1])
                
            if not vix_dict:
                st.info("VIX data temporarily unavailable.")
            else:
                filtered_df['EntryDateOnly'] = filtered_df['EntryTime'].dt.date
                filtered_df['VIX'] = filtered_df['EntryDateOnly'].map(vix_dict)
                
                vix_df = filtered_df.dropna(subset=['VIX']).copy()
                if vix_df.empty:
                    st.info("No trades matched historical VIX dates.")
                else:
                    def get_vix_regime(vix):
                        if vix < 13:
                            return "Low Volatility (<13 VIX)"
                        elif vix <= 18:
                            return "Normal Volatility (13-18 VIX)"
                        else:
                            return "High Volatility (>18 VIX)"
                            
                    vix_df['VIX_Regime'] = vix_df['VIX'].apply(get_vix_regime)
                    vix_regimes = vix_df.groupby('VIX_Regime').agg({
                        'NetPnL': ['sum', 'count'],
                        'Ticker': lambda x: len(x[vix_df.loc[x.index, 'NetPnL'] > 0])
                    }).reset_index()
                    vix_regimes.columns = ['Regime', 'Net P&L', 'Total Trades', 'Wins']
                    vix_regimes['Win Rate (%)'] = (vix_regimes['Wins'] / vix_regimes['Total Trades']) * 100
                    
                    st.dataframe(
                        vix_regimes[['Regime', 'Total Trades', 'Win Rate (%)', 'Net P&L']].style.format({
                            "Win Rate (%)": "{:.1f}%",
                            "Net P&L": "₹{:,.2f}"
                        }),
                        use_container_width=True
                    )
                    
        with col_cond2:
            st.markdown("##### Stock Gap Up / Down Performance (Equity)")
            equity_intraday_trades = filtered_df[filtered_df['AssetClass'] == "Equity Intraday"].copy()
            if equity_intraday_trades.empty:
                st.info("Gap analysis is only applicable to Equity Intraday trades.")
            else:
                with st.spinner("Downloading stock daily prices for Gap correlation..."):
                    unique_tickers_tuple = tuple(sorted(equity_intraday_trades['Ticker'].unique().tolist()))
                    gaps_dict = get_bulk_gaps_cached(unique_tickers_tuple, selected_range[0], selected_range[1])
                    
                if not gaps_dict:
                    st.info("Daily Stock gap data temporarily unavailable.")
                else:
                    equity_intraday_trades['EntryDateOnly'] = equity_intraday_trades['EntryTime'].dt.date
                    equity_intraday_trades['Gap_Pct'] = equity_intraday_trades.apply(
                        lambda r: gaps_dict.get((r['Ticker'], r['EntryDateOnly'])), axis=1
                    )
                    
                    gap_df = equity_intraday_trades.dropna(subset=['Gap_Pct']).copy()
                    if gap_df.empty:
                        st.info("No trades matched historical stock gap dates.")
                    else:
                        def get_gap_regime(gap):
                            if gap > 0.3:
                                return "Gap Up (>0.3%)"
                            elif gap < -0.3:
                                return "Gap Down (<-0.3%)"
                            else:
                                return "Flat"
                                
                        gap_df['Gap_Regime'] = gap_df['Gap_Pct'].apply(get_gap_regime)
                        gap_regimes = gap_df.groupby('Gap_Regime').agg({
                            'NetPnL': ['sum', 'count'],
                            'Ticker': lambda x: len(x[gap_df.loc[x.index, 'NetPnL'] > 0])
                        }).reset_index()
                        gap_regimes.columns = ['Gap Scenario', 'Total Trades', 'Wins', 'Net P&L']
                        gap_regimes['Win Rate (%)'] = (gap_regimes['Wins'] / gap_regimes['Total Trades']) * 100
                        
                        st.dataframe(
                            gap_regimes[['Gap Scenario', 'Total Trades', 'Win Rate (%)', 'Net P&L']].style.format({
                                "Win Rate (%)": "{:.1f}%",
                                "Net P&L": "₹{:,.2f}"
                            }),
                            use_container_width=True
                        )
