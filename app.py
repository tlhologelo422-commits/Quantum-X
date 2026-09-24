import streamlit as st
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

# ============================================================
# QUANTUM X PRO - IG DEMO XAU/USD M5 SCALPER
# ============================================================

st.set_page_config(
    page_title="Quantum X PRO Scalper",
    page_icon="🐎",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ============================================================
# CONFIG
# ============================================================

IG_BASE = "https://demo-api.ig.com/gateway/deal"

DEFAULT_EPIC = "CS.D.IN_GOLD.MFI.IP"

POLL_SECONDS = 5
M5_SECONDS = 300

MAX_TRADES_PER_DAY = 10
MAX_OPEN_POSITIONS = 1

MIN_COMPLETED_BARS = 6
MAX_LOCAL_BARS = 300

DEFAULT_SIZE = 0.1
DEFAULT_RISK_DISTANCE = 3.0
DEFAULT_RR = 1.5

# ============================================================
# SESSION STATE
# ============================================================

DEFAULT_STATE = {
    "connected": False,
    "cst": "",
    "security_token": "",
    "api_key": "",
    "epic": DEFAULT_EPIC,
    "bot_running": False,
    "bars": [],
    "last_tick": None,
    "last_signal": "WAIT",
    "last_reason": "Waiting for market data.",
    "last_order": None,
    "trade_count": 0,
    "trade_day": "",
    "last_trade_bar": "",
    "last_price": None,
    "last_bid": None,
    "last_offer": None,
    "market_status": "",
    "last_error": "",
    "size": DEFAULT_SIZE,
    "stop_distance": DEFAULT_RISK_DISTANCE,
    "rr": DEFAULT_RR,
}

for key, value in DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def today_key():
    return utc_now().strftime("%Y-%m-%d")


def reset_daily_counter_if_needed():
    today = today_key()

    if st.session_state["trade_day"] != today:
        st.session_state["trade_day"] = today
        st.session_state["trade_count"] = 0
        st.session_state["last_trade_bar"] = ""


def headers(version="2"):
    return {
        "X-IG-API-KEY": st.session_state["api_key"],
        "CST": st.session_state["cst"],
        "X-SECURITY-TOKEN": st.session_state["security_token"],
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Version": version,
    }


def api_error_text(response):
    try:
        data = response.json()

        if isinstance(data, dict):
            code = data.get("errorCode", "")
            message = data.get("errorMessage", "")

            if code and message:
                return f"{code}: {message}"

            if code:
                return code

            return str(data)

        return str(data)

    except Exception:
        return response.text[:500]


def safe_float(value, default=None):
    try:
        if value is None:
            return default

        if isinstance(value, str):
            value = value.replace(",", "")

        return float(value)

    except Exception:
        return default


# ============================================================
# SECRETS
# ============================================================

def load_secrets():
    username = st.secrets.get("IG_USERNAME", "")
    password = st.secrets.get("IG_PASSWORD", "")
    api_key = st.secrets.get("IG_API_KEY", "")

    st.session_state["api_key"] = api_key

    return username, password, api_key


# ============================================================
# IG LOGIN
# ============================================================

def login_ig():
    username, password, api_key = load_secrets()

    if not username or not password or not api_key:
        return False, "IG credentials are missing from Codespaces secrets."

    payload = {
        "identifier": username,
        "password": password,
        "encryptedPassword": False,
    }

    try:
        response = requests.post(
            f"{IG_BASE}/session",
            headers={
                "X-IG-API-KEY": api_key,
                "Content-Type": "application/json",
                "Version": "2",
            },
            json=payload,
            timeout=15,
        )

        if response.status_code not in (200, 201):
            return (
                False,
                f"Login failed ({response.status_code}): "
                f"{api_error_text(response)}",
            )

        cst = response.headers.get("CST")
        security_token = response.headers.get(
            "X-SECURITY-TOKEN"
        )

        if not cst or not security_token:
            return (
                False,
                "IG login succeeded but session tokens "
                "were not returned.",
            )

        st.session_state["cst"] = cst
        st.session_state["security_token"] = security_token
        st.session_state["connected"] = True
        st.session_state["last_error"] = ""

        return True, "Connected to IG Demo."

    except requests.RequestException as exc:
        return False, f"Connection error: {exc}"


# ============================================================
# FIND GOLD EPIC
# ============================================================

def find_gold_epic():
    try:
        response = requests.get(
            f"{IG_BASE}/markets",
            headers=headers("1"),
            params={"searchTerm": "XAUUSD"},
            timeout=15,
        )

        if response.status_code != 200:
            return (
                DEFAULT_EPIC,
                f"Gold search failed: "
                f"{api_error_text(response)}",
            )

        data = response.json()

        markets = data.get("markets", [])

        candidates = []

        for market in markets:
            epic = market.get("epic", "")
            instrument = market.get("instrumentName", "")
            name = market.get("name", "")

            text = (
                f"{epic} {instrument} {name}"
            ).lower()

            if (
                "gold" in text
                or "xau" in text
                or "spot gold" in text
            ):
                candidates.append(epic)

        if candidates:
            return candidates[0], ""

        return DEFAULT_EPIC, ""

    except Exception as exc:
        return DEFAULT_EPIC, str(exc)


# ============================================================
# CURRENT IG MARKET PRICE
# ============================================================

def get_current_price():
    epic = st.session_state["epic"]

    try:
        response = requests.get(
            f"{IG_BASE}/markets/{epic}",
            headers=headers("3"),
            timeout=15,
        )

        if response.status_code != 200:
            return (
                None,
                None,
                None,
                api_error_text(response),
            )

        data = response.json()

        snapshot = data.get("snapshot", {})

        bid = safe_float(
            snapshot.get("bid")
        )

        offer = safe_float(
            snapshot.get("offer")
        )

        if bid is None:
            bid = safe_float(
                snapshot.get("bidOpen")
            )

        if offer is None:
            offer = safe_float(
                snapshot.get("offerOpen")
            )

        if bid is None and offer is None:
            return (
                None,
                None,
                None,
                "IG returned no usable bid/offer.",
            )

        if bid is None:
            bid = offer

        if offer is None:
            offer = bid

        mid = (bid + offer) / 2.0

        market_status = snapshot.get(
            "marketStatus",
            "",
        )

        return (
            mid,
            bid,
            offer,
            market_status,
        )

    except requests.RequestException as exc:
        return (
            None,
            None,
            None,
            str(exc),
        )


# ============================================================
# LOCAL M5 BAR BUILDER
# ============================================================

def current_bar_start(dt=None):
    if dt is None:
        dt = utc_now()

    seconds_from_midnight = (
        dt.hour * 3600
        + dt.minute * 60
        + dt.second
    )

    floored = seconds_from_midnight - (
        seconds_from_midnight % M5_SECONDS
    )

    midnight = dt.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    return midnight + timedelta(
        seconds=floored
    )


def add_tick(price):
    now = utc_now()

    bar_start = current_bar_start(now)

    bars = st.session_state["bars"]

    if not bars:

        bars.append(
            {
                "time": bar_start.isoformat(),
                "open": price,
                "high": price,
                "low": price,
                "close": price,
            }
        )

    else:

        last = bars[-1]

        last_time = datetime.fromisoformat(
            last["time"]
        )

        if last_time.tzinfo is None:
            last_time = last_time.replace(
                tzinfo=timezone.utc
            )

        if bar_start > last_time:

            bars.append(
                {
                    "time": bar_start.isoformat(),
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                }
            )

        else:

            last["high"] = max(
                float(last["high"]),
                price,
            )

            last["low"] = min(
                float(last["low"]),
                price,
            )

            last["close"] = price

    if len(bars) > MAX_LOCAL_BARS:
        st.session_state["bars"] = (
            bars[-MAX_LOCAL_BARS:]
        )


def bars_dataframe():
    bars = st.session_state["bars"]

    if not bars:

        return pd.DataFrame(
            columns=[
                "time",
                "open",
                "high",
                "low",
                "close",
            ]
        )

    df = pd.DataFrame(bars)

    for col in [
        "open",
        "high",
        "low",
        "close",
    ]:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

    df["time"] = pd.to_datetime(
        df["time"],
        utc=True,
    )

    return df


# ============================================================
# ATR
# ============================================================

def calculate_atr(df, period=5):

    if len(df) < 2:
        return None

    previous_close = df["close"].shift(1)

    tr1 = (
        df["high"]
        - df["low"]
    )

    tr2 = (
        df["high"]
        - previous_close
    ).abs()

    tr3 = (
        df["low"]
        - previous_close
    ).abs()

    true_range = pd.concat(
        [
            tr1,
            tr2,
            tr3,
        ],
        axis=1,
    ).max(axis=1)

    atr = true_range.rolling(
        period,
        min_periods=1,
    ).mean()

    value = atr.iloc[-1]

    if pd.isna(value):
        return None

    return float(value)


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def find_levels(df):

    support = []
    resistance = []

    if len(df) < 4:
        return support, resistance

    highs = df["high"].values
    lows = df["low"].values

    for i in range(
        1,
        len(df) - 1,
    ):

        if (
            highs[i] >= highs[i - 1]
            and highs[i] >= highs[i + 1]
        ):

            resistance.append(
                float(highs[i])
            )

        if (
            lows[i] <= lows[i - 1]
            and lows[i] <= lows[i + 1]
        ):

            support.append(
                float(lows[i])
            )

    return support, resistance


def nearest_level(
    price,
    levels,
    max_distance,
):

    if not levels:
        return None

    valid = [
        level
        for level in levels
        if abs(price - level)
        <= max_distance
    ]

    if not valid:
        return None

    return min(
        valid,
        key=lambda level:
        abs(price - level),
    )


# ============================================================
# CONFIRMATION CANDLES
# ============================================================

def bullish_engulfing(prev, curr):

    return (
        prev["close"] < prev["open"]
        and curr["close"] > curr["open"]
        and curr["open"] <= prev["close"]
        and curr["close"] >= prev["open"]
    )


def bearish_engulfing(prev, curr):

    return (
        prev["close"] > prev["open"]
        and curr["close"] < curr["open"]
        and curr["open"] >= prev["close"]
        and curr["close"] <= prev["open"]
    )


def bullish_rejection(candle):

    body = abs(
        candle["close"]
        - candle["open"]
    )

    lower_wick = (
        min(
            candle["open"],
            candle["close"],
        )
        - candle["low"]
    )

    upper_wick = (
        candle["high"]
        - max(
            candle["open"],
            candle["close"],
        )
    )

    return (
        candle["close"]
        > candle["open"]
        and lower_wick > body * 1.2
        and lower_wick > upper_wick
    )


def bearish_rejection(candle):

    body = abs(
        candle["close"]
        - candle["open"]
    )

    upper_wick = (
        candle["high"]
        - max(
            candle["open"],
            candle["close"],
        )
    )

    lower_wick = (
        min(
            candle["open"],
            candle["close"],
        )
        - candle["low"]
    )

    return (
        candle["close"]
        < candle["open"]
        and upper_wick > body * 1.2
        and upper_wick > lower_wick
    )


def bullish_momentum(prev, curr):

    prev_range = (
        prev["high"]
        - prev["low"]
    )

    curr_range = (
        curr["high"]
        - curr["low"]
    )

    if curr_range <= 0:
        return False

    return (
        curr["close"]
        > curr["open"]
        and curr["close"]
        > prev["high"]
        and curr_range
        >= prev_range * 0.8
    )


def bearish_momentum(prev, curr):

    prev_range = (
        prev["high"]
        - prev["low"]
    )

    curr_range = (
        curr["high"]
        - curr["low"]
    )

    if curr_range <= 0:
        return False

    return (
        curr["close"]
        < curr["open"]
        and curr["close"]
        < prev["low"]
        and curr_range
        >= prev_range * 0.8
    )


def bullish_confirmation(
    prev,
    curr,
):

    patterns = []

    if bullish_engulfing(
        prev,
        curr,
    ):
        patterns.append(
            "Bullish Engulfing"
        )

    if bullish_rejection(curr):
        patterns.append(
            "Bullish Rejection"
        )

    if bullish_momentum(
        prev,
        curr,
    ):
        patterns.append(
            "Bullish Momentum"
        )

    if patterns:
        return " + ".join(patterns)

    return None


def bearish_confirmation(
    prev,
    curr,
):

    patterns = []

    if bearish_engulfing(
        prev,
        curr,
    ):
        patterns.append(
            "Bearish Engulfing"
        )

    if bearish_rejection(curr):
        patterns.append(
            "Bearish Rejection"
        )

    if bearish_momentum(
        prev,
        curr,
    ):
        patterns.append(
            "Bearish Momentum"
        )

    if patterns:
        return " + ".join(patterns)

    return None
