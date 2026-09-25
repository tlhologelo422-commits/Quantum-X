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
# ============================================================
# MARKET STRUCTURE — HH / HL / LH / LL
# ============================================================

def classify_swings(swings):
    highs = []
    lows = []

    classified = []

    for point in swings:
        if point["type"] == "HIGH":
            previous_high = highs[-1]["price"] if highs else None

            if previous_high is None:
                label = "H"
            elif point["price"] > previous_high:
                label = "HH"
            else:
                label = "LH"

            highs.append(point.copy())

        else:
            previous_low = lows[-1]["price"] if lows else None

            if previous_low is None:
                label = "L"
            elif point["price"] > previous_low:
                label = "HL"
            else:
                label = "LL"

            lows.append(point.copy())

        item = point.copy()
        item["label"] = label
        classified.append(item)

    return classified


# ============================================================
# STRUCTURE STATE
# ============================================================

def determine_structure_bias(classified):
    if not classified:
        return "NEUTRAL"

    recent = classified[-12:]

    bullish_score = 0
    bearish_score = 0

    for point in recent:
        label = point["label"]

        if label == "HH":
            bullish_score += 2

        elif label == "HL":
            bullish_score += 1

        elif label == "LH":
            bearish_score += 2

        elif label == "LL":
            bearish_score += 1

    if bullish_score > bearish_score:
        return "BULLISH"

    if bearish_score > bullish_score:
        return "BEARISH"

    return "NEUTRAL"


# ============================================================
# BOS / CHOCH ENGINE
# ============================================================

def detect_structure_events(df, classified):
    if df.empty or not classified:
        return []

    events = []

    bullish_break_reference = None
    bearish_break_reference = None

    current_bias = "NEUTRAL"

    for point in classified:
        if point["label"] in ("HH", "LH"):
            bullish_break_reference = point

        if point["label"] in ("LL", "HL"):
            bearish_break_reference = point

    for index in range(len(df)):
        candle = df.iloc[index]

        high = float(candle["high"])
        low = float(candle["low"])

        if bullish_break_reference is not None:
            level = bullish_break_reference["price"]

            if (
                index > bullish_break_reference["index"]
                and high > level
            ):
                previous_bias = current_bias

                current_bias = "BULLISH"

                event_type = (
                    "CHOCH"
                    if previous_bias == "BEARISH"
                    else "BOS"
                )

                events.append({
                    "time": candle["Datetime"],
                    "type": event_type,
                    "direction": "BULLISH",
                    "level": level,
                })

                bullish_break_reference = None

        if bearish_break_reference is not None:
            level = bearish_break_reference["price"]

            if (
                index > bearish_break_reference["index"]
                and low < level
            ):
                previous_bias = current_bias

                current_bias = "BEARISH"

                event_type = (
                    "CHOCH"
                    if previous_bias == "BULLISH"
                    else "BOS"
                )

                events.append({
                    "time": candle["Datetime"],
                    "type": event_type,
                    "direction": "BEARISH",
                    "level": level,
                })

                bearish_break_reference = None

    return events[-30:]


# ============================================================
# IMPROVED STRUCTURE EVENT ENGINE
# ============================================================

def latest_structure_event(df, classified):
    if df.empty or not classified:
        return None

    events = []

    last_price = float(df.iloc[-1]["close"])

    highs = [
        point for point in classified
        if point["type"] == "HIGH"
    ]

    lows = [
        point for point in classified
        if point["type"] == "LOW"
    ]

    if highs:
        latest_high = highs[-1]

        if last_price > latest_high["price"]:
            events.append({
                "time": df.iloc[-1]["Datetime"],
                "type": "BOS",
                "direction": "BULLISH",
                "level": latest_high["price"],
            })

    if lows:
        latest_low = lows[-1]

        if last_price < latest_low["price"]:
            events.append({
                "time": df.iloc[-1]["Datetime"],
                "type": "BOS",
                "direction": "BEARISH",
                "level": latest_low["price"],
            })

    if not events:
        return None

    return events[-1]


# ============================================================
# STRUCTURE SUMMARY
# ============================================================

def build_structure_summary(df):
    if df.empty:
        return {
            "bias": "NEUTRAL",
            "swings": [],
            "classified": [],
            "events": [],
            "latest_event": None,
            "last_high": None,
            "last_low": None,
        }

    swings = find_swings(df)
    classified = classify_swings(swings)

    bias = determine_structure_bias(classified)

    events = detect_structure_events(
        df,
        classified,
    )

    latest_event = latest_structure_event(
        df,
        classified,
    )

    highs = [
        point for point in classified
        if point["type"] == "HIGH"
    ]

    lows = [
        point for point in classified
        if point["type"] == "LOW"
    ]

    return {
        "bias": bias,
        "swings": swings,
        "classified": classified,
        "events": events,
        "latest_event": latest_event,
        "last_high": highs[-1] if highs else None,
        "last_low": lows[-1] if lows else None,
    }


