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
# ============================================================
# SIGNAL ENGINE
# ============================================================

def evaluate_signal():
    df = bars_dataframe()

    if len(df) < MIN_BOOTSTRAP_BARS:
        st.session_state.last_signal = "WAIT"
        st.session_state.last_signal_reason = (
            f"Need at least {MIN_BOOTSTRAP_BARS} candles."
        )
        return "WAIT"

    # Never use the currently forming candle
    # as the confirmation candle.
    completed = df.iloc[:-1].copy()

    if len(completed) < 5:
        st.session_state.last_signal = "WAIT"
        st.session_state.last_signal_reason = (
            "Waiting for completed M5 candles."
        )
        return "WAIT"

    support = st.session_state.support
    resistance = st.session_state.resistance

    if support is None and resistance is None:
        st.session_state.last_signal = "WAIT"
        st.session_state.last_signal_reason = (
            "No usable S/R level yet."
        )
        return "WAIT"

    confirmation = confirmation_type(
        completed
    )

    if confirmation is None:
        st.session_state.last_signal = "WAIT"
        st.session_state.last_signal_reason = (
            "No confirmation candle."
        )
        return "WAIT"

    current_price = st.session_state.live_mid

    if current_price is None:
        current_price = safe_float(
            completed["close"].iloc[-1]
        )

    if current_price is None:
        st.session_state.last_signal = "WAIT"
        st.session_state.last_signal_reason = (
            "No current price."
        )
        return "WAIT"

    bullish = confirmation.startswith("Bullish")
    bearish = confirmation.startswith("Bearish")

    near_support = (
        support is not None
        and abs(current_price - support)
        <= LEVEL_TOLERANCE
    )

    near_resistance = (
        resistance is not None
        and abs(current_price - resistance)
        <= LEVEL_TOLERANCE
    )

    if bullish and near_support:
        signal = "BUY"

        reason = (
            f"{confirmation} near support "
            f"{support:.2f}; IG price "
            f"{current_price:.2f}"
        )

    elif bearish and near_resistance:
        signal = "SELL"

        reason = (
            f"{confirmation} near resistance "
            f"{resistance:.2f}; IG price "
            f"{current_price:.2f}"
        )

    else:
        signal = "WAIT"

        reason = (
            f"{confirmation}, but IG price is "
            "not close enough to the matching "
            "S/R level."
        )

    st.session_state.last_signal = signal
    st.session_state.last_signal_reason = reason

    return signal


# ============================================================
# IG POSITIONS
# ============================================================

def get_open_xau_positions():
    url = f"{IG_BASE_URL}/positions"

    response = requests.get(
        url,
        headers=ig_headers("2"),
        timeout=15,
    )

    if response.status_code >= 400:
        raise RuntimeError(
            f"IG positions request failed: "
            f"{response.status_code} "
            f"{response.text[:500]}"
        )

    data = response.json()

    positions = data.get(
        "positions",
        [],
    )

    matching = []

    for item in positions:
        position = item.get(
            "position",
            {},
        )

        market = item.get(
            "market",
            {},
        )

        epic = (
            position.get("epic")
            or market.get("epic")
        )

        if epic == IG_EPIC:
            matching.append(item)

    return matching


# ============================================================
# TRADE DISTANCES
# ============================================================

def calculate_trade_distances(rr):
    df = bars_dataframe()

    if len(df) > 1:
        working = df.iloc[:-1]
    else:
        working = df

    atr = calculate_atr(working)

    if atr is None:
        atr = MIN_STOP_DISTANCE

    stop_distance = max(
        MIN_STOP_DISTANCE,
        atr,
    )

    limit_distance = (
        stop_distance * rr
    )

    return (
        stop_distance,
        limit_distance,
    )


# ============================================================
# IG ORDER
# ============================================================

