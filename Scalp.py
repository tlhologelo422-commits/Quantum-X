import streamlit as st, time, requests
from datetime import datetime, timezone

# --- CONFIG ---
TARGET = 2.50  # $ per scalp
SIZE = 0.02
EPIC = "CS.D.CFD.GOLD.CFDGC"  # XAU/USD
REFRESH = 1  # sec

st.set_page_config(page_title="STACKER SCALPER", layout="wide")
st.title("🔥 STACKER - BUY CLOSE SELL CLOSE REPEAT")

if "stack" not in st.session_state:
    st.session_state.stack = 0.0
    st.session_state.trades = 0
    st.session_state.log = []

# --- IG LOGIN (uses your secrets) ---
def ig_login():
    url = "https://api.ig.com/gateway/deal/session"
    h = {"X-IG-API-KEY": st.secrets["IG_API_KEY"], "Content-Type": "application/json"}
    r = requests.post(url, json={"identifier": st.secrets["IG_USERNAME"], "password": st.secrets["IG_PASSWORD"]}, headers=h)
    data = r.json()
    return data["oauthToken"]["access_token"], data["accountId"]

def get_market(token):
    url = f"https://api.ig.com/gateway/deal/markets/{EPIC}"
    h = {"Authorization": f"Bearer {token}", "X-IG-API-KEY": st.secrets["IG_API_KEY"], "Version": "3"}
    r = requests.get(url, headers=h).json()
    s = r["snapshot"]
    return {"bid": s["bid"], "offer": s["offer"], "status": r["market"]["marketStatus"]}

def open_pos(token, direction):
    url = "https://api.ig.com/gateway/deal/positions/otc"
    h = {"Authorization": f"Bearer {token}", "X-IG-API-KEY": st.secrets["IG_API_KEY"], "Version": "2", "Content-Type": "application/json"}
    body = {"epic": EPIC, "expiry": "-", "direction": direction, "size": str(SIZE), "orderType": "MARKET", "guaranteedStop": False, "forceOpen": True, "currencyCode": "USD"}
    r = requests.post(url, json=body, headers=h).json()
    return r

def get_positions(token, acc):
    url = "https://api.ig.com/gateway/deal/positions"
    h = {"Authorization": f"Bearer {token}", "X-IG-API-KEY": st.secrets["IG_API_KEY"], "Version": "2"}
    r = requests.get(url, headers=h).json()
    return r.get("positions", [])

def close_all(token):
    pos = get_positions(token, "")
    for p in pos:
        deal = p["position"]["dealId"]
        direction = "SELL" if p["position"]["direction"] == "BUY" else "BUY"
        size = p["position"]["size"]
        url = f"https://api.ig.com/gateway/deal/positions/otc/{deal}"
        h = {"Authorization": f"Bearer {token}", "X-IG-API-KEY": st.secrets["IG_API_KEY"], "Version": "2", "Content-Type": "application/json", "_method": "DELETE"}
        # IG close uses DELETE with body
        requests.post(f"https://api.ig.com/gateway/deal/positions/otc", json={"dealId": deal, "direction": direction, "size": str(size), "orderType": "MARKET"}, headers=h)

# --- UI ---
col1, col2, col3 = st.columns(3)
col1.metric("STACKED TODAY", f"${st.session_state.stack:.2f}")
col2.metric("SCALPS", st.session_state.trades)
col3.metric("SIZE", SIZE)

log_box = st.empty()

try:
    token, accId = ig_login()
    m = get_market(token)
    st.write(f"Market: {m['status']} | Bid {m['bid']} Offer {m['offer']}")
    
    if m["status"] != "TRADEABLE":
        st.warning(f"Waiting for market - {m['status']} - will auto stack at 00:05")
        time.sleep(2)
        st.rerun()
    
    positions = get_positions(token, accId)
    
    if not positions:
        # NO POSITION -> OPEN BUY TO START STACK
        direction = "BUY" if st.session_state.trades % 2 == 0 else "SELL"
        open_pos(token, direction)
        st.session_state.log.append(f"{datetime.now().strftime('%H:%M:%S')} OPEN {direction}")
    else:
        # HAS POSITION -> CHECK PNL
        p = positions[0]
        pnl = p["position"]["profit"] + p["position"]["pnl"] if "pnl" in p["position"] else p["position"]["profit"]
        # IG profit field is already USD
        if pnl >= TARGET:
            close_all(token)
            st.session_state.stack += pnl
            st.session_state.trades += 1
            st.session_state.log.append(f"{datetime.now().strftime('%H:%M:%S')} CLOSE +${pnl:.2f} | STACK ${st.session_state.stack:.2f}")
        elif pnl <= -5.0:  # hard stop -5
            close_all(token)
            st.session_state.stack += pnl
            st.session_state.trades += 1
            st.session_state.log.append(f"{datetime.now().strftime('%H:%M:%S')} STOP {pnl:.2f}")
        else:
            st.write(f"Holding {p['position']['direction']} PnL ${pnl:.2f} / Target ${TARGET}")

    with log_box:
        for line in st.session_state.log[-15:][::-1]:
            st.text(line)

    time.sleep(REFRESH)
    st.rerun()

except Exception as e:
    st.error(f"Error: {e}")
    time.sleep(2)
    st.rerun()
