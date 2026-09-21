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
    "ig_selected_market_name": None, "ig_prices": None,
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

def credentials_available():
    u,p,k = get_ig_credentials()
    return bool(u and p and k)

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
    username, password, api_key = get_ig_credentials()
    missing=[]
    if not username: missing.append("IG_USERNAME")
    if not password: missing.append("IG_PASSWORD")
    if not api_key: missing.append("IG_API_KEY")
    if missing:
        return False, "Missing secret(s): " + ", ".join(missing), None
    url = f"{IG_BASE_URL}/session"
    headers = {"X-IG-API-KEY": api_key, "Content-Type": "application/json", "Accept": "application/json", "Version": "2"}
    payload = {"identifier": username, "password": password, "encryptedPassword": False}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        if r.status_code!= 200:
            try: err = r.json().get("errorCode","IG authentication failed.")
            except: err = "IG authentication failed."
            return False, err, None
        cst = r.headers.get("CST")
        sec = r.headers.get("X-SECURITY-TOKEN")
        if not cst or not sec:
            return False, "Login succeeded but no tokens returned.", None
        return True, "IG Demo connection successful.", {"cst": cst, "security_token": sec, "account_data": r.json()}
    except Exception as e:
        return False, f"Network error: {e}", None

def search_ig_markets(search_term):
    url = f"{IG_BASE_URL}/markets"
    params = {"searchTerm": search_term}
    try:
        r = requests.get(url, headers=build_ig_headers("1"), params=params, timeout=20)
        if r.status_code==401: return False, "IG session expired. Please reconnect.", []
        if r.status_code!=200:
            try: err = r.json().get("errorCode","IG market search failed.")
            except: err = "IG market search failed."
            return False, err, []
        markets = r.json().get("markets", [])
        return True, "", markets if isinstance(markets, list) else []
    except Exception as e:
        return False, f"Market search error: {e}", []

def extract_market_info(market: dict[str, Any]):
    instrument = market.get("instrument", {}) if isinstance(market.get("instrument", {}), dict) else {}
    snapshot = market.get("snapshot", {}) if isinstance(market.get("snapshot", {}), dict) else {}
    return {
        "epic": instrument.get("epic") or market.get("epic"),
        "name": instrument.get("name") or market.get("name") or "Unknown",
        "market_status": snapshot.get("marketStatus") or market.get("marketStatus") or "UNKNOWN",
        "bid": snapshot.get("bid") or market.get("bid"),
        "offer": snapshot.get("offer") or snapshot.get("ask") or market.get("offer"),
    }

def get_ig_prices(epic, resolution="MINUTE_15", num_points=100):
    url = f"{IG_BASE_URL}/prices/{epic}/{resolution}/{num_points}"
    try:
        r = requests.get(url, headers=build_ig_headers("2"), timeout=30)
        if r.status_code==401: return False, "IG session expired.", None
        if r.status_code!=200:
            try: err = r.json().get("errorCode","Price request failed.")
            except: err = "Price request failed."
            return False, err, None
        prices = r.json().get("prices", [])
        if not prices: return False, "IG returned no prices.", None
        rows=[]
        for p in prices:
            def mid(v):
                if not isinstance(v, dict): return None
                b, a = v.get("bid"), v.get("ask")
                if b is not None and a is not None: return (float(b)+float(a))/2
                if b is not None: return float(b)
                if a is not None: return float(a)
                return None
            rows.append({
                "timestamp": p.get("snapshotTimeUTC", p.get("snapshotTime")),
                "open": mid(p.get("openPrice", {})), "high": mid(p.get("highPrice", {})),
                "low": mid(p.get("lowPrice", {})), "close": mid(p.get("closePrice", {})),
                "volume": p.get("lastTradedVolume", 0)
            })
        df = pd.DataFrame(rows).dropna(subset=["timestamp","open","high","low","close"])
        if df.empty: return False, "Could not convert to OHLC", None
        df["timestamp"]=pd.to_datetime(df["timestamp"], utc=True)
        df=df.sort_values("timestamp").reset_index(drop=True)
        return True, "", df
    except Exception as e:
        return False, f"Price error: {e}", None

# HEADER
st.title("📈 QUANTUM X PRO")
st.caption("AI-Powered Market Analysis & Smart Money Concepts")
if st.session_state["ig_connected"]: st.success("● SYSTEM ONLINE • IG DEMO CONNECTED")
else: st.success("● SYSTEM ONLINE")