def place_ig_order(
    direction,
    size,
    rr,
):
    if direction not in ("BUY", "SELL"):
        raise ValueError(
            "Invalid order direction."
        )

    reset_daily_counter_if_needed()

    if (
        st.session_state.trade_count
        >= MAX_TRADES_PER_DAY
    ):
        raise RuntimeError(
            "Daily trade limit reached."
        )

    positions = get_open_xau_positions()

    if len(positions) >= MAX_OPEN_POSITIONS:
        raise RuntimeError(
            "An XAU/USD position is already open."
        )

    market = get_ig_market()

    if market["market_status"] != "TRADEABLE":
        raise RuntimeError(
            f"IG market is not tradeable: "
            f"{market['market_status']}"
        )

    spread = (
        market["offer"] -
        market["bid"]
    )

    if spread > MAX_SPREAD:
        raise RuntimeError(
            f"Spread too wide: {spread:.2f}"
        )

    stop_distance, limit_distance = (
        calculate_trade_distances(rr)
    )

    payload = {
        "epic": IG_EPIC,
        "expiry": "-",
        "direction": direction,
        "size": float(size),
        "orderType": "MARKET",
        "currencyCode": "USD",
        "forceOpen": True,
        "guaranteedStop": False,
        "stopDistance": round(
            stop_distance,
            2,
        ),
        "limitDistance": round(
            limit_distance,
            2,
        ),
    }

    url = f"{IG_BASE_URL}/positions/otc"

    response = requests.post(
        url,
        headers=ig_headers("2"),
        json=payload,
        timeout=15,
    )

    if response.status_code >= 400:
        raise RuntimeError(
            f"IG order failed: "
            f"{response.status_code} "
            f"{response.text[:700]}"
        )

    result = response.json()

    st.session_state.trade_count += 1

    st.session_state.last_order = {
        "time": now_utc(),
        "direction": direction,
        "size": size,
        "stop_distance": stop_distance,
        "limit_distance": limit_distance,
        "response": result,
    }

    return result


# ============================================================
# TRADE SAFETY
# ============================================================

def can_trade(signal):
    if signal not in ("BUY", "SELL"):
        return False, "No trade signal."

    reset_daily_counter_if_needed()

    if not st.session_state.ig_connected:
        return False, "IG is not connected."

    if not st.session_state.bot_running:
        return False, "Bot is stopped."

    if (
        st.session_state.trade_count
        >= MAX_TRADES_PER_DAY
    ):
        return False, "10-trade daily limit reached."

    if (
        st.session_state.live_bid is None
        or st.session_state.live_offer is None
    ):
        return False, "No live IG quote."

    spread = (
        st.session_state.live_offer
        - st.session_state.live_bid
    )

    if spread > MAX_SPREAD:
        return False, (
            f"Spread too wide: {spread:.2f}"
        )

    try:
        positions = get_open_xau_positions()

        if len(positions) >= MAX_OPEN_POSITIONS:
            return False, (
                "One XAU/USD position is already open."
            )

    except Exception as exc:
        return False, (
            f"Could not verify positions: {exc}"
        )

    return True, "Trade checks passed."


# ============================================================
# AUTOMATION
# ============================================================

def automation_tick(size, rr):
    if not st.session_state.bot_running:
        return

    try:
        market = get_ig_market()

        update_live_m5(
            market["mid"]
        )

        update_structure()

        signal = evaluate_signal()

        if signal not in ("BUY", "SELL"):
            return

        # Prevent repeated orders on the same M5 candle.
        current_bucket = floor_m5(
            now_utc()
        )

        signal_key = (
            f"{current_bucket.isoformat()}:{signal}"
        )

        if (
            st.session_state.last_processed_signal_candle
            == signal_key
        ):
            return

        allowed, reason = can_trade(signal)

        if not allowed:
            st.session_state.last_error = reason
            return

        result = place_ig_order(
            signal,
            size,
            rr,
        )

        st.session_state.last_processed_signal_candle = (
            signal_key
        )

        st.session_state.last_error = None

        st.session_state.last_order = {
            **st.session_state.last_order,
            "signal_reason": (
                st.session_state.last_signal_reason
            ),
            "result": result,
        }

    except Exception as exc:
        st.session_state.last_error = str(exc)


# ============================================================
# DASHBOARD
# ============================================================

