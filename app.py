import time
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st
import yfinance as yf


# ============================================================
# QUANTUM X V2.1 — DATA ENGINE
# ============================================================
# Purpose:
#   - IG Demo live XAU/USD quote
#   - Yahoo GC=F historical M5/M15 bootstrap
#   - IG/Yahoo price calibration
#   - Local live M5/M15 candle construction
#   - Data health monitoring
#
# IMPORTANT:
#   V2.1 DOES NOT PLACE TRADES.
# ============================================================


# ------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------

st.set_page_config(
    page_title="Quantum X V2.1",
    page_icon="🐎",
    layout="wide",
)

IG_BASE_URL = "https://demo-api.ig.com/gateway/deal"
IG_EPIC = "CS.D.IN_GOLD.MFI.IP"
YAHOO_SYMBOL = "GC=F"

POLL_SECONDS = 5

M5 = "5min"
M15 = "15min"

M5_BOOTSTRAP_PERIOD = "5d"
M15_BOOTSTRAP_PERIOD = "5d"

MAX_M5_BARS = 500
MAX_M15_BARS = 250

MAX_STARTUP_SECONDS = 600  # 10 minutes


# ------------------------------------------------------------
# SESSION STATE
# ------------------------------------------------------------

DEFAULT_STATE = {
    "ig_connected": False,
    "cst": None,
    "security_token": None,

    "live_bid": None,
    "live_offer": None,
    "live_mid": None,
    "market_status": "UNKNOWN",

    "m5_bars": pd.DataFrame(),
    "m15_bars": pd.DataFrame(),

    "yahoo_m5_latest": None,
    "yahoo_m15_latest": None,

    "calibration_offset": None,

    "bootstrap_complete": False,
    "bootstrap_started_at": None,
    "bootstrap_completed_at": None,

    "last_update": None,
    "last_error": None,

    "engine_running": False,
}


for key, value in DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ------------------------------------------------------------
# UTILITY FUNCTIONS
# ------------------------------------------------------------

def utc_now():
    return pd.Timestamp.now(tz="UTC")


def floor_time(timestamp, timeframe):
    timestamp = pd.Timestamp(timestamp)

    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")

    return timestamp.floor(timeframe)


def format_price(value):
    if value is None:
        return "—"

    try:
        return f"{float(value):,.2f}"
    except Exception:
        return "—"


def format_timestamp(value):
    if value is None:
        return "—"

    try:
        ts = pd.Timestamp(value)

        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")

        return ts.tz_convert("UTC").strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return "—"


def read_secret(name):
    try:
        value = st.secrets[name]
        return str(value).strip()
    except Exception:
        return ""


# ------------------------------------------------------------
# IG AUTHENTICATION
# ------------------------------------------------------------

def ig_login():
    username = read_secret("IG_USERNAME")
    password = read_secret("IG_PASSWORD")
    api_key = read_secret("IG_API_KEY")

    if not username or not password or not api_key:
        raise RuntimeError(
            "Missing IG_USERNAME, IG_PASSWORD or IG_API_KEY "
            "in Streamlit secrets."
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

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=20,
    )

    if response.status_code >= 400:
        try:
            detail = response.json()
        except Exception:
            detail = response.text

        raise RuntimeError(
            f"IG login failed ({response.status_code}): {detail}"
        )

    cst = response.headers.get("CST")
    security_token = response.headers.get("X-SECURITY-TOKEN")

    if not cst or not security_token:
        raise RuntimeError(
            "IG login succeeded but authentication tokens were not returned."
        )

    st.session_state["cst"] = cst
    st.session_state["security_token"] = security_token
    st.session_state["ig_connected"] = True


def ig_headers(version="3"):
    api_key = read_secret("IG_API_KEY")

    if not api_key:
        raise RuntimeError("IG_API_KEY is missing from Streamlit secrets.")

    if not st.session_state["cst"] or not st.session_state["security_token"]:
        raise RuntimeError("IG authentication tokens are not available.")

    return {
        "X-IG-API-KEY": api_key,
        "CST": st.session_state["cst"],
        "X-SECURITY-TOKEN": st.session_state["security_token"],
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Version": str(version),
    }


# ------------------------------------------------------------
# IG LIVE MARKET DATA
# ------------------------------------------------------------

