import os
import requests
import streamlit as st
import streamlit.components.v1 as components


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="Quantum X PRO",
    page_icon="📈",
    layout="wide",
)


# ============================================================
# CONFIGURATION
# ============================================================

IG_BASE_URL = "https://demo-api.ig.com/gateway/deal"


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
# SESSION STATE
# ============================================================

if "ig_connected" not in st.session_state:
    st.session_state["ig_connected"] = False

if "ig_error" not in st.session_state:
    st.session_state["ig_error"] = ""

if "ig_account" not in st.session_state:
    st.session_state["ig_account"] = None

if "ig_cst" not in st.session_state:
    st.session_state["ig_cst"] = None

if "ig_security_token" not in st.session_state:
    st.session_state["ig_security_token"] = None


# ============================================================
# IG AUTHENTICATION
# ============================================================

def connect_to_ig():
    """
    Authenticate with the IG Demo REST API.

    Credentials are read only from environment variables:
        IG_USERNAME
        IG_PASSWORD
        IG_API_KEY

    The actual credentials are never displayed.
    """

    username = os.getenv("IG_USERNAME")
    password = os.getenv("IG_PASSWORD")
    api_key = os.getenv("IG_API_KEY")

    missing = []

    if not username:
        missing.append("IG_USERNAME")

    if not password:
        missing.append("IG_PASSWORD")

    if not api_key:
        missing.append("IG_API_KEY")

    if missing:
        return False, (
            "Missing Codespaces secret(s): "
            + ", ".join(missing)
        ), None

    url = f"{IG_BASE_URL}/session"

    headers = {
        "X-IG-API-KEY": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Version": "2",
    }

    payload = {
        "identifier": username,
        "password": password,
        "encryptedPassword": False,
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=20,
        )

        if response.status_code != 200:
            try:
                error_data = response.json()
                error_message = error_data.get(
                    "errorCode",
                    "IG authentication failed.",
                )
            except ValueError:
                error_message = "IG authentication failed."

            return False, error_message, None

        cst = response.headers.get("CST")
        security_token = response.headers.get(
            "X-SECURITY-TOKEN"
        )

        if not cst or not security_token:
            return (
                False,
                "IG login succeeded but session tokens were not returned.",
                None,
            )

        try:
            account_data = response.json()
        except ValueError:
            account_data = {}

        return True, "IG Demo connection successful.", {
            "cst": cst,
            "security_token": security_token,
            "account_data": account_data,
        }

    except requests.exceptions.Timeout:
        return (
            False,
            "IG connection timed out. Please try again.",
            None,
        )

    except requests.exceptions.ConnectionError:
        return (
            False,
            "Could not reach IG Demo. Check your internet connection.",
            None,
        )

    except requests.exceptions.RequestException:
        return (
            False,
            "An unexpected network error occurred.",
            None,
        )


# ============================================================
# HEADER
# ============================================================

st.title("📈 QUANTUM X PRO")

st.caption(
    "AI-Powered Market Analysis & Smart Money Concepts"
)

if st.session_state["ig_connected"]:
    st.success("● SYSTEM ONLINE • IG DEMO CONNECTED")
else:
    st.success("● SYSTEM ONLINE")


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header("⚙️ Market Settings")

    selected_market = st.selectbox(
        "Market",
        list(MARKETS.keys()),
    )

    selected_timeframe = st.selectbox(
        "Setup Timeframe",
        list(TIMEFRAMES.keys()),
        index=1,
    )

    analysis_mode = st.radio(
        "Analysis Mode",
        [
            "Manual Analysis",
            "Automatic Scanner",
        ],
    )

    st.divider()

    st.subheader("🔌 System")

    if st.session_state["ig_connected"]:

        st.success("IG Demo Connected")

        if st.session_state["ig_account"]:
            account_info = st.session_state["ig_account"]

            accounts = account_info.get(
                "accounts",
                [],
            )

            if accounts:
                preferred_account = next(
                    (
                        account
                        for account in accounts
                        if account.get("preferred") is True
                    ),
                    accounts[0],
                )

                account_name = preferred_account.get(
                    "accountName",
                    "Demo Account",
                )

                st.caption(
                    f"Account: {account_name}"
                )

    else:

        st.warning("IG Demo Not Connected")

        connect_button = st.button(
            "🔌 Connect to IG Demo",
            use_container_width=True,
        )

        if connect_button:

            with st.spinner(
                "Connecting securely to IG Demo..."
            ):

                success, message, session_data = (
                    connect_to_ig()
                )

            if success:

                st.session_state[
                    "ig_connected"
                ] = True

                st.session_state[
                    "ig_error"
                ] = ""

                st.session_state[
                    "ig_cst"
                ] = session_data["cst"]

                st.session_state[
                    "ig_security_token"
                ] = session_data[
                    "security_token"
                ]

                st.session_state[
                    "ig_account"
                ] = session_data[
                    "account_data"
                ]

                st.rerun()

            else:

                st.session_state[
                    "ig_connected"
                ] = False

                st.session_state[
                    "ig_error"
                ] = message

                st.error(message)


