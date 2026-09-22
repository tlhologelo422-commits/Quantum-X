import os
from typing import Any
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Quantum X PRO", page_icon="📈", layout="wide")

IG_BASE_URL = "https://demo-api.ig.com/gateway/deal"

MARKETS = {
    "XAU/USD": {"tradingview": "OANDA:XAUUSD", "ig_search": "Gold"},
    "EUR/USD": {"tradingview": "FX:EURUSD", "ig_search": "EUR/USD"},
    "GBP/USD": {"tradingview": "FX:GBPUSD", "ig_search": "GBP/USD"},
    "USD/JPY": {"tradingview": "FX:USDJPY", "ig_search": "USD/JPY"},
    "USD/CHF": {"tradingview": "FX:USDCHF", "ig_search": "USD/CHF"},
    "AUD/USD": {"tradingview": "FX:AUDUSD", "ig_search": "AUD/USD"},
    "USD/CAD": {"tradingview": "FX:USDCAD", "ig_search": "USD/CAD"},
    "NZD/USD": {"tradingview": "FX:NZDUSD", "ig_search": "NZD/USD"},
    "XAG/USD": {"tradingview": "OANDA:XAGUSD", "ig_search": "Silver"},
    "US30": {"tradingview": "OANDA:US30USD", "ig_search": "US 30"},
    "SPX500": {"tradingview": "OANDA:SPX500USD", "ig_search": "S&P 500"},
    "UK100": {"tradingview": "OANDA:UK100GBP", "ig_search": "FTSE 100"},
    "GER30": {"tradingview": "OANDA:DE30EUR", "ig_search": "DAX"},
}
TIMEFRAMES = {"5M": "5", "15M": "15", "30M": "30", "1H": "60", "4H": "240", "1D": "D"}

DEFAULT_STATE = {
    "ig_connected": False, "ig_error": "", "ig_cst": None,
    "ig_security_token": None, "ig_account": None,
    "ig_markets": [], "ig_selected_epic": None,
    "ig_prices": None,
}
for k,v in DEFAULT_STATE.items():
    if k not in st.session_state: st.session_state[k]=v

def get_ig_credentials():
    def get(name):
        try:
            if name in st.secrets: return st.secrets[name]
        except: pass
        return os.getenv(name)
    return get("IG_USERNAME"), get("IG_PASSWORD"), get("IG_API_KEY")

def build_ig_headers(version="2"):
    _,_,api_key = get_ig_credentials()
    return {
        "X-IG-API-KEY": api_key,
        "CST": st.session_state["ig_cst"],
        "X-SECURITY-TOKEN": st.session_state["ig_security_token"],
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Version": version,
    }

def connect_to_ig():
    u,p,k = get_ig_credentials()
    if not (u and p and k):
        return False, "Missing IG secrets - check Streamlit Secrets", None
    url = f"{IG_BASE_URL}/session"
    headers = {"X-IG-API-KEY": k, "Content-Type": "application/json", "Accept": "application/json", "Version": "2"}
    payload = {"identifier": u, "password": p, "encryptedPassword": False}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        if r.status_code!= 200:
            try: err = r.json().get("errorCode","Auth failed")
            except: err = r.text[:200]
            return False, err, None
        cst = r.headers.get("CST"); sec = r.headers.get("X-SECURITY-TOKEN")
        if not cst or not sec: return False, "No session tokens", None
        return True, "Connected", {"cst": cst, "security
