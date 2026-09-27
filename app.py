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
