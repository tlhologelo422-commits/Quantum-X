import time
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st
import yfinance as yf


# ============================================================
# QUANTUM X V2.2
# MARKET STRUCTURE ENGINE
# ============================================================

st.set_page_config(
    page_title="Quantum X V2.2",
    page_icon="🐎",
    layout="wide",
)


# ============================================================
# CONFIG
# ============================================================

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

SWING_LEFT = 2
SWING_RIGHT = 2

MAX_STRUCTURE_POINTS = 80


# ============================================================
# SESSION STATE
# ============================================================

DEFAULT_STATE = {
    "ig_connected": False,
    "ig_cst": None,
    "ig_security_token": None,

    "live_bid": None,
    "live_offer": None,
    "live_mid": None,
    "market_status": "UNKNOWN",

    "m5_bars": [],
    "m15_bars": [],

    "yahoo_m5_latest": None,
    "yahoo_m15_latest": None,

    "calibration_offset": 0.0,

    "bootstrap_complete": False,
    "bootstrap_time": None,

    "last_update": None,
    "last_error": None,

    "engine_running": True,
}


for key, value in DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def safe_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def floor_time(timestamp, minutes):
    timestamp = pd.Timestamp(timestamp)

    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")

    return timestamp.floor(f"{minutes}min")


def normalize_ohlcv(df):
    if df is None or df.empty:
        return pd.DataFrame()

    result = df.copy()

    if isinstance(result.columns, pd.MultiIndex):
        result.columns = [
            col[0] if isinstance(col, tuple) else col
            for col in result.columns
        ]

    result.columns = [str(col).lower() for col in result.columns]

    required = ["open", "high", "low", "close"]

    if not all(column in result.columns for column in required):
        return pd.DataFrame()

    if "volume" not in result.columns:
        result["volume"] = 0

    result = result[["open", "high", "low", "close", "volume"]].copy()

    for column in result.columns:
        result[column] = pd.to_numeric(
            result[column],
            errors="coerce",
        )

    result = result.dropna(subset=required)

    if result.index.tz is None:
        result.index = result.index.tz_localize("UTC")
    else:
        result.index = result.index.tz_convert("UTC")

    result = result.sort_index()

    return result


# ============================================================
# IG AUTHENTICATION
# ============================================================

def get_secret(name):
    try:
        value = st.secrets.get(name)
        if value:
            return str(value)
    except Exception:
        pass

    return None


def ig_login():
    username = get_secret("IG_USERNAME")
    password = get_secret("IG_PASSWORD")
    api_key = get_secret("IG_API_KEY")

    if not username or not password or not api_key:
        raise RuntimeError(
            "Missing IG_USERNAME, IG_PASSWORD or IG_API_KEY "
            "in Streamlit secrets."
        )

    headers = {
        "X-IG-API-KEY": api_key,
        "Content-Type": "application/json",
        "Version": "2",
        "Accept": "application/json",
    }

    payload = {
        "identifier": username,
        "password": password,
        "encryptedPassword": False,
    }

    response = requests.post(
        f"{IG_BASE_URL}/session",
        headers=headers,
        json=payload,
        timeout=20,
    )

    response.raise_for_status()

    cst = response.headers.get("CST")
    security_token = response.headers.get("X-SECURITY-TOKEN")

    if not cst or not security_token:
        raise RuntimeError(
            "IG login succeeded but authentication tokens "
            "were not returned."
        )

    st.session_state["ig_cst"] = cst
    st.session_state["ig_security_token"] = security_token
    st.session_state["ig_connected"] = True


def ig_headers(version="3"):
    api_key = get_secret("IG_API_KEY")

    return {
        "X-IG-API-KEY": api_key,
        "CST": st.session_state["ig_cst"],
        "X-SECURITY-TOKEN": st.session_state["ig_security_token"],
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Version": version,
    }


# ============================================================
# IG LIVE MARKET
# ============================================================

def get_ig_market():
    response = requests.get(
        f"{IG_BASE_URL}/markets/{IG_EPIC}",
        headers=ig_headers("3"),
        timeout=20,
    )

    if response.status_code == 401:
        ig_login()

        response = requests.get(
            f"{IG_BASE_URL}/markets/{IG_EPIC}",
            headers=ig_headers("3"),
            timeout=20,
        )

    response.raise_for_status()

    data = response.json()

    snapshot = data.get("snapshot", {})
    market = data.get("instrument", {})

    bid = safe_float(snapshot.get("bid"))
    offer = safe_float(snapshot.get("offer"))

    if bid is not None and offer is not None:
        mid = (bid + offer) / 2
    elif bid is not None:
        mid = bid
    elif offer is not None:
        mid = offer
    else:
        mid = None

    st.session_state["live_bid"] = bid
    st.session_state["live_offer"] = offer
    st.session_state["live_mid"] = mid

    st.session_state["market_status"] = (
        market.get("marketStatus")
        or snapshot.get("marketStatus")
        or "UNKNOWN"
    )

    return mid


# ============================================================
# YAHOO DATA
# ============================================================

def download_yahoo(interval, period):
    data = yf.download(
        YAHOO_SYMBOL,
        period=period,
        interval=interval,
        auto_adjust=False,
        progress=False,
        threads=False,
    )

    return normalize_ohlcv(data)


def remove_current_candle(df, minutes):
    if df.empty:
        return df

    current_bucket = floor_time(utc_now(), minutes)

    return df[df.index < current_bucket].copy()


def apply_calibration(df, offset):
    if df.empty:
        return df

    result = df.copy()

    for column in ["open", "high", "low", "close"]:
        result[column] = result[column] + offset

    return result


# ============================================================
# BOOTSTRAP
# ============================================================

