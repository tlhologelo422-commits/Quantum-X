import streamlit as st
import streamlit.components.v1 as components


# ============================================================
# QUANTUM X PRO
# ============================================================

st.set_page_config(
    page_title="Quantum X PRO",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# MARKET CONFIGURATION
# ============================================================

MARKETS = {
    "XAU/USD": "OANDA:XAUUSD",
    "EUR/USD": "FX:EURUSD",
    "GBP/USD": "FX:GBPUSD",
    "USD/JPY": "FX:USDJPY",
    "USD/CHF": "FX:USDCHF",
    "AUD/USD": "FX:AUDUSD",
    "USD/CAD": "FX:USDCAD",
    "NZD/USD": "FX:NZDUSD",
    "XAG/USD": "OANDA:XAGUSD",
    "US30": "OANDA:US30USD",
    "SPX500": "OANDA:SPX500USD",
    "UK100": "OANDA:UK100GBP",
    "GER30": "OANDA:DE30EUR",
}

TIMEFRAMES = {
    "5M": "5",
    "15M": "15",
    "30M": "30",
    "1H": "60",
    "4H": "240",
    "1D": "D",
}


# ============================================================
# PAGE STYLING
# ============================================================

st.markdown(
    """
    <style>

    .stApp {
        background:
            radial-gradient(
                circle at 15% 10%,
                rgba(0, 255, 170, 0.055),
                transparent 28%
            ),
            radial-gradient(
                circle at 85% 15%,
                rgba(0, 150, 255, 0.055),
                transparent 28%
            ),
            #070b12;
        color: #f4f7fb;
    }

    .block-container {
        max-width: 1500px;
        padding-top: 2rem;
        padding-bottom: 3rem;
    }

    section[data-testid="stSidebar"] {
        background: #080d15;
        border-right: 1px solid rgba(255,255,255,0.07);
    }

    h1, h2, h3 {
        color: #f5f7fa !important;
    }

    .qx-title {
        font-size: 42px;
        font-weight: 800;
        letter-spacing: 1px;
        margin-bottom: 0;
        background: linear-gradient(
            90deg,
            #ffffff,
            #57f5c2
        );
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }

    .qx-subtitle {
        color: #8f9aaa;
        font-size: 15px;
        margin-top: 3px;
        margin-bottom: 28px;
    }

    .status-badge {
        display: inline-block;
        padding: 6px 12px;
        border-radius: 999px;
        background: rgba(0, 255, 170, 0.08);
        border: 1px solid rgba(0, 255, 170, 0.22);
        color: #5fffc8;
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.4px;
    }

    .info-card {
        background: linear-gradient(
            145deg,
            rgba(19, 28, 41, 0.96),
            rgba(10, 16, 25, 0.96)
        );
        border: 1px solid rgba(255,255,255,0.07);
        border-radius: 14px;
        padding: 18px 20px;
        min-height: 96px;
        box-shadow: 0 10px 30px rgba(0,0,0,0.18);
    }

    .info-label {
        color: #7f8a9a;
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 1px;
        margin-bottom: 8px;
    }

    .info-value {
        color: #f4f7fb;
        font-size: 21px;
        font-weight: 700;
    }

    .info-value-green {
        color: #58f4bd;
        font-size: 21px;
        font-weight: 700;
    }

    .section-title {
        font-size: 19px;
        font-weight: 750;
        color: #ffffff;
        margin-top: 24px;
        margin-bottom: 12px;
    }

    .chart-wrapper {
        background: #0a0f17;
        border: 1px solid rgba(255,255,255,0.07);
        border-radius: 14px;
        overflow: hidden;
        box-shadow: 0 12px 35px rgba(0,0,0,0.20);
    }

    .engine-card {
        background: #0c121c;
        border: 1px solid rgba(255,255,255,0.06);
        border-radius: 12px;
        padding: 18px;
        min-height: 120px;
    }

    .engine-title {
        font-weight: 700;
        font-size: 15px;
        color: #f1f4f8;
        margin-bottom: 8px;
    }

    .engine-text {
        color: #8994a4;
        font-size: 13px;
        line-height: 1.55;
    }

    .signal-box {
        background:
            linear-gradient(
                135deg,
                rgba(15, 26, 38, 0.98),
                rgba(9, 16, 25, 0.98)
            );
        border: 1px solid rgba(0,255,170,0.13);
        border-radius: 15px;
        padding: 22px;
        margin-top: 10px;
    }

    .signal-title {
        color: #58f4bd;
        font-size: 13px;
        font-weight: 800;
        letter-spacing: 1px;
        text-transform: uppercase;
    }

    .signal-main {
        color: #f4f7fb;
        font-size: 24px;
        font-weight: 800;
        margin-top: 8px;
    }

    .signal-text {
        color: #8b96a6;
        font-size: 13px;
        margin-top: 6px;
    }

    .stButton > button {
        width: 100%;
        border-radius: 10px;
        border: 1px solid rgba(0,255,170,0.20);
        background: linear-gradient(
            135deg,
            #12b886,
            #0f9f77
        );
        color: white;
        font-weight: 800;
        min-height: 44px;
    }

    .stButton > button:hover {
        border-color: rgba(0,255,170,0.50);
        background: linear-gradient(
            135deg,
            #18c995,
            #10ae83
        );
    }

    div[data-baseweb="select"] > div {
        background-color: #0d141f;
        border-color: rgba(255,255,255,0.08);
        border-radius: 9px;
    }

    .stRadio label {
        color: #cbd3dd !important;
    }

    hr {
        border-color: rgba(255,255,255,0.06);
    }

    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.markdown("## 📈 QUANTUM X PRO")

    st.caption("AI-Powered Market Analysis & Smart Money Concepts")

    st.markdown("---")

    st.markdown("### ⚙️ Market Settings")

    selected_market = st.selectbox(
        "Market",
        list(MARKETS.keys()),
        index=0,
    )

    selected_timeframe = st.selectbox(
        "Setup Timeframe",
        list(TIMEFRAMES.keys()),
        index=2,
    )

    analysis_mode = st.radio(
        "Analysis Mode",
        [
            "Manual Analysis",
            "Automatic Scanner",
        ],
        index=0,
    )

    st.markdown("---")

    st.markdown("### 🔌 System")

    st.success("IG Data Layer: Ready")

    st.caption("API connection will be added next.")

    st.markdown("---")

    st.caption("Quantum X PRO")
    st.caption("TradingView → Visualization")
    st.caption("IG → Market Data")
    st.caption("GPT-5.6 Sol → Reasoning")


# ============================================================
# HEADER
# ============================================================

header_left, header_right = st.columns([5, 1])

with header_left:

    st.markdown(
        '<div class="qx-title">📈 QUANTUM X PRO</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="qx-subtitle">'
        'AI-Powered Market Analysis & Smart Money Concepts'
        '</div>',
        unsafe_allow_html=True,
    )


with header_right:

    st.markdown(
        '<div style="text-align:right; margin-top:12px;">'
        '<span class="status-badge">● SYSTEM ONLINE</span>'
        '</div>',
        unsafe_allow_html=True,
    )


# ============================================================
# MARKET INFORMATION
# ============================================================

col1, col2, col3, col4 = st.columns(4)

with col1:

    st.markdown(
        f"""
        <div class="info-card">
            <div class="info-label">Market</div>
            <div class="info-value">{selected_market}</div>
        </div>
        """,
        unsafe
