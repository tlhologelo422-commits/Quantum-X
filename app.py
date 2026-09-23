import os
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st


# ============================================================
# CONFIG
# ============================================================

IG_BASE = "https://demo-api.ig.com/gateway/deal"
API_VERSION = "2"

MAX_TRADES_PER_DAY = 10
M5_BARS = 100
POLL_SECONDS = 15

# IG commonly uses this EPIC for spot Gold CFD.
# The bot also searches IG and uses the matching market if found.
DEFAULT_EPIC = "CS.D.XAUUSD.CFD.IP"


# ============================================================
# PAGE
# ============================================================

st.set_page_config(
    page_title="Quantum XAU/USD Scalper",
    page_icon="🐎",
    layout="wide",
)

st.title("🐎 Quantum XAU/USD Scalper")
st.caption("IG DEMO • M5 • Support / Resistance • Confirmation Candles")


# ============================================================
# SESSION STATE
# ============================================================

if "ig_session" not in st.session_state:
    st.session_state.ig_session = None

if "epic" not in st.session_state:
    st.session_state.epic = DEFAULT_EPIC

if "bot_running" not in st.session_state:
    st.session_state.bot_running = False

if "last_trade_candle" not in st.session_state:
    st.session_state.last_trade_candle = None

if "trades_today" not in st.session_state:
    st.session_state.trades_today = 0

if "last_action" not in st.session_state:
    st.session_state.last_action = "WAIT"

if "last_message" not in st.session_state:
    st.session_state.last_message = "Bot ready."


# ============================================================
# IG AUTH
# ============================================================

def ig_login():
    username = os.getenv("IG_USERNAME")
    password = os.getenv("IG_PASSWORD")
    api_key = os.getenv("IG_API_KEY")

    if not username or not password or not api_key:
        return None, "IG secrets are missing from Codespaces."

    headers = {
        "X-IG-API-KEY": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Version": API_VERSION,
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
                f"IG login failed "
                f"({response.status_code}): "
                f"{response.text[:300]}"
            )

        cst = response.headers.get("CST")
        security_token = response.headers.get(
            "X-SECURITY-TOKEN"
        )

        if not cst or not security_token:
            return None, "IG session tokens were not returned."

        session = {
            "CST": cst,
            "X-SECURITY-TOKEN": security_token,
            "api_key": api_key,
        }

        return session, "Connected to IG Demo."

    except Exception as exc:
        return None, f"Connection error: {exc}"


def ig_headers(session, version=API_VERSION):
    return {
        "X-IG-API-KEY": session["api_key"],
        "CST": session["CST"],
        "X-SECURITY-TOKEN": session["X-SECURITY-TOKEN"],
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Version": version,
    }


# ============================================================
# FIND XAU/USD
# ============================================================

def find_gold_epic(session):
    try:
        response = requests.get(
            f"{IG_BASE}/markets",
            params={"searchTerm": "XAUUSD"},
            headers=ig_headers(session, "1"),
            timeout=15,
        )

        if response.status_code != 200:
            return DEFAULT_EPIC

        data = response.json()
        markets = data.get("markets", [])

        for market in markets:
            epic = market.get("epic", "")
            name = str(market.get("instrumentName", "")).lower()

            if (
                "xauusd" in epic.lower()
                or "gold" in name
            ):
                return epic

        return DEFAULT_EPIC

    except Exception:
        return DEFAULT_EPIC


# ============================================================
# M5 PRICES
# ============================================================

def get_m5_prices(session, epic):
    try:
        response = requests.get(
            f"{IG_BASE}/prices/{epic}/M5/{M5_BARS}",
            headers=ig_headers(session, "3"),
            timeout=15,
        )

        if response.status_code != 200:
            return None, (
                f"Price request failed "
                f"({response.status_code}): "
                f"{response.text[:250]}"
            )

        raw = response.json()

        rows = []

        for item in raw.get("prices", []):
            snapshot = item.get("snapshotTimeUTC")

            if not snapshot:
                continue

            bid = item.get("closePrice", {}).get("bid")
            ask = item.get("closePrice", {}).get("ask")

            open_bid = item.get("openPrice", {}).get("bid")
            high_bid = item.get("highPrice", {}).get("bid")
            low_bid = item.get("lowPrice", {}).get("bid")

            if None in (
                bid,
                ask,
                open_bid,
                high_bid,
                low_bid,
            ):
                continue

            close = (
                float(bid) + float(ask)
            ) / 2

            rows.append(
                {
                    "time": pd.to_datetime(
                        snapshot,
                        utc=True,
                    ),
                    "open": float(open_bid),
                    "high": float(high_bid),
                    "low": float(low_bid),
                    "close": close,
                    "bid": float(bid),
                    "ask": float(ask),
                }
            )

        if not rows:
            return None, "IG returned no M5 candles."

        df = pd.DataFrame(rows)
        df = df.sort_values("time")
        df = df.drop_duplicates("time")
        df = df.reset_index(drop=True)

        return df, ""

    except Exception as exc:
        return None, f"M5 data error: {exc}"


