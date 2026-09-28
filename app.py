import copy
import json
import math
import os
import time
from datetime import datetime, timezone, timedelta

import pandas as pd
import requests
import streamlit as st
import yfinance as yf


# ============================================================
# QUANTUM X PRO — VERSION 3
# MACD + DIVERGENCE SCALPER  |  IG DEMO  XAU/USD  M5
#
# MODES
#   1) NORMAL             : one trade, hard SL + TP, break-even move
#   2) HEDGING MARTINGALE : zone-recovery. Opposite hedge legs with
#                           escalating size, closed together at a
#                           basket profit target. HARD leg cap and
#                           HARD money cap per cycle.
#
# DATA
#   Yahoo GC=F  -> M5 history (bootstrap, calibrated to IG price)
#   IG XAU/USD  -> live price + execution (execution authority)
# ============================================================

st.set_page_config(page_title="Quantum X PRO", page_icon="🐎", layout="wide")

IG_BASE_URL = "https://demo-api.ig.com/gateway/deal"
IG_EPIC = "CS.D.IN_GOLD.MFI.IP"
YAHOO_SYMBOL = "GC=F"
CURRENCY = "USD"

POLL_SECONDS = 5
BOOTSTRAP_BARS = 300
MIN_BARS = 60                     # MACD(26+9) + EMA50 warm-up

# --- indicators -------------------------------------------------
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
EMA_TREND = 50
ATR_PERIOD = 14

# --- divergence -------------------------------------------------
PIVOT_LEFT, PIVOT_RIGHT = 3, 2    # swing = 3 bars left, 2 bars right
PIVOT_MIN_GAP, PIVOT_MAX_GAP = 4, 40
SIGNAL_MAX_AGE = 8                # bars since 2nd pivot (freshness)

# --- filters ----------------------------------------------------
ATR_MIN, ATR_MAX = 0.6, 10.0      # skip dead / spiking markets
MAX_CHASE_ATR = 0.6               # do not chase price away from signal
MAX_SPREAD = 1.50
MIN_STOP_DISTANCE = 2.0           # floor, IG's own minimum also applied
CYCLE_MAX_MIN = 120               # hedging cycle time stop
STATE_FILE = "quantum_x_state.json"

DEFAULT_CFG = {
    "mode": "Normal",
    "size": 0.01,
    "rr": 1.5,
    "min_score": 5,
    "signal_mode": "Divergence + MACD cross",
    "risk_usd": 10.0,           # Normal: max loss per trade
    "max_daily_loss": 60.0,     # both modes: stop opening new entries
    "max_cycle_risk": 30.0,     # Hedging: worst-case loss per cycle
    "max_trades": 10,
    "cooldown_min": 5,
    "be_trigger": 0.7,          # Normal: move SL to BE at 0.7R
    "zone_mult": 1.2,           # Hedging: zone width = 1.2 x ATR
    "mult": 2.0,                # Hedging: hedge size multiplier
    "max_legs": 4,              # Hedging: hard leg cap
    "contract": 1.0,            # USD per 1.0 point per 1.0 size (verify!)
}
DEFAULT_STATE = {
    "ig_connected": False,
    "bot_running": False,
    "ig_cst": None,
    "ig_security_token": None,
    "live_bid": None,
    "live_offer": None,
    "live_mid": None,
    "market_status": None,
    "min_stop": MIN_STOP_DISTANCE,
    "min_deal_size": 0.01,
    "bars": [],
    "signal": {"signal": "WAIT", "score": 0, "reason": "Waiting for data.",
               "id": None, "kind": None, "atr": None, "sl_ref": None},
    "calibration_offset": None,
    "yahoo_last_price": None,
    "last_error": None,
    "gate_msg": None,
    "log": [],
    "trade_count": 0,
    "trade_day": datetime.now(timezone.utc).date().isoformat(),
    "done_signal_ids": [],
    "cooldown_until": None,
    "entry_block_until": None,
    "equity": None,
    "day_start_equity": None,
    "account_ts": None,
    "cycle": None,
    "cycle_loaded": False,
    "active_normal": None,
    "last_order": None,
    "cfg": copy.deepcopy(DEFAULT_CFG),
}

for _key, _value in DEFAULT_STATE.items():
    if _key not in st.session_state:
        st.session_state[_key] = copy.deepcopy(_value)


class RiskRefused(RuntimeError):
    """Trade refused by the risk engine (deterministic, do not retry)."""


# ============================================================
# HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def floor_m5(value):
    ts = pd.Timestamp(value)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.floor("5min")


def safe_float(value):
    try:
        if value is None:
            return None
        result = float(value)
        return None if pd.isna(result) else result
    except Exception:
        return None


def floor2(x):
    return math.floor(x * 100 + 1e-9) / 100


def ceil2(x):
    return math.ceil(x * 100 - 1e-9) / 100


def log(message):
    stamp = now_utc().strftime("%H:%M:%S")
    st.session_state.log.insert(0, f"{stamp}  {message}")
    del st.session_state.log[80:]


def set_error(message):
    if message != st.session_state.last_error:
        log(f"⚠️ {message}")
    st.session_state.last_error = message
def reset_daily_if_needed():
    today = now_utc().date().isoformat()
    if st.session_state.trade_day != today:
        st.session_state.trade_day = today
        st.session_state.trade_count = 0
        st.session_state.day_start_equity = None
        st.session_state.done_signal_ids = []


