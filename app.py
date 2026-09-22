import os
from typing import Any

import pandas as pd
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
# IG CONFIGURATION
# ============================================================

IG_BASE_URL = "https://demo-api.ig.com/gateway/deal"


# TradingView symbols are ONLY for visualization.
# IG instruments are discovered dynamically from the API.
MARKETS = {
    "XAU/USD": {
        "tradingview": "OANDA:XAUUSD",
        "ig_search": "Gold",
    },
    "EUR/USD": {
        "tradingview": "FX:EURUSD",
        "ig_search": "EUR/USD",
    },
    "GBP/USD": {
        "tradingview": "FX:GBPUSD",
        "ig_search": "GBP/USD",
    },
    "USD/JPY": {
        "tradingview": "FX:USDJPY",
        "ig_search": "USD/JPY",
    },
    "USD/CHF": {
        "tradingview": "FX:USDCHF",
        "ig_search": "USD/CHF",
    },
    "AUD/USD": {
        "tradingview": "FX:AUDUSD",
        "ig_search": "AUD/USD",
    },
    "USD/CAD": {
        "tradingview": "FX:USDCAD",
        "ig_search": "USD/CAD",
    },
    "NZD/USD": {
        "tradingview": "FX:NZDUSD",
        "ig_search": "NZD/USD",
    },
    "XAG/USD": {
        "tradingview": "OANDA:XAGUSD",
        "ig_search": "Silver",
    },
    "US30": {
        "tradingview": "OANDA:US30USD",
        "ig_search": "US 30",
    },
    "SPX500": {
        "tradingview": "OANDA:SPX500USD",
        "ig_search": "S&P 500",
    },
    "UK100": {
        "tradingview": "OANDA:UK100GBP",
        "ig_search": "FTSE 100",
    },
    "GER30": {
        "tradingview": "OANDA:DE30EUR",
        "ig_search": "DAX",
    },
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

DEFAULT_STATE = {
    "ig_connected": False,
    "ig_error": "",
    "ig_cst": None,
    "ig_security_token": None,
    "ig_account": None,
    "ig_markets": [],
    "ig_selected_epic": None,
    "ig_selected_market_name": None,
    "ig_prices": None,
}


for key, value in DEFAULT_STATE.items():

    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def get_ig_credentials():
    """Read IG credentials from environment variables."""

    return (
        os.getenv("IG_USERNAME"),
        os.getenv("IG_PASSWORD"),
        os.getenv("IG_API_KEY"),
    )


def credentials_available():
    """Check whether all required IG secrets exist."""

    username, password, api_key = get_ig_credentials()

    return bool(
        username
        and password
        and api_key
    )


def build_ig_headers(version="2"):
    """Build authenticated IG request headers."""

    _, _, api_key = get_ig_credentials()

    return {
        "X-IG-API-KEY": api_key,
        "CST": st.session_state["ig_cst"],
        "X-SECURITY-TOKEN": st.session_state[
            "ig_security_token"
        ],
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Version": version,
    }


# ============================================================
# IG LOGIN
# ============================================================

def connect_to_ig():
    """Create an IG Demo trading session."""

    username, password, api_key = get_ig_credentials()

    missing = []

    if not username:
        missing.append("IG_USERNAME")

    if not password:
        missing.append("IG_PASSWORD")

    if not api_key:
        missing.append("IG_API_KEY")

    if missing:

        return (
            False,
            "Missing Codespaces secret(s): "
            + ", ".join(missing),
            None,
        )

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
                data = response.json()

                error_code = data.get(
                    "errorCode",
                    "IG authentication failed.",
                )

            except ValueError:

                error_code = (
                    "IG authentication failed."
                )

            return False, error_code, None

        cst = response.headers.get("CST")

        security_token = response.headers.get(
            "X-SECURITY-TOKEN"
        )

        if not cst or not security_token:

            return (
                False,
                "IG login succeeded but session tokens "
                "were not returned.",
                None,
            )

        try:
            account_data = response.json()
        except ValueError:
            account_data = {}

        return (
            True,
            "IG Demo connection successful.",
            {
                "cst": cst,
                "security_token": security_token,
                "account_data": account_data,
            },
        )

    except requests.exceptions.Timeout:

        return (
            False,
            "IG connection timed out.",
            None,
        )

    except requests.exceptions.ConnectionError:

        return (
            False,
            "Could not reach IG Demo.",
            None,
        )

    except requests.exceptions.RequestException:

        return (
            False,
            "Unexpected network error.",
            None,
        )


# ============================================================
# SEARCH IG MARKETS
# ============================================================

def search_ig_markets(search_term):
    """
    Search IG for markets matching a human-readable term.

    IG uses proprietary EPIC identifiers, so we discover
    them from the authenticated API instead of hardcoding them.
    """

    url = f"{IG_BASE_URL}/markets"

    params = {
        "searchTerm": search_term,
    }

    try:

        response = requests.get(
            url,
            headers=build_ig_headers("1"),
            params=params,
            timeout=20,
        )

        if response.status_code == 401:

            return (
                False,
                "IG session expired. Please reconnect.",
                [],
            )

        if response.status_code != 200:

            try:
                data = response.json()

                error_code = data.get(
                    "errorCode",
                    "IG market search failed.",
                )

            except ValueError:

                error_code = (
                    "IG market search failed."
                )

            return False, error_code, []

        data = response.json()

        markets = data.get(
            "markets",
            [],
        )

        if not isinstance(markets, list):
            markets = []

        return True, "", markets

    except requests.exceptions.RequestException as exc:

        return (
            False,
            f"Market search network error: {exc}",
            [],
        )


# ============================================================
# EXTRACT MARKET INFORMATION
# ============================================================

def extract_market_info(market: dict[str, Any]):
    """Extract useful fields from an IG market result."""

    instrument = market.get(
        "instrument",
        {},
    )

    if not isinstance(instrument, dict):
        instrument = {}

    snapshot = market.get(
        "snapshot",
        {},
    )

    if not isinstance(snapshot, dict):
        snapshot = {}

    epic = (
        instrument.get("epic")
        or market.get("epic")
    )

    name = (
        instrument.get("name")
        or market.get("name")
        or "Unknown market"
    )

    market_status = (
        snapshot.get("marketStatus")
        or market.get("marketStatus")
        or "UNKNOWN"
    )

    market_id = (
        instrument.get("marketId")
        or market.get("marketId")
        or ""
    )

    bid = (
        snapshot.get("bid")
        or market.get("bid")
    )

    offer = (
        snapshot.get("offer")
        or snapshot.get("ask")
        or market.get("offer")
        or market.get("ask")
    )

    return {
        "epic": epic,
        "name": name,
        "market_status": market_status,
        "market_id": market_id,
        "bid": bid,
        "offer": offer,
    }


# ============================================================
# GET HISTORICAL PRICES
# ============================================================

def get_ig_prices(
    epic,
    resolution="MINUTE_15",
    num_points=100,
):
    """
    Retrieve historical candles from IG.

    100 x 15-minute candles gives the deterministic engine
    enough recent data to begin technical analysis.
    """

    url = (
        f"{IG_BASE_URL}/prices/"
        f"{epic}/"
        f"{resolution}/"
        f"{num_points}"
    )

    try:

        response = requests.get(
            url,
            headers=build_ig_headers("2"),
            timeout=30,
        )

        if response.status_code == 401:

            return (
                False,
                "IG session expired. Please reconnect.",
                None,
            )

        if response.status_code != 200:

            try:
                data = response.json()

                error_code = data.get(
                    "errorCode",
                    "IG price request failed.",
                )

            except ValueError:

                error_code = (
                    "IG price request failed."
                )

            return False, error_code, None

        data = response.json()

        prices = data.get(
            "prices",
            [],
        )

        if not prices:

            return (
                False,
                "IG returned no historical prices.",
                None,
            )

        rows = []

        for price in prices:

            open_price = price.get(
                "openPrice",
                {},
            )

            high_price = price.get(
                "highPrice",
                {},
            )

            low_price = price.get(
                "lowPrice",
                {},
            )

            close_price = price.get(
                "closePrice",
                {},
            )

            def mid_price(value):

                if not isinstance(value, dict):
                    return None

                bid = value.get("bid")
                ask = value.get("ask")

                if bid is not None and ask is not None:

                    return (
                        float(bid)
                        + float(ask)
                    ) / 2.0

                if bid is not None:
                    return float(bid)

                if ask is not None:
                    return float(ask)

                return None

            rows.append(
                {
                    "timestamp": price.get(
                        "snapshotTimeUTC",
                        price.get(
                            "snapshotTime"
                        ),
                    ),
                    "open": mid_price(
                        open_price
                    ),
                    "high": mid_price(
                        high_price
                    ),
                    "low": mid_price(
                        low_price
                    ),
                    "close": mid_price(
                        close_price
                    ),
                    "volume": price.get(
                        "lastTradedVolume",
                        0,
                    ),
                }
            )

        dataframe = pd.DataFrame(rows)

        dataframe = dataframe.dropna(
            subset=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
            ]
        )

        if dataframe.empty:

            return (
                False,
                "IG returned candles, but they "
                "could not be converted into OHLC data.",
                None,
            )

        dataframe["timestamp"] = pd.to_datetime(
            dataframe["timestamp"],
            utc=True,
        )

        dataframe = dataframe.sort_values(
            "timestamp"
        )

        dataframe = dataframe.reset_index(
            drop=True
        )

        return True, "", dataframe

    except requests.exceptions.RequestException as exc:

        return (
            False,
            f"Price request network error: {exc}",
            None,
        )

    except (ValueError, TypeError) as exc:

        return (
            False,
            f"Could not parse IG price data: {exc}",
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

    st.success(
        "● SYSTEM ONLINE • IG DEMO CONNECTED"
    )

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

    st.subheader("🔌 IG Data Layer")

    if st.session_state["ig_connected"]:

        st.success(
            "IG Demo Connected"
        )

        reconnect_button = st.button(
            "🔄 Reconnect IG",
            use_container_width=True,
        )

        if reconnect_button:

            success, message, session_data = (
                connect_to_ig()
            )

            if success:

                st.session_state[
                    "ig_connected"
                ] = True

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

                st.session_state[
                    "ig_markets"
                ] = []

                st.session_state[
                    "ig_prices"
                ] = None

                st.success(
                    "IG session refreshed."
                )

            else:

                st.error(message)

    else:

        st.warning(
            "IG Demo Not Connected"
        )

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
# CONNECTION ERROR
# ============================================================

if st.session_state["ig_error"]:

    st.error(
        f"IG Error: "
        f"{st.session_state['ig_error']}"
    )


# ============================================================
# TOP METRICS
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
# TRADINGVIEW
# ============================================================

st.subheader("📊 Market Chart")

tv_symbol = MARKETS[
    selected_market
]["tradingview"]

tv_interval = TIMEFRAMES[
    selected_timeframe
]

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
# REAL IG MARKET DATA
# ============================================================

st.subheader("📡 IG Market Data")

if not st.session_state["ig_connected"]:

    st.info(
        "Connect to IG Demo first."
    )

else:

    search_term = MARKETS[
        selected_market
    ]["ig_search"]

    st.write(
        f"IG instrument search: **{search_term}**"
    )

    search_button = st.button(
        "🔎 Find IG Instrument",
        use_container_width=True,
    )

    if search_button:

        with st.spinner(
            f"Searching IG for {search_term}..."
        ):

            success, error, markets = (
                search_ig_markets(
                    search_term
                )
            )

        if success:

            extracted_markets = []

            for market in markets:

                if isinstance(market, dict):

                    info = extract_market_info(
                        market
                    )

                    if info["epic"]:

                        extracted_markets.append(
                            info
                
