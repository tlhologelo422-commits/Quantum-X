import os
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st


# ============================================================
# CONFIG
# ============================================================

IG_BASE = "https://demo-api.ig.com/gateway/deal"

DEFAULT_EPIC = "CS.D.XAUUSD.CFD.IP"

MAX_TRADES = 10
POLL_SECONDS = 15

st.set_page_config(
    page_title="Quantum XAU/USD Scalper",
    page_icon="🐎",
    layout="wide",
)


# ============================================================
# STATE
# ============================================================

state_defaults = {
    "session": None,
    "epic": DEFAULT_EPIC,
    "running": False,
    "trades": 0,
    "trade_day": None,
    "last_trade_bar": None,
    "signal": "WAIT",
    "message": "Ready.",
    "bars": [],
    "last_price": None,
    "last_tick": None,
}

for key, value in state_defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# DAILY RESET
# ============================================================

today = datetime.now(timezone.utc).date().isoformat()

if st.session_state.trade_day != today:
    st.session_state.trade_day = today
    st.session_state.trades = 0
    st.session_state.last_trade_bar = None


# ============================================================
# IG AUTHENTICATION
# ============================================================

def login():

    api_key = os.getenv("IG_API_KEY")
    username = os.getenv("IG_USERNAME")
    password = os.getenv("IG_PASSWORD")

    if not api_key:
        return None, "IG_API_KEY is missing."

    if not username:
        return None, "IG_USERNAME is missing."

    if not password:
        return None, "IG_PASSWORD is missing."

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

    try:

        response = requests.post(
            f"{IG_BASE}/session",
            headers=headers,
            json=payload,
            timeout=15,
        )

        if response.status_code not in (200, 201):

            return None, (
                f"Login failed "
                f"({response.status_code}): "
                f"{response.text[:300]}"
            )

        cst = response.headers.get("CST")
        security = response.headers.get(
            "X-SECURITY-TOKEN"
        )

        if not cst or not security:
            return None, (
                "IG did not return session tokens."
            )

        return {
            "api_key": api_key,
            "CST": cst,
            "X-SECURITY-TOKEN": security,
        }, "Connected to IG Demo."

    except Exception as exc:

        return None, f"Connection error: {exc}"


def ig_headers(session, version="2"):

    return {
        "X-IG-API-KEY": session["api_key"],
        "CST": session["CST"],
        "X-SECURITY-TOKEN": session[
            "X-SECURITY-TOKEN"
        ],
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Version": version,
    }


# ============================================================
# FIND GOLD EPIC
# ============================================================

def find_gold(session):

    try:

        response = requests.get(
            f"{IG_BASE}/markets",
            params={
                "searchTerm": "XAUUSD"
            },
            headers=ig_headers(
                session,
                "1",
            ),
            timeout=15,
        )

        if response.status_code != 200:
            return DEFAULT_EPIC

        markets = response.json().get(
            "markets",
            [],
        )

        for market in markets:

            epic = market.get(
                "epic",
                "",
            )

            name = str(
                market.get(
                    "instrumentName",
                    "",
                )
            ).lower()

            if (
                "xauusd" in epic.lower()
                or "gold" in name
            ):
                return epic

        return DEFAULT_EPIC

    except Exception:

        return DEFAULT_EPIC


# ============================================================
# CURRENT IG MARKET PRICE
#
# IMPORTANT:
# We DO NOT request historical candles anymore.
# ============================================================

def get_current_price(
    session,
    epic,
):

    try:

        response = requests.get(
            f"{IG_BASE}/markets/{epic}",
            headers=ig_headers(
                session,
                "3",
            ),
            timeout=15,
        )

        if response.status_code != 200:

            return None, (
                f"Market request failed "
                f"({response.status_code}): "
                f"{response.text[:300]}"
            )

        data = response.json()

        snapshot = data.get(
            "snapshot",
            {},
        )

        bid = snapshot.get("bid")
        offer = snapshot.get("offer")

        if bid is None or offer is None:

            return None, (
                "IG returned no bid/offer."
            )

        bid = float(bid)
        offer = float(offer)

        mid = (
            bid + offer
        ) / 2

        return {
            "bid": bid,
            "ask": offer,
            "price": mid,
            "spread": offer - bid,
        }, ""

    except Exception as exc:

        return None, str(exc)


