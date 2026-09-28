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
# ------------------------------------------------------------
# HISTORICAL DATA BOOTSTRAP
# ------------------------------------------------------------

def bootstrap_data():
    started = time.time()

    st.session_state[
        "bootstrap_started_at"
    ] = utc_now()

    st.session_state[
        "last_error"
    ] = None

    st.session_state[
        "bootstrap_complete"
    ] = False

    if not st.session_state[
        "ig_connected"
    ]:
        ig_login()

    market = get_ig_market()

    m5 = fetch_yahoo_m5()

    m15 = fetch_yahoo_m15()

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

    st.session_state[
        "m5_bars"
    ] = m5

    st.session_state[
        "m15_bars"
    ] = m15

    update_live_candles(
        market["mid"],
        create_if_missing=True,
    )

    elapsed = time.time() - started

    if elapsed > MAX_STARTUP_SECONDS:
        raise RuntimeError(
            f"Data bootstrap exceeded the "
            f"{MAX_STARTUP_SECONDS // 60}-minute "
            f"startup target."
        )

    st.session_state[
        "bootstrap_completed_at"
    ] = utc_now()

    st.session_state[
        "bootstrap_complete"
    ] = True

    st.session_state[
        "engine_running"
    ] = True

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

    m5_bucket = floor_time(
        now,
        M5,
    )

    m15_bucket = floor_time(
        now,
        M15,
    )

    # --------------------------------------------------------
    # M5
    # --------------------------------------------------------

    m5_df = st.session_state[
        "m5_bars"
    ].copy()

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

        row = m5_df.loc[
            m5_bucket
        ].copy()

        row["high"] = max(
            float(row["high"]),
            float(live_price),
        )

        row["low"] = min(
            float(row["low"]),
            float(live_price),
        )

        row["close"] = float(
            live_price
        )

        m5_df.loc[
            m5_bucket
        ] = row

    elif create_if_missing:

        m5_df.loc[
            m5_bucket
        ] = {
            "open": float(
                live_price
            ),
            "high": float(
                live_price
            ),
            "low": float(
                live_price
            ),
            "close": float(
                live_price
            ),
            "volume": 0.0,
        }

    m5_df = (
        m5_df
        .sort_index()
        .tail(MAX_M5_BARS)
    )

    st.session_state[
        "m5_bars"
    ] = m5_df

    # --------------------------------------------------------
    # M15
    # --------------------------------------------------------

    m15_df = st.session_state[
        "m15_bars"
    ].copy()

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

        row = m15_df.loc[
            m15_bucket
        ].copy()

        row["high"] = max(
            float(row["high"]),
            float(live_price),
        )

        row["low"] = min(
            float(row["low"]),
            float(live_price),
        )

        row["close"] = float(
            live_price
        )

        m15_df.loc[
            m15_bucket
        ] = row

    elif create_if_missing:

        m15_df.loc[
            m15_bucket
        ] = {
            "open": float(
                live_price
            ),
            "high": float(
                live_price
            ),
            "low": float(
                live_price
            ),
            "close": float(
                live_price
            ),
            "volume": 0.0,
        }

    m15_df = (
        m15_df
        .sort_index()
        .tail(MAX_M15_BARS)
    )

    st.session_state[
        "m15_bars"
    ] = m15_df


# ------------------------------------------------------------
# LIVE UPDATE
# ------------------------------------------------------------

def live_data_tick():

    if not st.session_state[
        "ig_connected"
    ]:
        return

    market = get_ig_market()

    update_live_candles(
        market["mid"],
        create_if_missing=True,
    )


# ============================================================
# V2.2 MARKET STRUCTURE ENGINE
# ============================================================
#
# Structure is calculated from COMPLETED candles only.
#
# The current forming candle is excluded so that temporary
# intrabar movement does not immediately become structure.
#
# M15 will later provide directional context.
# M5 will later provide execution structure.
#
# NO TRADING LOGIC EXISTS HERE.
# ============================================================


# ------------------------------------------------------------
# COMPLETED TIMEFRAME DATA
# ------------------------------------------------------------

def get_completed_timeframe_data(
    df,
    timeframe,
    max_rows=None,
):
    if df is None or df.empty:
        return pd.DataFrame()

    result = df.copy()

    result = result.sort_index()

    current_bucket = floor_time(
        utc_now(),
        timeframe,
    )

    # Only completed candles are allowed
    # into the structure engine.
    result = result[
        result.index < current_bucket
    ].copy()

    if max_rows is not None:
        result = result.tail(
            int(max_rows)
        ).copy()

    return result


# ------------------------------------------------------------
# SWING DETECTION
# ------------------------------------------------------------

def detect_swing_points(
    df,
    left=STRUCTURE_SWING_LEFT,
    right=STRUCTURE_SWING_RIGHT,
):
    if df is None or df.empty:
        return pd.DataFrame(
            columns=[
                "time",
                "type",
                "price",
            ]
        )

    minimum_bars = (
        left
        + right
        + 1
    )

    if len(df) < minimum_bars:
        return pd.DataFrame(
            columns=[
                "time",
                "type",
                "price",
            ]
        )

    source = df.copy()

    source = source.sort_index()

    highs = source[
        "high"
    ].astype(float)

    lows = source[
        "low"
    ].astype(float)

    records = []

    for i in range(
        left,
        len(source) - right,
    ):
        current_high = float(
            highs.iloc[i]
        )

        current_low = float(
            lows.iloc[i]
        )

        left_highs = highs.iloc[
            i - left:i
        ]

        right_highs = highs.iloc[
            i + 1:i + 1 + right
        ]

        left_lows = lows.iloc[
            i - left:i
        ]

        right_lows = lows.iloc[
            i + 1:i + 1 + right
        ]

        is_swing_high = (
            current_high
            >= left_highs.max()
            and current_high
            >= right_highs.max()
        )

        is_swing_low = (
            current_low
            <= left_lows.min()
            and current_low
            <= right_lows.min()
        )

        timestamp = source.index[i]

        if is_swing_high:
            records.append(
                {
                    "time": timestamp,
                    "type": "HIGH",
                    "price": current_high,
                }
            )

        if is_swing_low:
            records.append(
                {
                    "time": timestamp,
                    "type": "LOW",
                    "price": current_low,
                }
            )

    if not records:
        return pd.DataFrame(
            columns=[
                "time",
                "type",
                "price",
            ]
        )

    result = pd.DataFrame(
        records
    )

    result = result.sort_values(
        [
            "time",
            "type",
        ]
    ).reset_index(
        drop=True
    )

    return result


# ------------------------------------------------------------
# SWING CLASSIFICATION
# ------------------------------------------------------------

def classify_swings(
    swing_df,
):
    if swing_df is None or swing_df.empty:
        return pd.DataFrame(
            columns=[
                "time",
                "type",
                "price",
                "label",
            ]
        )

    result = swing_df.copy()

    result["label"] = ""

    previous_high = None
    previous_low = None

    for i in range(
        len(result)
    ):
        swing_type = str(
            result.at[
                i,
                "type",
            ]
        )

        price = float(
            result.at[
                i,
                "price",
            ]
        )

        if swing_type == "HIGH":

            if previous_high is None:
                label = "HIGH"

            elif price > previous_high:
                label = "HH"

            else:
                label = "LH"

            previous_high = price

        else:

            if previous_low is None:
                label = "LOW"

            elif price > previous_low:
                label = "HL"

            else:
                label = "LL"

            previous_low = price

        result.at[
            i,
            "label",
        ] = label

    return result


# ------------------------------------------------------------
# LATEST SWING HELPERS
# ------------------------------------------------------------

def latest_swing_of_type(
    swings,
    swing_type,
):
    if swings is None or swings.empty:
        return None

    subset = swings[
        swings["type"]
        == swing_type
    ]

    if subset.empty:
        return None

    row = subset.iloc[-1]

    return {
        "time": row["time"],
        "price": float(
            row["price"]
        ),
        "label": str(
            row["label"]
        ),
    }


# ------------------------------------------------------------
# BASIC STRUCTURE BIAS
# ------------------------------------------------------------

def determine_structure_bias(
    swings,
):
    if swings is None or swings.empty:
        return "NEUTRAL"

    recent = swings.tail(
        8
    ).copy()

    labels = (
        recent["label"]
        .astype(str)
        .tolist()
    )

    bullish_labels = {
        "HH",
        "HL",
    }

    bearish_labels = {
        "LH",
        "LL",
    }

    bullish_count = sum(
        label in bullish_labels
        for label in labels
    )

    bearish_count = sum(
        label in bearish_labels
        for label in labels
    )

    if bullish_count > bearish_count:
        return "BULLISH"

    if bearish_count > bullish_count:
        return "BEARISH"

    return "NEUTRAL"


# ------------------------------------------------------------
# STRUCTURE SNAPSHOT
# ------------------------------------------------------------

def build_structure_snapshot(
    df,
    timeframe,
    lookback,
):
    completed = (
        get_completed_timeframe_data(
            df,
            timeframe,
            max_rows=lookback,
        )
    )

    if completed.empty:
        return {
            "candles": pd.DataFrame(),
            "swings": pd.DataFrame(),
            "bias": "NEUTRAL",
            "last_event": "NONE",
            "last_event_time": None,
            "last_event_level": None,
        }

    swings = detect_swing_points(
        completed
    )

    classified = classify_swings(
        swings
    )

    bias = determine_structure_bias(
        classified
    )

    return {
        "candles": completed,
        "swings": classified,
        "bias": bias,
        "last_event": "NONE",
        "last_event_time": None,
        "last_event_level": None,
    }


# ------------------------------------------------------------
# M5 STRUCTURE DATA
# ------------------------------------------------------------

def get_m5_structure_data():

    return build_structure_snapshot(
        st.session_state[
            "m5_bars"
        ],
        M5,
        STRUCTURE_LOOKBACK_M5,
    )


# ------------------------------------------------------------
# M15 STRUCTURE DATA
# ------------------------------------------------------------

def get_m15_structure_data():

    return build_structure_snapshot(
        st.session_state[
            "m15_bars"
        ],
        M15,
        STRUCTURE_LOOKBACK_M15,
        )
# ============================================================
# QUANTUM X V2.2 — HALF 2
# MARKET STRUCTURE EVENTS + ANALYSIS
# ============================================================


# ------------------------------------------------------------
# STRUCTURE EVENT DETECTION
# ------------------------------------------------------------

def detect_structure_events(
    completed_df,
    swings,
):
    columns = [
        "time",
        "event",
        "direction",
        "price",
        "broken_level",
    ]

    if (
        completed_df is None
        or completed_df.empty
        or swings is None
        or swings.empty
    ):
        return pd.DataFrame(
            columns=columns
        )

    source = completed_df.copy()
    source = source.sort_index()

    swing_highs = swings[
        swings["type"] == "HIGH"
    ].copy()

    swing_lows = swings[
        swings["type"] == "LOW"
    ].copy()

    events = []

    previous_bias = "NEUTRAL"

    last_high_level = None
    last_low_level = None

    last_high_time = None
    last_low_time = None

    broken_high_times = set()
    broken_low_times = set()

    for timestamp, candle in source.iterrows():

        close_price = float(
            candle["close"]
        )

        # ----------------------------------------------------
        # Find the latest confirmed swing high before this
        # candle.
        # ----------------------------------------------------

        available_highs = swing_highs[
            swing_highs["time"] < timestamp
        ]

        if not available_highs.empty:

            latest_high = available_highs.iloc[-1]

            candidate_time = latest_high[
                "time"
            ]

            candidate_price = float(
                latest_high[
                    "price"
                ]
            )

            if (
                candidate_time
                != last_high_time
            ):
                last_high_time = candidate_time
                last_high_level = candidate_price

        # ----------------------------------------------------
        # Find the latest confirmed swing low before this
        # candle.
        # ----------------------------------------------------

        available_lows = swing_lows[
            swing_lows["time"] < timestamp
        ]

        if not available_lows.empty:

            latest_low = available_lows.iloc[-1]

            candidate_time = latest_low[
                "time"
            ]

            candidate_price = float(
                latest_low[
                    "price"
                ]
            )

            if (
                candidate_time
                != last_low_time
            ):
                last_low_time = candidate_time
                last_low_level = candidate_price

        # ----------------------------------------------------
        # BULLISH STRUCTURE BREAK
        # ----------------------------------------------------

        if (
            last_high_level is not None
            and last_high_time not in broken_high_times
            and close_price > last_high_level
        ):

            if previous_bias == "BEARISH":
                event_name = "CHOCH"
            else:
                event_name = "BOS"

            events.append(
                {
                    "time": timestamp,
                    "event": event_name,
                    "direction": "BULLISH",
                    "price": close_price,
                    "broken_level": last_high_level,
                }
            )

            previous_bias = "BULLISH"

            broken_high_times.add(
                last_high_time
            )

        # ----------------------------------------------------
        # BEARISH STRUCTURE BREAK
        # ----------------------------------------------------

        elif (
            last_low_level is not None
            and last_low_time not in broken_low_times
            and close_price < last_low_level
        ):

            if previous_bias == "BULLISH":
                event_name = "CHOCH"
            else:
                event_name = "BOS"

            events.append(
                {
                    "time": timestamp,
                    "event": event_name,
                    "direction": "BEARISH",
                    "price": close_price,
                    "broken_level": last_low_level,
                }
            )

            previous_bias = "BEARISH"

            broken_low_times.add(
                last_low_time
            )

    if not events:
        return pd.DataFrame(
            columns=columns
        )

    result = pd.DataFrame(
        events
    )

    result = result.drop_duplicates(
        subset=[
            "time",
            "event",
            "direction",
        ],
        keep="last",
    )

    result = result.sort_values(
        "time"
    ).reset_index(
        drop=True
    )

    return result


# ------------------------------------------------------------
# STRUCTURE ANALYSIS
# ------------------------------------------------------------

def analyze_market_structure(
    df,
    timeframe,
    lookback,
):
    completed = (
        get_completed_timeframe_data(
            df,
            timeframe,
            max_rows=lookback,
        )
    )

    if completed.empty:
        return {
            "completed": pd.DataFrame(),
            "swings": pd.DataFrame(),
            "events": pd.DataFrame(),
            "bias": "NEUTRAL",
            "last_event": "NONE",
            "last_event_direction": "NONE",
            "last_event_time": None,
            "last_event_level": None,
        }

    swings = detect_swing_points(
        completed,
        left=STRUCTURE_SWING_LEFT,
        right=STRUCTURE_SWING_RIGHT,
    )

    classified = classify_swings(
        swings
    )

    events = detect_structure_events(
        completed,
        classified,
    )

    # --------------------------------------------------------
    # Start with swing-based bias.
    # --------------------------------------------------------

    bias = determine_structure_bias(
        classified
    )

    last_event = "NONE"
    last_event_direction = "NONE"
    last_event_time = None
    last_event_level = None

    # --------------------------------------------------------
    # A confirmed BOS/CHOCH gets priority over the basic
    # swing-count bias.
    # --------------------------------------------------------

    if not events.empty:

        last_event_row = events.iloc[-1]

        last_event = str(
            last_event_row[
                "event"
            ]
        )

        last_event_direction = str(
            last_event_row[
                "direction"
            ]
        )

        last_event_time = (
            last_event_row[
                "time"
            ]
        )

        last_event_level = float(
            last_event_row[
                "broken_level"
            ]
        )

        bias = last_event_direction

    return {
        "completed": completed,
        "swings": classified,
        "events": events,
        "bias": bias,
        "last_event": last_event,
        "last_event_direction": last_event_direction,
        "last_event_time": last_event_time,
        "last_event_level": last_event_level,
    }


# ------------------------------------------------------------
# M5 STRUCTURE ANALYSIS
# ------------------------------------------------------------

def get_m5_structure():

    return analyze_market_structure(
        st.session_state[
            "m5_bars"
        ],
        M5,
        STRUCTURE_LOOKBACK_M5,
    )


# ------------------------------------------------------------
# M15 STRUCTURE ANALYSIS
# ------------------------------------------------------------

def get_m15_structure():

    return analyze_market_structure(
        st.session_state[
            "m15_bars"
        ],
        M15,
        STRUCTURE_LOOKBACK_M15,
    )


# ------------------------------------------------------------
# M5 + M15 ALIGNMENT
# ------------------------------------------------------------

def get_structure_alignment(
    m5_structure,
    m15_structure,
):
    m5_bias = str(
        m5_structure.get(
            "bias",
            "NEUTRAL",
        )
    )

    m15_bias = str(
        m15_structure.get(
            "bias",
            "NEUTRAL",
        )
    )

    if (
        m5_bias == "BULLISH"
        and m15_bias == "BULLISH"
    ):
        return "BULLISH ALIGNMENT"

    if (
        m5_bias == "BEARISH"
        and m15_bias == "BEARISH"
    ):
        return "BEARISH ALIGNMENT"

    if (
        m5_bias == "NEUTRAL"
        or m15_bias == "NEUTRAL"
    ):
        return "MIXED / WAIT"

    return "M5 / M15 CONFLICT"


# ------------------------------------------------------------
# STRUCTURE DISPLAY TABLE
# ------------------------------------------------------------

def prepare_structure_table(
    swings,
    rows=12,
):
    if swings is None or swings.empty:
        return pd.DataFrame()

    result = swings.tail(
        rows
    ).copy()

    result["time"] = pd.to_datetime(
        result["time"],
        utc=True,
    ).dt.strftime(
        "%H:%M:%S"
    )

    result["price"] = (
        result["price"]
        .astype(float)
        .round(2)
    )

    result = result[
        [
            "time",
            "type",
            "price",
            "label",
        ]
    ]

    return result


# ------------------------------------------------------------
# STRUCTURE EVENT TABLE
# ------------------------------------------------------------

def prepare_event_table(
    events,
    rows=10,
):
    if events is None or events.empty:
        return pd.DataFrame()

    result = events.tail(
        rows
    ).copy()

    result["time"] = pd.to_datetime(
        result["time"],
        utc=True,
    ).dt.strftime(
        "%H:%M:%S"
    )

    result["price"] = (
        result["price"]
        .astype(float)
        .round(2)
    )

    result["broken_level"] = (
        result["broken_level"]
        .astype(float)
        .round(2)
    )

    result = result[
        [
            "time",
            "event",
            "direction",
            "price",
            "broken_level",
        ]
    ]

    return result


# ------------------------------------------------------------
# STRUCTURE STATUS TEXT
# ------------------------------------------------------------

def structure_bias_text(
    bias,
):
    if bias == "BULLISH":
        return "🟢 BULLISH"

    if bias == "BEARISH":
        return "🔴 BEARISH"

    return "🟡 NEUTRAL"