def get_ig_market():
    url = f"{IG_BASE_URL}/markets/{IG_EPIC}"

    response = requests.get(
        url,
        headers=ig_headers("3"),
        timeout=20,
    )

    if response.status_code >= 400:
        try:
            detail = response.json()
        except Exception:
            detail = response.text

        raise RuntimeError(
            f"IG market request failed ({response.status_code}): {detail}"
        )

    data = response.json()

    snapshot = data.get("snapshot", {})
    market = data.get("instrument", {})

    bid = snapshot.get("bid")
    offer = snapshot.get("offer")

    if bid is None or offer is None:
        raise RuntimeError(
            "IG returned market data without bid/offer."
        )

    bid = float(bid)
    offer = float(offer)
    mid = (bid + offer) / 2.0

    market_status = str(
        snapshot.get("marketStatus")
        or market.get("marketStatus")
        or "UNKNOWN"
    )

    st.session_state["live_bid"] = bid
    st.session_state["live_offer"] = offer
    st.session_state["live_mid"] = mid
    st.session_state["market_status"] = market_status
    st.session_state["last_update"] = utc_now()
    st.session_state["last_error"] = None

    return {
        "bid": bid,
        "offer": offer,
        "mid": mid,
        "market_status": market_status,
    }


# ------------------------------------------------------------
# YAHOO DATA NORMALIZATION
# ------------------------------------------------------------

def normalize_yahoo_dataframe(raw_df):
    if raw_df is None or raw_df.empty:
        raise RuntimeError("Yahoo returned no candle data.")

    df = raw_df.copy()

    # yfinance can sometimes return MultiIndex columns.
    if isinstance(df.columns, pd.MultiIndex):
        if len(df.columns.levels) >= 1:
            try:
                df.columns = df.columns.get_level_values(0)
            except Exception:
                pass

    required = ["Open", "High", "Low", "Close"]

    missing = [column for column in required if column not in df.columns]

    if missing:
        raise RuntimeError(
            f"Yahoo data is missing columns: {missing}"
        )

    df = df.copy()

    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)
    else:
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

    rename_map = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    }

    df = df.rename(columns=rename_map)

    if "volume" not in df.columns:
        df["volume"] = 0.0

    df = df[
        ["open", "high", "low", "close", "volume"]
    ].copy()

    for column in ["open", "high", "low", "close", "volume"]:
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

    df = df.dropna(
        subset=["open", "high", "low", "close"]
    )

    df = df[~df.index.duplicated(keep="last")]
    df = df.sort_index()

    return df


# ------------------------------------------------------------
# YAHOO M5
# ------------------------------------------------------------

def fetch_yahoo_m5():
    raw = yf.download(
        YAHOO_SYMBOL,
        period=M5_BOOTSTRAP_PERIOD,
        interval="5m",
        auto_adjust=False,
        progress=False,
        threads=False,
    )

    df = normalize_yahoo_dataframe(raw)

    current_bucket = floor_time(
        utc_now(),
        M5,
    )

    # Do not treat Yahoo's currently forming candle
    # as a completed historical candle.
    df = df[df.index < current_bucket].copy()

    if df.empty:
        raise RuntimeError(
            "Yahoo M5 returned no completed candles."
        )

    df = df.tail(MAX_M5_BARS)

    latest_close = float(df["close"].iloc[-1])

    st.session_state["yahoo_m5_latest"] = latest_close

    return df


# ------------------------------------------------------------
# YAHOO M15
# ------------------------------------------------------------

def fetch_yahoo_m15():
    raw = yf.download(
        YAHOO_SYMBOL,
        period=M15_BOOTSTRAP_PERIOD,
        interval="15m",
        auto_adjust=False,
        progress=False,
        threads=False,
    )

    df = normalize_yahoo_dataframe(raw)

    current_bucket = floor_time(
        utc_now(),
        M15,
    )

    df = df[df.index < current_bucket].copy()

    if df.empty:
        raise RuntimeError(
            "Yahoo M15 returned no completed candles."
        )

    df = df.tail(MAX_M15_BARS)

    latest_close = float(df["close"].iloc[-1])

    st.session_state["yahoo_m15_latest"] = latest_close

    return df


# ------------------------------------------------------------
# CALIBRATION
# ------------------------------------------------------------

def calibrate_yahoo_to_ig(ig_mid, yahoo_df):
    if ig_mid is None:
        raise RuntimeError(
            "Cannot calibrate without IG live price."
        )

    if yahoo_df is None or yahoo_df.empty:
        raise RuntimeError(
            "Cannot calibrate without Yahoo candles."
        )

    yahoo_close = float(
        yahoo_df["close"].iloc[-1]
    )

    offset = float(ig_mid) - yahoo_close

    st.session_state["calibration_offset"] = offset

    return offset


