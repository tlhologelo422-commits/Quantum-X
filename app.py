import os
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st


IG_BASE = "https://demo-api.ig.com/gateway/deal"
MAX_TRADES = 10
DEFAULT_EPIC = "CS.D.XAUUSD.CFD.IP"


st.set_page_config(
    page_title="Quantum XAU/USD Scalper",
    page_icon="🐎",
    layout="wide",
)


# ============================================================
# STATE
# ============================================================

defaults = {
    "session": None,
    "epic": DEFAULT_EPIC,
    "running": False,
    "trades": 0,
    "last_candle": None,
    "signal": "WAIT",
    "message": "Ready.",
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# IG
# ============================================================

def login():
    api_key = os.getenv("IG_API_KEY")
    username = os.getenv("IG_USERNAME")
    password = os.getenv("IG_PASSWORD")

    if not api_key or not username or not password:
        return None, "IG secrets are missing."

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
        r = requests.post(
            f"{IG_BASE}/session",
            headers=headers,
            json=payload,
            timeout=15,
        )

        if r.status_code not in (200, 201):
            return None, (
                f"Login failed ({r.status_code}): "
                f"{r.text[:300]}"
            )

        cst = r.headers.get("CST")
        token = r.headers.get("X-SECURITY-TOKEN")

        if not cst or not token:
            return None, "IG session tokens missing."

        return {
            "api_key": api_key,
            "CST": cst,
            "X-SECURITY-TOKEN": token,
        }, "Connected to IG Demo."

    except Exception as e:
        return None, str(e)


def headers(session, version="2"):
    return {
        "X-IG-API-KEY": session["api_key"],
        "CST": session["CST"],
        "X-SECURITY-TOKEN": session["X-SECURITY-TOKEN"],
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Version": version,
    }


# ============================================================
# XAU MARKET
# ============================================================

def find_gold(session):
    try:
        r = requests.get(
            f"{IG_BASE}/markets",
            params={"searchTerm": "XAUUSD"},
            headers=headers(session, "1"),
            timeout=15,
        )

        if r.status_code != 200:
            return DEFAULT_EPIC

        markets = r.json().get("markets", [])

        for m in markets:
            epic = m.get("epic", "")
            name = str(
                m.get("instrumentName", "")
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
# M5 CANDLES
# ============================================================

def get_m5(session, epic):
    """
    IG historical prices endpoint.
    """

    try:
        r = requests.get(
            f"{IG_BASE}/prices/{epic}",
            params={
                "resolution": "MINUTE_5",
                "numPoints": 100,
            },
            headers=headers(session, "3"),
            timeout=15,
        )

        if r.status_code != 200:
            return None, (
                f"Price request failed "
                f"({r.status_code}): "
                f"{r.text[:300]}"
            )

        prices = r.json().get("prices", [])

        if not prices:
            return None, "IG returned no M5 candles."

        rows = []

        for p in prices:
            time_value = (
                p.get("snapshotTimeUTC")
                or p.get("snapshotTime")
            )

            if not time_value:
                continue

            op = p.get("openPrice", {})
            hi = p.get("highPrice", {})
            lo = p.get("lowPrice", {})
            cl = p.get("closePrice", {})

            def mid(x):
                bid = x.get("bid")
                ask = x.get("ask")

                if bid is not None and ask is not None:
                    return (
                        float(bid)
                        + float(ask)
                    ) / 2

                if bid is not None:
                    return float(bid)

                return None

            o = mid(op)
            h = mid(hi)
            l = mid(lo)
            c = mid(cl)

            if None in (o, h, l, c):
                continue

            rows.append(
                {
                    "time": pd.to_datetime(
                        time_value,
                        utc=True,
                    ),
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                }
            )

        if not rows:
            return None, "No usable M5 candles."

        df = pd.DataFrame(rows)

        return (
            df.sort_values("time")
            .drop_duplicates("time")
            .reset_index(drop=True),
            "",
        )

    except Exception as e:
        return None, f"M5 error: {e}"


# ============================================================
# STRATEGY
# ============================================================

def strategy(df):
    if len(df) < 30:
        return "WAIT", "Not enough M5 candles.", None

    current = df.iloc[-1]
    price = float(current["close"])

    supports = []
    resistances = []

    for i in range(2, len(df) - 2):
        if (
            df["low"].iloc[i]
            < df["low"].iloc[i - 1]
            and df["low"].iloc[i]
            < df["low"].iloc[i + 1]
            and df["low"].iloc[i]
            < df["low"].iloc[i - 2]
            and df["low"].iloc[i]
            < df["low"].iloc[i + 2]
        ):
            supports.append(
                float(df["low"].iloc[i])
            )

        if (
            df["high"].iloc[i]
            > df["high"].iloc[i - 1]
            and df["high"].iloc[i]
            > df["high"].iloc[i + 1]
            and df["high"].iloc[i]
            > df["high"].iloc[i - 2]
            and df["high"].iloc[i]
            > df["high"].iloc[i + 2]
        ):
            resistances.append(
                float(df["high"].iloc[i])
            )

    atr = (
        df["high"] - df["low"]
    ).tail(14).mean()

    tolerance = max(
        float(atr) * 0.5,
        price * 0.0005,
    )

    support = [
        x for x in supports
        if x <= price
        and price - x <= tolerance
    ]

    resistance = [
        x for x in resistances
        if x >= price
        and x - price <= tolerance
    ]

    prev = df.iloc[-2]

    o = float(current["open"])
    h = float(current["high"])
    l = float(current["low"])
    c = float(current["close"])

    po = float(prev["open"])
    pc = float(prev["close"])

    body = abs(c - o)
    rng = max(h - l, 0.00001)

    upper = h - max(o, c)
    lower = min(o, c) - l

    bullish = (
        (
            pc < po
            and c > o
            and o <= pc
            and c >= po
        )
        or (
            c > o
            and lower >= body * 1.5
            and lower >= rng * 0.30
        )
        or (
            c > o
            and body >= rng * 0.60
        )
    )

    bearish = (
        (
            pc > po
            and c < o
            and o >= pc
            and c <= po
        )
        or (
            c < o
            and upper >= body * 1.5
            and upper >= rng * 0.30
        )
        or (
            c < o
            and body >= rng * 0.60
        )
    )

    if support and bullish:
        return (
            "BUY",
            "Support + bullish confirmation.",
            max(support),
        )

    if resistance and bearish:
        return (
            "SELL",
            "Resistance + bearish confirmation.",
            min(resistance),
        )

    return (
        "WAIT",
        "No valid M5 setup.",
        None,
    )


# ============================================================
# POSITIONS
# ============================================================

def open_positions(session, epic):
    try:
        r = requests.get(
            f"{IG_BASE}/positions",
            headers=headers(session, "2"),
            timeout=15,
        )

        if r.status_code != 200:
            return None, r.text[:250]

        positions = r.json().get(
            "positions",
            [],
        )

        return [
            p for p in positions
            if p.get("market", {}).get(
                "epic"
            ) == epic
        ], ""

    except Exception as e:
        return None, str(e)


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
        r = requests.post(
            f"{IG_BASE}/positions/otc",
            headers=headers(session, "2"),
            json=payload,
            timeout=15,
        )

        if r.status_code not in (200, 201):
            return False, (
                f"Order failed "
                f"({r.status_code}): "
                f"{r.text[:350]}"
            )

        data = r.json()

        return True, (
            f"{direction} order sent. "
            f"Reference: "
            f"{data.get('dealReference', 'N/A')}"
        )

    except Exception as e:
        return False, str(e)


# ============================================================
# BOT CYCLE
# ============================================================

def cycle(size, rr):
    session = st.session_state.session
    epic = st.session_state.epic

    df, error = get_m5(
        session,
        epic,
    )

    if error:
        st.session_state.message = error
        return None

    action, reason, level = strategy(df)

    st.session_state.signal = action

    positions, error = open_positions(
        session,
        epic,
    )

    if positions is None:
        st.session_state.message = (
            "Position check: " + error
        )
        return df

    if positions:
        st.session_state.message = (
            "Existing XAU/USD position. "
            "No duplicate trade."
        )
        return df

    if st.session_state.trades >= MAX_TRADES:
        st.session_state.message = (
            "10-trade daily limit reached."
        )
        return df

    candle_time = str(
        df.iloc[-1]["time"]
    )

    if (
        action != "WAIT"
        and candle_time
        != st.session_state.last_candle
    ):
        atr = float(
            (
                df["high"]
                - df["low"]
            ).tail(14).mean()
        )

        success, message = place_order(
            session,
            epic,
            action,
            size,
            atr,
            atr * rr,
        )

        st.session_state.last_candle = (
            candle_time
        )

        if success:
            st.session_state.trades += 1

        st.session_state.message = message

    else:
        st.session_state.message = (
            f"{action}: {reason}"
        )

    return df


# ============================================================
# UI
# ============================================================

st.sidebar.header("🐎 Bot Controls")

size = st.sidebar.number_input(
    "Deal Size",
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

if st.sidebar.button(
    "🔌 CONNECT IG DEMO",
    use_container_width=True,
):
    session, message = login()

    if session:
        st.session_state.session = session
        st.session_state.epic = find_gold(
            session
        )
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
            "Bot running."
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
# EXECUTION
# ============================================================

df = None

if (
    st.session_state.running
    and st.session_state.session
):
    df = cycle(size, rr)

elif st.session_state.session:
    df, error = get_m5(
        st.session_state.session,
        st.session_state.epic,
    )

    if error:
        st.session_state.message = error


# ============================================================
# DASHBOARD
# ============================================================

a, b, c, d = st.columns(4)

with a:
    st.metric(
        "IG",
        "CONNECTED"
        if st.session_state.session
        else "OFFLINE",
    )

with b:
    st.metric(
        "BOT",
        "RUNNING"
        if st.session_state.running
        else "STOPPED",
    )

with c:
    st.metric(
        "SIGNAL",
        st.session_state.signal,
    )

with d:
    st.metric(
        "TRADES",
        f"{st.session_state.trades}/{MAX_TRADES}",
    )


st.divider()

st.subheader("🥇 XAU/USD")

st.write(
    f"IG EPIC: `{st.session_state.epic}`"
)

st.write(
    f"Status: **{st.session_state.message}**"
)

if df is not None and not df.empty:

    latest = df.iloc[-1]

    x1, x2, x3 = st.columns(3)

    with x1:
        st.metric(
            "M5 Price",
            f"{float(latest['close']):.2f}",
        )

    with x2:
        st.metric(
            "M5 High",
            f"{float(latest['high']):.2f}",
        )

    with x3:
        st.metric(
            "M5 Low",
            f"{float(latest['low']):.2f}",
        )

    st.dataframe(
        df.tail(10),
        use_container_width=True,
        hide_index=True,
    )


if st.session_state.running:
    st.info(
        "Bot is running. Streamlit will refresh "
        "automatically."
    )

    # Streamlit-compatible refresh.
    # No time.sleep() — that was the previous crash.
    st.markdown(
        """
        <script>
        setTimeout(function() {
            window.parent.location.reload();
        }, 15000);
        </script>
        """,
        unsafe_allow_html=True,
)