def structure_event_text(
    event,
    direction,
):
    if event == "BOS":
        if direction == "BULLISH":
            return "🟢 Bullish BOS"

        if direction == "BEARISH":
            return "🔴 Bearish BOS"

        return "BOS"

    if event == "CHOCH":
        if direction == "BULLISH":
            return "🟢 Bullish CHOCH"

        if direction == "BEARISH":
            return "🔴 Bearish CHOCH"

        return "CHOCH"

    return "NONE"


# ------------------------------------------------------------
# STRUCTURE SUMMARY
# ------------------------------------------------------------

def get_structure_summary(
    structure,
):
    if structure is None:
        return {
            "bias": "NEUTRAL",
            "event": "NONE",
            "direction": "NONE",
            "event_time": None,
            "level": None,
            "swing_count": 0,
            "event_count": 0,
        }

    swings = structure.get(
        "swings",
        pd.DataFrame(),
    )

    events = structure.get(
        "events",
        pd.DataFrame(),
    )

    return {
        "bias": structure.get(
            "bias",
            "NEUTRAL",
        ),
        "event": structure.get(
            "last_event",
            "NONE",
        ),
        "direction": structure.get(
            "last_event_direction",
            "NONE",
        ),
        "event_time": structure.get(
            "last_event_time",
            None,
        ),
        "level": structure.get(
            "last_event_level",
            None,
        ),
        "swing_count": len(
            swings
        ),
        "event_count": len(
            events
        ),
    }


# ------------------------------------------------------------
# DATA HEALTH
# ------------------------------------------------------------

def get_data_health():
    m5 = st.session_state[
        "m5_bars"
    ]

    m15 = st.session_state[
        "m15_bars"
    ]

    now = utc_now()

    m5_count = len(m5)

    m15_count = len(m15)

    m5_current = (
        floor_time(
            now,
            M5,
        )
        if not m5.empty
        else None
    )

    m15_current = (
        floor_time(
            now,
            M15,
        )
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
        "ig_mid": st.session_state[
            "live_mid"
        ],
        "offset": st.session_state[
            "calibration_offset"
        ],
        "market_status": st.session_state[
            "market_status"
        ],
    }


# ------------------------------------------------------------
# DATAFRAME DISPLAY HELPER
# ------------------------------------------------------------

def style_dataframe(
    df,
    rows=20,
):
    if df is None or df.empty:
        return pd.DataFrame()

    display_df = df.tail(
        rows
    ).copy()

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
# ============================================================
# QUANTUM X V2.2 — HALF 2B
# STRUCTURE DISPLAY + LIVE STATUS
# ============================================================


# ------------------------------------------------------------
# STATUS CARDS
# ------------------------------------------------------------

def render_status_cards():

    health = get_data_health()

    col1, col2, col3, col4 = st.columns(4)

    with col1:

        if st.session_state[
            "ig_connected"
        ]:

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
            format_price(
                health["ig_mid"]
            ),
        )

    with col3:

        st.metric(
            "M5 candles",
            str(
                health["m5_count"]
            ),
        )

    with col4:

        st.metric(
            "M15 candles",
            str(
                health["m15_count"]
            ),
        )


# ------------------------------------------------------------
# DATA ENGINE DETAILS
# ------------------------------------------------------------

def render_data_details():

    health = get_data_health()

    st.subheader(
        "📡 Data Engine"
    )

    col1, col2 = st.columns(2)

    with col1:

        st.write(
            "**IG live market**"
        )

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

        st.write(
            "**Yahoo structure proxy**"
        )

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

        st.write(
            "**M5**"
        )

        st.write(
            f"Latest candle: `{format_timestamp(health['m5_latest'])}`"
        )

        st.write(
            f"Current bucket: `{format_timestamp(health['m5_current'])}`"
        )

    with col2:

        st.write(
            "**M15**"
        )

        st.write(
            f"Latest candle: `{format_timestamp(health['m15_latest'])}`"
        )

        st.write(
            f"Current bucket: `{format_timestamp(health['m15_current'])}`"
        )

    st.write(
        f"Last IG update: "
        f"`{format_timestamp(st.session_state['last_update'])}`"
    )

    st.write(
        f"Bootstrap completed: "
        f"`{format_timestamp(st.session_state['bootstrap_completed_at'])}`"
    )


# ============================================================
# V2.2 STRUCTURE DASHBOARD
# ============================================================

def render_structure_dashboard():

    m5_structure = get_m5_structure()

    m15_structure = get_m15_structure()

    alignment = get_structure_alignment(
        m5_structure,
        m15_structure,
    )

    m5_summary = get_structure_summary(
        m5_structure
    )

    m15_summary = get_structure_summary(
        m15_structure
    )

    st.subheader(
        "🧠 Market Structure"
    )

    # --------------------------------------------------------
    # TOP STRUCTURE CARDS
    # --------------------------------------------------------

    col1, col2, col3 = st.columns(3)

    with col1:

        st.metric(
            "M15 Bias",
            structure_bias_text(
                m15_summary["bias"]
            ),
        )

    with col2:

        st.metric(
            "M5 Structure",
            structure_bias_text(
                m5_summary["bias"]
            ),
        )

    with col3:

        st.metric(
            "M5 / M15",
            alignment,
        )

    st.divider()

    # --------------------------------------------------------
    # LAST STRUCTURE EVENTS
    # --------------------------------------------------------

    col1, col2 = st.columns(2)

    with col1:

        st.write(
            "**M15 Structure Event**"
        )

        st.write(
            structure_event_text(
                m15_summary["event"],
                m15_summary["direction"],
            )
        )

        if m15_summary[
            "event_time"
        ] is not None:

            st.write(
                "Time: "
                f"`{format_timestamp(m15_summary['event_time'])}`"
            )

        if m15_summary[
            "level"
        ] is not None:

            st.write(
                "Broken level: "
                f"`{format_price(m15_summary['level'])}`"
            )

    with col2:

        st.write(
            "**M5 Structure Event**"
        )

        st.write(
            structure_event_text(
                m5_summary["event"],
                m5_summary["direction"],
            )
        )

        if m5_summary[
            "event_time"
        ] is not None:

            st.write(
                "Time: "
                f"`{format_timestamp(m5_summary['event_time'])}`"
            )

        if m5_summary[
            "level"
        ] is not None:

            st.write(
                "Broken level: "
                f"`{format_price(m5_summary['level'])}`"
            )

    st.divider()

    # --------------------------------------------------------
    # M15 SWINGS
    # --------------------------------------------------------

    st.write(
        "### 🕐 M15 Swing Structure"
    )

    m15_swings = prepare_structure_table(
        m15_structure["swings"],
        rows=12,
    )

    if not m15_swings.empty:

        st.dataframe(
            m15_swings,
            use_container_width=True,
            hide_index=True,
        )

    else:

        st.info(
            "Not enough completed M15 candles "
            "to establish swing structure."
        )

    # --------------------------------------------------------
    # M15 EVENTS
    # --------------------------------------------------------

    st.write(
        "### 🧭 M15 BOS / CHOCH"
    )

    m15_events = prepare_event_table(
        m15_structure["events"],
        rows=10,
    )

    if not m15_events.empty:

        st.dataframe(
            m15_events,
            use_container_width=True,
            hide_index=True,
        )

    else:

        st.info(
            "No confirmed M15 BOS/CHOCH events yet."
        )

    st.divider()

    # --------------------------------------------------------
    # M5 SWINGS
    # --------------------------------------------------------

    st.write(
        "### 🕐 M5 Swing Structure"
    )

    m5_swings = prepare_structure_table(
        m5_structure["swings"],
        rows=15,
    )

    if not m5_swings.empty:

        st.dataframe(
            m5_swings,
            use_container_width=True,
            hide_index=True,
        )

    else:

        st.info(
            "Not enough completed M5 candles "
            "to establish swing structure."
        )

    # --------------------------------------------------------
    # M5 EVENTS
    # --------------------------------------------------------

    st.write(
        "### 🧭 M5 BOS / CHOCH"
    )

    m5_events = prepare_event_table(
        m5_structure["events"],
        rows=12,
    )

    if not m5_events.empty:

        st.dataframe(
            m5_events,
            use_container_width=True,
            hide_index=True,
        )

    else:

        st.info(
            "No confirmed M5 BOS/CHOCH events yet."
        )


# ============================================================
# LIVE CANDLE TABLES
# ============================================================

def render_live_candle_tables():

    st.subheader(
        "🕯️ Live M5"
    )

    m5_display = style_dataframe(
        st.session_state[
            "m5_bars"
        ],
        rows=20,
    )

    if not m5_display.empty:

        st.dataframe(
            m5_display,
            use_container_width=True,
        )

    else:

        st.warning(
            "No M5 candles available."
        )

    st.subheader(
        "🕯️ Live M15"
    )

    m15_display = style_dataframe(
        st.session_state[
            "m15_bars"
        ],
        rows=15,
    )

    if not m15_display.empty:

        st.dataframe(
            m15_display,
            use_container_width=True,
        )

    else:

        st.warning(
            "No M15 candles available."
        )


# ============================================================
# MAIN PAGE
# ============================================================

st.title(
    "🐎 Quantum X V2.2"
)

st.caption(
    "Market Structure Layer — "
    "M5 execution structure + M15 directional context."
)

st.info(
    "V2.2 is a structure-analysis milestone. "
    "Automatic trading is DISABLED."
)


# ------------------------------------------------------------
# SIDEBAR
# ------------------------------------------------------------

with st.sidebar:

    st.header(
        "Quantum X V2.2"
    )

    st.write(
        "Branch: `quantum-x-v2`"
    )

    st.divider()

    connect_clicked = st.button(
        "🔌 Connect & Bootstrap",
        use_container_width=True,
        type="primary",
    )

    refresh_clicked = st.button(
        "🔄 Refresh Data",
        use_container_width=True,
    )

    st.divider()

    st.write(
        "**V2.2 Status**"
    )

    st.success(
        "STRUCTURE ENGINE: ON"
    )

    st.success(
        "AUTOMATIC TRADING: OFF"
    )

    st.write(
        "M5 swing window: "
        f"`{STRUCTURE_SWING_LEFT}/{STRUCTURE_SWING_RIGHT}`"
    )

    st.write(
        "Startup target: "
        f"≤ {MAX_STARTUP_SECONDS // 60} minutes"
    )


# ------------------------------------------------------------
# CONNECT / BOOTSTRAP
# ------------------------------------------------------------

if connect_clicked:

    with st.spinner(
        "Connecting to IG Demo and loading M5/M15 data..."
    ):

        try:

            elapsed = bootstrap_data()

            st.success(
                f"V2.2 Data Engine ready in "
                f"{elapsed:.1f} seconds."
            )

        except Exception as exc:

            st.session_state[
                "last_error"
            ] = str(exc)

            st.error(
                f"Bootstrap failed: {exc}"
            )


# ------------------------------------------------------------
# MANUAL REFRESH
# ------------------------------------------------------------

if refresh_clicked:

    if not st.session_state[
        "ig_connected"
    ]:

        st.warning(
            "Connect to IG first."
        )

    else:

        try:

            live_data_tick()

            st.success(
                "Live M5/M15 data refreshed."
            )

        except Exception as exc:

            st.session_state[
                "last_error"
            ] = str(exc)

            st.error(
                f"Refresh failed: {exc}"
            )


# ------------------------------------------------------------
# AUTOMATIC LIVE DATA LOOP
# ------------------------------------------------------------

if (
    st.session_state[
        "ig_connected"
    ]
    and st.session_state[
        "bootstrap_complete"
    ]
):
    @st.fragment(
        run_every=POLL_SECONDS
    )
    def live_engine_fragment():
        try:
            live_data_tick()
        except Exception as exc:
            st.session_state[
                "last_error"
            ] = str(exc)

        st.subheader(
            "📊 Live Status"
        )

        render_status_cards()

        if st.session_state[
            "last_error"
        ]:
            st.error(
                st.session_state[
                    "last_error"
                ]
            )

        render_data_details()
        render_structure_dashboard()

        # V2.3 — Liquidity + upgraded S/R
        if (
            "render_v23_liquidity_dashboard" in globals()
            and "build_v23_snapshot" in globals()
        ):
            v23_snapshot = build_v23_snapshot(
                st.session_state["m5_bars"],
                st.session_state["m15_bars"],
                st.session_state["live_mid"],
            )
            render_v23_liquidity_dashboard(v23_snapshot)
            render_v23_confluence_summary(v23_snapshot)
            # V2.4 — Order Blocks + Fair Value Gaps
        if (
            "render_v24_live_layer" in globals()
            and "V24_READY" in globals()
        ):
            render_v24_live_layer()
        render_live_candle_tables()

        st.caption(
            f"Live engine refresh: "
            f"every {POLL_SECONDS} seconds"
        )

    live_engine_fragment()
    st.subheader(
        "📡 Waiting for Data Engine"
    )

    st.write(
        "Press **Connect & Bootstrap** "
        "in the sidebar."
    )

    st.write(
        "The engine will load recent Yahoo "
        "M5/M15 structure and connect it to "
        "live IG XAU/USD pricing."
    )


# ------------------------------------------------------------
# FOOTER
# ------------------------------------------------------------

st.divider()

st.caption(
    "Quantum X V2.2 — Market Structure milestone | "
    "IG Demo execution disabled"
)

st.caption(
    "M15 = directional context | "
    "M5 = execution structure | "
    "BOS/CHOCH = structure information only"
)


# ============================================================
# END OF QUANTUM X V2.2
# ============================================================
#
# V2.2 currently provides:
#
#   ✔ IG Demo live XAU/USD
#   ✔ Yahoo GC=F M5/M15 bootstrap
#   ✔ IG/Yahoo calibration
#   ✔ Live M5/M15 candles
#   ✔ Swing highs/lows
#   ✔ HH / HL / LH / LL
#   ✔ M5 structure
#   ✔ M15 structure
#   ✔ BOS
#   ✔ CHOCH
#   ✔ M5/M15 alignment
#
# V2.2 DOES NOT PLACE TRADES.
#
# Future layers:
#
# V2.3  Liquidity + upgraded S/R
# V2.4  Order Blocks + FVG
# V2.5  VWAP + ATR + Volume
# V2.6  Fibonacci Confluence
# V2.7  Entry Engine
# V2.8  Exit + Risk Engine
# V2.9  Demo Validation
# V2.10 Automated Demo Execution
# ============================================================
# ============================================================
# QUANTUM X V2.3 — LIQUIDITY + UPGRADED S/R
# HALF 1 — PART 1A
# ============================================================

# ------------------------------------------------------------
# V2.3 CONFIGURATION
# ------------------------------------------------------------

LIQUIDITY_TOLERANCE_ATR = 0.20
SR_ZONE_ATR_MULTIPLIER = 0.35
MAX_LIQUIDITY_LEVELS = 12
MAX_SR_ZONES = 8

# Session windows are UTC.
# These are contextual levels only — NOT trading sessions.
ASIA_START_HOUR = 0
ASIA_END_HOUR = 8

LONDON_START_HOUR = 7
LONDON_END_HOUR = 16

NEW_YORK_START_HOUR = 13
NEW_YORK_END_HOUR = 21


# ------------------------------------------------------------
# V2.3 DATA HELPERS
# ------------------------------------------------------------