# ============================================================
# LOCAL M5 CANDLE BUILDER
# ============================================================

def current_bar_start():

    now = datetime.now(
        timezone.utc
    )

    minute = (
        now.minute
        - (now.minute % 5)
    )

    return now.replace(
        minute=minute,
        second=0,
        microsecond=0,
    )


def add_tick(price):

    bar_time = current_bar_start()

    bars = st.session_state.bars

    if not bars:

        bars.append(
            {
                "time": bar_time,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
            }
        )

        return

    last = bars[-1]

    if last["time"] == bar_time:

        last["high"] = max(
            last["high"],
            price,
        )

        last["low"] = min(
            last["low"],
            price,
        )

        last["close"] = price

    elif bar_time > last["time"]:

        bars.append(
            {
                "time": bar_time,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
            }
        )

        # Keep memory under control.
        if len(bars) > 300:
            del bars[:-300]


def bars_dataframe():

    if not st.session_state.bars:
        return pd.DataFrame()

    return pd.DataFrame(
        st.session_state.bars
    )


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def find_levels(df):

    if len(df) < 15:
        return [], []

    recent = df.tail(100)

    supports = []
    resistances = []

    for i in range(
        2,
        len(recent) - 2,
    ):

        low = float(
            recent["low"].iloc[i]
        )

        high = float(
            recent["high"].iloc[i]
        )

        if (
            low
            < recent["low"]
            .iloc[i - 1]
            and low
            < recent["low"]
            .iloc[i + 1]
            and low
            < recent["low"]
            .iloc[i - 2]
            and low
            < recent["low"]
            .iloc[i + 2]
        ):

            supports.append(low)

        if (
            high
            > recent["high"]
            .iloc[i - 1]
            and high
            > recent["high"]
            .iloc[i + 1]
            and high
            > recent["high"]
            .iloc[i - 2]
            and high
            > recent["high"]
            .iloc[i + 2]
        ):

            resistances.append(high)

    return supports, resistances


# ============================================================
# CONFIRMATION CANDLE
# ============================================================

def confirmation(df):

    if len(df) < 3:
        return "NONE"

    previous = df.iloc[-2]
    current = df.iloc[-1]

    po = float(previous["open"])
    pc = float(previous["close"])

    o = float(current["open"])
    h = float(current["high"])
    l = float(current["low"])
    c = float(current["close"])

    body = abs(c - o)

    candle_range = max(
        h - l,
        0.00001,
    )

    upper_wick = (
        h - max(o, c)
    )

    lower_wick = (
        min(o, c) - l
    )

    bullish_engulfing = (
        pc < po
        and c > o
        and o <= pc
        and c >= po
    )

    bearish_engulfing = (
        pc > po
        and c < o
        and o >= pc
        and c <= po
    )

    bullish_rejection = (
        c > o
        and lower_wick >= body * 1.5
        and lower_wick >= candle_range * 0.30
    )

    bearish_rejection = (
        c < o
        and upper_wick >= body * 1.5
        and upper_wick >= candle_range * 0.30
    )

    bullish_momentum = (
        c > o
        and body >= candle_range * 0.60
    )

    bearish_momentum = (
        c < o
        and body >= candle_range * 0.60
    )

    if bullish_engulfing:
        return "BULLISH ENGULFING"

    if bearish_engulfing:
        return "BEARISH ENGULFING"

    if bullish_rejection:
        return "BULLISH REJECTION"

    if bearish_rejection:
        return "BEARISH REJECTION"

    if bullish_momentum:
        return "BULLISH MOMENTUM"

    if bearish_momentum:
        return "BEARISH MOMENTUM"

    return "NONE"


# ============================================================
# SIGNAL
# ============================================================

