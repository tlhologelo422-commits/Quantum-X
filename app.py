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
# ============================================================
# STRATEGY
# ============================================================

def make_signal():

    df = bars_dataframe()

    if len(df) < MIN_COMPLETED_BARS + 1:

        return (
            "WAIT",
            f"Building M5 structure: "
            f"{max(0, len(df) - 1)}/"
            f"{MIN_COMPLETED_BARS} completed bars.",
            None,
            None,
        )

    completed = df.iloc[:-1].copy()

    if len(completed) < MIN_COMPLETED_BARS:

        return (
            "WAIT",
            f"Waiting for "
            f"{MIN_COMPLETED_BARS} completed M5 candles.",
            None,
            None,
        )

    current_price = (
        st.session_state["last_price"]
    )

    if current_price is None:

        return (
            "WAIT",
            "Waiting for live price.",
            None,
            None,
        )

    atr = calculate_atr(
        completed,
        period=5,
    )

    if atr is None or atr <= 0:

        return (
            "WAIT",
            "ATR is not ready.",
            None,
            None,
        )

    support_levels, resistance_levels = (
        find_levels(completed)
    )

    tolerance = max(
        atr * 0.8,
        current_price * 0.00025,
    )

    support = nearest_level(
        current_price,
        support_levels,
        tolerance,
    )

    resistance = nearest_level(
        current_price,
        resistance_levels,
        tolerance,
    )

    prev = completed.iloc[-2]
    curr = completed.iloc[-1]

    bullish_pattern = bullish_confirmation(
        prev,
        curr,
    )

    bearish_pattern = bearish_confirmation(
        prev,
        curr,
    )

    if (
        support is not None
        and bullish_pattern
    ):

        reason = (
            f"BUY: price near support "
            f"{support:.2f} + "
            f"{bullish_pattern}"
        )

        return (
            "BUY",
            reason,
            support,
            atr,
        )

    if (
        resistance is not None
        and bearish_pattern
    ):

        reason = (
            f"SELL: price near resistance "
            f"{resistance:.2f} + "
            f"{bearish_pattern}"
        )

        return (
            "SELL",
            reason,
            resistance,
            atr,
        )

    return (
        "WAIT",
        "No confirmed support/resistance setup.",
        support,
        atr,
    )


# ============================================================
# IG POSITIONS
# ============================================================

def get_matching_positions():

    try:

        response = requests.get(
            f"{IG_BASE}/positions",
            headers=headers("2"),
            timeout=15,
        )

        if response.status_code != 200:

            return (
                None,
                api_error_text(response),
            )

        data = response.json()

        positions = data.get(
            "positions",
            [],
        )

        matches = []

        target_epic = (
            st.session_state["epic"]
        )

        for item in positions:

            position = item.get(
                "position",
                {},
            )

            epic = position.get(
                "epic",
                "",
            )

            if epic == target_epic:
                matches.append(item)

        return matches, ""

    except requests.RequestException as exc:

        return None, str(exc)


# ============================================================
# ORDER EXECUTION
# ============================================================