# ============================================================
# STRATEGY
# ============================================================

def find_levels(df):
    recent = df.tail(80).copy()

    supports = []
    resistances = []

    for i in range(2, len(recent) - 2):
        h = recent.iloc[i]["high"]
        l = recent.iloc[i]["low"]

        left_high = recent.iloc[i - 2:i]["high"].max()
        right_high = recent.iloc[i + 1:i + 3]["high"].max()

        left_low = recent.iloc[i - 2:i]["low"].min()
        right_low = recent.iloc[i + 1:i + 3]["low"].min()

        if h > left_high and h > right_high:
            resistances.append(float(h))

        if l < left_low and l < right_low:
            supports.append(float(l))

    return supports, resistances


def confirmation(df):
    if len(df) < 3:
        return "NONE"

    prev = df.iloc[-2]
    cur = df.iloc[-1]

    po = float(prev["open"])
    pc = float(prev["close"])

    o = float(cur["open"])
    h = float(cur["high"])
    l = float(cur["low"])
    c = float(cur["close"])

    body = abs(c - o)
    rng = max(h - l, 0.0000001)

    upper = h - max(o, c)
    lower = min(o, c) - l

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
        and lower >= body * 1.5
        and lower >= rng * 0.30
    )

    bearish_rejection = (
        c < o
        and upper >= body * 1.5
        and upper >= rng * 0.30
    )

    bullish_momentum = (
        c > o
        and body >= rng * 0.60
    )

    bearish_momentum = (
        c < o
        and body >= rng * 0.60
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


def create_signal(df):
    if len(df) < 30:
        return {
            "action": "WAIT",
            "reason": "Not enough M5 candles.",
            "level": None,
            "confirmation": "NONE",
        }

    supports, resistances = find_levels(df)

    current = df.iloc[-1]

    price = float(current["close"])

    ranges = (
        df["high"] - df["low"]
    ).tail(14)

    atr = float(ranges.mean())

    if atr <= 0:
        return {
            "action": "WAIT",
            "reason": "Invalid volatility.",
            "level": None,
            "confirmation": "NONE",
        }

    tolerance = max(
        atr * 0.45,
        price * 0.0005,
    )

    support = None
    resistance = None

    below = [
        x for x in supports
        if x <= price
        and price - x <= tolerance
    ]

    above = [
        x for x in resistances
        if x >= price
        and x - price <= tolerance
    ]

    if below:
        support = max(below)

    if above:
        resistance = min(above)

    candle = confirmation(df)

    if support is not None and candle in (
        "BULLISH ENGULFING",
        "BULLISH REJECTION",
        "BULLISH MOMENTUM",
    ):
        return {
            "action": "BUY",
            "reason": "Support + bullish confirmation.",
            "level": support,
            "confirmation": candle,
            "atr": atr,
        }

    if resistance is not None and candle in (
        "BEARISH ENGULFING",
        "BEARISH REJECTION",
        "BEARISH MOMENTUM",
    ):
        return {
            "action": "SELL",
            "reason": "Resistance + bearish confirmation.",
            "level": resistance,
            "confirmation": candle,
            "atr": atr,
        }

    return {
        "action": "WAIT",
        "reason": "No valid M5 setup.",
        "level": support or resistance,
        "confirmation": candle,
        "atr": atr,
    }


# ============================================================
# OPEN POSITIONS
# ============================================================

def get_open_positions(session, epic):
    try:
        response = requests.get(
            f"{IG_BASE}/positions",
            headers=ig_headers(session, "2"),
            timeout=15,
        )

        if response.status_code != 200:
            return None, response.text[:250]

        positions = response.json().get(
            "positions",
            [],
        )

        matching = []

        for item in positions:
            position = item.get("market", {})
            if position.get("epic") == epic:
                matching.append(item)

        return matching, ""

    except Exception as exc:
        return None, str(exc)


# ============================================================
# OPEN IG DEMO TRADE
# ============================================================

def open_trade(
    session,
    epic,
    action,
    size,
    stop_distance,
    limit_distance,
):
    payload = {
        "epic": epic,
        "expiry": "-",
        "direction": action,
        "size": float(size),
        "orderType": "MARKET",
        "guaranteedStop": False,
        "forceOpen": True,
        "stopDistance": float(stop_distance),
        "limitDistance": float(limit_distance),
    }

    try:
        response = requests.post(
            f"{IG_BASE}/positions/otc",
            headers=ig_headers(session, "2"),
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

        deal_reference = result.get(
            "dealReference",
            "UNKNOWN",
        )

        return True, (
            f"{action} opened. "
            f"Deal reference: {deal_reference}"
        )

    except Exception as exc:
        return False, f"Order error: {exc}"


# ============================================================
# DAILY TRADE COUNTER
# ============================================================

def reset_daily_counter():
    today = datetime.now(
        timezone.utc
    ).date().isoformat()

    saved_day = st.session_state.get(
        "trade_day"
    )

    if saved_day != today:
        st.session_state.trade_day = today
        st.session_state.trades_today = 0


reset_daily_counter()


# ============================================================
# CONTROLS
# ============================================================

st.sidebar.header("⚙️ Bot Controls")

trade_size = st.sidebar.number_input(
    "IG Deal Size",
    min_value=0.01,
    value=0.10,
    step=0.01,
)

rr = st.sidebar.number_input(
    "Risk / Reward",
    min_value=0.5,
    max_value=5.0,
    value=1.5,
    step=0.5,
)

st.sidebar.write(
    f"Daily limit: {MAX_TRADES_PER_DAY}"
)

connect = st.sidebar.button(
    "🔌 Connect IG Demo",
    use_container_width=True,
)

start = st.sidebar.button(
    "▶️ START BOT",
    use_container_width=True,
)

stop = st.sidebar.button(
    "⏹ STOP BOT",
    use_container_width=True,
)


# ============================================================
# CONNECTION
# ============================================================

if connect:

    with st.spinner("Connecting to IG Demo..."):

        session, message = ig_login()

    if session:

        st.session_state.ig_session = session

        epic = find_gold_epic(session)

        st.session_state.epic = epic

        st.success(
            f"{message} Gold EPIC: {epic}"
        )

    else:

        st.error(message)


if start:

    if st.session_state.ig_session is None:

        session, message = ig_login()

        if session:

            st.session_state.ig_session = session
            st.session_state.epic = find_gold_epic(
                session
            )

        else:

            st.error(message)

    if st.session_state.ig_session:

        st.session_state.bot_running = True
        st.session_state.last_message = "Bot started."


if stop:

    st.session_state.bot_running = False
    st.session_state.last_message = "Bot stopped."


# ============================================================
# BOT CYCLE
# ============================================================

def run_cycle():

    session = st.session_state.ig_session
    epic = st.session_state.epic

    if session is None:
        return

    df, error = get_m5_prices(
        session,
        epic,
    )

    if error:

        st.session_state.last_message = error
        return

    signal = create_signal(df)

    last_candle = df.iloc[-1]
    candle_time = str(last_candle["time"])

    current_price = float(
        last_candle["close"]
    )

    st.session_state.last_action = (
        signal["action"]
    )

    positions, position_error = (
        get_open_positions(
            session,
            epic,
        )
    )

    if positions is None:

        st.session_state.last_message = (
            f"Position check failed: "
            f"{position_error}"
        )
        return

    if positions:

        st.session_state.last_message = (
            "Open XAU/USD position detected. "
            "No duplicate trade."
        )
        return

    if st.session_state.trades_today >= MAX_TRADES_PER_DAY:

        st.session_state.last_message = (
            "Daily trade limit reached."
        )
        return

    if signal["action"] == "WAIT":

        st.session_state.last_message = (
            "Waiting — "
            + signal["reason"]
        )
        return

    if (
        st.session_state.last_trade_candle
        == candle_time
    ):

        st.session_state.last_message = (
            "Signal already processed."
        )
        return

    atr = float(
        signal.get("atr", 0)
    )

    if atr <= 0:
        return

    stop_distance = atr

    limit_distance = (
        atr * float(rr)
    )

    success, message = open_trade(
        session=session,
        epic=epic,
        action=signal["action"],
        size=trade_size,
        stop_distance=stop_distance,
        limit_distance=limit_distance,
    )

    st.session_state.last_trade_candle = (
        candle_time
    )

    if success:

        st.session_state.trades_today += 1
        st.session_state.last_message = message

    else:

        st.session_state.last_message = message


# ============================================================
# RUN
# ============================================================

if (
    st.session_state.bot_running
    and st.session_state.ig_session
):

    run_cycle()


# ============================================================
# DASHBOARD
# ============================================================

st.divider()

c1, c2, c3, c4 = st.columns(4)

with c1:

    connected = (
        st.session_state.ig_session
        is not None
    )

    st.metric(
        "IG Demo",
        "CONNECTED"
        if connected
        else "OFFLINE",
    )

with c2:

    st.metric(
        "Bot",
        "RUNNING"
        if st.session_state.bot_running
        else "STOPPED",
    )

with c3:

    st.metric(
        "Signal",
        st.session_state.last_action,
    )

with c4:

    st.metric(
        "Trades Today",
        f"{st.session_state.trades_today}/{MAX_TRADES_PER_DAY}",
    )


st.subheader("📡 Bot Message")

if (
    st.session_state.bot_running
    and st.session_state.ig_session
):

    st.success(
        st.session_state.last_message
    )

else:

    st.info(
        st.session_state.last_message
    )


st.subheader("🥇 XAU/USD")

st.write(
    f"IG EPIC: `{st.session_state.epic}`"
)

st.write(
    "Timeframe: **M5**"
)

st.write(
    "Strategy: **Support + Resistance + Confirmation Candle**"
)

st.write(
    "Execution: **IG Demo**"
)

st.caption(
    "The page must remain running for the Streamlit bot loop to continue."
)

if st.session_state.bot_running:

    time.sleep(POLL_SECONDS)
    st.rerun()