# ============================================================
# M15 BIAS + M5 STRUCTURE
# ============================================================

def build_all_structure():
    m15_df = state_to_dataframe("m15_bars")
    m5_df = state_to_dataframe("m5_bars")

    m15_structure = build_structure_summary(m15_df)
    m5_structure = build_structure_summary(m5_df)

    return m15_df, m5_df, m15_structure, m5_structure


# ============================================================
# DATA HEALTH
# ============================================================

def data_health():
    m5_df = state_to_dataframe("m5_bars")
    m15_df = state_to_dataframe("m15_bars")

    health = {
        "IG": st.session_state.get("ig_connected", False),
        "M5": len(m5_df) > 10,
        "M15": len(m15_df) > 10,
        "Live": st.session_state.get("live_mid") is not None,
        "Bootstrap": st.session_state.get(
            "bootstrap_complete",
            False,
        ),
    }

    return health


# ============================================================
# UI HELPERS
# ============================================================

def structure_badge(value):
    if value == "BULLISH":
        return "🟢 BULLISH"

    if value == "BEARISH":
        return "🔴 BEARISH"

    return "⚪ NEUTRAL"


def format_price(value):
    if value is None:
        return "—"

    return f"{float(value):,.2f}"


def render_structure_table(classified):
    if not classified:
        st.info("Waiting for enough confirmed swing points.")
        return

    rows = []

    for point in reversed(classified[-15:]):
        rows.append({
            "Time": pd.Timestamp(
                point["time"]
            ).strftime("%H:%M"),
            "Type": point["type"],
            "Structure": point["label"],
            "Price": round(
                float(point["price"]),
                2,
            ),
        })

    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
    )


def render_event_table(events):
    if not events:
        st.info("No BOS/CHOCH events detected yet.")
        return

    rows = []

    for event in reversed(events[-12:]):
        rows.append({
            "Time": pd.Timestamp(
                event["time"]
            ).strftime("%H:%M"),
            "Event": event["type"],
            "Direction": event["direction"],
            "Level": round(
                float(event["level"]),
                2,
            ),
        })

    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
    )


# ============================================================
# INITIAL CONNECTION / BOOTSTRAP
# ============================================================

def initialize_engine():
    try:
        if not st.session_state["ig_connected"]:
            ig_login()

        get_ig_market()

        if not st.session_state["bootstrap_complete"]:
            bootstrap_market_data()

        st.session_state["last_error"] = None

    except Exception as exc:
        st.session_state["last_error"] = str(exc)


# ============================================================
# LIVE ENGINE LOOP
# ============================================================

@st.fragment(run_every=POLL_SECONDS)
def live_engine():
    if not st.session_state["engine_running"]:
        return

    try:
        if not st.session_state["ig_connected"]:
            ig_login()

        mid = get_ig_market()

        if not st.session_state["bootstrap_complete"]:
            bootstrap_market_data()

        update_live_candles(mid)

        st.session_state["last_update"] = utc_now()
        st.session_state["last_error"] = None

    except Exception as exc:
        st.session_state["last_error"] = str(exc)

    render_dashboard()


# ============================================================
# DASHBOARD
# ============================================================