def get_completed_candles(df):
    """
    Return only completed candles.

    The currently forming candle is excluded so liquidity and
    support/resistance levels are not distorted by live movement.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    data = df.copy()

    if not isinstance(data.index, pd.DatetimeIndex):
        data.index = pd.to_datetime(data.index, utc=True)

    data = data.sort_index()

    if len(data) < 5:
        return data.iloc[0:0].copy()

    current_bucket = (
        pd.Timestamp.now(tz="UTC")
        .floor("5min")
    )

    completed = data[data.index < current_bucket].copy()

    return completed


def calculate_v23_atr(df, period=14):
    """
    ATR-style volatility measurement used to determine
    reasonable liquidity/S&R clustering distances.
    """
    if df is None or df.empty:
        return 0.0

    data = df.copy()

    required = {"high", "low", "close"}

    if not required.issubset(data.columns):
        return 0.0

    previous_close = data["close"].shift(1)

    true_range = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - previous_close).abs(),
            (data["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = true_range.rolling(
        window=period,
        min_periods=max(3, period // 2),
    ).mean()

    value = atr.iloc[-1]

    if pd.isna(value):
        return 0.0

    return float(value)


def price_distance(price_a, price_b):
    """
    Absolute distance between two prices.
    """
    try:
        return abs(float(price_a) - float(price_b))
    except (TypeError, ValueError):
        return float("inf")


def levels_are_close(level_a, level_b, tolerance):
    """
    Determines whether two price levels belong to the same
    liquidity/S&R area.
    """
    return price_distance(level_a, level_b) <= tolerance


# ------------------------------------------------------------
# V2.3 SWING EXTRACTION
# ------------------------------------------------------------

def detect_v23_swings(df, left=2, right=2):
    """
    Detect confirmed swing highs and swing lows.

    A swing is confirmed only when candles exist on both sides.
    This prevents the current candle from creating artificial
    liquidity levels.
    """
    if df is None or df.empty:
        return pd.DataFrame(), pd.DataFrame()

    data = get_completed_candles(df)

    if len(data) < left + right + 1:
        return pd.DataFrame(), pd.DataFrame()

    swing_highs = []
    swing_lows = []

    for i in range(left, len(data) - right):
        row = data.iloc[i]

        left_highs = data["high"].iloc[i - left:i]
        right_highs = data["high"].iloc[i + 1:i + right + 1]

        left_lows = data["low"].iloc[i - left:i]
        right_lows = data["low"].iloc[i + 1:i + right + 1]

        if (
            float(row["high"]) > float(left_highs.max())
            and float(row["high"]) > float(right_highs.max())
        ):
            swing_highs.append(
                {
                    "time": data.index[i],
                    "price": float(row["high"]),
                }
            )

        if (
            float(row["low"]) < float(left_lows.min())
            and float(row["low"]) < float(right_lows.min())
        ):
            swing_lows.append(
                {
                    "time": data.index[i],
                    "price": float(row["low"]),
                }
            )

    high_df = pd.DataFrame(swing_highs)
    low_df = pd.DataFrame(swing_lows)

    return high_df, low_df


# ------------------------------------------------------------
# V2.3 EQUAL HIGH / LOW DETECTION
# ------------------------------------------------------------

def detect_equal_highs(highs_df, tolerance):
    """
    Detect clusters of swing highs that are close enough to be
    treated as buy-side liquidity.
    """
    if highs_df is None or highs_df.empty:
        return []

    levels = []

    for _, row in highs_df.iterrows():
        price = float(row["price"])

        matched = None

        for cluster in levels:
            if levels_are_close(
                price,
                cluster["level"],
                tolerance,
            ):
                matched = cluster
                break

        if matched is not None:
            matched["prices"].append(price)
            matched["times"].append(row["time"])
            matched["level"] = sum(matched["prices"]) / len(
                matched["prices"]
            )
            matched["touches"] = len(matched["prices"])
        else:
            levels.append(
                {
                    "level": price,
                    "prices": [price],
                    "times": [row["time"]],
                    "touches": 1,
                    "type": "EQUAL HIGH",
                }
            )

    return [
        level
        for level in levels
        if level["touches"] >= 2
    ]


def detect_equal_lows(lows_df, tolerance):
    """
    Detect clusters of swing lows that are close enough to be
    treated as sell-side liquidity.
    """
    if lows_df is None or lows_df.empty:
        return []

    levels = []

    for _, row in lows_df.iterrows():
        price = float(row["price"])

        matched = None

        for cluster in levels:
            if levels_are_close(
                price,
                cluster["level"],
                tolerance,
            ):
                matched = cluster
                break

        if matched is not None:
            matched["prices"].append(price)
            matched["times"].append(row["time"])
            matched["level"] = sum(matched["prices"]) / len(
                matched["prices"]
            )
            matched["touches"] = len(matched["prices"])
        else:
            levels.append(
                {
                    "level": price,
                    "prices": [price],
                    "times": [row["time"]],
                    "touches": 1,
                    "type": "EQUAL LOW",
                }
            )

    return [
        level
        for level in levels
        if level["touches"] >= 2
    ]


# ------------------------------------------------------------
# V2.3 PRIOR EXTREMES
# ------------------------------------------------------------

def get_previous_day_levels(df):
    """
    Calculate previous UTC trading-day high and low.

    These levels are important external liquidity references.
    """
    if df is None or df.empty:
        return {}

    data = df.copy()

    if not isinstance(data.index, pd.DatetimeIndex):
        data.index = pd.to_datetime(data.index, utc=True)

    data = data.sort_index()

    completed = get_completed_candles(data)

    if completed.empty:
        return {}

    current_day = completed.index[-1].date()

    previous_days = completed[
        completed.index.date < current_day
    ]

    if previous_days.empty:
        return {}

    previous_day = previous_days.index.date[-1]

    previous_data = completed[
        completed.index.date == previous_day
    ]

    if previous_data.empty:
        return {}

    return {
        "previous_day_high": float(
            previous_data["high"].max()
        ),
        "previous_day_low": float(
            previous_data["low"].min()
        ),
        "date": str(previous_day),
    }


# ------------------------------------------------------------
# V2.3 SESSION EXTREMES
# ------------------------------------------------------------

def get_session_levels(df):
    """
    Calculate Asia, London and New York session highs/lows
    from completed candles.

    These are liquidity references, not direct entry signals.
    """
    if df is None or df.empty:
        return []

    data = get_completed_candles(df)

    if data.empty:
        return []

    sessions = [
        (
            "ASIA",
            ASIA_START_HOUR,
            ASIA_END_HOUR,
        ),
        (
            "LONDON",
            LONDON_START_HOUR,
            LONDON_END_HOUR,
        ),
        (
            "NEW YORK",
            NEW_YORK_START_HOUR,
            NEW_YORK_END_HOUR,
        ),
    ]

    results = []

    for name, start_hour, end_hour in sessions:

        hours = data.index.hour

        if start_hour < end_hour:
            mask = (
                (hours >= start_hour)
                & (hours < end_hour)
            )
        else:
            mask = (
                (hours >= start_hour)
                | (hours < end_hour)
            )

        session_data = data.loc[mask]

        if session_data.empty:
            continue

        results.append(
            {
                "type": f"{name} HIGH",
                "level": float(session_data["high"].max()),
                "session": name,
            }
        )

        results.append(
            {
                "type": f"{name} LOW",
                "level": float(session_data["low"].min()),
                "session": name,
            }
        )

    return results
# ============================================================
# QUANTUM X V2.3 — LIQUIDITY + UPGRADED S/R
# HALF 1 — PART 1B
# ============================================================

# ------------------------------------------------------------
# V2.3 LIQUIDITY LEVEL NORMALIZATION
# ------------------------------------------------------------

def add_liquidity_level(levels, level, level_type, source, score=1):
    """
    Add a liquidity level while preventing duplicate levels.

    A level receives a score based on how many independent
    sources identify approximately the same price.
    """
    if level is None:
        return levels

    try:
        price = float(level)
    except (TypeError, ValueError):
        return levels

    if not pd.notna(price):
        return levels

    for existing in levels:
        if levels_are_close(
            price,
            existing["level"],
            existing["tolerance"],
        ):
            existing["level"] = (
                existing["level"] + price
            ) / 2.0

            existing["score"] += int(score)

            if source not in existing["sources"]:
                existing["sources"].append(source)

            if level_type not in existing["types"]:
                existing["types"].append(level_type)

            existing["touches"] += 1

            return levels

    levels.append(
        {
            "level": price,
            "type": level_type,
            "types": [level_type],
            "source": source,
            "sources": [source],
            "score": int(score),
            "touches": 1,
            "tolerance": 0.01,
        }
    )

    return levels


def finalize_liquidity_levels(levels, atr):
    """
    Apply an ATR-based tolerance and produce a clean liquidity
    level list for the dashboard.
    """
    if not levels:
        return []

    if atr <= 0:
        tolerance = 0.50
    else:
        tolerance = max(
            0.20,
            atr * LIQUIDITY_TOLERANCE_ATR,
        )

    finalized = []

    for item in levels:
        cleaned = item.copy()
        cleaned["tolerance"] = tolerance

        cleaned["score"] = max(
            1,
            int(cleaned.get("score", 1)),
        )

        cleaned["strength"] = (
            "HIGH"
            if cleaned["score"] >= 4
            else "MEDIUM"
            if cleaned["score"] >= 2
            else "LOW"
        )

        finalized.append(cleaned)

    finalized.sort(
        key=lambda x: (
            -x["score"],
            -x["touches"],
        )
    )

    return finalized[:MAX_LIQUIDITY_LEVELS]


# ------------------------------------------------------------
# V2.3 BUILD COMPLETE LIQUIDITY MAP
# ------------------------------------------------------------

def build_liquidity_map(df):
    """
    Build a complete liquidity map from:

    - Equal highs
    - Equal lows
    - Previous day high/low
    - Session highs/lows
    - Recent swing highs/lows

    This function only maps market structure.
    It does NOT generate trading orders.
    """
    if df is None or df.empty:
        return {
            "levels": [],
            "buy_side": [],
            "sell_side": [],
            "atr": 0.0,
            "status": "NO DATA",
        }

    completed = get_completed_candles(df)

    if completed.empty:
        return {
            "levels": [],
            "buy_side": [],
            "sell_side": [],
            "atr": 0.0,
            "status": "WAITING FOR COMPLETED CANDLES",
        }

    atr = calculate_v23_atr(completed)

    swing_highs, swing_lows = detect_v23_swings(
        completed
    )

    if atr > 0:
        tolerance = max(
            0.20,
            atr * LIQUIDITY_TOLERANCE_ATR,
        )
    else:
        tolerance = 0.50

    equal_highs = detect_equal_highs(
        swing_highs,
        tolerance,
    )

    equal_lows = detect_equal_lows(
        swing_lows,
        tolerance,
    )

    levels = []

    # --------------------------------------------------------
    # Equal highs = buy-side liquidity
    # --------------------------------------------------------

    for item in equal_highs:
        levels = add_liquidity_level(
            levels,
            item["level"],
            "BUY-SIDE LIQUIDITY",
            "Equal High",
            score=3 + item["touches"],
        )

    # --------------------------------------------------------
    # Equal lows = sell-side liquidity
    # --------------------------------------------------------

    for item in equal_lows:
        levels = add_liquidity_level(
            levels,
            item["level"],
            "SELL-SIDE LIQUIDITY",
            "Equal Low",
            score=3 + item["touches"],
        )

    # --------------------------------------------------------
    # Swing highs/lows
    # --------------------------------------------------------

    if not swing_highs.empty:

        recent_highs = swing_highs.tail(8)

        for _, row in recent_highs.iterrows():

            levels = add_liquidity_level(
                levels,
                row["price"],
                "BUY-SIDE",
                "Swing High",
                score=1,
            )

    if not swing_lows.empty:

        recent_lows = swing_lows.tail(8)

        for _, row in recent_lows.iterrows():

            levels = add_liquidity_level(
                levels,
                row["price"],
                "SELL-SIDE",
                "Swing Low",
                score=1,
            )

    # --------------------------------------------------------
    # Previous day high / low
    # --------------------------------------------------------

    previous_day = get_previous_day_levels(
        completed
    )

    if previous_day:

        if "previous_day_high" in previous_day:

            levels = add_liquidity_level(
                levels,
                previous_day["previous_day_high"],
                "BUY-SIDE",
                "Previous Day High",
                score=4,
            )

        if "previous_day_low" in previous_day:

            levels = add_liquidity_level(
                levels,
                previous_day["previous_day_low"],
                "SELL-SIDE",
                "Previous Day Low",
                score=4,
            )

    # --------------------------------------------------------
    # Session highs/lows
    # --------------------------------------------------------

    session_levels = get_session_levels(
        completed
    )

    for item in session_levels:

        level_type = str(
            item["type"]
        ).upper()

        if "HIGH" in level_type:
            side = "BUY-SIDE"
        else:
            side = "SELL-SIDE"

        levels = add_liquidity_level(
            levels,
            item["level"],
            side,
            item["session"],
            score=2,
        )

    # --------------------------------------------------------
    # Finalize
    # --------------------------------------------------------

    finalized = finalize_liquidity_levels(
        levels,
        atr,
    )

    buy_side = [
        item
        for item in finalized
        if (
            "BUY-SIDE" in item["type"]
            or "BUY-SIDE" in item["types"]
            or any(
                "HIGH" in str(t).upper()
                for t in item["types"]
            )
        )
    ]

    sell_side = [
        item
        for item in finalized
        if (
            "SELL-SIDE" in item["type"]
            or "SELL-SIDE" in item["types"]
            or any(
                "LOW" in str(t).upper()
                for t in item["types"]
            )
        )
    ]

    return {
        "levels": finalized,
        "buy_side": buy_side,
        "sell_side": sell_side,
        "atr": atr,
        "tolerance": tolerance,
        "swing_highs": swing_highs,
        "swing_lows": swing_lows,
        "previous_day": previous_day,
        "session_levels": session_levels,
        "status": "READY",
    }


# ------------------------------------------------------------
# V2.3 SUPPORT / RESISTANCE ZONES
# ------------------------------------------------------------

def build_sr_zones(df):
    """
    Build upgraded support/resistance zones from confirmed
    swing points and important liquidity references.

    A zone has a center price and ATR-adjusted upper/lower
    boundaries.
    """
    if df is None or df.empty:
        return {
            "support": [],
            "resistance": [],
            "atr": 0.0,
        }

    completed = get_completed_candles(df)

    if completed.empty:
        return {
            "support": [],
            "resistance": [],
            "atr": 0.0,
        }

    atr = calculate_v23_atr(completed)

    if atr <= 0:
        zone_width = 0.50
    else:
        zone_width = max(
            0.20,
            atr * SR_ZONE_ATR_MULTIPLIER,
        )

    swing_highs, swing_lows = detect_v23_swings(
        completed
    )

    resistance = []
    support = []

    # --------------------------------------------------------
    # Resistance from swing highs
    # --------------------------------------------------------

    if not swing_highs.empty:

        for _, row in swing_highs.tail(12).iterrows():

            price = float(row["price"])

            resistance.append(
                {
                    "level": price,
                    "lower": price - zone_width,
                    "upper": price + zone_width,
                    "type": "RESISTANCE",
                    "source": "Swing High",
                    "strength": 1,
                }
            )

    # --------------------------------------------------------
    # Support from swing lows
    # --------------------------------------------------------

    if not swing_lows.empty:

        for _, row in swing_lows.tail(12).iterrows():

            price = float(row["price"])

            support.append(
                {
                    "level": price,
                    "lower": price - zone_width,
                    "upper": price + zone_width,
                    "type": "SUPPORT",
                    "source": "Swing Low",
                    "strength": 1,
                }
            )

    # --------------------------------------------------------
    # Merge nearby resistance zones
    # --------------------------------------------------------

    resistance = merge_sr_zones(
        resistance,
        zone_width,
    )

    # --------------------------------------------------------
    # Merge nearby support zones
    # --------------------------------------------------------

    support = merge_sr_zones(
        support,
        zone_width,
    )

    return {
        "support": support[:MAX_SR_ZONES],
        "resistance": resistance[:MAX_SR_ZONES],
        "atr": atr,
        "zone_width": zone_width,
    }


def merge_sr_zones(zones, tolerance):
    """
    Merge nearby S/R zones into broader price areas.
    """
    if not zones:
        return []

    merged = []

    for zone in sorted(
        zones,
        key=lambda x: x["level"],
    ):

        matched = None

        for existing in merged:

            if levels_are_close(
                zone["level"],
                existing["level"],
                tolerance,
            ):
                matched = existing
                break

        if matched is not None:

            matched["level"] = (
                matched["level"]
                + zone["level"]
            ) / 2.0

            matched["lower"] = min(
                matched["lower"],
                zone["lower"],
            )

            matched["upper"] = max(
                matched["upper"],
                zone["upper"],
            )

            matched["strength"] += (
                zone.get("strength", 1)
            )

        else:

            merged.append(
                zone.copy()
            )

    merged.sort(
        key=lambda x: (
            -x["strength"],
            x["level"],
        )
    )

    return merged
# ============================================================
# QUANTUM X V2.3 — LIQUIDITY + UPGRADED S/R
# HALF 2 — PART 2A
# ============================================================

# ------------------------------------------------------------
# V2.3 TIMEFRAME LIQUIDITY ENGINE
# ------------------------------------------------------------

def build_v23_timeframe_map(df, timeframe_name):
    """
    Build the complete V2.3 liquidity and S/R map for one
    timeframe.

    timeframe_name is normally M5 or M15.
    """
    if df is None or df.empty:
        return {
            "timeframe": timeframe_name,
            "status": "NO DATA",
            "liquidity": {},
            "sr": {},
        }

    liquidity = build_liquidity_map(df)
    sr = build_sr_zones(df)

    return {
        "timeframe": timeframe_name,
        "status": (
            "READY"
            if liquidity.get("status") == "READY"
            else liquidity.get("status", "WAITING")
        ),
        "liquidity": liquidity,
        "sr": sr,
    }


# ------------------------------------------------------------
# V2.3 BUILD M5 + M15 MARKET MAP
# ------------------------------------------------------------

def build_v23_market_map(m5_df, m15_df):
    """
    Build both execution-timeframe and higher-timeframe
    liquidity maps.

    M5:
        Execution liquidity and S/R.

    M15:
        Higher-timeframe liquidity and S/R context.
    """
    m5_map = build_v23_timeframe_map(
        m5_df,
        "M5",
    )

    m15_map = build_v23_timeframe_map(
        m15_df,
        "M15",
    )

    return {
        "m5": m5_map,
        "m15": m15_map,
        "status": (
            "READY"
            if (
                m5_map["status"] == "READY"
                and m15_map["status"] == "READY"
            )
            else "PARTIAL"
        ),
    }


# ------------------------------------------------------------
# V2.3 CURRENT PRICE LOCATION
# ------------------------------------------------------------

def classify_price_location(
    price,
    support_zones,
    resistance_zones,
    tolerance=0.0,
):
    """
    Determine whether price is currently:

        - Inside support
        - Near support
        - Inside resistance
        - Near resistance
        - Between levels
    """
    try:
        current_price = float(price)
    except (TypeError, ValueError):
        return {
            "location": "UNKNOWN",
            "level": None,
            "distance": None,
        }

    nearest_support = None
    nearest_support_distance = float("inf")

    for zone in support_zones or []:

        lower = float(zone["lower"]) - tolerance
        upper = float(zone["upper"]) + tolerance

        if lower <= current_price <= upper:
            return {
                "location": "INSIDE SUPPORT",
                "level": float(zone["level"]),
                "distance": 0.0,
                "zone": zone,
            }

        distance = min(
            abs(current_price - lower),
            abs(current_price - upper),
            abs(current_price - float(zone["level"])),
        )

        if distance < nearest_support_distance:
            nearest_support_distance = distance
            nearest_support = zone

    nearest_resistance = None
    nearest_resistance_distance = float("inf")

    for zone in resistance_zones or []:

        lower = float(zone["lower"]) - tolerance
        upper = float(zone["upper"]) + tolerance

        if lower <= current_price <= upper:
            return {
                "location": "INSIDE RESISTANCE",
                "level": float(zone["level"]),
                "distance": 0.0,
                "zone": zone,
            }

        distance = min(
            abs(current_price - lower),
            abs(current_price - upper),
            abs(current_price - float(zone["level"])),
        )

        if distance < nearest_resistance_distance:
            nearest_resistance_distance = distance
            nearest_resistance = zone

    candidates = []

    if nearest_support is not None:
        candidates.append(
            (
                nearest_support_distance,
                "NEAR SUPPORT",
                nearest_support,
            )
        )

    if nearest_resistance is not None:
        candidates.append(
            (
                nearest_resistance_distance,
                "NEAR RESISTANCE",
                nearest_resistance,
            )
        )

    if not candidates:
        return {
            "location": "BETWEEN LEVELS",
            "level": None,
            "distance": None,
        }

    candidates.sort(key=lambda item: item[0])

    distance, location, zone = candidates[0]

    return {
        "location": location,
        "level": float(zone["level"]),
        "distance": float(distance),
        "zone": zone,
    }


# ------------------------------------------------------------
# V2.3 NEAREST LIQUIDITY
# ------------------------------------------------------------

def find_nearest_liquidity(
    price,
    liquidity_levels,
):
    """
    Find the closest liquidity level above and below current
    price.

    This does not predict a move. It only maps where liquidity
    currently sits.
    """
    try:
        current_price = float(price)
    except (TypeError, ValueError):
        return {
            "above": None,
            "below": None,
        }

    above = []
    below = []

    for item in liquidity_levels or []:

        try:
            level = float(item["level"])
        except (TypeError, ValueError):
            continue

        if level > current_price:
            above.append(
                {
                    **item,
                    "distance": level - current_price,
                }
            )

        elif level < current_price:
            below.append(
                {
                    **item,
                    "distance": current_price - level,
                }
            )

    above.sort(
        key=lambda item: item["distance"]
    )

    below.sort(
        key=lambda item: item["distance"]
    )

    return {
        "above": above[0] if above else None,
        "below": below[0] if below else None,
    }


# ------------------------------------------------------------
# V2.3 M5/M15 PRICE CONTEXT
# ------------------------------------------------------------

def build_price_context(
    live_price,
    market_map,
):
    """
    Combine M5 and M15 liquidity/S&R information around the
    current IG price.
    """
    if live_price is None:
        return {
            "status": "NO LIVE PRICE",
        }

    try:
        price = float(live_price)
    except (TypeError, ValueError):
        return {
            "status": "INVALID LIVE PRICE",
        }

    m5_map = market_map.get("m5", {})
    m15_map = market_map.get("m15", {})

    m5_liquidity = m5_map.get(
        "liquidity",
        {},
    )

    m15_liquidity = m15_map.get(
        "liquidity",
        {},
    )

    m5_sr = m5_map.get(
        "sr",
        {},
    )

    m15_sr = m15_map.get(
        "sr",
        {},
    )

    m5_liquidity_levels = m5_liquidity.get(
        "levels",
        [],
    )

    m15_liquidity_levels = m15_liquidity.get(
        "levels",
        [],
    )

    m5_nearest = find_nearest_liquidity(
        price,
        m5_liquidity_levels,
    )

    m15_nearest = find_nearest_liquidity(
        price,
        m15_liquidity.get(
            "levels",
            [],
        ),
    )

    m5_location = classify_price_location(
        price,
        m5_sr.get("support", []),
        m5_sr.get("resistance", []),
        tolerance=m5_sr.get(
            "zone_width",
            0.0,
        ),
    )

    m15_location = classify_price_location(
        price,
        m15_sr.get("support", []),
        m15_sr.get("resistance", []),
        tolerance=m15_sr.get(
            "zone_width",
            0.0,
        ),
    )

    return {
        "status": "READY",
        "price": price,

        "m5": {
            "location": m5_location,
            "nearest_liquidity": m5_nearest,
            "support": m5_sr.get(
                "support",
                [],
            ),
            "resistance": m5_sr.get(
                "resistance",
                [],
            ),
        },

        "m15": {
            "location": m15_location,
            "nearest_liquidity": m15_nearest,
            "support": m15_sr.get(
                "support",
                [],
            ),
            "resistance": m15_sr.get(
                "resistance",
                [],
            ),
        },
    }


# ------------------------------------------------------------
# V2.3 LIQUIDITY SIDE CLASSIFICATION
# ------------------------------------------------------------

def get_liquidity_side(level):
    """
    Normalize liquidity side classification.

    HIGH / equal-high liquidity = buy-side
    LOW / equal-low liquidity   = sell-side
    """
    if not level:
        return "UNKNOWN"

    level_type = str(
        level.get("type", "")
    ).upper()

    level_types = " ".join(
        str(item).upper()
        for item in level.get(
            "types",
            [],
        )
    )

    combined = (
        level_type
        + " "
        + level_types
    )

    if (
        "BUY-SIDE" in combined
        or "HIGH" in combined
    ):
        return "BUY-SIDE"

    if (
        "SELL-SIDE" in combined
        or "LOW" in combined
    ):
        return "SELL-SIDE"

    return "UNKNOWN"


# ------------------------------------------------------------
# V2.3 LIQUIDITY DISTANCE SUMMARY
# ------------------------------------------------------------

def build_liquidity_distance_summary(
    price,
    liquidity_levels,
):
    """
    Return the closest liquidity levels above and below price
    together with their distances.
    """
    nearest = find_nearest_liquidity(
        price,
        liquidity_levels,
    )

    above = nearest.get("above")
    below = nearest.get("below")

    return {
        "above_price": (
            float(above["level"])
            if above
            else None
        ),
        "above_distance": (
            float(above["distance"])
            if above
            else None
        ),
        "above_side": (
            get_liquidity_side(above)
            if above
            else None
        ),

        "below_price": (
            float(below["level"])
            if below
            else None
        ),
        "below_distance": (
            float(below["distance"])
            if below
            else None
        ),
        "below_side": (
            get_liquidity_side(below)
            if below
            else None
        ),
    }


# ------------------------------------------------------------
# V2.3 LIQUIDITY SNAPSHOT
# ------------------------------------------------------------

def build_v23_snapshot(
    m5_df,
    m15_df,
    live_price,
):
    """
    Single function used by the future dashboard to obtain
    the complete V2.3 market map.

    No trading decision is made here.
    """
    market_map = build_v23_market_map(
        m5_df,
        m15_df,
    )

    price_context = build_price_context(
        live_price,
        market_map,
    )

    m5_levels = (
        market_map
        .get("m5", {})
        .get("liquidity", {})
        .get("levels", [])
    )

    m15_levels = (
        market_map
        .get("m15", {})
        .get("liquidity", {})
        .get("levels", [])
    )

    m5_distance = build_liquidity_distance_summary(
        live_price,
        m5_levels,
    )

    m15_distance = build_liquidity_distance_summary(
        live_price,
        m15_levels,
    )

    return {
        "status": market_map.get(
            "status",
            "UNKNOWN",
        ),

        "market_map": market_map,

        "price_context": price_context,

        "m5_distance": m5_distance,

        "m15_distance": m15_distance,

        "live_price": live_price,
}
# ============================================================
# QUANTUM X V2.3 — LIQUIDITY + UPGRADED S/R
# HALF 2 — PART 2B
# ============================================================

# ------------------------------------------------------------
# V2.3 DISPLAY HELPERS
# ------------------------------------------------------------

def format_price_value(value):
    """
    Format XAU/USD prices consistently.
    """
    if value is None:
        return "—"

    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def format_distance_value(value):
    """
    Format price distance values.
    """
    if value is None:
        return "—"

    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def liquidity_strength_label(level):
    """
    Convert the numeric liquidity score into a readable label.
    """
    if not level:
        return "—"

    strength = str(
        level.get("strength", "")
    ).upper()

    if strength:
        return strength

    score = int(
        level.get("score", 0)
    )

    if score >= 4:
        return "HIGH"

    if score >= 2:
        return "MEDIUM"

    return "LOW"


# ------------------------------------------------------------
# V2.3 LIQUIDITY TABLE
# ------------------------------------------------------------

def render_liquidity_table(
    levels,
    title,
):
    """
    Render a compact liquidity table.
    """
    st.markdown(
        f"#### {title}"
    )

    if not levels:
        st.info(
            "No confirmed liquidity levels yet."
        )
        return

    rows = []

    for level in levels:

        rows.append(
            {
                "Level": format_price_value(
                    level.get("level")
                ),
                "Type": level.get(
                    "type",
                    "—",
                ),
                "Source": ", ".join(
                    level.get(
                        "sources",
                        [],
                    )
                ),
                "Touches": int(
                    level.get(
                        "touches",
                        0,
                    )
                ),
                "Score": int(
                    level.get(
                        "score",
                        0,
                    )
                ),
                "Strength": liquidity_strength_label(
                    level
                ),
            }
        )

    table = pd.DataFrame(rows)

    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
    )


# ------------------------------------------------------------
# V2.3 S/R ZONE TABLE
# ------------------------------------------------------------

def render_sr_zone_table(
    zones,
    title,
):
    """
    Render support/resistance zones.
    """
    st.markdown(
        f"#### {title}"
    )

    if not zones:
        st.info(
            "No confirmed zones yet."
        )
        return

    rows = []

    for zone in zones:

        rows.append(
            {
                "Level": format_price_value(
                    zone.get("level")
                ),
                "Lower": format_price_value(
                    zone.get("lower")
                ),
                "Upper": format_price_value(
                    zone.get("upper")
                ),
                "Source": zone.get(
                    "source",
                    "—",
                ),
                "Strength": int(
                    zone.get(
                        "strength",
                        0,
                    )
                ),
            }
        )

    table = pd.DataFrame(rows)

    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
    )


# ------------------------------------------------------------
# V2.3 PRICE CONTEXT DISPLAY
# ------------------------------------------------------------

def render_price_context(
    snapshot,
):
    """
    Display current price relationship to M5/M15
    liquidity and S/R.
    """
    context = snapshot.get(
        "price_context",
        {},
    )

    if not context:
        return

    st.markdown(
        "### 🎯 Current Price Context"
    )

    price = context.get(
        "price"
    )

    st.metric(
        "IG Live Price",
        format_price_value(price),
    )

    m5_context = context.get(
        "m5",
        {},
    )

    m15_context = context.get(
        "m15",
        {},
    )

    m5_location = m5_context.get(
        "location",
        {},
    )

    m15_location = m15_context.get(
        "location",
        {},
    )

    col1, col2 = st.columns(2)

    with col1:

        st.markdown(
            "#### M5"
        )

        st.write(
            f"**Location:** "
            f"{m5_location.get('location', '—')}"
        )

        st.write(
            f"**Nearest S/R:** "
            f"{format_price_value(m5_location.get('level'))}"
        )

        st.write(
            f"**Distance:** "
            f"{format_distance_value(m5_location.get('distance'))}"
        )

    with col2:

        st.markdown(
            "#### M15"
        )

        st.write(
            f"**Location:** "
            f"{m15_location.get('location', '—')}"
        )

        st.write(
            f"**Nearest S/R:** "
            f"{format_price_value(m15_location.get('level'))}"
        )

        st.write(
            f"**Distance:** "
            f"{format_distance_value(m15_location.get('distance'))}"
        )


# ------------------------------------------------------------
# V2.3 NEAREST LIQUIDITY DISPLAY
# ------------------------------------------------------------

def render_nearest_liquidity(
    snapshot,
):
    """
    Display closest liquidity above and below live price.
    """
    st.markdown(
        "### 💧 Nearest Liquidity"
    )

    m5 = snapshot.get(
        "m5_distance",
        {},
    )

    m15 = snapshot.get(
        "m15_distance",
        {},
    )

    col1, col2 = st.columns(2)

    with col1:

        st.markdown(
            "#### M5"
        )

        st.write(
            f"**Above:** "
            f"{format_price_value(m5.get('above_price'))}"
        )

        st.write(
            f"**Distance:** "
            f"{format_distance_value(m5.get('above_distance'))}"
        )

        st.write(
            f"**Side:** "
            f"{m5.get('above_side') or '—'}"
        )

        st.write(
            f"**Below:** "
            f"{format_price_value(m5.get('below_price'))}"
        )

        st.write(
            f"**Distance:** "
            f"{format_distance_value(m5.get('below_distance'))}"
        )

        st.write(
            f"**Side:** "
            f"{m5.get('below_side') or '—'}"
        )

    with col2:

        st.markdown(
            "#### M15"
        )

        st.write(
            f"**Above:** "
            f"{format_price_value(m15.get('above_price'))}"
        )

        st.write(
            f"**Distance:** "
            f"{format_distance_value(m15.get('above_distance'))}"
        )

        st.write(
            f"**Side:** "
            f"{m15.get('above_side') or '—'}"
        )

        st.write(
            f"**Below:** "
            f"{format_price_value(m15.get('below_price'))}"
        )

        st.write(
            f"**Distance:** "
            f"{format_distance_value(m15.get('below_distance'))}"
        )

        st.write(
            f"**Side:** "
            f"{m15.get('below_side') or '—'}"
        )


# ------------------------------------------------------------
# V2.3 COMPLETE DASHBOARD
# ------------------------------------------------------------

def render_v23_liquidity_dashboard(
    snapshot,
):
    """
    Render the complete V2.3 liquidity dashboard.
    """
    if not snapshot:
        return

    st.markdown(
        "---"
    )

    st.markdown(
        "## 💧 V2.3 Liquidity Intelligence"
    )

    status = snapshot.get(
        "status",
        "UNKNOWN",
    )

    if status == "READY":

        st.success(
            "Liquidity engine healthy — "
            "M5 + M15 market map available."
        )

    elif status == "PARTIAL":

        st.warning(
            "Liquidity engine partially ready."
        )

    else:

        st.info(
            f"Liquidity engine status: {status}"
        )

    render_price_context(
        snapshot
    )

    render_nearest_liquidity(
        snapshot
    )

    market_map = snapshot.get(
        "market_map",
        {},
    )

    m5_map = market_map.get(
        "m5",
        {},
    )

    m15_map = market_map.get(
        "m15",
        {},
    )

    m5_liquidity = m5_map.get(
        "liquidity",
        {},
    )

    m15_liquidity = m15_map.get(
        "liquidity",
        {},
    )

    m5_sr = m5_map.get(
        "sr",
        {},
    )

    m15_sr = m15_map.get(
        "sr",
        {},
    )

    # --------------------------------------------------------
    # M5 LIQUIDITY
    # --------------------------------------------------------

    st.markdown(
        "### 📊 M5 Liquidity Map"
    )

    render_liquidity_table(
        m5_liquidity.get(
            "levels",
            [],
        ),
        "M5 Liquidity Levels",
    )

    render_sr_zone_table(
        m5_sr.get(
            "support",
            [],
        ),
        "M5 Support Zones",
    )

    render_sr_zone_table(
        m5_sr.get(
            "resistance",
            [],
        ),
        "M5 Resistance Zones",
    )

    # --------------------------------------------------------
    # M15 LIQUIDITY
    # --------------------------------------------------------

    st.markdown(
        "### 📊 M15 Liquidity Map"
    )

    render_liquidity_table(
        m15_liquidity.get(
            "levels",
            [],
        ),
        "M15 Liquidity Levels",
    )

    render_sr_zone_table(
        m15_sr.get(
            "support",
            [],
        ),
        "M15 Support Zones",
    )

    render_sr_zone_table(
        m15_sr.get(
            "resistance",
            [],
        ),
        "M15 Resistance Zones",
    )
# ============================================================
# QUANTUM X V2.3 — LIQUIDITY + UPGRADED S/R
# HALF 3 — PART 3B
# ============================================================

# ------------------------------------------------------------
# V2.3 CONFLUENCE SUMMARY
# ------------------------------------------------------------

def build_v23_confluence_summary(
    snapshot,
):
    """
    Build a compact summary of the existing V2.3
    liquidity and S/R information.

    This is analysis only.
    It does NOT create a trading signal.
    """

    if not snapshot:
        return {
            "status": "NO SNAPSHOT",
        }

    market_map = snapshot.get(
        "market_map",
        {},
    )

    m5_map = market_map.get(
        "m5",
        {},
    )

    m15_map = market_map.get(
        "m15",
        {},
    )

    m5_liquidity = m5_map.get(
        "liquidity",
        {},
    )

    m15_liquidity = m15_map.get(
        "liquidity",
        {},
    )

    m5_sr = m5_map.get(
        "sr",
        {},
    )

    m15_sr = m15_map.get(
        "sr",
        {},
    )

    m5_levels = m5_liquidity.get(
        "levels",
        [],
    )

    m15_levels = m15_liquidity.get(
        "levels",
        [],
    )

    m5_support = m5_sr.get(
        "support",
        [],
    )

    m5_resistance = m5_sr.get(
        "resistance",
        [],
    )

    m15_support = m15_sr.get(
        "support",
        [],
    )

    m15_resistance = m15_sr.get(
        "resistance",
        [],
    )

    price_context = snapshot.get(
        "price_context",
        {},
    )

    m5_context = price_context.get(
        "m5",
        {},
    )

    m15_context = price_context.get(
        "m15",
        {},
    )

    m5_location = m5_context.get(
        "location",
        {},
    )

    m15_location = m15_context.get(
        "location",
        {},
    )

    return {
        "status": snapshot.get(
            "status",
            "UNKNOWN",
        ),

        "live_price": snapshot.get(
            "live_price",
        ),

        "m5": {
            "liquidity_count": len(
                m5_levels
            ),
            "support_count": len(
                m5_support
            ),
            "resistance_count": len(
                m5_resistance
            ),
            "location": m5_location.get(
                "location",
                "UNKNOWN",
            ),
            "nearest_sr": m5_location.get(
                "level",
            ),
            "nearest_liquidity_above": (
                snapshot
                .get("m5_distance", {})
                .get("above_price")
            ),
            "nearest_liquidity_below": (
                snapshot
                .get("m5_distance", {})
                .get("below_price")
            ),
        },

        "m15": {
            "liquidity_count": len(
                m15_levels
            ),
            "support_count": len(
                m15_support
            ),
            "resistance_count": len(
                m15_resistance
            ),
            "location": m15_location.get(
                "location",
                "UNKNOWN",
            ),
            "nearest_sr": m15_location.get(
                "level",
            ),
            "nearest_liquidity_above": (
                snapshot
                .get("m15_distance", {})
                .get("above_price")
            ),
            "nearest_liquidity_below": (
                snapshot
                .get("m15_distance", {})
                .get("below_price")
            ),
        },
    }


# ------------------------------------------------------------
# V2.3 CONFLUENCE DISPLAY
# ------------------------------------------------------------

def render_v23_confluence_summary(
    snapshot,
):
    """
    Display the existing V2.3 market-map information
    in a compact summary.

    Analysis only — no trading decision.
    """

    summary = build_v23_confluence_summary(
        snapshot
    )

    if summary.get("status") == "NO SNAPSHOT":
        return

    st.markdown(
        "### 🧩 V2.3 Confluence Summary"
    )

    m5 = summary.get(
        "m5",
        {},
    )

    m15 = summary.get(
        "m15",
        {},
    )

    col1, col2 = st.columns(2)

    with col1:

        st.markdown(
            "#### M5 Execution Context"
        )

        st.write(
            f"**S/R Location:** "
            f"{m5.get('location', 'UNKNOWN')}"
        )

        st.write(
            f"**Nearest S/R:** "
            f"{format_price_value(m5.get('nearest_sr'))}"
        )

        st.write(
            f"**Liquidity Levels:** "
            f"{m5.get('liquidity_count', 0)}"
        )

        st.write(
            f"**Support Zones:** "
            f"{m5.get('support_count', 0)}"
        )

        st.write(
            f"**Resistance Zones:** "
            f"{m5.get('resistance_count', 0)}"
        )

    with col2:

        st.markdown(
            "#### M15 Higher-Timeframe Context"
        )

        st.write(
            f"**S/R Location:** "
            f"{m15.get('location', 'UNKNOWN')}"
        )

        st.write(
            f"**Nearest S/R:** "
            f"{format_price_value(m15.get('nearest_sr'))}"
        )

        st.write(
            f"**Liquidity Levels:** "
            f"{m15.get('liquidity_count', 0)}"
        )

        st.write(
            f"**Support Zones:** "
            f"{m15.get('support_count', 0)}"
        )

        st.write(
            f"**Resistance Zones:** "
            f"{m15.get('resistance_count', 0)}"
        )

    st.caption(
        "V2.3 confluence summary is informational only. "
        "No trading decision is generated."
        )
# ============================================================
# QUANTUM X V2.4 — ORDER BLOCKS + FAIR VALUE GAPS
# HALF 1 — PART 1A
# ============================================================

# ------------------------------------------------------------
# V2.4 CONFIGURATION
# ------------------------------------------------------------

V24_OB_ATR_MIN = 0.20
V24_OB_ATR_MAX = 2.50

V24_FVG_ATR_MIN = 0.10
V24_FVG_MAX_AGE = 120

V24_DISPLACEMENT_ATR = 1.00

V24_MAX_ORDER_BLOCKS = 10
V24_MAX_FVGS = 12


# ------------------------------------------------------------
# V2.4 CANDLE HELPERS
# ------------------------------------------------------------

def get_v24_completed_candles(
    df,
    timeframe_minutes,
):
    """
    Return completed candles only.

    The currently forming candle is excluded.
    """

    if df is None or df.empty:
        return pd.DataFrame()

    data = df.copy()

    if "time" not in data.columns:
        return pd.DataFrame()

    data["time"] = pd.to_datetime(
        data["time"],
        utc=True,
        errors="coerce",
    )

    data = data.dropna(
        subset=["time"]
    ).copy()

    if data.empty:
        return data

    now_utc = pd.Timestamp.now(
        tz="UTC"
    )

    current_bucket = (
        now_utc.floor(
            f"{timeframe_minutes}min"
        )
    )

    data = data[
        data["time"] < current_bucket
    ].copy()

    return data.reset_index(
        drop=True
    )


def calculate_v24_atr(
    df,
    period=14,
):
    """
    Calculate ATR-style volatility measurement.
    """

    if df is None or df.empty:
        return None

    required = {
        "high",
        "low",
        "close",
    }

    if not required.issubset(
        set(df.columns)
    ):
        return None

    data = df.copy()

    previous_close = (
        data["close"].shift(1)
    )

    true_range = pd.concat(
        [
            data["high"] - data["low"],
            (
                data["high"]
                - previous_close
            ).abs(),
            (
                data["low"]
                - previous_close
            ).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = (
        true_range
        .rolling(
            period,
            min_periods=period,
        )
        .mean()
    )

    if atr.empty:
        return None

    value = atr.iloc[-1]

    if pd.isna(value):
        return None

    return float(value)


def get_v24_candle_body(
    candle,
):
    return abs(
        float(candle["close"])
        - float(candle["open"])
    )


def get_v24_candle_range(
    candle,
):
    return (
        float(candle["high"])
        - float(candle["low"])
    )


def get_v24_body_ratio(
    candle,
):
    candle_range = (
        get_v24_candle_range(
            candle
        )
    )

    if candle_range <= 0:
        return 0.0

    return (
        get_v24_candle_body(
            candle
        )
        / candle_range
    )


def is_v24_bullish_candle(
    candle,
):
    return float(
        candle["close"]
    ) > float(
        candle["open"]
    )


def is_v24_bearish_candle(
    candle,
):
    return float(
        candle["close"]
    ) < float(
        candle["open"]
    )


# ------------------------------------------------------------
# V2.4 DISPLACEMENT DETECTION
# ------------------------------------------------------------

def detect_v24_displacement(
    df,
    atr_value,
    index,
):
    """
    Detect a strong directional candle.

    Displacement is used as confirmation that price
    moved away from an Order Block with meaningful force.
    """

    if (
        df is None
        or df.empty
        or atr_value is None
        or atr_value <= 0
    ):
        return None

    if index < 0 or index >= len(df):
        return None

    candle = df.iloc[index]

    candle_range = (
        get_v24_candle_range(
            candle
        )
    )

    body = (
        get_v24_candle_body(
            candle
        )
    )

    if candle_range <= 0:
        return None

    if candle_range < (
        atr_value
        * V24_DISPLACEMENT_ATR
    ):
        return None

    body_ratio = (
        body
        / candle_range
    )

    if body_ratio < 0.60:
        return None

    if is_v24_bullish_candle(
        candle
    ):
        return {
            "direction": "BULLISH",
            "time": candle["time"],
            "open": float(
                candle["open"]
            ),
            "high": float(
                candle["high"]
            ),
            "low": float(
                candle["low"]
            ),
            "close": float(
                candle["close"]
            ),
            "range": candle_range,
            "body": body,
            "body_ratio": body_ratio,
        }

    if is_v24_bearish_candle(
        candle
    ):
        return {
            "direction": "BEARISH",
            "time": candle["time"],
            "open": float(
                candle["open"]
            ),
            "high": float(
                candle["high"]
            ),
            "low": float(
                candle["low"]
            ),
            "close": float(
                candle["close"]
            ),
            "range": candle_range,
            "body": body,
            "body_ratio": body_ratio,
        }

    return None
# ============================================================
# QUANTUM X V2.4 — ORDER BLOCKS + FAIR VALUE GAPS
# HALF 1 — PART 1B
# ============================================================


# ------------------------------------------------------------
# V2.4 ORDER BLOCK DETECTION
# ------------------------------------------------------------

def detect_v24_order_blocks(
    df,
    timeframe_minutes,
):
    """
    Detect Order Blocks from completed candles.

    Bullish OB:
        Last meaningful bearish candle before
        bullish displacement.

    Bearish OB:
        Last meaningful bullish candle before
        bearish displacement.
    """

    data = get_v24_completed_candles(
        df,
        timeframe_minutes,
    )

    if data.empty:
        return []

    atr_value = calculate_v24_atr(
        data
    )

    if (
        atr_value is None
        or atr_value <= 0
    ):
        return []

    order_blocks = []

    for index in range(
        1,
        len(data),
    ):

        displacement = (
            detect_v24_displacement(
                data,
                atr_value,
                index,
            )
        )

        if displacement is None:
            continue

        displacement_direction = (
            displacement["direction"]
        )

        opposite_index = index - 1

        opposite_candle = (
            data.iloc[
                opposite_index
            ]
        )

        candle_range = (
            get_v24_candle_range(
                opposite_candle
            )
        )

        if candle_range <= 0:
            continue

        if candle_range > (
            atr_value
            * V24_OB_ATR_MAX
        ):
            continue

        if candle_range < (
            atr_value
            * V24_OB_ATR_MIN
        ):
            continue

        if (
            displacement_direction
            == "BULLISH"
            and is_v24_bearish_candle(
                opposite_candle
            )
        ):

            order_blocks.append(
                {
                    "type": "BULLISH",
                    "time": opposite_candle[
                        "time"
                    ],
                    "high": float(
                        opposite_candle[
                            "high"
                        ]
                    ),
                    "low": float(
                        opposite_candle[
                            "low"
                        ]
                    ),
                    "open": float(
                        opposite_candle[
                            "open"
                        ]
                    ),
                    "close": float(
                        opposite_candle[
                            "close"
                        ]
                    ),
                    "atr": float(
                        atr_value
                    ),
                    "displacement_time": (
                        displacement[
                            "time"
                        ]
                    ),
                    "displacement_range": (
                        displacement[
                            "range"
                        ]
                    ),
                    "body_ratio": (
                        displacement[
                            "body_ratio"
                        ]
                    ),
                    "active": True,
                }
            )

        elif (
            displacement_direction
            == "BEARISH"
            and is_v24_bullish_candle(
                opposite_candle
            )
        ):

            order_blocks.append(
                {
                    "type": "BEARISH",
                    "time": opposite_candle[
                        "time"
                    ],
                    "high": float(
                        opposite_candle[
                            "high"
                        ]
                    ),
                    "low": float(
                        opposite_candle[
                            "low"
                        ]
                    ),
                    "open": float(
                        opposite_candle[
                            "open"
                        ]
                    ),
                    "close": float(
                        opposite_candle[
                            "close"
                        ]
                    ),
                    "atr": float(
                        atr_value
                    ),
                    "displacement_time": (
                        displacement[
                            "time"
                        ]
                    ),
                    "displacement_range": (
                        displacement[
                            "range"
                        ]
                    ),
                    "body_ratio": (
                        displacement[
                            "body_ratio"
                        ]
                    ),
                    "active": True,
                }
            )

    if not order_blocks:
        return []

    # Keep newest Order Blocks first.
    order_blocks = sorted(
        order_blocks,
        key=lambda item: item[
            "time"
        ],
        reverse=True,
    )

    return order_blocks[
        :V24_MAX_ORDER_BLOCKS
    ]


# ------------------------------------------------------------
# V2.4 ORDER BLOCK STATUS
# ------------------------------------------------------------

def update_v24_order_block_status(
    order_blocks,
    current_price,
):
    """
    Determine whether price is currently inside,
    above, or below each Order Block.
    """

    if not order_blocks:
        return []

    updated = []

    for block in order_blocks:

        low = float(
            block["low"]
        )

        high = float(
            block["high"]
        )

        item = dict(
            block
        )

        if (
            low
            <= current_price
            <= high
        ):
            item[
                "price_location"
            ] = "INSIDE"

        elif current_price > high:
            item[
                "price_location"
            ] = "ABOVE"

        else:
            item[
                "price_location"
            ] = "BELOW"

        updated.append(
            item
        )

    return updated


# ------------------------------------------------------------
# V2.4 FVG DETECTION
# ------------------------------------------------------------

def detect_v24_fvgs(
    df,
    timeframe_minutes,
):
    """
    Detect classic three-candle Fair Value Gaps.

    Bullish FVG:
        Current candle low is above
        candle two bars earlier high.

    Bearish FVG:
        Current candle high is below
        candle two bars earlier low.
    """

    data = get_v24_completed_candles(
        df,
        timeframe_minutes,
    )

    if len(data) < 3:
        return []

    atr_value = calculate_v24_atr(
        data
    )

    if (
        atr_value is None
        or atr_value <= 0
    ):
        return []

    fvgs = []

    for index in range(
        2,
        len(data),
    ):

        first = data.iloc[
            index - 2
        ]

        middle = data.iloc[
            index - 1
        ]

        third = data.iloc[
            index
        ]

        # ----------------------------------------------------
        # BULLISH FVG
        # ----------------------------------------------------

        bullish_gap = (
            float(third["low"])
            - float(first["high"])
        )

        if bullish_gap > 0:

            middle_range = (
                get_v24_candle_range(
                    middle
                )
            )

            if (
                bullish_gap
                >= atr_value
                * V24_FVG_ATR_MIN
            ):

                fvgs.append(
                    {
                        "type": "BULLISH",
                        "time": third[
                            "time"
                        ],
                        "start_time": first[
                            "time"
                        ],
                        "end_time": third[
                            "time"
                        ],
                        "upper": float(
                            third["low"]
                        ),
                        "lower": float(
                            first["high"]
                        ),
                        "size": float(
                            bullish_gap
                        ),
                        "atr": float(
                            atr_value
                        ),
                        "middle_range": float(
                            middle_range
                        ),
                        "filled": False,
                        "active": True,
                    }
                )

        # ----------------------------------------------------
        # BEARISH FVG
        # ----------------------------------------------------

        bearish_gap = (
            float(first["low"])
            - float(third["high"])
        )

        if bearish_gap > 0:

            middle_range = (
                get_v24_candle_range(
                    middle
                )
            )

            if (
                bearish_gap
                >= atr_value
                * V24_FVG_ATR_MIN
            ):

                fvgs.append(
                    {
                        "type": "BEARISH",
                        "time": third[
                            "time"
                        ],
                        "start_time": first[
                            "time"
                        ],
                        "end_time": third[
                            "time"
                        ],
                        "upper": float(
                            first["low"]
                        ),
                        "lower": float(
                            third["high"]
                        ),
                        "size": float(
                            bearish_gap
                        ),
                        "atr": float(
                            atr_value
                        ),
                        "middle_range": float(
                            middle_range
                        ),
                        "filled": False,
                        "active": True,
                    }
                )

    if not fvgs:
        return []

    # Newest first.
    fvgs = sorted(
        fvgs,
        key=lambda item: item[
            "time"
        ],
        reverse=True,
    )

    return fvgs[
        :V24_MAX_FVGS
            ]
# ============================================================
# QUANTUM X V2.4 — ORDER BLOCKS + FAIR VALUE GAPS
# HALF 2 — PART 2A
# ============================================================


# ------------------------------------------------------------
# V2.4 FVG STATUS
# ------------------------------------------------------------

def update_v24_fvg_status(
    fvgs,
    current_price,
):
    """
    Update Fair Value Gap status based on current price.

    A gap is considered filled when price reaches
    the opposite boundary of the gap.
    """

    if not fvgs:
        return []

    updated = []

    for gap in fvgs:

        item = dict(
            gap
        )

        upper = float(
            gap["upper"]
        )

        lower = float(
            gap["lower"]
        )

        gap_type = gap[
            "type"
        ]

        if gap_type == "BULLISH":

            if current_price <= lower:
                item[
                    "price_location"
                ] = "BELOW"

            elif (
                lower
                < current_price
                < upper
            ):
                item[
                    "price_location"
                ] = "INSIDE"

            else:
                item[
                    "price_location"
                ] = "ABOVE"

            if current_price <= lower:
                item[
                    "filled"
                ] = True
                item[
                    "active"
                ] = False

        elif gap_type == "BEARISH":

            if current_price >= upper:
                item[
                    "price_location"
                ] = "ABOVE"

            elif (
                lower
                < current_price
                < upper
            ):
                item[
                    "price_location"
                ] = "INSIDE"

            else:
                item[
                    "price_location"
                ] = "BELOW"

            if current_price >= upper:
                item[
                    "filled"
                ] = True
                item[
                    "active"
                ] = False

        updated.append(
            item
        )

    return updated


# ------------------------------------------------------------
# V2.4 ORDER BLOCK + FVG DISTANCE
# ------------------------------------------------------------

def calculate_v24_zone_distance(
    current_price,
    lower,
    upper,
):
    """
    Calculate distance from current price to a zone.

    Returns:
        0 when price is inside the zone.
        Positive distance otherwise.
    """

    current_price = float(
        current_price
    )

    lower = float(
        lower
    )

    upper = float(
        upper
    )

    if (
        lower
        <= current_price
        <= upper
    ):
        return 0.0

    if current_price < lower:
        return (
            lower
            - current_price
        )

    return (
        current_price
        - upper
    )


def add_v24_zone_distance(
    zones,
    current_price,
):
    """
    Add distance and price-location information
    to Order Blocks or FVG zones.
    """

    if not zones:
        return []

    updated = []

    for zone in zones:

        item = dict(
            zone
        )

        lower = float(
            zone["lower"]
        )

        upper = float(
            zone["upper"]
        )

        distance = (
            calculate_v24_zone_distance(
                current_price,
                lower,
                upper,
            )
        )

        item[
            "distance"
        ] = float(
            distance
        )

        if (
            lower
            <= current_price
            <= upper
        ):
            item[
                "price_location"
            ] = "INSIDE"

        elif current_price > upper:
            item[
                "price_location"
            ] = "ABOVE"

        else:
            item[
                "price_location"
            ] = "BELOW"

        updated.append(
            item
        )

    return updated


# ------------------------------------------------------------
# V2.4 ACTIVE ZONE FILTER
# ------------------------------------------------------------

def filter_v24_active_fvgs(
    fvgs,
):
    """
    Keep only active, unfilled FVGs.
    """

    if not fvgs:
        return []

    active = []

    for gap in fvgs:

        if not gap.get(
            "active",
            True,
        ):
            continue

        if gap.get(
            "filled",
            False,
        ):
            continue

        active.append(
            gap
        )

    return active


def filter_v24_relevant_order_blocks(
    order_blocks,
):
    """
    Keep valid active Order Blocks.
    """

    if not order_blocks:
        return []

    active = []

    for block in order_blocks:

        if not block.get(
            "active",
            True,
        ):
            continue

        high = float(
            block["high"]
        )

        low = float(
            block["low"]
        )

        if high <= low:
            continue

        active.append(
            block
        )

    return active


# ------------------------------------------------------------
# V2.4 NEAREST ZONE HELPERS
# ------------------------------------------------------------

def find_v24_nearest_zone(
    zones,
    current_price,
):
    """
    Find the nearest zone to current price.
    """

    if not zones:
        return None

    enriched = (
        add_v24_zone_distance(
            zones,
            current_price,
        )
    )

    if not enriched:
        return None

    enriched = sorted(
        enriched,
        key=lambda item: (
            item.get(
                "distance",
                float("inf"),
            )
        ),
    )

    return enriched[0]


def get_v24_nearest_order_block(
    order_blocks,
    current_price,
):
    """
    Return nearest active Order Block.
    """

    active = (
        filter_v24_relevant_order_blocks(
            order_blocks
        )
    )

    return find_v24_nearest_zone(
        [
            {
                **block,
                "lower": block[
                    "low"
                ],
                "upper": block[
                    "high"
                ],
            }
            for block in active
        ],
        current_price,
    )


def get_v24_nearest_fvg(
    fvgs,
    current_price,
):
    """
    Return nearest active Fair Value Gap.
    """

    active = (
        filter_v24_active_fvgs(
            fvgs
        )
    )

    return find_v24_nearest_zone(
        active,
        current_price,
    )


# ------------------------------------------------------------
# V2.4 TIMEFRAME ZONE MAP
# ------------------------------------------------------------

def build_v24_timeframe_map(
    df,
    timeframe_minutes,
    current_price,
):
    """
    Build complete Order Block + FVG map
    for one timeframe.
    """

    completed = (
        get_v24_completed_candles(
            df,
            timeframe_minutes,
        )
    )

    if completed.empty:
        return {
            "timeframe": (
                timeframe_minutes
            ),
            "atr": None,
            "order_blocks": [],
            "fvgs": [],
            "nearest_order_block": None,
            "nearest_fvg": None,
        }

    atr_value = (
        calculate_v24_atr(
            completed
        )
    )

    order_blocks = (
        detect_v24_order_blocks(
            completed,
            timeframe_minutes,
        )
    )

    fvgs = (
        detect_v24_fvgs(
            completed,
            timeframe_minutes,
        )
    )

    order_blocks = (
        add_v24_zone_distance(
            [
                {
                    **block,
                    "lower": block[
                        "low"
                    ],
                    "upper": block[
                        "high"
                    ],
                }
                for block in order_blocks
            ],
            current_price,
        )
    )

    fvgs = (
        update_v24_fvg_status(
            fvgs,
            current_price,
        )
    )

    fvgs = (
        add_v24_zone_distance(
            fvgs,
            current_price,
        )
    )

    return {
        "timeframe": (
            timeframe_minutes
        ),
        "atr": atr_value,
        "order_blocks": (
            order_blocks
        ),
        "fvgs": fvgs,
        "nearest_order_block": (
            find_v24_nearest_zone(
                order_blocks,
                current_price,
            )
        ),
        "nearest_fvg": (
            find_v24_nearest_zone(
                filter_v24_active_fvgs(
                    fvgs
                ),
                current_price,
            )
        ),
}
# ============================================================
# QUANTUM X V2.4 — ORDER BLOCKS + FAIR VALUE GAPS
# HALF 2 — PART 2B
# ============================================================


# ------------------------------------------------------------
# V2.4 M5 + M15 ZONE ENGINE
# ------------------------------------------------------------

def build_v24_market_map(
    m5_df,
    m15_df,
    current_price,
):
    """
    Build the complete V2.4 Order Block + FVG
    context for both M5 and M15.
    """

    m5_map = build_v24_timeframe_map(
        m5_df,
        5,
        current_price,
    )

    m15_map = build_v24_timeframe_map(
        m15_df,
        15,
        current_price,
    )

    return {
        "m5": m5_map,
        "m15": m15_map,
        "current_price": float(
            current_price
        ),
    }


# ------------------------------------------------------------
# V2.4 ZONE CONFLUENCE
# ------------------------------------------------------------

def classify_v24_zone_confluence(
    market_map,
):
    """
    Identify whether price is interacting with
    meaningful Order Block / FVG areas.

    This is context only.
    It does NOT create a trade signal.
    """

    if not market_map:
        return {
            "m5": "NONE",
            "m15": "NONE",
            "overall": "NONE",
        }

    m5 = market_map.get(
        "m5",
        {},
    )

    m15 = market_map.get(
        "m15",
        {},
    )

    m5_ob = m5.get(
        "nearest_order_block"
    )

    m5_fvg = m5.get(
        "nearest_fvg"
    )

    m15_ob = m15.get(
        "nearest_order_block"
    )

    m15_fvg = m15.get(
        "nearest_fvg"
    )

    def timeframe_state(
        ob,
        fvg,
    ):

        states = []

        if ob is not None:

            location = ob.get(
                "price_location",
                "UNKNOWN",
            )

            if location == "INSIDE":
                states.append(
                    "OB"
                )

        if fvg is not None:

            location = fvg.get(
                "price_location",
                "UNKNOWN",
            )

            if location == "INSIDE":
                states.append(
                    "FVG"
                )

        if (
            "OB" in states
            and "FVG" in states
        ):
            return "OB + FVG"

        if "OB" in states:
            return "OB"

        if "FVG" in states:
            return "FVG"

        return "NONE"

    m5_state = timeframe_state(
        m5_ob,
        m5_fvg,
    )

    m15_state = timeframe_state(
        m15_ob,
        m15_fvg,
    )

    if (
        m5_state == "OB + FVG"
        or m15_state == "OB + FVG"
    ):
        overall = "STRONG ZONE CONFLUENCE"

    elif (
        m5_state != "NONE"
        and m15_state != "NONE"
    ):
        overall = "MULTI-TIMEFRAME CONFLUENCE"

    elif (
        m5_state != "NONE"
        or m15_state != "NONE"
    ):
        overall = "SINGLE-TIMEFRAME CONFLUENCE"

    else:
        overall = "NONE"

    return {
        "m5": m5_state,
        "m15": m15_state,
        "overall": overall,
    }


# ------------------------------------------------------------
# V2.4 DIRECTIONAL ZONE CONTEXT
# ------------------------------------------------------------

def get_v24_zone_direction(
    zone,
):
    """
    Return the directional meaning of a zone.
    """

    if not zone:
        return "NONE"

    zone_type = zone.get(
        "type",
        "",
    )

    if zone_type == "BULLISH":
        return "BULLISH"

    if zone_type == "BEARISH":
        return "BEARISH"

    return "NONE"


def build_v24_directional_context(
    market_map,
):
    """
    Summarize bullish/bearish zone pressure
    across M5 and M15.
    """

    if not market_map:
        return {
            "m5": "NONE",
            "m15": "NONE",
            "overall": "NEUTRAL",
        }

    results = {}

    for timeframe in (
        "m5",
        "m15",
    ):

        timeframe_map = market_map.get(
            timeframe,
            {},
        )

        order_blocks = (
            timeframe_map.get(
                "order_blocks",
                [],
            )
        )

        fvgs = (
            timeframe_map.get(
                "fvgs",
                [],
            )
        )

        bullish = 0
        bearish = 0

        for zone in order_blocks:

            if zone.get(
                "type"
            ) == "BULLISH":
                bullish += 1

            elif zone.get(
                "type"
            ) == "BEARISH":
                bearish += 1

        for zone in fvgs:

            if not zone.get(
                "active",
                True,
            ):
                continue

            if zone.get(
                "type"
            ) == "BULLISH":
                bullish += 1

            elif zone.get(
                "type"
            ) == "BEARISH":
                bearish += 1

        if (
            bullish > bearish
        ):
            state = "BULLISH"

        elif (
            bearish > bullish
        ):
            state = "BEARISH"

        else:
            state = "NEUTRAL"

        results[timeframe] = state

    if (
        results["m5"]
        == results["m15"]
        and results["m5"]
        in (
            "BULLISH",
            "BEARISH",
        )
    ):
        overall = results[
            "m5"
        ]

    elif (
        results["m15"]
        in (
            "BULLISH",
            "BEARISH",
        )
    ):
        overall = (
            "M15 "
            + results["m15"]
        )

    else:
        overall = "NEUTRAL"

    results[
        "overall"
    ] = overall

    return results


# ------------------------------------------------------------
# V2.4 COMPLETE SNAPSHOT
# ------------------------------------------------------------

def build_v24_snapshot(
    m5_df,
    m15_df,
    current_price,
):
    """
    Build the complete V2.4 analysis snapshot.

    V2.3 liquidity/S&R remains separate and is
    intentionally not modified here.
    """

    market_map = (
        build_v24_market_map(
            m5_df,
            m15_df,
            current_price,
        )
    )

    confluence = (
        classify_v24_zone_confluence(
            market_map
        )
    )

    directional = (
        build_v24_directional_context(
            market_map
        )
    )

    return {
        "current_price": float(
            current_price
        ),
        "market_map": market_map,
        "confluence": confluence,
        "directional": directional,
    }


# ------------------------------------------------------------
# V2.4 SUMMARY HELPERS
# ------------------------------------------------------------

def format_v24_zone(
    zone,
):
    """
    Convert a zone dictionary into a compact
    display-ready structure.
    """

    if not zone:
        return None

    return {
        "type": zone.get(
            "type",
            "UNKNOWN",
        ),
        "time": zone.get(
            "time"
        ),
        "lower": float(
            zone.get(
                "lower",
                0.0,
            )
        ),
        "upper": float(
            zone.get(
                "upper",
                0.0,
            )
        ),
        "distance": float(
            zone.get(
                "distance",
                0.0,
            )
        ),
        "location": zone.get(
            "price_location",
            "UNKNOWN",
        ),
        "active": zone.get(
            "active",
            True,
        ),
    }


def get_v24_summary(
    snapshot,
):
    """
    Return a compact summary for the UI layer.
    """

    if not snapshot:
        return {
            "price": None,
            "m5_confluence": "NONE",
            "m15_confluence": "NONE",
            "overall_confluence": "NONE",
            "m5_direction": "NEUTRAL",
            "m15_direction": "NEUTRAL",
            "overall_direction": "NEUTRAL",
            "nearest_m5_ob": None,
            "nearest_m5_fvg": None,
            "nearest_m15_ob": None,
            "nearest_m15_fvg": None,
        }

    market_map = snapshot.get(
        "market_map",
        {},
    )

    confluence = snapshot.get(
        "confluence",
        {},
    )

    directional = snapshot.get(
        "directional",
        {},
    )

    m5 = market_map.get(
        "m5",
        {},
    )

    m15 = market_map.get(
        "m15",
        {},
    )

    return {
        "price": snapshot.get(
            "current_price"
        ),
        "m5_confluence": confluence.get(
            "m5",
            "NONE",
        ),
        "m15_confluence": confluence.get(
            "m15",
            "NONE",
        ),
        "overall_confluence": confluence.get(
            "overall",
            "NONE",
        ),
        "m5_direction": directional.get(
            "m5",
            "NEUTRAL",
        ),
        "m15_direction": directional.get(
            "m15",
            "NEUTRAL",
        ),
        "overall_direction": directional.get(
            "overall",
            "NEUTRAL",
        ),
        "nearest_m5_ob": format_v24_zone(
            m5.get(
                "nearest_order_block"
            )
        ),
        "nearest_m5_fvg": format_v24_zone(
            m5.get(
                "nearest_fvg"
            )
        ),
        "nearest_m15_ob": format_v24_zone(
            m15.get(
                "nearest_order_block"
            )
        ),
        "nearest_m15_fvg": format_v24_zone(
            m15.get(
                "nearest_fvg"
            )
        ),
}
# ============================================================
# QUANTUM X V2.4 — ORDER BLOCKS + FAIR VALUE GAPS
# HALF 3 — PART 3A
# ============================================================


# ------------------------------------------------------------
# V2.4 DISPLAY HELPERS
# ------------------------------------------------------------

def format_v24_price(
    value,
):
    """
    Format a price safely for display.
    """

    if value is None:
        return "—"

    try:
        return f"{float(value):,.2f}"

    except (
        TypeError,
        ValueError,
    ):
        return "—"


def format_v24_distance(
    value,
):
    """
    Format zone distance safely.
    """

    if value is None:
        return "—"

    try:
        return f"{float(value):,.2f}"

    except (
        TypeError,
        ValueError,
    ):
        return "—"


def v24_zone_type_label(
    zone_type,
):
    """
    Human-readable zone label.
    """

    if zone_type == "BULLISH":
        return "🟢 Bullish"

    if zone_type == "BEARISH":
        return "🔴 Bearish"

    return "⚪ Neutral"


def v24_location_label(
    location,
):
    """
    Human-readable price location.
    """

    if location == "INSIDE":
        return "📍 Inside"

    if location == "ABOVE":
        return "⬆️ Above"

    if location == "BELOW":
        return "⬇️ Below"

    return "—"


# ------------------------------------------------------------
# V2.4 ORDER BLOCK TABLE
# ------------------------------------------------------------

def render_v24_order_block_table(
    order_blocks,
    title,
):
    """
    Render Order Blocks for one timeframe.
    """

    st.markdown(
        f"#### {title}"
    )

    if not order_blocks:

        st.info(
            "No valid Order Blocks detected."
        )

        return

    rows = []

    for block in order_blocks:

        rows.append(
            {
                "Type": (
                    v24_zone_type_label(
                        block.get(
                            "type"
                        )
                    )
                ),
                "Time": str(
                    block.get(
                        "time",
                        "—",
                    )
                ),
                "Low": format_v24_price(
                    block.get(
                        "lower"
                    )
                ),
                "High": format_v24_price(
                    block.get(
                        "upper"
                    )
                ),
                "Distance": (
                    format_v24_distance(
                        block.get(
                            "distance"
                        )
                    )
                ),
                "Price": (
                    v24_location_label(
                        block.get(
                            "price_location"
                        )
                    )
                ),
                "Active": (
                    "YES"
                    if block.get(
                        "active",
                        True,
                    )
                    else "NO"
                ),
            }
        )

    st.dataframe(
        pd.DataFrame(
            rows
        ),
        use_container_width=True,
        hide_index=True,
    )


# ------------------------------------------------------------
# V2.4 FVG TABLE
# ------------------------------------------------------------

def render_v24_fvg_table(
    fvgs,
    title,
):
    """
    Render Fair Value Gaps for one timeframe.
    """

    st.markdown(
        f"#### {title}"
    )

    if not fvgs:

        st.info(
            "No valid Fair Value Gaps detected."
        )

        return

    rows = []

    for gap in fvgs:

        rows.append(
            {
                "Type": (
                    v24_zone_type_label(
                        gap.get(
                            "type"
                        )
                    )
                ),
                "Time": str(
                    gap.get(
                        "time",
                        "—",
                    )
                ),
                "Lower": format_v24_price(
                    gap.get(
                        "lower"
                    )
                ),
                "Upper": format_v24_price(
                    gap.get(
                        "upper"
                    )
                ),
                "Size": format_v24_distance(
                    gap.get(
                        "size"
                    )
                ),
                "Distance": (
                    format_v24_distance(
                        gap.get(
                            "distance"
                        )
                    )
                ),
                "Price": (
                    v24_location_label(
                        gap.get(
                            "price_location"
                        )
                    )
                ),
                "Filled": (
                    "YES"
                    if gap.get(
                        "filled",
                        False,
                    )
                    else "NO"
                ),
                "Active": (
                    "YES"
                    if gap.get(
                        "active",
                        True,
                    )
                    else "NO"
                ),
            }
        )

    st.dataframe(
        pd.DataFrame(
            rows
        ),
        use_container_width=True,
        hide_index=True,
    )


# ------------------------------------------------------------
# V2.4 CONFLUENCE DISPLAY
# ------------------------------------------------------------

def render_v24_confluence(
    snapshot,
):
    """
    Render high-level V2.4 zone confluence.
    """

    if not snapshot:
        return

    summary = get_v24_summary(
        snapshot
    )

    st.markdown(
        "### 🧩 V2.4 Zone Confluence"
    )

    col1, col2, col3 = st.columns(
        3
    )

    with col1:

        st.metric(
            "M5 Zone",
            summary[
                "m5_confluence"
            ],
        )

    with col2:

        st.metric(
            "M15 Zone",
            summary[
                "m15_confluence"
            ],
        )

    with col3:

        st.metric(
            "Overall",
            summary[
                "overall_confluence"
            ],
        )

    st.caption(
        "Order Blocks and FVGs are context only at V2.4. "
        "No trade decision is generated here."
    )


# ------------------------------------------------------------
# V2.4 DIRECTIONAL DISPLAY
# ------------------------------------------------------------

def render_v24_directional_context(
    snapshot,
):
    """
    Render directional pressure from OB/FVG zones.
    """

    if not snapshot:
        return

    summary = get_v24_summary(
        snapshot
    )

    st.markdown(
        "### 🧭 V2.4 Zone Direction"
    )

    col1, col2, col3 = st.columns(
        3
    )

    with col1:

        st.metric(
            "M5",
            summary[
                "m5_direction"
            ],
        )

    with col2:

        st.metric(
            "M15",
            summary[
                "m15_direction"
            ],
        )

    with col3:

        st.metric(
            "Overall",
            summary[
                "overall_direction"
            ],
        )


# ------------------------------------------------------------
# V2.4 NEAREST ZONES
# ------------------------------------------------------------

def render_v24_nearest_zones(
    snapshot,
):
    """
    Display the nearest Order Blocks and FVGs.
    """

    if not snapshot:
        return

    summary = get_v24_summary(
        snapshot
    )

    st.markdown(
        "### 🎯 Nearest V2.4 Zones"
    )

    rows = []

    nearest_items = [
        (
            "M5",
            "Order Block",
            summary[
                "nearest_m5_ob"
            ],
        ),
        (
            "M5",
            "FVG",
            summary[
                "nearest_m5_fvg"
            ],
        ),
        (
            "M15",
            "Order Block",
            summary[
                "nearest_m15_ob"
            ],
        ),
        (
            "M15",
            "FVG",
            summary[
                "nearest_m15_fvg"
            ],
        ),
    ]

    for timeframe, zone_name, zone in (
        nearest_items
    ):

        if zone is None:

            rows.append(
                {
                    "Timeframe": timeframe,
                    "Zone": zone_name,
                    "Type": "—",
                    "Lower": "—",
                    "Upper": "—",
                    "Distance": "—",
                    "Price": "—",
                }
            )

            continue

        rows.append(
            {
                "Timeframe": timeframe,
                "Zone": zone_name,
                "Type": (
                    v24_zone_type_label(
                        zone.get(
                            "type"
                        )
                    )
                ),
                "Lower": (
                    format_v24_price(
                        zone.get(
                            "lower"
                        )
                    )
                ),
                "Upper": (
                    format_v24_price(
                        zone.get(
                            "upper"
                        )
                    )
                ),
                "Distance": (
                    format_v24_distance(
                        zone.get(
                            "distance"
                        )
                    )
                ),
                "Price": (
                    v24_location_label(
                        zone.get(
                            "location"
                        )
                    )
                ),
            }
        )

    st.dataframe(
        pd.DataFrame(
            rows
        ),
        use_container_width=True,
        hide_index=True,
)
# ============================================================
# QUANTUM X V2.4 — ORDER BLOCKS + FAIR VALUE GAPS
# HALF 3 — PART 3B
# ============================================================


# ------------------------------------------------------------
# V2.4 COMPLETE DASHBOARD
# ------------------------------------------------------------

def render_v24_dashboard(
    snapshot,
):
    """
    Render the complete V2.4 Order Block + FVG dashboard.
    """

    if not snapshot:
        return

    market_map = snapshot.get(
        "market_map",
        {},
    )

    m5 = market_map.get(
        "m5",
        {},
    )

    m15 = market_map.get(
        "m15",
        {},
    )

    current_price = snapshot.get(
        "current_price"
    )

    st.markdown(
        "## 🧱 Quantum X V2.4 — Order Blocks + FVG"
    )

    st.caption(
        "V2.4 analysis layer: Order Blocks, Fair Value Gaps "
        "and multi-timeframe zone context."
    )

    # --------------------------------------------------------
    # CURRENT PRICE
    # --------------------------------------------------------

    st.metric(
        "IG XAU/USD Price",
        format_v24_price(
            current_price
        ),
    )

    # --------------------------------------------------------
    # CONFLUENCE
    # --------------------------------------------------------

    render_v24_confluence(
        snapshot
    )

    render_v24_directional_context(
        snapshot
    )

    render_v24_nearest_zones(
        snapshot
    )

    # --------------------------------------------------------
    # M5 ZONES
    # --------------------------------------------------------

    st.markdown(
        "## 🕐 M5 Zones"
    )

    render_v24_order_block_table(
        m5.get(
            "order_blocks",
            [],
        ),
        "M5 Order Blocks",
    )

    render_v24_fvg_table(
        m5.get(
            "fvgs",
            [],
        ),
        "M5 Fair Value Gaps",
    )

    # --------------------------------------------------------
    # M15 ZONES
    # --------------------------------------------------------

    st.markdown(
        "## 🕒 M15 Zones"
    )

    render_v24_order_block_table(
        m15.get(
            "order_blocks",
            [],
        ),
        "M15 Order Blocks",
    )

    render_v24_fvg_table(
        m15.get(
            "fvgs",
            [],
        ),
        "M15 Fair Value Gaps",
    )

    # --------------------------------------------------------
    # VOLATILITY
    # --------------------------------------------------------

    st.markdown(
        "### 📏 V2.4 Volatility"
    )

    col1, col2 = st.columns(
        2
    )

    with col1:

        st.metric(
            "M5 ATR",
            format_v24_distance(
                m5.get(
                    "atr"
                )
            ),
        )

    with col2:

        st.metric(
            "M15 ATR",
            format_v24_distance(
                m15.get(
                    "atr"
                )
            ),
        )

    st.caption(
        "V2.4 does not place trades. "
        "Zones are currently analytical context only."
    )


# ============================================================
# V2.4 LIVE ENGINE INTEGRATION
# ============================================================

def render_v24_live_layer():
    """
    Build and render the V2.4 layer from the current
    live M5/M15 data.
    """

    if (
        "m5_bars"
        not in st.session_state
    ):
        return

    if (
        "m15_bars"
        not in st.session_state
    ):
        return

    if (
        "live_mid"
        not in st.session_state
    ):
        return

    m5_df = st.session_state[
        "m5_bars"
    ]

    m15_df = st.session_state[
        "m15_bars"
    ]

    current_price = (
        st.session_state[
            "live_mid"
        ]
    )

    if (
        m5_df is None
        or m15_df is None
    ):
        return

    if (
        m5_df.empty
        or m15_df.empty
    ):
        return

    snapshot = build_v24_snapshot(
        m5_df,
        m15_df,
        current_price,
    )

    render_v24_dashboard(
        snapshot
    )


# ============================================================
# V2.4 INTEGRATION MARKER
# ============================================================

V24_READY = True
# ============================================================
# QUANTUM X V2.5 — VWAP + ATR + VOLUME
# HALF 1 — PART 1A
# ============================================================


# ------------------------------------------------------------
# V2.5 CONFIGURATION
# ------------------------------------------------------------

V25_VWAP_MIN_BARS = 3

V25_VOLUME_LOOKBACK = 20

V25_VOLUME_EXPANSION_MULTIPLIER = 1.50
V25_VOLUME_CONTRACTION_MULTIPLIER = 0.75

V25_ATR_PERIOD = 14


# ------------------------------------------------------------
# V2.5 COMPLETED CANDLE HELPER
# ------------------------------------------------------------

def get_v25_completed_candles(
    df,
    timeframe_minutes,
):
    """
    Return completed candles only.

    The currently forming candle is excluded.
    """

    if df is None or df.empty:
        return pd.DataFrame()

    data = df.copy()

    if "time" not in data.columns:
        return pd.DataFrame()

    data["time"] = pd.to_datetime(
        data["time"],
        utc=True,
        errors="coerce",
    )

    data = data.dropna(
        subset=["time"]
    ).copy()

    if data.empty:
        return data

    now_utc = pd.Timestamp.now(
        tz="UTC"
    )

    current_bucket = (
        now_utc.floor(
            f"{timeframe_minutes}min"
        )
    )

    data = data[
        data["time"] < current_bucket
    ].copy()

    return data.reset_index(
        drop=True
    )


# ------------------------------------------------------------
# V2.5 TYPICAL PRICE
# ------------------------------------------------------------

def calculate_v25_typical_price(
    df,
):
    """
    Calculate candle typical price:

        (High + Low + Close) / 3
    """

    if df is None or df.empty:
        return pd.Series(
            dtype=float
        )

    required = {
        "high",
        "low",
        "close",
    }

    if not required.issubset(
        set(df.columns)
    ):
        return pd.Series(
            dtype=float
        )

    return (
        df["high"]
        + df["low"]
        + df["close"]
    ) / 3.0


# ------------------------------------------------------------
# V2.5 VOLUME PREPARATION
# ------------------------------------------------------------

def get_v25_volume_series(
    df,
):
    """
    Return the volume series when available.

    The function safely handles feeds where volume
    is missing or unusable.
    """

    if df is None or df.empty:
        return pd.Series(
            dtype=float
        )

    if "volume" not in df.columns:
        return pd.Series(
            dtype=float
        )

    volume = pd.to_numeric(
        df["volume"],
        errors="coerce",
    )

    volume = volume.replace(
        [float("inf"), float("-inf")],
        pd.NA,
    )

    return volume.astype(
        "float64"
    )


# ------------------------------------------------------------
# V2.5 VWAP CALCULATION
# ------------------------------------------------------------

def calculate_v25_vwap(
    df,
):
    """
    Calculate session VWAP.

    VWAP =
        cumulative(price * volume)
        /
        cumulative(volume)

    Uses typical price as the price component.
    """

    if df is None or df.empty:
        return None

    data = df.copy()

    typical_price = (
        calculate_v25_typical_price(
            data
        )
    )

    volume = (
        get_v25_volume_series(
            data
        )
    )

    if typical_price.empty:
        return None

    if volume.empty:
        return None

    valid = (
        typical_price.notna()
        & volume.notna()
        & (volume > 0)
    )

    if not valid.any():
        return None

    price = (
        typical_price[
            valid
        ]
    )

    vol = (
        volume[
            valid
        ]
    )

    cumulative_volume = (
        vol.cumsum()
    )

    if cumulative_volume.empty:
        return None

    cumulative_pv = (
        (
            price * vol
        ).cumsum()
    )

    vwap_series = (
        cumulative_pv
        / cumulative_volume
    )

    if vwap_series.empty:
        return None

    latest_vwap = (
        vwap_series.iloc[-1]
    )

    if pd.isna(
        latest_vwap
    ):
        return None

    return float(
        latest_vwap
    )


# ------------------------------------------------------------
# V2.5 VWAP SERIES
# ------------------------------------------------------------

def calculate_v25_vwap_series(
    df,
):
    """
    Return the complete VWAP series for analysis/display.
    """

    if df is None or df.empty:
        return pd.Series(
            dtype=float
        )

    typical_price = (
        calculate_v25_typical_price(
            df
        )
    )

    volume = (
        get_v25_volume_series(
            df
        )
    )

    if (
        typical_price.empty
        or volume.empty
    ):
        return pd.Series(
            dtype=float
        )

    valid_volume = (
        volume.fillna(0.0)
        .clip(lower=0.0)
    )

    cumulative_volume = (
        valid_volume.cumsum()
    )

    cumulative_pv = (
        (
            typical_price
            * valid_volume
        ).cumsum()
    )

    result = (
        cumulative_pv
        / cumulative_volume.replace(
            0,
            pd.NA,
        )
    )

    return result.astype(
        "float64"
    )


# ------------------------------------------------------------
# V2.5 PRICE VS VWAP
# ------------------------------------------------------------

def classify_v25_vwap_location(
    current_price,
    vwap,
):
    """
    Classify current price relative to VWAP.
    """

    if (
        current_price is None
        or vwap is None
    ):
        return "UNKNOWN"

    current_price = float(
        current_price
    )

    vwap = float(
        vwap
    )

    if current_price > vwap:
        return "ABOVE"

    if current_price < vwap:
        return "BELOW"

    return "AT_VWAP"


def calculate_v25_vwap_distance(
    current_price,
    vwap,
):
    """
    Calculate absolute price distance from VWAP.
    """

    if (
        current_price is None
        or vwap is None
    ):
        return None

    return abs(
        float(current_price)
        - float(vwap)
        )
# ============================================================
# QUANTUM X V2.5 — VWAP + ATR + VOLUME
# HALF 1 — PART 1B
# ============================================================


# ------------------------------------------------------------
# V2.5 TRUE RANGE
# ------------------------------------------------------------

def calculate_v25_true_range(
    df,
):
    """
    Calculate candle-by-candle True Range.
    """

    if df is None or df.empty:
        return pd.Series(
            dtype=float
        )

    required = {
        "high",
        "low",
        "close",
    }

    if not required.issubset(
        set(df.columns)
    ):
        return pd.Series(
            dtype=float
        )

    previous_close = (
        df["close"].shift(1)
    )

    range_one = (
        df["high"]
        - df["low"]
    )

    range_two = (
        df["high"]
        - previous_close
    ).abs()

    range_three = (
        df["low"]
        - previous_close
    ).abs()

    true_range = pd.concat(
        [
            range_one,
            range_two,
            range_three,
        ],
        axis=1,
    ).max(
        axis=1
    )

    return true_range


# ------------------------------------------------------------
# V2.5 ATR
# ------------------------------------------------------------

def calculate_v25_atr(
    df,
    period=V25_ATR_PERIOD,
):
    """
    Calculate Average True Range.
    """

    if df is None or df.empty:
        return None

    true_range = (
        calculate_v25_true_range(
            df
        )
    )

    if true_range.empty:
        return None

    atr = (
        true_range
        .rolling(
            period,
            min_periods=period,
        )
        .mean()
    )

    if atr.empty:
        return None

    latest = atr.iloc[-1]

    if pd.isna(latest):
        return None

    return float(
        latest
    )


# ------------------------------------------------------------
# V2.5 ATR SERIES
# ------------------------------------------------------------

def calculate_v25_atr_series(
    df,
    period=V25_ATR_PERIOD,
):
    """
    Return the complete ATR series.
    """

    if df is None or df.empty:
        return pd.Series(
            dtype=float
        )

    true_range = (
        calculate_v25_true_range(
            df
        )
    )

    if true_range.empty:
        return pd.Series(
            dtype=float
        )

    return (
        true_range
        .rolling(
            period,
            min_periods=period,
        )
        .mean()
    )


# ------------------------------------------------------------
# V2.5 VOLUME AVERAGE
# ------------------------------------------------------------

def calculate_v25_average_volume(
    df,
    lookback=V25_VOLUME_LOOKBACK,
):
    """
    Calculate recent average volume.
    """

    volume = (
        get_v25_volume_series(
            df
        )
    )

    if volume.empty:
        return None

    valid_volume = (
        volume.dropna()
    )

    if valid_volume.empty:
        return None

    average = (
        valid_volume
        .rolling(
            lookback,
            min_periods=1,
        )
        .mean()
        .iloc[-1]
    )

    if pd.isna(
        average
    ):
        return None

    return float(
        average
    )


# ------------------------------------------------------------
# V2.5 CURRENT VOLUME
# ------------------------------------------------------------

def get_v25_current_volume(
    df,
):
    """
    Return the latest completed candle volume.
    """

    volume = (
        get_v25_volume_series(
            df
        )
    )

    if volume.empty:
        return None

    valid = (
        volume.dropna()
    )

    if valid.empty:
        return None

    latest = valid.iloc[-1]

    if pd.isna(latest):
        return None

    return float(
        latest
    )


# ------------------------------------------------------------
# V2.5 RELATIVE VOLUME
# ------------------------------------------------------------

def calculate_v25_relative_volume(
    df,
    lookback=V25_VOLUME_LOOKBACK,
):
    """
    Compare latest volume with its recent average.

    Example:
        1.00 = average volume
        1.50 = 150% of average
        2.00 = 200% of average
    """

    current_volume = (
        get_v25_current_volume(
            df
        )
    )

    average_volume = (
        calculate_v25_average_volume(
            df,
            lookback,
        )
    )

    if (
        current_volume is None
        or average_volume is None
        or average_volume <= 0
    ):
        return None

    return float(
        current_volume
        / average_volume
    )


# ------------------------------------------------------------
# V2.5 VOLUME STATE
# ------------------------------------------------------------

def classify_v25_volume_state(
    relative_volume,
):
    """
    Classify current volume conditions.
    """

    if relative_volume is None:
        return "UNAVAILABLE"

    relative_volume = float(
        relative_volume
    )

    if (
        relative_volume
        >= V25_VOLUME_EXPANSION_MULTIPLIER
    ):
        return "EXPANSION"

    if (
        relative_volume
        <= V25_VOLUME_CONTRACTION_MULTIPLIER
    ):
        return "CONTRACTION"

    return "NORMAL"


# ------------------------------------------------------------
# V2.5 VOLATILITY STATE
# ------------------------------------------------------------

def classify_v25_volatility(
    df,
):
    """
    Classify ATR relative to recent ATR values.

    This is a context measurement, not a trade signal.
    """

    atr_series = (
        calculate_v25_atr_series(
            df
        )
    )

    if atr_series.empty:
        return {
            "atr": None,
            "average_atr": None,
            "relative_atr": None,
            "state": "UNAVAILABLE",
        }

    valid = (
        atr_series.dropna()
    )

    if valid.empty:
        return {
            "atr": None,
            "average_atr": None,
            "relative_atr": None,
            "state": "UNAVAILABLE",
        }

    current_atr = float(
        valid.iloc[-1]
    )

    recent_average = float(
        valid.tail(
            V25_VOLUME_LOOKBACK
        ).mean()
    )

    if recent_average <= 0:
        return {
            "atr": current_atr,
            "average_atr": recent_average,
            "relative_atr": None,
            "state": "UNAVAILABLE",
        }

    relative_atr = (
        current_atr
        / recent_average
    )

    if relative_atr >= 1.25:
        state = "EXPANDING"

    elif relative_atr <= 0.75:
        state = "CONTRACTING"

    else:
        state = "NORMAL"

    return {
        "atr": current_atr,
        "average_atr": recent_average,
        "relative_atr": float(
            relative_atr
        ),
        "state": state,
    }


# ------------------------------------------------------------
# V2.5 TIMEFRAME METRICS
# ------------------------------------------------------------

def build_v25_timeframe_metrics(
    df,
    timeframe_minutes,
    current_price,
):
    """
    Build VWAP, ATR and volume metrics for one timeframe.
    """

    completed = (
        get_v25_completed_candles(
            df,
            timeframe_minutes,
        )
    )

    if completed.empty:
        return {
            "timeframe": timeframe_minutes,
            "bars": 0,
            "vwap": None,
            "vwap_location": "UNKNOWN",
            "vwap_distance": None,
            "atr": None,
            "volume": None,
            "average_volume": None,
            "relative_volume": None,
            "volume_state": "UNAVAILABLE",
            "volatility": {
                "atr": None,
                "average_atr": None,
                "relative_atr": None,
                "state": "UNAVAILABLE",
            },
        }

    vwap = (
        calculate_v25_vwap(
            completed
        )
    )

    atr = (
        calculate_v25_atr(
            completed
        )
    )

    current_volume = (
        get_v25_current_volume(
            completed
        )
    )

    average_volume = (
        calculate_v25_average_volume(
            completed
        )
    )

    relative_volume = (
        calculate_v25_relative_volume(
            completed
        )
    )

    return {
        "timeframe": timeframe_minutes,
        "bars": len(
            completed
        ),
        "vwap": vwap,
        "vwap_location": (
            classify_v25_vwap_location(
                current_price,
                vwap,
            )
        ),
        "vwap_distance": (
            calculate_v25_vwap_distance(
                current_price,
                vwap,
            )
        ),
        "atr": atr,
        "volume": current_volume,
        "average_volume": average_volume,
        "relative_volume": relative_volume,
        "volume_state": (
            classify_v25_volume_state(
                relative_volume
            )
        ),
        "volatility": (
            classify_v25_volatility(
                completed
            )
        ),
}
# ============================================================
# QUANTUM X V2.4 — DIAGNOSTIC MODE
# HALF 1 — PART 1A
# ============================================================
#
# Diagnostic-only layer.
#
# PURPOSE:
#   Measure the raw candle environment BEFORE we loosen or
#   tighten the existing V2.4 Order Block / FVG detector.
#
# IMPORTANT:
#   - Does NOT place trades.
#   - Does NOT modify existing V2.4 detection.
#   - Does NOT change thresholds.
#   - Uses COMPLETED candles only.
# ============================================================


# ------------------------------------------------------------
# V2.4 DIAGNOSTIC CONFIGURATION
# ------------------------------------------------------------

V24_DIAGNOSTIC_LOOKBACK_M5 = 180
V24_DIAGNOSTIC_LOOKBACK_M15 = 120


# ------------------------------------------------------------
# V2.4 DIAGNOSTIC DATA PREPARATION
# ------------------------------------------------------------

def prepare_v24_diagnostic_data(
    df,
    timeframe,
    lookback,
):
    """
    Prepare completed candles for V2.4 diagnostics.

    The diagnostic intentionally uses the same completed-candle
    principle as the existing structure engine.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    try:
        data = df.copy()
        data = data.sort_index()

        current_bucket = floor_time(
            utc_now(),
            timeframe,
        )

        data = data[
            data.index < current_bucket
        ].copy()

        if lookback is not None:
            data = data.tail(
                int(lookback)
            ).copy()

        required_columns = {
            "open",
            "high",
            "low",
            "close",
        }

        if not required_columns.issubset(
            data.columns
        ):
            return pd.DataFrame()

        for column in required_columns:
            data[column] = pd.to_numeric(
                data[column],
                errors="coerce",
            )

        data = data.dropna(
            subset=[
                "open",
                "high",
                "low",
                "close",
            ]
        )

        return data

    except Exception:
        return pd.DataFrame()