def make_signal(df):

    if len(df) < 20:

        return (
            "WAIT",
            "Building M5 history locally.",
            None,
            "NONE",
            0,
        )

    supports, resistances = (
        find_levels(df)
    )

    price = float(
        df.iloc[-1]["close"]
    )

    atr = float(
        (
            df["high"]
            - df["low"]
        ).tail(14).mean()
    )

    if atr <= 0:

        return (
            "WAIT",
            "Invalid M5 range.",
            None,
            "NONE",
            atr,
        )

    tolerance = max(
        atr * 0.50,
        price * 0.0005,
    )

    nearby_support = [
        x for x in supports
        if x <= price
        and price - x <= tolerance
    ]

    nearby_resistance = [
        x for x in resistances
        if x >= price
        and x - price <= tolerance
    ]

    candle = confirmation(df)

    bullish_patterns = {
        "BULLISH ENGULFING",
        "BULLISH REJECTION",
        "BULLISH MOMENTUM",
    }

    bearish_patterns = {
        "BEARISH ENGULFING",
        "BEARISH REJECTION",
        "BEARISH MOMENTUM",
    }

    if (
        nearby_support
        and candle in bullish_patterns
    ):

        return (
            "BUY",
            "Support + bullish confirmation.",
            max(nearby_support),
            candle,
            atr,
        )

    if (
        nearby_resistance
        and candle in bearish_patterns
    ):

        return (
            "SELL",
            "Resistance + bearish confirmation.",
            min(nearby_resistance),
            candle,
            atr,
        )

    return (
        "WAIT",
        "No valid M5 setup.",
        None,
        candle,
        atr,
    )


# ============================================================
# OPEN POSITIONS
# ============================================================

def get_positions(
    session,
    epic,
):

    try:

        response = requests.get(
            f"{IG_BASE}/positions",
            headers=ig_headers(
                session,
                "2",
            ),
            timeout=15,
        )

        if response.status_code != 200:

            return None, (
                f"Position request failed "
                f"({response.status_code})"
            )

        positions = response.json().get(
            "positions",
            [],
        )

        matching = [
            p for p in positions
            if p.get(
                "market",
                {},
            ).get("epic") == epic
        ]

        return matching, ""

    except Exception as exc:

        return None, str(exc)


# ============================================================
# ORDER
# ============================================================

def place_order(
    session,
    epic,
    direction,
    size,
    stop_distance,
    limit_distance,
):

    payload = {
        "epic": epic,
        "expiry": "-",
        "direction": direction,
        "size": float(size),
        "orderType": "MARKET",
        "guaranteedStop": False,
        "forceOpen": True,
        "stopDistance": float(
            stop_distance
        ),
        "limitDistance": float(
            limit_distance
        ),
    }

    try:

        response = requests.post(
            f"{IG_BASE}/positions/otc",
            headers=ig_headers(
                session,
                "2",
            ),
            json=payload,
            timeout=15,
        )

        if response.status_code not in (
            200,
            201,
        ):

            return False, (
                f"Order failed "
                f"({response.status_code}): "
                f"{response.text[:350]}"
            )

        result = response.json()

        return True, (
            f"{direction} order sent. "
            f"Reference: "
            f"{result.get('dealReference', 'N/A')}"
        )

    except Exception as exc:

        return False, str(exc)


# ============================================================
# BOT CYCLE
# ============================================================

def run_bot(
    size,
    rr,
):

    session = st.session_state.session

    epic = st.session_state.epic

    quote, error = get_current_price(
        session,
        epic,
    )

    if error:

        st.session_state.message = error

        return

    price = quote["price"]

    st.session_state.last_price = price

    st.session_state.last_tick = (
        datetime.now(
            timezone.utc
        ).strftime(
            "%H:%M:%S"
        )
    )

    # Build local M5 candle.
    add_tick(price)

    df = bars_dataframe()

    (
        action,
        reason,
        level,
        pattern,
        atr,
    ) = make_signal(df)

    st.session_state.signal = action

    # Don't trade until enough local history exists.
    if len(df) < 20:

        st.session_state.message = (
            f"Building local M5 history: "
            f"{len(df)}/20 bars."
        )

        return

    positions, error = get_positions(
        session,
        epic,
    )

    if positions is None:

        st.session_state.message = error

        return

    if positions:

        st.session_state.message = (
            "Existing XAU/USD position. "
            "Duplicate entry blocked."
        )

        return

    if (
        st.session_state.trades
        >= MAX_TRADES
    ):

        st.session_state.message = (
            "Daily limit reached."
        )

        return

    if action == "WAIT":

        st.session_state.message = (
            f"WAIT — {reason}"
        )

        return

    current_bar = str(
        df.iloc[-1]["time"]
    )

    # Only one entry per M5 candle.
    if (
        st.session_state.last_trade_bar
        == current_bar
    ):

        st.session_state.message = (
            "Signal already processed "
            "for this M5 candle."
        )

        return

    if atr <= 0:

        return

    stop_distance = atr

    limit_distance = (
        atr * rr
    )

    success, message = place_order(
        session,
        epic,
        action,
        size,
        stop_distance,
        limit_distance,
    )

    st.session_state.last_trade_bar = (
        current_bar
    )

    if success:

        st.session_state.trades += 1

    st.session_state.message = message


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.header(
    "🐎 XAU/USD Bot"
)

