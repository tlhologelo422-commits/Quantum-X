import streamlit as st
import pandas as pd
import requests
import yfinance as yf

from datetime import datetime, timezone, date


# ============================================================
# QUANTUM X PRO — VERSION 2
# IG DEMO XAU/USD M5 SCALPER
#
# REAL DATA:
#   Yahoo GC=F -> recent M5 bootstrap / structure
#   IG XAU/USD -> live price + execution
#
# Yahoo futures are a structural proxy.
# IG remains the execution authority.
# ============================================================

st.set_page_config(
    page_title="Quantum X PRO",
    page_icon="🐎",
    layout="wide",
)

IG_BASE_URL = "https://demo-api.ig.com/gateway/deal"
IG_EPIC = "CS.D.IN_GOLD.MFI.IP"
YAHOO_SYMBOL = "GC=F"

POLL_SECONDS = 5

MAX_TRADES_PER_DAY = 10
MAX_OPEN_POSITIONS = 1

MIN_BOOTSTRAP_BARS = 6
BOOTSTRAP_BARS = 80

MIN_STOP_DISTANCE = 2.0
DEFAULT_RR = 1.5
DEFAULT_SIZE = 0.01

MAX_SPREAD = 1.50
LEVEL_TOLERANCE = 1.50

SWING_LEFT = 1
SWING_RIGHT = 1


# ============================================================
# SESSION STATE
# ============================================================

DEFAULT_STATE = {
    "ig_connected": False,
    "bot_running": False,
    "ig_cst": None,
    "ig_security_token": None,
    "live_bid": None,
    "live_offer": None,
    "live_mid": None,
    "market_status": None,
    "bars": [],
    "last_signal": "WAIT",
    "last_signal_reason": "Waiting for data.",
    "support": None,
    "resistance": None,
    "calibration_offset": None,
    "yahoo_last_price": None,
    "last_order": None,
    "last_error": None,
    "trade_count": 0,
    "trade_day": str(date.today()),
    "last_processed_signal_candle": None,
    "last_bootstrap_time": None,
    "last_tick_time": None,
}

for key, value in DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# GENERAL HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def floor_m5(value):
    ts = pd.Timestamp(value)

    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")

    return ts.floor("5min")


def safe_float(value):
    try:
        if value is None:
            return None

        result = float(value)

        if pd.isna(result):
            return None

        return result
    except Exception:
        return None


def reset_daily_counter_if_needed():
    today = str(date.today())

    if st.session_state.trade_day != today:
        st.session_state.trade_day = today
        st.session_state.trade_count = 0
        st.session_state.last_processed_signal_candle = None


# ============================================================
# SECRETS
# ============================================================

def get_secret(name):
    try:
        value = st.secrets.get(name)

        if value:
            return value
    except Exception:
        pass

    return None


# ============================================================
# IG LOGIN
# ============================================================

def ig_login():
    username = get_secret("IG_USERNAME")
    password = get_secret("IG_PASSWORD")
    api_key = get_secret("IG_API_KEY")

    if not username or not password or not api_key:
        raise RuntimeError(
            "IG credentials are missing from Codespaces secrets."
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
        timeout=15,
    )

    if response.status_code >= 400:
        raise RuntimeError(
            f"IG login failed: "
            f"{response.status_code} "
            f"{response.text[:500]}"
        )

    cst = response.headers.get("CST")
    security_token = response.headers.get(
        "X-SECURITY-TOKEN"
    )

    if not cst or not security_token:
        raise RuntimeError(
            "IG login succeeded but security tokens were missing."
        )

    st.session_state.ig_cst = cst
    st.session_state.ig_security_token = security_token
    st.session_state.ig_connected = True


def ig_headers(version="3"):
    api_key = get_secret("IG_API_KEY")

    if not api_key:
        raise RuntimeError("IG API key is missing.")

    if not st.session_state.ig_cst:
        raise RuntimeError("IG CST token is missing.")

    if not st.session_state.ig_security_token:
        raise RuntimeError(
            "IG security token is missing."
        )

    return {
        "X-IG-API-KEY": api_key,
        "CST": st.session_state.ig_cst,
        "X-SECURITY-TOKEN": st.session_state.ig_security_token,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Version": version,
    }


# ============================================================
# IG LIVE MARKET
# ============================================================

def get_ig_market():
    url = f"{IG_BASE_URL}/markets/{IG_EPIC}"

    response = requests.get(
        url,
        headers=ig_headers("3"),
        timeout=15,
    )

    if response.status_code >= 400:
        raise RuntimeError(
            f"IG market request failed: "
            f"{response.status_code} "
            f"{response.text[:500]}"
        )

    data = response.json()
    snapshot = data.get("snapshot", {})

    bid = safe_float(snapshot.get("bid"))
    offer = safe_float(snapshot.get("offer"))

    if bid is None or offer is None:
        raise RuntimeError(
            "IG returned no usable bid/offer."
        )

    mid = (bid + offer) / 2.0

    st.session_state.live_bid = bid
    st.session_state.live_offer = offer
    st.session_state.live_mid = mid
    st.session_state.market_status = snapshot.get(
        "marketStatus",
        "UNKNOWN",
    )
    st.session_state.last_tick_time = now_utc()

    return {
        "bid": bid,
        "offer": offer,
        "mid": mid,
        "market_status": st.session_state.market_status,
    }


