import time
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st
import yfinance as yf


# ============================================================
# QUANTUM X V2.2
# ============================================================
# V2.1 FOUNDATION:
#   - IG Demo live XAU/USD quote
#   - Yahoo GC=F historical M5/M15 bootstrap
#   - IG/Yahoo price calibration
#   - Local live M5/M15 candle construction
#   - Data health monitoring
#
# V2.2 ADDED:
#   - Market structure engine
#   - Swing highs / lows
#   - HH / HL / LH / LL
#   - BOS
#   - CHOCH
#   - M5 execution structure
#   - M15 directional structure
#
# IMPORTANT:
#   V2.2 DOES NOT PLACE TRADES.
# ============================================================


# ------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------

st.set_page_config(
    page_title="Quantum X V2.2",
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

MAX_STARTUP_SECONDS = 600


# ------------------------------------------------------------
# V2.2 STRUCTURE CONFIGURATION
# ------------------------------------------------------------

STRUCTURE_SWING_LEFT = 2
STRUCTURE_SWING_RIGHT = 2

STRUCTURE_LOOKBACK_M5 = 180
STRUCTURE_LOOKBACK_M15 = 120

STRUCTURE_EVENT_LOOKBACK = 40


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

        return ts.tz_convert("UTC").strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
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
    security_token = response.headers.get(
        "X-SECURITY-TOKEN"
    )

    if not cst or not security_token:
        raise RuntimeError(
            "IG login succeeded but authentication tokens "
            "were not returned."
        )

    st.session_state["cst"] = cst
    st.session_state["security_token"] = security_token
    st.session_state["ig_connected"] = True


def ig_headers(version="3"):
    api_key = read_secret("IG_API_KEY")

    if not api_key:
        raise RuntimeError(
            "IG_API_KEY is missing from Streamlit secrets."
        )

    if (
        not st.session_state["cst"]
        or not st.session_state["security_token"]
    ):
        raise RuntimeError(
            "IG authentication tokens are not available."
        )

    return {
        "X-IG-API-KEY": api_key,
        "CST": st.session_state["cst"],
        "X-SECURITY-TOKEN": st.session_state[
            "security_token"
        ],
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
            f"IG market request failed "
            f"({response.status_code}): {detail}"
        )

    data = response.json()

    snapshot = data.get(
        "snapshot",
        {}
    )

    market = data.get(
        "instrument",
        {}
    )

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
        raise RuntimeError(
            "Yahoo returned no candle data."
        )

    df = raw_df.copy()

    if isinstance(
        df.columns,
        pd.MultiIndex,
    ):
        if len(df.columns.levels) >= 1:
            try:
                df.columns = (
                    df.columns
                    .get_level_values(0)
                )
            except Exception:
                pass

    required = [
        "Open",
        "High",
        "Low",
        "Close",
    ]

    missing = [
        column
        for column in required
        if column not in df.columns
    ]

    if missing:
        raise RuntimeError(
            f"Yahoo data is missing columns: {missing}"
        )

    df = df.copy()

    if not isinstance(
        df.index,
        pd.DatetimeIndex,
    ):
        df.index = pd.to_datetime(
            df.index,
            utc=True,
        )
    else:
        if df.index.tz is None:
            df.index = df.index.tz_localize(
                "UTC"
            )
        else:
            df.index = df.index.tz_convert(
                "UTC"
            )

    rename_map = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    }

    df = df.rename(
        columns=rename_map
    )

    if "volume" not in df.columns:
        df["volume"] = 0.0

    df = df[
        [
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    ].copy()

    for column in [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]:
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
        ]
    )

    df = df[
        ~df.index.duplicated(
            keep="last"
        )
    ]

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

    df = normalize_yahoo_dataframe(
        raw
    )

    current_bucket = floor_time(
        utc_now(),
        M5,
    )

    df = df[
        df.index < current_bucket
    ].copy()

    if df.empty:
        raise RuntimeError(
            "Yahoo M5 returned no completed candles."
        )

    df = df.tail(
        MAX_M5_BARS
    )

    latest_close = float(
        df["close"].iloc[-1]
    )

    st.session_state[
        "yahoo_m5_latest"
    ] = latest_close

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

    df = normalize_yahoo_dataframe(
        raw
    )

    current_bucket = floor_time(
        utc_now(),
        M15,
    )

    df = df[
        df.index < current_bucket
    ].copy()

    if df.empty:
        raise RuntimeError(
            "Yahoo M15 returned no completed candles."
        )

    df = df.tail(
        MAX_M15_BARS
    )

    latest_close = float(
        df["close"].iloc[-1]
    )

    st.session_state[
        "yahoo_m15_latest"
    ] = latest_close

    return df


# ------------------------------------------------------------
# CALIBRATION
# ------------------------------------------------------------

def calibrate_yahoo_to_ig(
    ig_mid,
    yahoo_df,
):
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

    offset = float(
        ig_mid
    ) - yahoo_close

    st.session_state[
        "calibration_offset"
    ] = offset

    return offset


def apply_calibration(
    df,
    offset,
):
    if df is None or df.empty:
        return df

    result = df.copy()

    for column in [
        "open",
        "high",
        "low",
        "close",
    ]:
        result[column] = (
            result[column]
            .astype(float)
            + offset
        )

    return result
