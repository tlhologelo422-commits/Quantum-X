import os
import requests
import pandas as pd
import numpy as np
import streamlit as st

st.set_page_config(page_title="Quantum X PRO", layout="wide")

BASE = "https://demo-api.ig.com/gateway/deal"

# ---------- SETTINGS ----------
TP = 1.0
SL = 1.0
ATR_MULT = 0.8
SIZE = 1
EXECUTION = False   # Change to True only after testing

# ---------- SESSION ----------
for k in ["cst", "token", "epic", "connected"]:
    st.session_state.setdefault(k, None)

# ---------- IG ----------
def connect():
    u = os.getenv("IG_USERNAME")
    p = os.getenv("IG_PASSWORD")
    k = os.getenv("IG_API_KEY")

    if not all([u, p, k]):
        return False, "Missing IG credentials."

    r = requests.post(
        BASE + "/session",
        headers={
            "X-IG-API-KEY": k,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Version": "2"
        },
        json={
            "identifier": u,
            "password": p,
            "encryptedPassword": False
        },
        timeout=20
    )

    if r.status_code != 200:
        return False, "IG login failed."

    st.session_state.cst = r.headers.get("CST")
    st.session_state.token = r.headers.get("X-SECURITY-TOKEN")
    st.session_state.connected = True

    return True, "IG Demo connected."

def headers():
    return {
        "X-IG-API-KEY": os.getenv("IG_API_KEY", ""),
        "CST": st.session_state.cst,
        "X-SECURITY-TOKEN": st.session_state.token,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Version": "2"
    }

# ---------- FIND GOLD ----------
def find_gold():
    r = requests.get(
        BASE + "/markets",
        headers={**headers(), "Version": "1"},
        params={"searchTerm": "Gold"},
        timeout=20
    )

    if r.status_code != 200:
        return None

    markets = r.json().get("markets", [])

    for m in markets:
        ins = m.get("instrument", {})
        epic = ins.get("epic", "")
        name = ins.get("name", "")

        if "gold" in (epic + name).lower():
            return epic

    return None

# ---------- PRICE DATA ----------
def get_prices(epic):
    r = requests.get(
        f"{BASE}/prices/{epic}/MINUTE/100",
        headers=headers(),
        timeout=20
    )

    if r.status_code != 200:
        return None

    rows = []

    for p in r.json().get("prices", []):
        def mid(x):
            if not isinstance(x, dict):
                return np.nan
            b, a = x.get("bid"), x.get("ask")
            if b is not None and a is not None:
                return (float(b) + float(a)) / 2
            return float(b if b is not None else a)

        rows.append({
            "time": p.get("snapshotTimeUTC"),
            "open": mid(p.get("openPrice")),
            "high": mid(p.get("highPrice")),
            "low": mid(p.get("lowPrice")),
            "close": mid(p.get("closePrice"))
        })

    df = pd.DataFrame(rows)

    if df.empty:
        return None

    return df.dropna().sort_values("time").reset_index(drop=True)

# ---------- REVERSAL ENGINE ----------
def signal(df):
    if len(df) < 20:
        return "WAIT", 0

    h = df.high
    l = df.low
    c = df.close
    o = df.open

    tr = pd.concat([
        h - l,
        (h - c.shift()).abs(),
        (l - c.shift()).abs()
    ], axis=1).max(axis=1)

    atr = tr.rolling(14).mean().iloc[-1]

    # Last two candles
    prev = df.iloc[-2]
    last = df.iloc[-1]

    move = abs(last.close - prev.close)

    # Strong downward movement + bullish reversal
    if (
        prev.close < prev.open and
        move >= atr * ATR_MULT and
        last.close > last.open
    ):
        return "BUY", last.close

    # Strong upward movement + bearish reversal
    if (
        prev.close > prev.open and
        move >= atr * ATR_MULT and
        last.close < last.open
    ):
        return "SELL", last.close

    return "WAIT", last.close

# ---------- ORDER ----------
def order(direction):
    if not EXECUTION:
        return "Execution OFF — signal only."

    side = "BUY" if direction == "BUY" else "SELL"

    payload = {
        "epic": st.session_state.epic,
        "direction": side,
        "size": SIZE,
        "orderType": "MARKET",
        "currencyCode": "USD",
        "forceOpen": True,
        "guaranteedStop": False,
        "timeInForce": "FILL_OR_KILL"
    }

    r = requests.post(
        BASE + "/positions/otc",
        headers=headers(),
        json=payload,
        timeout=20
    )

    return r.text

# ---------- UI ----------
st.title("⚛️ Quantum X PRO")
st.caption("Gold Reversal Scalper V1")

if st.button("🔌 CONNECT IG DEMO"):
    ok, msg = connect()
    st.success(msg) if ok else st.error(msg)

if st.session_state.connected:

    if st.session_state.epic is None:
        st.session_state.epic = find_gold()

    st.success("🟢 IG ONLINE")
    st.code(st.session_state.epic or "Gold not found")

    if st.button("⚡ SCAN GOLD", type="primary"):

        df = get_prices(st.session_state.epic)

        if df is None:
            st.error("No Gold data.")
        else:
            sig, price = signal(df)

            st.metric("PRICE", f"{price:.2f}")
            st.metric("SIGNAL", sig)

            if sig == "BUY":
                st.success("🟢 BUY REVERSAL")
                st.write(f"TP: {price + TP:.2f}")
                st.write(f"SL: {price - SL:.2f}")

                if st.button("EXECUTE BUY"):
                    st.write(order("BUY"))

            elif sig == "SELL":
                st.error("🔴 SELL REVERSAL")
                st.write(f"TP: {price - TP:.2f}")
                st.write(f"SL: {price + SL:.2f}")

                if st.button("EXECUTE SELL"):
                    st.write(order("SELL"))

            else:
                st.info("⚪ No reversal yet.")

st.divider()
st.caption("Quantum X PRO • XAUUSD • Reversal Scalper V1")