def get_secret(name):
    try:
        value = st.secrets.get(name)
        if value:
            return str(value)
    except Exception:
        pass
    return os.environ.get(name)      # Codespaces secrets live here


# ============================================================
# IG API LAYER
# ============================================================

def ig_login():
    username = get_secret("IG_USERNAME")
    password = get_secret("IG_PASSWORD")
    api_key = get_secret("IG_API_KEY")

    if not (username and password and api_key):
        raise RuntimeError("IG credentials missing (IG_USERNAME / IG_PASSWORD / IG_API_KEY).")

    response = requests.post(
        f"{IG_BASE_URL}/session",
        headers={"X-IG-API-KEY": api_key, "Content-Type": "application/json",
                 "Accept": "application/json", "Version": "2"},
        json={"identifier": username, "password": password, "encryptedPassword": False},
        timeout=15,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"IG login failed: {response.status_code} {response.text[:300]}")

    cst = response.headers.get("CST")
    token = response.headers.get("X-SECURITY-TOKEN")
    if not cst or not token:
        raise RuntimeError("IG login OK but security tokens missing.")

    st.session_state.ig_cst = cst
    st.session_state.ig_security_token = token
    st.session_state.ig_connected = True


def ig_headers(version="2", delete_override=False):
    api_key = get_secret("IG_API_KEY")
    if not (api_key and st.session_state.ig_cst and st.session_state.ig_security_token):
        raise RuntimeError("Not logged in to IG.")
    headers = {
        "X-IG-API-KEY": api_key,
        "CST": st.session_state.ig_cst,
        "X-SECURITY-TOKEN": st.session_state.ig_security_token,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Version": version,
    }
    if delete_override:
        headers["_method"] = "DELETE"
    return headers


def ig_request(method, path, version="2", body=None, delete_override=False, _retry=True):
    response = requests.request(
        method,
        f"{IG_BASE_URL}{path}",
        headers=ig_headers(version, delete_override),
        json=body,
        timeout=15,
    )
    if response.status_code == 401 and _retry:      # token expired -> re-login once
        ig_login()
        return ig_request(method, path, version, body, delete_override, _retry=False)
    if response.status_code >= 400:
        raise RuntimeError(f"IG {method} {path}: {response.status_code} {response.text[:300]}")
    return response.json() if response.text else {}
   def get_ig_market():
    data = ig_request("GET", f"/markets/{IG_EPIC}", "3")
    snapshot = data.get("snapshot", {})
    rules = data.get("dealingRules", {})

    bid = safe_float(snapshot.get("bid"))
    offer = safe_float(snapshot.get("offer"))
    if bid is None or offer is None:
        raise RuntimeError("IG returned no usable bid/offer.")

    min_stop = safe_float((rules.get("minNormalStopOrLimitDistance") or {}).get("value"))
    min_size = safe_float((rules.get("minDealSize") or {}).get("value"))
    if min_stop:
        st.session_state.min_stop = max(min_stop, 0.1)
    if min_size:
        st.session_state.min_deal_size = min_size

    st.session_state.live_bid = bid
    st.session_state.live_offer = offer
    st.session_state.live_mid = (bid + offer) / 2.0
    st.session_state.market_status = snapshot.get("marketStatus", "UNKNOWN")

    return {"bid": bid, "offer": offer, "mid": (bid + offer) / 2.0,
            "market_status": st.session_state.market_status}


def refresh_account(force=False):
    last = st.session_state.account_ts
    if not force and last and (now_utc() - last).total_seconds() < 60:
        return
    data = ig_request("GET", "/accounts", "1")
    accounts = data.get("accounts", [])
    if not accounts:
        return
    acct = next((a for a in accounts if a.get("preferred")), accounts[0])
    bal = acct.get("balance", {})
    balance = safe_float(bal.get("balance"))
    if balance is None:
        return
    equity = balance + (safe_float(bal.get("profitLoss")) or 0.0)
    st.session_state.equity = equity
    st.session_state.account_ts = now_utc()
    if st.session_state.day_start_equity is None:
        st.session_state.day_start_equity = equity


def list_positions():
    data = ig_request("GET", "/positions", "2")
    out = []
    for item in data.get("positions", []):
        pos = item.get("position", {})
        mkt = item.get("market", {})
        if (pos.get("epic") or mkt.get("epic")) != IG_EPIC:
            continue
        out.append({
            "dealId": pos.get("dealId"),
            "direction": pos.get("direction"),
            "size": safe_float(pos.get("size")),
            "level": safe_float(pos.get("level")),
            "stopLevel": safe_float(pos.get("stopLevel")),
            "limitLevel": safe_float(pos.get("limitLevel")),
        })
    return out


def confirm_deal(reference):
    time.sleep(0.5)
    data = ig_request("GET", f"/confirms/{reference}", "1")
    if data.get("dealStatus") != "ACCEPTED":
        raise RuntimeError(f"IG rejected the deal: {data.get('reason', 'unknown reason')}")
    return data


def open_position(direction, size, stop_distance=None, limit_distance=None):
    payload = {
        "epic": IG_EPIC, "expiry": "-", "direction": direction,
        "size": float(size), "orderType": "MARKET",
        "currencyCode": CURRENCY, "forceOpen": True, "guaranteedStop": False,
    }
    if stop_distance:
        payload["stopDistance"] = round(stop_distance, 2)
    if limit_distance:
        payload["limitDistance"] = round(limit_distance, 2)
    res = ig_request("POST", "/positions/otc", "2", payload)
    return confirm_deal(res["dealReference"])