# ------------------------------------------------------------
# V2.4 DIAGNOSTIC CANDLE METRICS
# ------------------------------------------------------------

def calculate_v24_diagnostic_metrics(
    df,
):
    """
    Calculate basic candle statistics used by the diagnostic.

    This does not decide whether a candle is an OB or FVG.
    """
    if df is None or df.empty:
        return {
            "bars": 0,
            "atr": 0.0,
            "average_range": 0.0,
            "average_body": 0.0,
            "largest_range": 0.0,
            "largest_body": 0.0,
        }

    data = df.copy()

    data["range"] = (
        data["high"]
        - data["low"]
    ).abs()

    data["body"] = (
        data["close"]
        - data["open"]
    ).abs()

    data["body_ratio"] = 0.0

    valid_range = data["range"] > 0

    data.loc[
        valid_range,
        "body_ratio",
    ] = (
        data.loc[
            valid_range,
            "body",
        ]
        / data.loc[
            valid_range,
            "range",
        ]
    )

    previous_close = data[
        "close"
    ].shift(1)

    true_range = pd.concat(
        [
            data["high"] - data["low"],
            (
                data["high"]
                - previous_close
            ).abs(),
            (
                data["low"]
                - previous_close
            ).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr_series = true_range.rolling(
        window=14,
        min_periods=7,
    ).mean()

    atr = (
        float(atr_series.iloc[-1])
        if not pd.isna(
            atr_series.iloc[-1]
        )
        else 0.0
    )

    return {
        "bars": int(len(data)),
        "atr": atr,
        "average_range": float(
            data["range"].mean()
        ),
        "average_body": float(
            data["body"].mean()
        ),
        "largest_range": float(
            data["range"].max()
        ),
        "largest_body": float(
            data["body"].max()
        ),
    }


# ------------------------------------------------------------
# V2.4 DIAGNOSTIC CANDLE CLASSIFICATION
# ------------------------------------------------------------

def classify_v24_diagnostic_candles(
    df,
    atr,
):
    """
    Count basic displacement candidates without applying the
    existing V2.4 Order Block detector.

    This is deliberately permissive.

    A displacement candidate is simply a candle whose range is
    at least the configured displacement ATR multiple.

    Body-ratio filtering is reported separately.
    """
    result = {
        "total_candles": 0,
        "range_candidates": 0,
        "body_candidates": 0,
        "bullish_candidates": 0,
        "bearish_candidates": 0,
        "zero_range_candles": 0,
    }

    if df is None or df.empty:
        return result

    data = df.copy()

    for column in [
        "open",
        "high",
        "low",
        "close",
    ]:
        data[column] = pd.to_numeric(
            data[column],
            errors="coerce",
        )

    data = data.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
        ]
    )

    result[
        "total_candles"
    ] = int(len(data))

    if data.empty:
        return result

    for _, row in data.iterrows():

        candle_range = abs(
            float(row["high"])
            - float(row["low"])
        )

        candle_body = abs(
            float(row["close"])
            - float(row["open"])
        )

        if candle_range <= 0:
            result[
                "zero_range_candles"
            ] += 1
            continue

        body_ratio = (
            candle_body
            / candle_range
        )

        if (
            atr > 0
            and candle_range
            >= (
                atr
                * V24_DISPLACEMENT_ATR
            )
        ):
            result[
                "range_candidates"
            ] += 1

            if (
                body_ratio
                >= 0.60
            ):
                result[
                    "body_candidates"
                ] += 1

                if float(
                    row["close"]
                ) > float(
                    row["open"]
                ):
                    result[
                        "bullish_candidates"
                    ] += 1

                elif float(
                    row["close"]
                ) < float(
                    row["open"]
                ):
                    result[
                        "bearish_candidates"
                    ] += 1

    return result