# ============================================================
# YAHOO REAL M5 DATA
# ============================================================

def fetch_yahoo_m5():
    try:
        data = yf.download(
            YAHOO_SYMBOL,
            period="1d",
            interval="5m",
            auto_adjust=False,
            progress=False,
            threads=False,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Yahoo M5 download failed: {exc}"
        )

    if data is None or data.empty:
        raise RuntimeError(
            "Yahoo returned no GC=F M5 data."
        )

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    required = [
        "Open",
        "High",
        "Low",
        "Close",
    ]

    missing = [
        column
        for column in required
        if column not in data.columns
    ]

    if missing:
        raise RuntimeError(
            f"Yahoo data is missing columns: {missing}"
        )

    data = data[required].copy()

    for column in required:
        data[column] = pd.to_numeric(
            data[column],
            errors="coerce",
        )

    data = data.dropna()

    if data.empty:
        raise RuntimeError(
            "Yahoo returned no valid M5 rows."
        )

    data.index = [
        floor_m5(index)
        for index in data.index
    ]

    data = data[
        ~data.index.duplicated(
            keep="last"
        )
    ].sort_index()

    # Never use the currently forming Yahoo candle.
    current_bucket = floor_m5(now_utc())

    data = data[
        data.index < current_bucket
    ]

    if len(data) < MIN_BOOTSTRAP_BARS:
        raise RuntimeError(
            f"Yahoo supplied only {len(data)} "
            f"completed M5 candles. "
            f"Need at least {MIN_BOOTSTRAP_BARS}."
        )

    return data.tail(BOOTSTRAP_BARS)


# ============================================================
# PRICE CALIBRATION
# ============================================================

def calibrate_yahoo_to_ig(yahoo_df, ig_mid):
    yahoo_last = safe_float(
        yahoo_df["Close"].iloc[-1]
    )

    if yahoo_last is None:
        raise RuntimeError(
            "Yahoo latest close is invalid."
        )

    offset = ig_mid - yahoo_last

    st.session_state.yahoo_last_price = yahoo_last
    st.session_state.calibration_offset = offset

    return offset


def apply_calibration(data, offset):
    calibrated = data.copy()

    for column in [
        "Open",
        "High",
        "Low",
        "Close",
    ]:
        calibrated[column] = (
            calibrated[column] + offset
        )

    return calibrated


# ============================================================
# INITIAL M5 BOOTSTRAP
# ============================================================

def bootstrap_m5():
    if not st.session_state.ig_connected:
        raise RuntimeError(
            "Connect to IG before bootstrapping."
        )

    market = get_ig_market()

    yahoo_data = fetch_yahoo_m5()

    offset = calibrate_yahoo_to_ig(
        yahoo_data,
        market["mid"],
    )

    calibrated = apply_calibration(
        yahoo_data,
        offset,
    )

    bars = []

    for timestamp, row in calibrated.iterrows():
        bars.append(
            {
                "time": pd.Timestamp(timestamp),
                "open": safe_float(row["Open"]),
                "high": safe_float(row["High"]),
                "low": safe_float(row["Low"]),
                "close": safe_float(row["Close"]),
                "source": "Yahoo GC=F calibrated",
            }
        )

    if len(bars) < MIN_BOOTSTRAP_BARS:
        raise RuntimeError(
            "Not enough calibrated M5 candles."
        )

    st.session_state.bars = bars
    st.session_state.last_bootstrap_time = now_utc()

    update_structure()
    evaluate_signal()

    return len(bars)


# ============================================================
# LIVE IG M5 CANDLE
# ============================================================

def update_live_m5(mid_price):
    if mid_price is None:
        return

    bucket = floor_m5(now_utc())
    bars = st.session_state.bars

    if not bars:
        bars.append(
            {
                "time": bucket,
                "open": mid_price,
                "high": mid_price,
                "low": mid_price,
                "close": mid_price,
                "source": "IG live",
            }
        )

        st.session_state.bars = bars
        return

    last = bars[-1]
    last_time = floor_m5(last["time"])

    if bucket == last_time:
        last["high"] = max(
            float(last["high"]),
            mid_price,
        )

        last["low"] = min(
            float(last["low"]),
            mid_price,
        )

        last["close"] = mid_price
        last["source"] = (
            "Yahoo calibrated + IG live"
        )

    elif bucket > last_time:
        bars.append(
            {
                "time": bucket,
                "open": mid_price,
                "high": mid_price,
                "low": mid_price,
                "close": mid_price,
                "source": "IG live",
            }
        )

    if len(bars) > 300:
        bars = bars[-300:]

    st.session_state.bars = bars


# ============================================================
# DATAFRAME
# ============================================================