def render_dashboard():
    reset_daily_counter_if_needed()

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "Trades",
            f"{st.session_state.trade_count}/"
            f"{MAX_TRADES_PER_DAY}",
        )

    with col2:
        st.metric(
            "Automation",
            (
                "ACTIVE"
                if st.session_state.bot_running
                else "STOPPED"
            ),
        )

    with col3:
        signal = st.session_state.last_signal

        if signal == "BUY":
            display_signal = "🟢 BUY"
        elif signal == "SELL":
            display_signal = "🔴 SELL"
        else:
            display_signal = "⏳ WAIT"

        st.metric(
            "Signal",
            display_signal,
        )

    st.divider()

    price_col, bid_col, offer_col = st.columns(3)

    with price_col:
        st.subheader("Live IG Price")

        if st.session_state.live_mid is not None:
            st.metric(
                "XAU/USD",
                f"{st.session_state.live_mid:.2f}",
            )
        else:
            st.write("Waiting for IG price...")

    with bid_col:
        st.metric(
            "Bid",
            (
                f"{st.session_state.live_bid:.2f}"
                if st.session_state.live_bid is not None
                else "—"
            ),
        )

    with offer_col:
        st.metric(
            "Offer",
            (
                f"{st.session_state.live_offer:.2f}"
                if st.session_state.live_offer is not None
                else "—"
            ),
        )

    if st.session_state.market_status:
        st.write(
            f"**IG Market Status:** "
            f"`{st.session_state.market_status}`"
        )

    st.divider()

    # --------------------------------------------------------
    # BOOTSTRAP
    # --------------------------------------------------------

    st.subheader("📚 M5 Data Bootstrap")

    df = bars_dataframe()

    if len(df) >= MIN_BOOTSTRAP_BARS:
        st.success(
            f"Real M5 structure loaded: "
            f"{len(df)} candles"
        )

        if (
            st.session_state.calibration_offset
            is not None
        ):
            st.write(
                "Calibration offset: "
                f"`{st.session_state.calibration_offset:+.2f}`"
            )

        if (
            st.session_state.yahoo_last_price
            is not None
        ):
            st.write(
                "Yahoo GC=F bootstrap close: "
                f"`{st.session_state.yahoo_last_price:.2f}`"
            )

    else:
        st.info(
            f"M5 structure: "
            f"{len(df)}/{MIN_BOOTSTRAP_BARS} "
            "completed candles."
        )

    # --------------------------------------------------------
    # S/R
    # --------------------------------------------------------

    sr1, sr2 = st.columns(2)

    with sr1:
        st.metric(
            "🟢 Support",
            (
                f"{st.session_state.support:.2f}"
                if st.session_state.support is not None
                else "—"
            ),
        )

    with sr2:
        st.metric(
            "🔴 Resistance",
            (
                f"{st.session_state.resistance:.2f}"
                if st.session_state.resistance is not None
                else "—"
            ),
        )

    # --------------------------------------------------------
    # SIGNAL
    # --------------------------------------------------------

    st.subheader("🤖 Signal Engine")

    signal = st.session_state.last_signal

    if signal == "BUY":
        st.success(
            "🟢 BUY — "
            + st.session_state.last_signal_reason
        )

    elif signal == "SELL":
        st.error(
            "🔴 SELL — "
            + st.session_state.last_signal_reason
        )

    else:
        st.info(
            "⏳ WAIT — "
            + st.session_state.last_signal_reason
        )

    if st.session_state.bot_running:
        st.success(
            f"🤖 AUTOMATION ACTIVE — "
            f"checking every {POLL_SECONDS} seconds"
        )
    else:
        st.warning(
            "⏹️ Automation stopped."
        )

    # --------------------------------------------------------
    # LAST ORDER
    # --------------------------------------------------------

    if st.session_state.last_order:
        st.subheader("📋 Last Order")

        order = st.session_state.last_order

        st.write(
            f"Direction: **{order.get('direction', '—')}**"
        )

        st.write(
            f"Size: **{order.get('size', '—')}**"
        )

        if order.get("stop_distance") is not None:
            st.write(
                "Stop distance: "
                f"**{order['stop_distance']:.2f}**"
            )

        if order.get("limit_distance") is not None:
            st.write(
                "Limit distance: "
                f"**{order['limit_distance']:.2f}**"
            )

        if order.get("signal_reason"):
            st.write(
                "Reason: "
                f"{order['signal_reason']}"
            )

    # --------------------------------------------------------
    # ERRORS
    # --------------------------------------------------------

    if st.session_state.last_error:
        st.error(
            "⚠️ "
            + st.session_state.last_error
        )

    # --------------------------------------------------------
    # M5 TABLE
    # --------------------------------------------------------

    st.subheader("📊 Local M5 Candles")

    if not df.empty:
        display_df = df.tail(20).copy()

        display_df["time"] = (
            display_df["time"]
            .dt.strftime("%Y-%m-%d %H:%M")
        )

        for column in [
            "open",
            "high",
            "low",
            "close",
        ]:
            display_df[column] = (
                display_df[column].round(2)
            )

        st.dataframe(
            display_df[
                [
                    "time",
                    "open",
                    "high",
                    "low",
                    "close",
                    "source",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )

    else:
        st.info(
            "No M5 candles loaded yet."
        )


# ============================================================
# PAGE
# ============================================================

st.title("🐎 Quantum X PRO")

st.caption(
    "Automated IG Demo — XAU/USD M5 Scalper"
)

st.caption(
    "Real GC=F M5 bootstrap + IG live execution "
    "• Support + Resistance + Confirmation Candles "
    "• No AI • M5 only"
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:
    st.header("⚙️ Scalper Settings")

    size = st.number_input(
        "Trade Size",
        min_value=0.01,
        max_value=100.0,
        value=DEFAULT_SIZE,
        step=0.01,
        format="%.2f",
    )

    rr = st.number_input(
        "Risk / Reward",
        min_value=0.5,
        max_value=5.0,
        value=DEFAULT_RR,
        step=0.1,
        format="%.1f",
    )

    st.divider()

    st.write("🥇 XAU/USD")
    st.code(IG_EPIC)

    st.write(
        f"Daily trades: "
        f"{st.session_state.trade_count}/"
        f"{MAX_TRADES_PER_DAY}"
    )

    st.write(
        f"Max open positions: "
        f"{MAX_OPEN_POSITIONS}"
    )

    st.write(
        f"Max spread: "
        f"{MAX_SPREAD:.2f}"
    )

    st.divider()

    if st.button(
        "🔌 Connect IG Demo",
        use_container_width=True,
    ):
        try:
            ig_login()

            count = bootstrap_m5()

            st.session_state.last_error = None

            st.success(
                f"IG connected + "
                f"{count} real M5 candles loaded."
            )

        except Exception as exc:
            st.session_state.ig_connected = False
            st.session_state.last_error = str(exc)

    if st.session_state.ig_connected:
        st.success(
            "🟢 IG DEMO CONNECTED"
        )
    else:
        st.warning(
            "🔴 IG NOT CONNECTED"
        )

    if st.button(
        "🚀 Start Bot",
        use_container_width=True,
        disabled=not st.session_state.ig_connected,
    ):
        try:
            if not st.session_state.bars:
                bootstrap_m5()

            st.session_state.bot_running = True
            st.session_state.last_error = None

        except Exception as exc:
            st.session_state.last_error = str(exc)

    if st.button(
        "🛑 Stop Bot",
        use_container_width=True,
    ):
        st.session_state.bot_running = False

    if st.session_state.bot_running:
        st.success(
            "🟢 BOT RUNNING"
        )
    else:
        st.info(
            "⏹️ BOT STOPPED"
        )


# ============================================================
# LIVE AUTOMATION
# ============================================================

@st.fragment(run_every=POLL_SECONDS)
def live_automation():
    reset_daily_counter_if_needed()

    if (
        st.session_state.ig_connected
        and st.session_state.bot_running
    ):
        automation_tick(
            size=size,
            rr=rr,
        )

    render_dashboard()


live_automation()