# ------------------------------------------------------------
# V2.4 DIAGNOSTIC TIMEFRAME ANALYSIS
# ------------------------------------------------------------

def build_v24_diagnostic_timeframe(
    df,
    timeframe,
    lookback,
):
    """
    Build the Part 1A diagnostic snapshot for one timeframe.
    """
    data = prepare_v24_diagnostic_data(
        df,
        timeframe,
        lookback,
    )

    if data.empty:
        return {
            "timeframe": timeframe,
            "status": "NO DATA",
            "metrics": {
                "bars": 0,
                "atr": 0.0,
                "average_range": 0.0,
                "average_body": 0.0,
                "largest_range": 0.0,
                "largest_body": 0.0,
            },
            "candidates": {
                "total_candles": 0,
                "range_candidates": 0,
                "body_candidates": 0,
                "bullish_candidates": 0,
                "bearish_candidates": 0,
                "zero_range_candles": 0,
            },
        }

    metrics = (
        calculate_v24_diagnostic_metrics(
            data
        )
    )

    candidates = (
        classify_v24_diagnostic_candles(
            data,
            metrics["atr"],
        )
    )

    return {
        "timeframe": timeframe,
        "status": "READY",
        "metrics": metrics,
        "candidates": candidates,
    }


# ------------------------------------------------------------
# V2.4 DIAGNOSTIC SNAPSHOT
# ------------------------------------------------------------