# SIDEBAR
with st.sidebar:
    st.header("⚙️ Market Settings")
    selected_market = st.selectbox("Market", list(MARKETS.keys()))
    selected_timeframe = st.selectbox("Setup Timeframe", list(TIMEFRAMES.keys()), index=1)
    analysis_mode = st.radio("Analysis Mode", ["Manual Analysis", "Automatic Scanner"])
    st.divider()
    st.subheader("🔌 IG Data Layer")
    if st.session_state["ig_connected"]:
        st.success("IG Demo Connected")
        if st.button("🔄 Reconnect IG", use_container_width=True):
            ok,msg,data = connect_to_ig()
            if ok:
                st.session_state["ig_connected"]=True
                st.session_state["ig_cst"]=data["cst"]
                st.session_state["ig_security_token"]=data["security_token"]
                st.session_state["ig_account"]=data["account_data"]
                st.session_state["ig_markets"]=[]; st.session_state["ig_prices"]=None
                st.success("IG session refreshed.")
            else: st.error(msg)
    else:
        st.warning("IG Demo Not Connected")
        if st.button("🔌 Connect to IG Demo", use_container_width=True):
            with st.spinner("Connecting securely to IG Demo..."):
                ok,msg,data = connect_to_ig()
            if ok:
                st.session_state["ig_connected"]=True; st.session_state["ig_error"]=""
                st.session_state["ig_cst"]=data["cst"]; st.session_state["ig_security_token"]=data["security_token"]
                st.session_state["ig_account"]=data["account_data"]
                st.rerun()
            else:
                st.session_state["ig_connected"]=False; st.session_state["ig_error"]=msg
                st.error(msg)

if st.session_state["ig_error"]: st.error(f"IG Error: {st.session_state['ig_error']}")

col1,col2,col3,col4 = st.columns(4)
col1.metric("Market", selected_market); col2.metric("Timeframe", selected_timeframe)
col3.metric("Data Provider", "IG Demo"); col4.metric("AI Engine", "GPT-5.6 Sol")

# TRADINGVIEW
st.subheader("📊 Market Chart")
tv_symbol = MARKETS[selected_market]["tradingview"]
tv_interval = TIMEFRAMES[selected_timeframe]
chart_html = f"""<div id="tradingview_chart" style="height:600px;width:100%;"></div>
<script src="https://s3.tradingview.com/tv.js"></script>
<script>new TradingView.widget({{"autosize": true,"symbol": "{tv_symbol}","interval": "{tv_interval}",
"timezone": "Africa/Johannesburg","theme": "dark","style": "1","locale": "en","container_id": "tradingview_chart"}});</script>"""
components.html(chart_html, height=620, scrolling=False)

# REAL IG DATA
st.subheader("📡 IG Market Data")
if not st.session_state["ig_connected"]:
    st.info("Connect to IG Demo first.")
else:
    search_term = MARKETS[selected_market]["ig_search"]
    st.write(f"IG instrument search: **{search_term}**")
    if st.button("🔎 Find IG Instrument", use_container_width=True):
        with st.spinner(f"Searching IG for {search_term}..."):
            success, error, markets = search_ig_markets(search_term)
        if success:
            extracted=[]
            for m in markets:
                if isinstance(m, dict):
                    info=extract_market_info(m)
                    if info["epic"]: extracted.append(info)
            if extracted:
                st.session_state["ig_markets"]=extracted
                st.success(f"Found {len(extracted)} instruments")
                st.dataframe(pd.DataFrame(extracted), use_container_width=True)
            else: st.warning("No instruments with EPIC found.")
        else: st.error(error)

    if st.session_state["ig_markets"]:
        epics = [m["epic"] for m in st.session_state["ig_markets"]]
        epic_labels = [f"{m['epic']} - {m['name']}" for m in st.session_state["ig_markets"]]
        sel_idx = st.selectbox("Select EPIC:", range(len(epics)), format_func=lambda i: epic_labels[i])
        st.session_state["ig_selected_epic"]=epics[sel_idx]

        if st.button("📦 Load Real IG 15M Candles", use_container_width=True):
            with st.spinner(f"Pulling 15M candles for {st.session_state['ig_selected_epic']}..."):
                ok, err, df = get_ig_prices(st.session_state["ig_selected_epic"], "MINUTE_15", 100)
            if ok:
                st.session_state["ig_prices"]=df
                st.success(f"Loaded {len(df)} real candles")
                st.dataframe(df, use_container_width=True)
                st.line_chart(df.set_index("timestamp")[["close"]])
                st.caption("timestamp | open | high | low | close | volume - Real IG Demo data")
            else:
                st.error(err)

    if st.session_state["ig_prices"] is not None:
        st.divider()
        st.subheader("✅ Real IG → Quantum X PRO Pipeline Active")
        st.write("Next: indicators, SMC detection, AI analysis")
