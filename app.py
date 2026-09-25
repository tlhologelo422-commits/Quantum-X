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