def build_v24_diagnostic_snapshot():
    """
    Build M5 and M15 diagnostic information.

    No trading decision is made here.
    """
    m5_df = st.session_state.get(
        "m5_bars",
        pd.DataFrame(),
    )

    m15_df = st.session_state.get(
        "m15_bars",
        pd.DataFrame(),
    )

    m5 = build_v24_diagnostic_timeframe(
        m5_df,
        M5,
        V24_DIAGNOSTIC_LOOKBACK_M5,
    )

    m15 = build_v24_diagnostic_timeframe(
        m15_df,
        M15,
        V24_DIAGNOSTIC_LOOKBACK_M15,
    )

    return {
        "status": (
            "READY"
            if (
                m5["status"] == "READY"
                or m15["status"] == "READY"
            )
            else "NO DATA"
        ),
        "m5": m5,
        "m15": m15,
        "generated_at": utc_now(),
    }


# ------------------------------------------------------------
# V2.4 DIAGNOSTIC TEXT HELPERS
# ------------------------------------------------------------

def v24_diagnostic_percentage(
    numerator,
    denominator,
):
    """
    Safely calculate a diagnostic percentage.
    """
    try:
        denominator = float(
            denominator
        )

        if denominator <= 0:
            return 0.0

        return (
            float(numerator)
            / denominator
        ) * 100.0

    except (
        TypeError,
        ValueError,
    ):
        return 0.0