def apply_calibration(df, offset):
    if df is None or df.empty:
        return df

    result = df.copy()

    for column in ["open", "high", "low", "close"]:
        result[column] = (
            result[column].astype(float) + offset
        )

    return result


# ------------------------------------------------------------
# HISTORICAL DATA BOOTSTRAP
# ------------------------------------------------------------

def bootstrap_data():
    started = time.time()

    st.session_state["bootstrap_started_at"] = utc_now()
    st.session_state["last_error"] = None
    st.session_state["bootstrap_complete"] = False

    # Make sure IG is connected.
    if not st.session_state["ig_connected"]:
        ig_login()

    # Get the current IG execution price first.
    market = get_ig_market()

    # Download historical proxy data.
    m5 = fetch_yahoo_m5()
    m15 = fetch_yahoo_m15()

    # Calibrate Yahoo structure to the current IG price.
    offset = calibrate_yahoo_to_ig(
        market["mid"],
        m5,
    )

    m5 = apply_calibration(
        m5,
        offset,
    )

    m15 = apply_calibration(
        m15,
        offset,
    )

    # Store historical data.
    st.session_state["m5_bars"] = m5
    st.session_state["m15_bars"] = m15

    # Immediately add the current IG candle.
    update_live_candles(
        market["mid"],
        create_if_missing=True,
    )

    elapsed = time.time() - started

    if elapsed > MAX_STARTUP_SECONDS:
        raise RuntimeError(
            f"Data bootstrap exceeded the "
            f"{MAX_STARTUP_SECONDS // 60}-minute startup target."
        )

    st.session_state["bootstrap_completed_at"] = utc_now()
    st.session_state["bootstrap_complete"] = True
    st.session_state["engine_running"] = True

    return elapsed


# ------------------------------------------------------------
# LIVE CANDLE ENGINE
# ------------------------------------------------------------

def update_live_candles(
    live_price,
    create_if_missing=True,
):
    if live_price is None:
        return

    now = utc_now()

    m5_bucket = floor_time(now, M5)
    m15_bucket = floor_time(now, M15)

    # ----------------------------
    # M5
    # ----------------------------

    m5_df = st.session_state["m5_bars"].copy()

    if m5_df.empty:
        if not create_if_missing:
            return

        m5_df = pd.DataFrame(
            columns=[
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]
        )

    if m5_bucket in m5_df.index:
        row = m5_df.loc[m5_bucket].copy()

        row["high"] = max(
            float(row["high"]),
            float(live_price),
        )

        row["low"] = min(
            float(row["low"]),
            float(live_price),
        )

        row["close"] = float(live_price)

        m5_df.loc[m5_bucket] = row

    elif create_if_missing:
        m5_df.loc[m5_bucket] = {
            "open": float(live_price),
            "high": float(live_price),
            "low": float(live_price),
            "close": float(live_price),
            "volume": 0.0,
        }

    m5_df = (
        m5_df.sort_index()
        .tail(MAX_M5_BARS)
    )

    st.session_state["m5_bars"] = m5_df

    # ----------------------------
    # M15
    # ----------------------------

    m15_df = st.session_state["m15_bars"].copy()

    if m15_df.empty:
        if not create_if_missing:
            return

        m15_df = pd.DataFrame(
            columns=[
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]
        )

    if m15_bucket in m15_df.index:
        row = m15_df.loc[m15_bucket].copy()

        row["high"] = max(
            float(row["high"]),
            float(live_price),
        )

        row["low"] = min(
            float(row["low"]),
            float(live_price),
        )

        row["close"] = float(live_price)

        m15_df.loc[m15_bucket] = row

    elif create_if_missing:
        m15_df.loc[m15_bucket] = {
            "open": float(live_price),
            "high": float(live_price),
            "low": float(live_price),
            "close": float(live_price),
            "volume": 0.0,
        }

    m15_df = (
        m15_df.sort_index()
        .tail(MAX_M15_BARS)
    )

    st.session_state["m15_bars"] = m15_df


# ------------------------------------------------------------
# LIVE UPDATE
# ------------------------------------------------------------

def live_data_tick():
    if not st.session_state["ig_connected"]:
        return

    market = get_ig_market()

    update_live_candles(
        market["mid"],
        create_if_missing=True,
    )