# ============================================================
# CONNECTION STATUS
# ============================================================

if st.session_state["ig_error"]:

    st.error(
        f"IG Connection Error: "
        f"{st.session_state['ig_error']}"
    )


# ============================================================
# INFORMATION
# ============================================================

col1, col2, col3, col4 = st.columns(4)

with col1:

    st.metric(
        "Market",
        selected_market,
    )

with col2:

    st.metric(
        "Timeframe",
        selected_timeframe,
    )

with col3:

    st.metric(
        "Data Provider",
        "IG Demo",
    )

with col4:

    st.metric(
        "AI Engine",
        "GPT-5.6 Sol",
    )


# ============================================================
# TRADINGVIEW CHART
# ============================================================

st.subheader("📊 Market Chart")

tv_symbol = MARKETS[selected_market]
tv_interval = TIMEFRAMES[selected_timeframe]

chart_html = f"""
<div
    id="tradingview_chart"
    style="height:600px;width:100%;">
</div>

<script
    src="https://s3.tradingview.com/tv.js">
</script>

<script>

new TradingView.widget({{
    "autosize": true,
    "symbol": "{tv_symbol}",
    "interval": "{tv_interval}",
    "timezone": "Africa/Johannesburg",
    "theme": "dark",
    "style": "1",
    "locale": "en",
    "enable_publishing": false,
    "hide_top_toolbar": false,
    "hide_legend": false,
    "save_image": false,
    "container_id": "tradingview_chart"
}});

</script>
"""

components.html(
    chart_html,
    height=620,
    scrolling=False,
)


# ============================================================
# MARKET ANALYSIS
# ============================================================

st.subheader("🔎 Market Analysis")

scan_button = st.button(
    "🔍 Scan Market",
    use_container_width=True,
)


if scan_button:

    if not st.session_state["ig_connected"]:

        st.warning(
            "Connect to IG Demo before scanning the market."
        )

    else:

        st.session_state[
            "scan_requested"
        ] = True


if st.session_state.get(
    "scan_requested",
    False,
):

    if st.session_state["ig_connected"]:

        st.success(
            "IG Demo is connected. "
            "Real market-data acquisition is ready "
            "for the next development stage."
        )

    else:

        st.warning(
            "Waiting for verified IG market data."
        )

else:

    if st.session_state["ig_connected"]:

        st.info(
            "IG connection verified. "
            "Press Scan Market to begin analysis."
        )

    else:

        st.info(
            "Connect IG Demo, then press Scan Market."
        )


# ============================================================
# ANALYSIS ENGINE
# ============================================================

st.subheader("🧠 Analysis Engine")

engine1, engine2, engine3 = st.columns(3)


with engine1:

    st.markdown(
        "### 📐 Technical Analysis"
    )

    st.write(
        "RSI, EMA, MACD, ADX, ATR, VWAP, "
        "volume and volatility."
    )


with engine2:

    st.markdown(
        "### 🧱 Smart Money Concepts"
    )

    st.write(
        "Market structure, BOS, CHoCH, "
        "liquidity, FVGs and order blocks."
    )


with engine3:

    st.markdown(
        "### 🤖 AI Reasoning"
    )

    st.write(
        "GPT-5.6 Sol will reason over "
        "deterministic market evidence."
    )


# ============================================================
# AI SIGNAL
# ============================================================

st.subheader("🎯 AI Signal")

if st.session_state["ig_connected"]:

    st.info(
        "IG authentication is verified. "
        "The next stage will retrieve real IG candles "
        "and build the deterministic analysis engine."
    )

else:

    st.info(
        "No trading signal yet. "
        "Connect to IG Demo first."
    )


# ============================================================
# SECURITY STATUS
# ============================================================

st.subheader("🔐 Security")

security_col1, security_col2, security_col3 = (
    st.columns(3)
)

with security_col1:

    if os.getenv("IG_USERNAME"):
        st.success("Username Secret: Loaded")
    else:
        st.error("Username Secret: Missing")


with security_col2:

    if os.getenv("IG_PASSWORD"):
        st.success("Password Secret: Loaded")
    else:
        st.error("Password Secret: Missing")


with security_col3:

    if os.getenv("IG_API_KEY"):
        st.success("API Key Secret: Loaded")
    else:
        st.error("API Key Secret: Missing")


st.caption(
    "🔒 Credential values are never displayed by Quantum X PRO."
)


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    "QUANTUM X PRO • IG Demo Market Data • "
    "TradingView Visualization • GPT-5.6 Sol"
)