def v24_diagnostic_status(
    count,
):
    """
    Convert a diagnostic count into a simple status.
    """
    try:
        value = int(count)
    except (
        TypeError,
        ValueError,
    ):
        value = 0

    if value > 0:
        return "DETECTED"

    return "NONE"
# ============================================================
# QUANTUM X V2.4 — DIAGNOSTIC MODE
# HALF 1 — PART 1B
# ============================================================
#
# RAW ORDER BLOCK + FVG STRUCTURE COUNTS
#
# PURPOSE:
#   Detect raw structural candidates BEFORE the existing
#   V2.4 filters are applied.
#
# IMPORTANT:
#   - Diagnostic only.
#   - No trading.
#   - Does not modify V2.4 detector functions.
#   - Does not modify V2.5 VWAP / ATR / Volume logic.
# ============================================================


# ------------------------------------------------------------
# RAW DISPLACEMENT CANDIDATES
# ------------------------------------------------------------

def detect_v24_diagnostic_displacements(
    df,
    atr,
):
    """
    Detect broad displacement candidates.

    This deliberately uses fewer restrictions than the actual
    V2.4 OB detector.

    The purpose is to answer:

        "Are there actually large directional candles?"

    A candle qualifies when its range is at least the configured
    displacement ATR multiple.
    """

    candidates = []

    if df is None or df.empty:
        return candidates

    if atr is None or atr <= 0:
        return candidates

    data = df.copy()

    required_columns = {
        "open",
        "high",
        "low",
        "close",
    }

    if not required_columns.issubset(
        data.columns
    ):
        return candidates

    data = data.sort_index()

    for index, row in data.iterrows():

        try:
            candle_open = float(
                row["open"]
            )
            candle_high = float(
                row["high"]
            )
            candle_low = float(
                row["low"]
            )
            candle_close = float(
                row["close"]
            )
        except (
            TypeError,
            ValueError,
        ):
            continue

        candle_range = (
            candle_high
            - candle_low
        )

        candle_body = abs(
            candle_close
            - candle_open
        )

        if candle_range <= 0:
            continue

        body_ratio = (
            candle_body
            / candle_range
        )

        atr_multiple = (
            candle_range
            / atr
        )

        if (
            atr_multiple
            >= V24_DISPLACEMENT_ATR
        ):

            if candle_close > candle_open:
                direction = "BULLISH"

            elif candle_close < candle_open:
                direction = "BEARISH"

            else:
                direction = "NEUTRAL"

            candidates.append(
                {
                    "timestamp": index,
                    "open": candle_open,
                    "high": candle_high,
                    "low": candle_low,
                    "close": candle_close,
                    "range": candle_range,
                    "body": candle_body,
                    "body_ratio": body_ratio,
                    "atr_multiple": atr_multiple,
                    "direction": direction,
                }
            )

    return candidates