def bootstrap_market_data():
    m5 = download_yahoo(M5, M5_BOOTSTRAP_PERIOD)
    m15 = download_yahoo(M15, M15_BOOTSTRAP_PERIOD)

    m5 = remove_current_candle(m5, 5)
    m15 = remove_current_candle(m15, 15)

    if m5.empty:
        raise RuntimeError("Yahoo M5 bootstrap returned no candles.")

    if m15.empty:
        raise RuntimeError("Yahoo M15 bootstrap returned no candles.")

    yahoo_latest = safe_float(m5["close"].iloc[-1])
    ig_latest = st.session_state.get("live_mid")

    if yahoo_latest is None:
        raise RuntimeError("Yahoo latest M5 close is invalid.")

    if ig_latest is None:
        raise RuntimeError("IG live price is unavailable.")

    offset = ig_latest - yahoo_latest

    m5 = apply_calibration(m5, offset)
    m15 = apply_calibration(m15, offset)

    m5 = m5.tail(MAX_M5_BARS)
    m15 = m15.tail(MAX_M15_BARS)

    st.session_state["m5_bars"] = m5.reset_index().to_dict(
        orient="records"
    )

    st.session_state["m15_bars"] = m15.reset_index().to_dict(
        orient="records"
    )

    st.session_state["yahoo_m5_latest"] = yahoo_latest
    st.session_state["yahoo_m15_latest"] = safe_float(
        m15["close"].iloc[-1] - offset
    )

    st.session_state["calibration_offset"] = offset
    st.session_state["bootstrap_complete"] = True
    st.session_state["bootstrap_time"] = utc_now()


# ============================================================
# LOCAL LIVE CANDLE ENGINE
# ============================================================

def update_live_candles(mid):
    if mid is None:
        return

    now = utc_now()

    m5_bucket = floor_time(now, 5)
    m15_bucket = floor_time(now, 15)

    update_one_live_series(
        state_key="m5_bars",
        bucket=m5_bucket,
        mid=mid,
        max_bars=MAX_M5_BARS,
    )

    update_one_live_series(
        state_key="m15_bars",
        bucket=m15_bucket,
        mid=mid,
        max_bars=MAX_M15_BARS,
    )


def update_one_live_series(state_key, bucket, mid, max_bars):
    bars = st.session_state.get(state_key, [])

    if not bars:
        bars = [{
            "Datetime": bucket,
            "open": mid,
            "high": mid,
            "low": mid,
            "close": mid,
            "volume": 0,
        }]

        st.session_state[state_key] = bars
        return

    last = bars[-1]

    last_time = pd.Timestamp(last["Datetime"])

    if last_time.tzinfo is None:
        last_time = last_time.tz_localize("UTC")
    else:
        last_time = last_time.tz_convert("UTC")

    bucket = pd.Timestamp(bucket)

    if last_time == bucket:
        last["high"] = max(
            safe_float(last["high"]) or mid,
            mid,
        )

        last["low"] = min(
            safe_float(last["low"]) or mid,
            mid,
        )

        last["close"] = mid

    elif bucket > last_time:
        bars.append({
            "Datetime": bucket,
            "open": mid,
            "high": mid,
            "low": mid,
            "close": mid,
            "volume": 0,
        })

    st.session_state[state_key] = bars[-max_bars:]


# ============================================================
# DATAFRAME CONVERSION
# ============================================================

def state_to_dataframe(state_key):
    bars = st.session_state.get(state_key, [])

    if not bars:
        return pd.DataFrame()

    df = pd.DataFrame(bars)

    if "Datetime" in df.columns:
        df["Datetime"] = pd.to_datetime(
            df["Datetime"],
            utc=True,
        )

        df = df.sort_values("Datetime")

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]

    for column in numeric_columns:
        if column in df.columns:
            df[column] = pd.to_numeric(
                df[column],
                errors="coerce",
            )

    return df.reset_index(drop=True)


# ============================================================
# MARKET STRUCTURE — SWING DETECTION
# ============================================================

def detect_swing_high(df, index):
    if index < SWING_LEFT:
        return False

    if index + SWING_RIGHT >= len(df):
        return False

    current_high = float(df.iloc[index]["high"])

    left_highs = [
        float(df.iloc[index - x]["high"])
        for x in range(1, SWING_LEFT + 1)
    ]

    right_highs = [
        float(df.iloc[index + x]["high"])
        for x in range(1, SWING_RIGHT + 1)
    ]

    return (
        current_high > max(left_highs)
        and current_high >= max(right_highs)
    )


def detect_swing_low(df, index):
    if index < SWING_LEFT:
        return False

    if index + SWING_RIGHT >= len(df):
        return False

    current_low = float(df.iloc[index]["low"])

    left_lows = [
        float(df.iloc[index - x]["low"])
        for x in range(1, SWING_LEFT + 1)
    ]

    right_lows = [
        float(df.iloc[index + x]["low"])
        for x in range(1, SWING_RIGHT + 1)
    ]

    return (
        current_low < min(left_lows)
        and current_low <= min(right_lows)
    )


def find_swings(df):
    if df.empty or len(df) < (
        SWING_LEFT + SWING_RIGHT + 1
    ):
        return []

    points = []

    for index in range(
        SWING_LEFT,
        len(df) - SWING_RIGHT,
    ):
        row = df.iloc[index]

        if detect_swing_high(df, index):
            points.append({
                "index": index,
                "time": row["Datetime"],
                "type": "HIGH",
                "price": float(row["high"]),
            })

        if detect_swing_low(df, index):
            points.append({
                "index": index,
                "time": row["Datetime"],
                "type": "LOW",
                "price": float(row["low"]),
            })

    return points[-MAX_STRUCTURE_POINTS:]