def bars_dataframe():
    if not st.session_state.bars:
        return pd.DataFrame()

    df = pd.DataFrame(
        st.session_state.bars
    )

    df["time"] = pd.to_datetime(
        df["time"],
        utc=True,
    )

    for column in [
        "open",
        "high",
        "low",
        "close",
    ]:
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

    return df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
        ]
    ).reset_index(drop=True)


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def find_swing_levels(df):
    if len(df) < 5:
        return None, None

    supports = []
    resistances = []

    for i in range(
        SWING_LEFT,
        len(df) - SWING_RIGHT,
    ):
        low = float(df.loc[i, "low"])
        high = float(df.loc[i, "high"])

        left_lows = [
            float(df.loc[j, "low"])
            for j in range(
                i - SWING_LEFT,
                i,
            )
        ]

        right_lows = [
            float(df.loc[j, "low"])
            for j in range(
                i + 1,
                i + SWING_RIGHT + 1,
            )
        ]

        left_highs = [
            float(df.loc[j, "high"])
            for j in range(
                i - SWING_LEFT,
                i,
            )
        ]

        right_highs = [
            float(df.loc[j, "high"])
            for j in range(
                i + 1,
                i + SWING_RIGHT + 1,
            )
        ]

        if all(
            low <= value
            for value in left_lows + right_lows
        ):
            supports.append(low)

        if all(
            high >= value
            for value in left_highs + right_highs
        ):
            resistances.append(high)

    current_price = float(
        df["close"].iloc[-1]
    )

    below = [
        value
        for value in supports
        if value <= current_price
    ]

    above = [
        value
        for value in resistances
        if value >= current_price
    ]

    support = max(below) if below else None
    resistance = min(above) if above else None

    return support, resistance


def update_structure():
    df = bars_dataframe()

    if len(df) < MIN_BOOTSTRAP_BARS:
        st.session_state.support = None
        st.session_state.resistance = None
        return

    completed = df.iloc[:-1].copy()

    support, resistance = find_swing_levels(
        completed
    )

    st.session_state.support = support
    st.session_state.resistance = resistance


# ============================================================
# ATR
# ============================================================

def calculate_atr(df, period=14):
    if len(df) < 2:
        return None

    previous_close = df["close"].shift(1)

    tr1 = (
        df["high"] -
        df["low"]
    ).abs()

    tr2 = (
        df["high"] -
        previous_close
    ).abs()

    tr3 = (
        df["low"] -
        previous_close
    ).abs()

    true_range = pd.concat(
        [tr1, tr2, tr3],
        axis=1,
    ).max(axis=1)

    atr = true_range.rolling(
        period,
        min_periods=1,
    ).mean()

    return safe_float(
        atr.iloc[-1]
    )


# ============================================================
# CONFIRMATION CANDLES
# ============================================================

def bullish_engulfing(previous, current):
    return (
        previous["close"] < previous["open"]
        and current["close"] > current["open"]
        and current["open"] <= previous["close"]
        and current["close"] >= previous["open"]
    )


def bearish_engulfing(previous, current):
    return (
        previous["close"] > previous["open"]
        and current["close"] < current["open"]
        and current["open"] >= previous["close"]
        and current["close"] <= previous["open"]
    )


def bullish_rejection(candle):
    body = abs(
        candle["close"] -
        candle["open"]
    )

    lower_wick = (
        min(
            candle["open"],
            candle["close"],
        ) -
        candle["low"]
    )

    upper_wick = (
        candle["high"] -
        max(
            candle["open"],
            candle["close"],
        )
    )

    return (
        candle["close"] > candle["open"]
        and lower_wick >= max(
            body * 1.2,
            0.10,
        )
        and lower_wick > upper_wick
    )


def bearish_rejection(candle):
    body = abs(
        candle["close"] -
        candle["open"]
    )

    upper_wick = (
        candle["high"] -
        max(
            candle["open"],
            candle["close"],
        )
    )

    lower_wick = (
        min(
            candle["open"],
            candle["close"],
        ) -
        candle["low"]
    )

    return (
        candle["close"] < candle["open"]
        and upper_wick >= max(
            body * 1.2,
            0.10,
        )
        and upper_wick > lower_wick
    )


def bullish_momentum(candle, atr):
    if atr is None or atr <= 0:
        return False

    body = abs(
        candle["close"] -
        candle["open"]
    )

    return (
        candle["close"] > candle["open"]
        and body >= atr * 0.55
    )


def bearish_momentum(candle, atr):
    if atr is None or atr <= 0:
        return False

    body = abs(
        candle["close"] -
        candle["open"]
    )

    return (
        candle["close"] < candle["open"]
        and body >= atr * 0.55
    )


def confirmation_type(df):
    if len(df) < 3:
        return None

    previous = df.iloc[-2]
    current = df.iloc[-1]

    atr = calculate_atr(df)

    if bullish_engulfing(
        previous,
        current,
    ):
        return "Bullish Engulfing"

    if bearish_engulfing(
        previous,
        current,
    ):
        return "Bearish Engulfing"

    if bullish_rejection(current):
        return "Bullish Rejection"

    if bearish_rejection(current):
        return "Bearish Rejection"

    if bullish_momentum(
        current,
        atr,
    ):
        return "Bullish Momentum"

    if bearish_momentum(
        current,
        atr,
    ):
        return "Bearish Momentum"

    return None