def render_dashboard():
    m15_df, m5_df, m15_structure, m5_structure = (
        build_all_structure()
    )

    health = data_health()

    st.title("🐎 Quantum X V2.2")
    st.caption(
        "Market Structure Engine — M15 Bias + M5 Execution"
    )

    # --------------------------------------------------------
    # LIVE STATUS
    # --------------------------------------------------------

    st.subheader("📊 Live Status")

    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric(
            "IG Demo",
            "CONNECTED"
            if health["IG"]
            else "DISCONNECTED",
        )

    with col2:
        st.metric(
            "XAU/USD",
            format_price(
                st.session_state.get("live_mid")
            ),
        )

    with col3:
        st.metric(
            "M5 candles",
            len(m5_df),
        )

    with col4:
        st.metric(
            "M15 candles",
            len(m15_df),
        )

    with col5:
        st.metric(
            "Market",
            st.session_state.get(
                "market_status",
                "UNKNOWN",
            ),
        )

    # --------------------------------------------------------
    # STRUCTURE BIAS
    # --------------------------------------------------------

    st.subheader("🧠 Market Structure")

    bias_col1, bias_col2 = st.columns(2)

    with bias_col1:
        st.markdown("### M15 Bias")
        st.markdown(
            f"## {structure_badge(m15_structure['bias'])}"
        )

        latest_m15_event = m15_structure["latest_event"]

        if latest_m15_event:
            st.write(
                f"Latest structure event: "
                f"**{latest_m15_event['type']} "
                f"{latest_m15_event['direction']}** "
                f"@ {format_price(latest_m15_event['level'])}"
            )
        else:
            st.write(
                "No current BOS/CHOCH event."
            )

    with bias_col2:
        st.markdown("### M5 Execution Structure")
        st.markdown(
            f"## {structure_badge(m5_structure['bias'])}"
        )

        latest_m5_event = m5_structure["latest_event"]

        if latest_m5_event:
            st.write(
                f"Latest structure event: "
                f"**{latest_m5_event['type']} "
                f"{latest_m5_event['direction']}** "
                f"@ {format_price(latest_m5_event['level'])}"
            )
        else:
            st.write(
                "No current BOS/CHOCH event."
            )

    # --------------------------------------------------------
    # STRUCTURAL LEVELS
    # --------------------------------------------------------

    st.subheader("📐 Structural Levels")

    level1, level2, level3, level4 = st.columns(4)

    with level1:
        m15_high = m15_structure["last_high"]
        st.metric(
            "M15 Swing High",
            format_price(
                m15_high["price"]
                if m15_high
                else None
            ),
        )

    with level2:
        m15_low = m15_structure["last_low"]
        st.metric(
            "M15 Swing Low",
            format_price(
                m15_low["price"]
                if m15_low
                else None
            ),
        )

    with level3:
        m5_high = m5_structure["last_high"]
        st.metric(
            "M5 Swing High",
            format_price(
                m5_high["price"]
                if m5_high
                else None
            ),
        )

    with level4:
        m5_low = m5_structure["last_low"]
        st.metric(
            "M5 Swing Low",
            format_price(
                m5_low["price"]
                if m5_low
                else None
            ),
        )

    # --------------------------------------------------------
    # STRUCTURE TABLES
    # --------------------------------------------------------

    tab1, tab2, tab3 = st.tabs([
        "M15 Structure",
        "M5 Structure",
        "BOS / CHOCH",
    ])

    with tab1:
        st.markdown(
            "### M15 confirmed swing structure"
        )

        render_structure_table(
            m15_structure["classified"]
        )

    with tab2:
        st.markdown(
            "### M5 confirmed swing structure"
        )

        render_structure_table(
            m5_structure["classified"]
        )

    with tab3:
        st.markdown(
            "### Recent M15 structure events"
        )

        render_event_table(
            m15_structure["events"]
        )

        st.markdown(
            "### Recent M5 structure events"
        )

        render_event_table(
            m5_structure["events"]
        )

    # --------------------------------------------------------
    # ENGINE STATUS
    # --------------------------------------------------------

    st.subheader("📡 Data Engine")

    status_cols = st.columns(5)

    labels = [
        ("IG", health["IG"]),
        ("M5", health["M5"]),
        ("M15", health["M15"]),
        ("Live", health["Live"]),
        ("Bootstrap", health["Bootstrap"]),
    ]

    for column, (label, status) in zip(
        status_cols,
        labels,
    ):
        with column:
            st.metric(
                label,
                "OK" if status else "WAIT",
            )

    # --------------------------------------------------------
    # CALIBRATION
    # --------------------------------------------------------

    offset = st.session_state.get(
        "calibration_offset",
        0.0,
    )

    st.caption(
        f"Yahoo GC=F calibration offset: "
        f"{offset:+.2f}"
    )

    # --------------------------------------------------------
    # ENGINE WARNING
    # --------------------------------------------------------

    st.info(
        "V2.2 is analysis-only. "
        "Automatic trading is intentionally OFF."
    )

    # --------------------------------------------------------
    # ERROR
    # --------------------------------------------------------

    error = st.session_state.get("last_error")

    if error:
        st.error(
            f"Engine error: {error}"
        )

    last_update = st.session_state.get(
        "last_update"
    )

    if last_update:
        st.caption(
            "Last engine update: "
            + pd.Timestamp(
                last_update
            ).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )
        )


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:
    st.header("🐎 Quantum X V2.2")

    st.markdown(
        "**Market Structure Engine**"
    )

    st.divider()

    if st.button(
        "🔄 Refresh Data",
        use_container_width=True,
    ):
        st.session_state["bootstrap_complete"] = False
        st.session_state["m5_bars"] = []
        st.session_state["m15_bars"] = []

        st.rerun()

    st.divider()

    st.markdown("### Engine")

    st.write(
        "M15 → Directional Bias"
    )

    st.write(
        "M5 → Execution Structure"
    )

    st.write(
        "HH / HL / LH / LL"
    )

    st.write(
        "BOS / CHOCH"
    )

    st.divider()

    st.warning(
        "AUTO TRADING: OFF"
    )

    st.caption(
        "V2.2 builds the Monster's "
        "market-structure brain."
    )


# ============================================================
# START
# ============================================================

initialize_engine()

live_engine()