# ------------------------------------------------------------
# RAW ORDER-BLOCK CANDIDATES
# ------------------------------------------------------------

def detect_v24_diagnostic_order_blocks(
    df,
    displacement_candidates,
):
    """
    Detect broad potential Order Blocks.

    Definition used here:

        Bullish displacement
            -> previous candle becomes a potential bearish OB

        Bearish displacement
            -> previous candle becomes a potential bullish OB

    No ATR range filter is applied to the OB candle here.

    This is intentionally broader than the production detector.
    """

    order_blocks = []

    if df is None or df.empty:
        return order_blocks

    if not displacement_candidates:
        return order_blocks

    data = df.copy()
    data = data.sort_index()

    for displacement in displacement_candidates:

        timestamp = displacement[
            "timestamp"
        ]

        try:
            position = data.index.get_loc(
                timestamp
            )
        except (
            KeyError,
            TypeError,
        ):
            continue

        if isinstance(
            position,
            slice,
        ):
            continue

        if position <= 0:
            continue

        previous_timestamp = (
            data.index[position - 1]
        )

        previous = data.loc[
            previous_timestamp
        ]

        try:
            previous_open = float(
                previous["open"]
            )
            previous_high = float(
                previous["high"]
            )
            previous_low = float(
                previous["low"]
            )
            previous_close = float(
                previous["close"]
            )
        except (
            TypeError,
            ValueError,
        ):
            continue

        previous_range = (
            previous_high
            - previous_low
        )

        if previous_range <= 0:
            continue

        previous_body = abs(
            previous_close
            - previous_open
        )

        previous_body_ratio = (
            previous_body
            / previous_range
        )

        displacement_direction = (
            displacement["direction"]
        )

        if (
            displacement_direction
            == "BULLISH"
            and previous_close
            < previous_open
        ):
            order_block_type = (
                "BULLISH_OB"
            )

        elif (
            displacement_direction
            == "BEARISH"
            and previous_close
            > previous_open
        ):
            order_block_type = (
                "BEARISH_OB"
            )

        else:
            continue

        order_blocks.append(
            {
                "timestamp": previous_timestamp,
                "displacement_timestamp": timestamp,
                "type": order_block_type,
                "open": previous_open,
                "high": previous_high,
                "low": previous_low,
                "close": previous_close,
                "range": previous_range,
                "body": previous_body,
                "body_ratio": previous_body_ratio,
                "displacement_range": displacement[
                    "range"
                ],
                "displacement_atr_multiple": displacement[
                    "atr_multiple"
                ],
            }
        )

    return order_blocks


# ------------------------------------------------------------
# RAW FVG CANDIDATES
# ------------------------------------------------------------

def detect_v24_diagnostic_fvgs(
    df,
):
    """
    Detect classic three-candle Fair Value Gaps.

    IMPORTANT:

    No ATR-size filter is applied here.

    This gives us the RAW number of FVG structures present
    in the candle data.

    Bullish FVG:

        Candle 3 low > Candle 1 high

    Bearish FVG:

        Candle 1 low > Candle 3 high
    """

    fvgs = []

    if df is None or df.empty:
        return fvgs

    data = df.copy()
    data = data.sort_index()

    if len(data) < 3:
        return fvgs

    for position in range(
        2,
        len(data),
    ):

        first = data.iloc[
            position - 2
        ]

        middle = data.iloc[
            position - 1
        ]

        third = data.iloc[
            position
        ]

        try:
            first_high = float(
                first["high"]
            )
            first_low = float(
                first["low"]
            )

            middle_high = float(
                middle["high"]
            )
            middle_low = float(
                middle["low"]
            )

            third_high = float(
                third["high"]
            )
            third_low = float(
                third["low"]
            )
        except (
            TypeError,
            ValueError,
        ):
            continue

        first_timestamp = data.index[
            position - 2
        ]

        middle_timestamp = data.index[
            position - 1
        ]

        third_timestamp = data.index[
            position
        ]

        # ----------------------------------------------------
        # BULLISH FVG
        # ----------------------------------------------------

        if third_low > first_high:

            gap_size = (
                third_low
                - first_high
            )

            fvgs.append(
                {
                    "timestamp": third_timestamp,
                    "first_timestamp": first_timestamp,
                    "middle_timestamp": middle_timestamp,
                    "type": "BULLISH_FVG",
                    "upper": third_low,
                    "lower": first_high,
                    "gap": gap_size,
                    "middle_range": (
                        middle_high
                        - middle_low
                    ),
                }
            )

        # ----------------------------------------------------
        # BEARISH FVG
        # ----------------------------------------------------

        elif first_low > third_high:

            gap_size = (
                first_low
                - third_high
            )

            fvgs.append(
                {
                    "timestamp": third_timestamp,
                    "first_timestamp": first_timestamp,
                    "middle_timestamp": middle_timestamp,
                    "type": "BEARISH_FVG",
                    "upper": first_low,
                    "lower": third_high,
                    "gap": gap_size,
                    "middle_range": (
                        middle_high
                        - middle_low
                    ),
                }
            )

    return fvgs


# ------------------------------------------------------------
# FVG ATR FILTER DIAGNOSTIC
# ------------------------------------------------------------

def classify_v24_diagnostic_fvgs(
    fvgs,
    atr,
):
    """
    Separate raw FVGs from FVGs that would pass the current
    V2.4 ATR minimum.

    This is diagnostic only.

    It does NOT call the production FVG detector.
    """

    result = {
        "raw": [],
        "atr_pass": [],
        "atr_rejected": [],
    }

    if not fvgs:
        return result

    if atr is None or atr <= 0:
        result[
            "atr_rejected"
        ] = list(fvgs)

        return result

    for fvg in fvgs:

        gap = float(
            fvg["gap"]
        )

        gap_atr_multiple = (
            gap
            / atr
        )

        diagnostic_fvg = dict(
            fvg
        )

        diagnostic_fvg[
            "gap_atr_multiple"
        ] = gap_atr_multiple

        result[
            "raw"
        ].append(
            diagnostic_fvg
        )

        if (
            gap_atr_multiple
            >= V24_FVG_ATR_MIN
        ):
            result[
                "atr_pass"
            ].append(
                diagnostic_fvg
            )

        else:
            result[
                "atr_rejected"
            ].append(
                diagnostic_fvg
            )

    return result


# ------------------------------------------------------------
# RAW OB FILTER DIAGNOSTIC
# ------------------------------------------------------------

def classify_v24_diagnostic_order_blocks(
    order_blocks,
    atr,
):
    """
    Separate potential OBs from OBs passing the current
    production OB range filter.

    This allows us to see whether the OB ATR limits are
    eliminating most candidates.
    """

    result = {
        "raw": [],
        "atr_pass": [],
        "atr_rejected": [],
    }

    if not order_blocks:
        return result

    if atr is None or atr <= 0:
        result[
            "atr_rejected"
        ] = list(order_blocks)

        return result

    for order_block in order_blocks:

        block_range = float(
            order_block["range"]
        )

        range_atr_multiple = (
            block_range
            / atr
        )

        diagnostic_ob = dict(
            order_block
        )

        diagnostic_ob[
            "range_atr_multiple"
        ] = range_atr_multiple

        result[
            "raw"
        ].append(
            diagnostic_ob
        )

        if (
            range_atr_multiple
            >= V24_OB_ATR_MIN
            and
            range_atr_multiple
            <= V24_OB_ATR_MAX
        ):
            result[
                "atr_pass"
            ].append(
                diagnostic_ob
            )

        else:
            result[
                "atr_rejected"
            ].append(
                diagnostic_ob
            )

    return result


# ------------------------------------------------------------
# COMPLETE RAW V2.4 STRUCTURE DIAGNOSTIC
# ------------------------------------------------------------

def build_v24_diagnostic_structure_analysis(
    df,
    timeframe,
    lookback,
):
    """
    Run the raw OB/FVG diagnostic pipeline for one timeframe.
    """

    data = prepare_v24_diagnostic_data(
        df,
        timeframe,
        lookback,
    )

    if data.empty:
        return {
            "timeframe": timeframe,
            "status": "NO DATA",
            "displacements": [],
            "order_blocks": {
                "raw": [],
                "atr_pass": [],
                "atr_rejected": [],
            },
            "fvgs": {
                "raw": [],
                "atr_pass": [],
                "atr_rejected": [],
            },
        }

    metrics = (
        calculate_v24_diagnostic_metrics(
            data
        )
    )

    atr = metrics[
        "atr"
    ]

    displacements = (
        detect_v24_diagnostic_displacements(
            data,
            atr,
        )
    )

    raw_order_blocks = (
        detect_v24_diagnostic_order_blocks(
            data,
            displacements,
        )
    )

    classified_order_blocks = (
        classify_v24_diagnostic_order_blocks(
            raw_order_blocks,
            atr,
        )
    )

    raw_fvgs = (
        detect_v24_diagnostic_fvgs(
            data
        )
    )

    classified_fvgs = (
        classify_v24_diagnostic_fvgs(
            raw_fvgs,
            atr,
        )
    )

    return {
        "timeframe": timeframe,
        "status": "READY",
        "bars": int(len(data)),
        "atr": float(atr),
        "displacements": displacements,
        "order_blocks": classified_order_blocks,
        "fvgs": classified_fvgs,
    }


# ------------------------------------------------------------
# DIAGNOSTIC COUNTER SUMMARY
# ------------------------------------------------------------

def summarize_v24_diagnostic_structure(
    analysis,
):
    """
    Convert the detailed diagnostic structures into simple
    counts suitable for the dashboard.
    """

    if not analysis:
        return {
            "status": "NO DATA",
        }

    if analysis.get(
        "status"
    ) != "READY":
        return {
            "status": "NO DATA",
        }

    displacements = analysis.get(
        "displacements",
        [],
    )

    order_blocks = analysis.get(
        "order_blocks",
        {},
    )

    fvgs = analysis.get(
        "fvgs",
        {},
    )

    return {
        "status": "READY",

        "bars": int(
            analysis.get(
                "bars",
                0,
            )
        ),

        "atr": float(
            analysis.get(
                "atr",
                0.0,
            )
        ),

        "displacement_candidates": int(
            len(displacements)
        ),

        "bullish_displacements": int(
            sum(
                1
                for item in displacements
                if item.get(
                    "direction"
                )
                == "BULLISH"
            )
        ),

        "bearish_displacements": int(
            sum(
                1
                for item in displacements
                if item.get(
                    "direction"
                )
                == "BEARISH"
            )
        ),

        "raw_order_blocks": int(
            len(
                order_blocks.get(
                    "raw",
                    [],
                )
            )
        ),

        "atr_pass_order_blocks": int(
            len(
                order_blocks.get(
                    "atr_pass",
                    [],
                )
            )
        ),

        "rejected_order_blocks": int(
            len(
                order_blocks.get(
                    "atr_rejected",
                    [],
                )
            )
        ),

        "raw_fvgs": int(
            len(
                fvgs.get(
                    "raw",
                    [],
                )
            )
        ),

        "atr_pass_fvgs": int(
            len(
                fvgs.get(
                    "atr_pass",
                    [],
                )
            )
        ),

        "rejected_fvgs": int(
            len(
                fvgs.get(
                    "atr_rejected",
                    [],
                )
            )
        ),
    }


# ------------------------------------------------------------
# V2.4 DIAGNOSTIC STRUCTURE SNAPSHOT
# ------------------------------------------------------------

def build_v24_diagnostic_structure_snapshot():
    """
    Build diagnostic information for both M5 and M15.
    """

    m5_df = st.session_state.get(
        "m5_bars",
        pd.DataFrame(),
    )

    m15_df = st.session_state.get(
        "m15_bars",
        pd.DataFrame(),
    )

    m5_analysis = (
        build_v24_diagnostic_structure_analysis(
            m5_df,
            M5,
            V24_DIAGNOSTIC_LOOKBACK_M5,
        )
    )

    m15_analysis = (
        build_v24_diagnostic_structure_analysis(
            m15_df,
            M15,
            V24_DIAGNOSTIC_LOOKBACK_M15,
        )
    )

    return {
        "m5": {
            "analysis": m5_analysis,
            "summary": (
                summarize_v24_diagnostic_structure(
                    m5_analysis
                )
            ),
        },

        "m15": {
            "analysis": m15_analysis,
            "summary": (
                summarize_v24_diagnostic_structure(
                    m15_analysis
                )
            ),
        },

        "generated_at": utc_now(),
    }


# ------------------------------------------------------------
# V2.4 DIAGNOSTIC READY FLAG
# ------------------------------------------------------------

V24_DIAGNOSTIC_PART_1B_READY = True
