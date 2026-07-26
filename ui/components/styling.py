import streamlit as st

def apply_styling():
    """
    Applies custom CSS styling for the professional UI.
    """
    st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');
        
        html, body, [class*="css"] {
            font-family: 'Inter', sans-serif;
        }
        
        .main {
            background-color: var(--background-color);
        }
        
        /* Global Card Style */
        .stMetric {
            background-color: var(--secondary-background-color) !important;
            padding: 20px !important;
            border-radius: 12px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
            border: 1px solid rgba(128, 128, 128, 0.2) !important;
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
            background: var(--secondary-background-color);
            color: var(--text-color);
            padding: 25px;
            border-radius: 16px;
            border: 1px solid rgba(128, 128, 128, 0.2);
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
        
        </style>
    """, unsafe_allow_html=True)