deal_size = st.sidebar.number_input(
    "Deal Size",
    min_value=0.01,
    value=0.10,
    step=0.01,
)

risk_reward = st.sidebar.number_input(
    "Risk / Reward",
    min_value=0.5,
    max_value=5.0,
    value=1.5,
    step=0.5,
)


if st.sidebar.button(
    "🔌 CONNECT IG DEMO",
    use_container_width=True,
):

    session, message = login()

    if session:

        st.session_state.session = session

        st.session_state.epic = (
            find_gold(session)
        )

        st.session_state.message = message

        st.success(
            f"{message} "
            f"EPIC: {st.session_state.epic}"
        )

    else:

        st.error(message)


if st.sidebar.button(
    "▶️ START BOT",
    use_container_width=True,
):

    if st.session_state.session:

        st.session_state.running = True

        st.session_state.message = (
            "Bot started."
        )

    else:

        st.error(
            "Connect IG Demo first."
        )


if st.sidebar.button(
    "⏹ STOP BOT",
    use_container_width=True,
):

    st.session_state.running = False

    st.session_state.message = (
        "Bot stopped."
    )


# ============================================================
# RUN
# ============================================================

if (
    st.session_state.running
    and st.session_state.session
):

    run_bot(
        deal_size,
        risk_reward,
    )


# ============================================================
# DASHBOARD
# ============================================================

c1, c2, c3, c4 = st.columns(4)

with c1:

    st.metric(
        "IG Demo",
        "CONNECTED"
        if st.session_state.session
        else "OFFLINE",
    )

with c2:

    st.metric(
        "BOT",
        "RUNNING"
        if st.session_state.running
        else "STOPPED",
    )

with c3:

    st.metric(
        "SIGNAL",
        st.session_state.signal,
    )

with c4:

    st.metric(
        "TRADES",
        f"{st.session_state.trades}/{MAX_TRADES}",
    )


st.divider()

st.subheader(
    "🥇 XAU/USD"
)

st.write(
    f"EPIC: `{st.session_state.epic}`"
)

if st.session_state.last_price is not None:

    st.metric(
        "Live IG Price",
        f"{st.session_state.last_price:.2f}",
    )

st.write(
    f"Status: **{st.session_state.message}**"
)

if st.session_state.last_tick:

    st.caption(
        f"Last IG tick: "
        f"{st.session_state.last_tick} UTC"
    )


df = bars_dataframe()

if not df.empty:

    st.subheader(
        "M5 Candles Built Locally"
    )

    st.write(
        f"Local M5 bars: **{len(df)}**"
    )

    st.dataframe(
        df.tail(10),
        use_container_width=True,
        hide_index=True,
    )

else:

    st.info(
        "No local M5 candles yet."
    )


st.divider()

st.caption(
    "IG historical candles are not requested. "
    "The bot builds M5 candles from live IG prices."
)


# ============================================================
# AUTO REFRESH
# ============================================================

if (
    st.session_state.running
    and st.session_state.session
):

    st.markdown(
        f"""
        <script>
        setTimeout(function() {{
            window.parent.location.reload();
        }}, {POLL_SECONDS * 1000});
        </script>
        """,
        unsafe_allow_html=True,
    )