# ------------------------------------------------------------
# DATA HEALTH
# ------------------------------------------------------------

def get_data_health():
    m5 = st.session_state["m5_bars"]
    m15 = st.session_state["m15_bars"]

    now = utc_now()

    m5_count = len(m5)
    m15_count = len(m15)

    m5_current = (
        floor_time(now, M5)
        if not m5.empty
        else None
    )

    m15_current = (
        floor_time(now, M15)
        if not m15.empty
        else None
    )

    m5_latest = (
        m5.index[-1]
        if not m5.empty
        else None
    )

    m15_latest = (
        m15.index[-1]
        if not m15.empty
        else None
    )

    return {
        "m5_count": m5_count,
        "m15_count": m15_count,
        "m5_latest": m5_latest,
        "m15_latest": m15_latest,
        "m5_current": m5_current,
        "m15_current": m15_current,
        "ig_mid": st.session_state["live_mid"],
        "offset": st.session_state["calibration_offset"],
        "market_status": st.session_state["market_status"],
    }


# ------------------------------------------------------------
# DISPLAY HELPERS
# ------------------------------------------------------------

def style_dataframe(df, rows=20):
    if df is None or df.empty:
        return pd.DataFrame()

    display_df = df.tail(rows).copy()

    display_df.index = display_df.index.strftime(
        "%H:%M:%S"
    )

    return display_df.round(
        {
            "open": 2,
            "high": 2,
            "low": 2,
            "close": 2,
            "volume": 0,
        }
    )


def render_status_cards():
    health = get_data_health()

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        if st.session_state["ig_connected"]:
            st.metric(
                "IG Demo",
                "CONNECTED",
            )
        else:
            st.metric(
                "IG Demo",
                "OFFLINE",
            )

    with col2:
        st.metric(
            "XAU/USD",
            format_price(health["ig_mid"]),
        )

    with col3:
        st.metric(
            "M5 candles",
            str(health["m5_count"]),
        )

    with col4:
        st.metric(
            "M15 candles",
            str(health["m15_count"]),
        )


def render_data_details():
    health = get_data_health()

    st.subheader("📡 Data Engine")

    col1, col2 = st.columns(2)

    with col1:
        st.write("**IG live market**")
        st.write(
            f"Bid: `{format_price(st.session_state['live_bid'])}`"
        )
        st.write(
            f"Offer: `{format_price(st.session_state['live_offer'])}`"
        )
        st.write(
            f"Mid: `{format_price(st.session_state['live_mid'])}`"
        )
        st.write(
            f"Status: `{health['market_status']}`"
        )

    with col2:
        st.write("**Yahoo structure proxy**")
        st.write(
            f"Symbol: `{YAHOO_SYMBOL}`"
        )
        st.write(
            f"M5 latest: `{format_price(st.session_state['yahoo_m5_latest'])}`"
        )
        st.write(
            f"M15 latest: `{format_price(st.session_state['yahoo_m15_latest'])}`"
        )
        st.write(
            f"Calibration offset: `{format_price(health['offset'])}`"
        )

    st.divider()

    col1, col2 = st.columns(2)

    with col1:
        st.write("**M5**")
        st.write(
            f"Latest candle: `{format_timestamp(health['m5_latest'])}`"
        )
        st.write(
            f"Current bucket: `{format_timestamp(health['m5_current'])}`"
        )

    with col2:
        st.write("**M15**")
        st.write(
            f"Latest candle: `{format_timestamp(health['m15_latest'])}`"
        )
        st.write(
            f"Current bucket: `{format_timestamp(health['m15_current'])}`"
        )

    st.write(
        f"Last IG update: `{format_timestamp(st.session_state['last_update'])}`"
    )

    st.write(
        f"Bootstrap completed: "
        f"`{format_timestamp(st.session_state['bootstrap_completed_at'])}`"
    )


# ------------------------------------------------------------
# MAIN UI
# ------------------------------------------------------------

st.title("🐎 Quantum X V2.1")
st.caption(
    "Data Engine — M5 + M15 structure foundation. "
    "Automatic trading is DISABLED in V2.1."
)

st.info(
    "V2.1 is a data milestone only. "
    "It can connect to IG Demo and build live candles, "
    "but it cannot place trades."
)


# ------------------------------------------------------------
# SIDEBAR
# --------------------------------------------