def place_order(
    direction,
    atr,
):

    if direction not in (
        "BUY",
        "SELL",
    ):

        return (
            False,
            "Invalid direction.",
        )

    size = float(
        st.session_state["size"]
    )

    if size <= 0:

        return (
            False,
            "Trade size must be greater than zero.",
        )

    stop_distance = float(
        st.session_state[
            "stop_distance"
        ]
    )

    if atr is not None and atr > 0:

        stop_distance = max(
            stop_distance,
            atr,
        )

    rr = float(
        st.session_state["rr"]
    )

    limit_distance = (
        stop_distance * rr
    )

    payload = {
        "epic": st.session_state["epic"],
        "direction": direction,
        "size": size,
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

    try:

        response = requests.post(
            f"{IG_BASE}/positions/otc",
            headers=headers("2"),
            json=payload,
            timeout=15,
        )

        if response.status_code not in (
            200,
            201,
        ):

            return (
                False,
                f"Order failed "
                f"({response.status_code}): "
                f"{api_error_text(response)}",
            )

        try:
            data = response.json()
        except Exception:
            data = {}

        deal_reference = data.get(
            "dealReference",
            "unknown",
        )

        deal_status = data.get(
            "dealStatus",
            "UNKNOWN",
        )

        st.session_state[
            "last_order"
        ] = {
            "time": utc_now().isoformat(),
            "direction": direction,
            "size": size,
            "stop": stop_distance,
            "limit": limit_distance,
            "dealReference": deal_reference,
            "dealStatus": deal_status,
        }

        return (
            True,
            f"{direction} order submitted. "
            f"Deal: {deal_reference}",
        )

    except requests.RequestException as exc:

        return (
            False,
            f"Order connection error: {exc}",
        )


# ============================================================
# BOT ENGINE
# ============================================================

def run_bot_cycle():

    reset_daily_counter_if_needed()

    if not st.session_state["connected"]:
        return

    if not st.session_state["bot_running"]:
        return

    price, bid, offer, market_status = (
        get_current_price()
    )

    if price is None:

        st.session_state[
            "last_error"
        ] = str(market_status)

        return

    st.session_state[
        "last_price"
    ] = price

    st.session_state[
        "last_bid"
    ] = bid

    st.session_state[
        "last_offer"
    ] = offer

    st.session_state[
        "market_status"
    ] = str(market_status)

    st.session_state[
        "last_tick"
    ] = utc_now()

    add_tick(price)

    signal, reason, level, atr = (
        make_signal()
    )

    st.session_state[
        "last_signal"
    ] = signal

    st.session_state[
        "last_reason"
    ] = reason

    st.session_state[
        "last_error"
    ] = ""

    if signal not in (
        "BUY",
        "SELL",
    ):
        return

    if (
        st.session_state[
            "trade_count"
        ]
        >= MAX_TRADES_PER_DAY
    ):

        st.session_state[
            "last_signal"
        ] = "LIMIT"

        st.session_state[
            "last_reason"
        ] = (
            "Daily maximum of 10 trades reached."
        )

        return

    positions, error = (
        get_matching_positions()
    )

    if positions is None:

        st.session_state[
            "last_error"
        ] = (
            "Position check failed: "
            + error
        )

        return

    if len(positions) >= MAX_OPEN_POSITIONS:

        st.session_state[
            "last_signal"
        ] = "WAIT"

        st.session_state[
            "last_reason"
        ] = (
            "Existing XAU/USD position is open."
        )

        return

    df = bars_dataframe()

    if df.empty:
        return

    current_bar = df.iloc[-1]["time"]

    current_bar_key = pd.Timestamp(
        current_bar
    ).isoformat()

    if (
        st.session_state[
            "last_trade_bar"
        ]
        == current_bar_key
    ):

        st.session_state[
            "last_signal"
        ] = "WAIT"

        st.session_state[
            "last_reason"
        ] = (
            "Already traded this M5 candle."
        )

        return

    success, message = place_order(
        signal,
        atr,
    )

    if success:

        st.session_state[
            "trade_count"
        ] += 1

        st.session_state[
            "last_trade_bar"
        ] = current_bar_key

        st.session_state[
            "last_signal"
        ] = signal

        st.session_state[
            "last_reason"
        ] = message

        st.session_state[
            "last_error"
        ] = ""

    else:

        st.session_state[
            "last_error"
        ] = message


# ============================================================
# USER INTERFACE
# ============================================================

st.title(
    "🐎 Quantum X PRO"
)

st.subheader(
    "Automated IG Demo — XAU/USD M5 Scalper"
)

st.caption(
    "Support + Resistance + Confirmation Candles • "
    "No AI • M5 only"
)

reset_daily_counter_if_needed()

connected = st.session_state[
    "connected"
]

running = st.session_state[
    "bot_running"
]

col1, col2, col3 = st.columns(3)

with col1:

    if connected:

        st.success(
            "🟢 IG DEMO CONNECTED"
        )

    else:

        st.error(
            "🔴 IG NOT CONNECTED"
        )

with col2:

    if running:

        st.success(
            "🟢 BOT RUNNING"
        )

    else:

        st.warning(
            "🟡 BOT STOPPED"
        )

with col3:

    st.metric(
        "TRADES",
        f"{st.session_state['trade_count']}/"
        f"{MAX_TRADES_PER_DAY}",
    )


# ============================================================
# SETTINGS
# ============================================================

with st.expander(
    "⚙️ Scalper Settings",
    expanded=False,
):

    size = st.number_input(
        "IG trade size",
        min_value=0.01,
        max_value=100.0,
        value=float(
            st.session_state["size"]
        ),
        step=0.01,
    )

    stop_distance = st.number_input(
        "Minimum stop distance",
        min_value=0.1,
        max_value=1000.0,
        value=float(
            st.session_state[
                "stop_distance"
            ]
        ),
        step=0.1,
    )

    rr = st.number_input(
        "Risk / Reward",
        min_value=0.5,
        max_value=10.0,
        value=float(
            st.session_state["rr"]
        ),
        step=0.1,
    )

    st.session_state[
        "size"
    ] = size

    st.session_state[
        "stop_distance"
    ] = stop_distance

    st.session_state[
        "rr"
    ] = rr

    st.info(
        "The bot uses the larger of the configured "
        "stop distance and local M5 ATR."
    )


# ============================================================
# CONNECTION BUTTONS
# ============================================================

button_col1, button_col2, button_col3 = (
    st.columns(3)
)

with button_col1:

    if st.button(
        "🔌 Connect IG Demo",
        use_container_width=True,
    ):

        success, message = login_ig()

        if success:

            epic, search_error = (
                find_gold_epic()
            )

            if epic:
                st.session_state[
                    "epic"
                ] = epic

            st.success(message)

            if search_error:

                st.warning(
                    "Gold search: "
                    + search_error
                )

            st.rerun()

        else:

            st.error(message)


with button_col2:

    if st.button(
        "▶️ START BOT",
        use_container_width=True,
        disabled=not connected,
    ):

        if not connected:

            st.error(
                "Connect to IG Demo first."
            )

        else:

            st.session_state[
                "bot_running"
            ] = True

            st.session_state[
                "last_error"
            ] = ""

            st.success(
                "Bot started."
            )

            st.rerun()


with button_col3:

    if st.button(
        "⛔ STOP BOT",
        use_container_width=True,
    ):

        st.session_state[
            "bot_running"
        ] = False

        st.info(
            "Bot stopped. Existing IG positions "
            "are NOT automatically closed."
        )

        st.rerun()


# ============================================================
# LIVE BOT
# ============================================================

@st.fragment(run_every=POLL_SECONDS)
def live_bot():

    if (
        st.session_state["connected"]
        and st.session_state["bot_running"]
    ):

        run_bot_cycle()

    st.divider()

    st.header(
        "🥇 XAU/USD"
    )

    st.write(
        f"**EPIC:** "
        f"`{st.session_state['epic']}`"
    )

    price = st.session_state[
        "last_price"
    ]

    if price is not None:

        price_col1, price_col2, price_col3 = (
            st.columns(3)
        )

        with price_col1:

            st.metric(
                "Live IG Price",
                f"{price:.2f}",
            )

        with price_col2:

            bid = st.session_state[
                "last_bid"
            ]

            if bid is not None:

                st.metric(
                    "Bid",
                    f"{bid:.2f}",
                )

        with price_col3:

            offer = st.session_state[
                "last_offer"
            ]

            if offer is not None:

                st.metric(
                    "Offer",
                    f"{offer:.2f}",
                )

    else:

        st.info(
            "Waiting for live IG XAU/USD price..."
        )

    market_status = (
        st.session_state[
            "market_status"
        ]
    )

    if market_status:

        st.caption(
            "IG Market Status: "
            + market_status
        )

    # --------------------------------------------------------
    # SIGNAL
    # --------------------------------------------------------

    signal = st.session_state[
        "last_signal"
    ]

    reason = st.session_state[
        "last_reason"
    ]

    if signal == "BUY":

        st.success(
            "🟢 BUY SIGNAL — "
            + reason
        )

    elif signal == "SELL":

        st.error(
            "🔴 SELL SIGNAL — "
            + reason
        )

    elif signal == "LIMIT":

        st.warning(
            "⚠️ "
            + reason
        )

    else:

        st.info(
            "⏳ "
            + reason
        )

    # --------------------------------------------------------
    # BOT STATUS
    # --------------------------------------------------------

    if st.session_state[
        "bot_running"
    ]:

        st.success(
            "🤖 AUTOMATION ACTIVE — "
            f"checking every {POLL_SECONDS} seconds"
        )

    else:

        st.warning(
            "Bot is stopped."
        )

    # --------------------------------------------------------
    # ERRORS
    # --------------------------------------------------------

    if st.session_state[
        "last_error"
    ]:

        st.error(
            st.session_state[
                "last_error"
            ]
        )

    # --------------------------------------------------------
    # M5 CANDLES
    # --------------------------------------------------------

    df = bars_dataframe()

    st.divider()

    st.header(
        "📊 Local M5 Candles"
    )

    if df.empty:

        st.info(
            "No M5 candles built yet."
        )

    else:

        completed_count = max(
            0,
            len(df) - 1,
        )

        if completed_count < (
            MIN_COMPLETED_BARS
        ):

            st.warning(
                "Building M5 structure: "
                f"{completed_count}/"
                f"{MIN_COMPLETED_BARS} "
                "completed candles."
            )

        else:

            st.success(
                "M5 structure ready — "
                f"{completed_count} "
                "completed candles."
            )

        display_df = df.tail(
            12
        ).copy()

        display_df["time"] = (
            display_df["time"]
            .dt.strftime(
                "%H:%M:%S"
            )
        )

        for col in [
            "open",
            "high",
            "low",
            "close",
        ]:

            display_df[col] = (
                display_df[col]
                .map(
                    lambda x:
                    f"{x:.2f}"
                )
            )

        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
        )

    # --------------------------------------------------------
    # LAST ORDER
    # --------------------------------------------------------

    if st.session_state[
        "last_order"
    ]:

        st.divider()

        st.header(
            "📋 Last Order"
        )

        order = (
            st.session_state[
                "last_order"
            ]
        )

        st.write(
            f"**Direction:** "
            f"{order['direction']}"
        )

        st.write(
            f"**Size:** "
            f"{order['size']}"
        )

        st.write(
            f"**Stop:** "
            f"{order['stop']:.2f}"
        )

        st.write(
            f"**Target:** "
            f"{order['limit']:.2f}"
        )

        st.write(
            f"**Deal Status:** "
            f"{order['dealStatus']}"
        )

        st.write(
            f"**Deal Reference:** "
            f"`{order['dealReference']}`"
        )

    st.divider()

    st.caption(
        "IG historical candles are NOT requested. "
        "The bot builds M5 candles locally from live "
        "IG market prices."
    )

    last_tick = (
        st.session_state[
            "last_tick"
        ]
    )

    if last_tick:

        st.caption(
            "Last IG tick: "
            + last_tick.strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )
        )

    st.caption(
        "Maximum: 10 trades/day • "
        "Maximum 1 open XAU/USD position • "
        "One entry per M5 candle"
    )


# ============================================================
# START LIVE ENGINE
# ============================================================

live_bot()
