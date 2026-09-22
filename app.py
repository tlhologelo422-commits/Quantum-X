import os
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(
    page_title="Quantum X PRO",
    page_icon="📈",
    layout="wide"
)

IG_URL = "https://demo-api.ig.com/gateway/deal"

MARKETS = {
    "XAU/USD": {
        "tv": "OANDA:XAUUSD",
        "search": "Gold"
    },
    "EUR/USD": {
        "tv": "FX:EURUSD",
        "search": "EUR/USD"
    },
    "GBP/USD": {
        "tv": "FX:GBPUSD",
        "search": "GBP/USD"
    },
    "USD/JPY": {
        "tv": "FX:USDJPY",
        "search": "USD/JPY"
    },
    "US30": {
        "tv": "OANDA:US30USD",
        "search": "US 30"
    },
}

TF = {
    "5M": "5",
    "15M": "15",
    "30M": "30",
    "1H": "60",
    "4H": "240",
    "1D": "D",
}

for k, v in {
    "connected": False,
    "error": "",
    "cst": None,
    "xsec": None,
    "markets": [],
    "epic": None,
    "prices": None,
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

def get_creds():
    def get(n):
        try:
            if n in st.secrets:
                return st.secrets[n]
        except:
            pass
        return os.getenv(n)
    return get("IG_USERNAME"), get("IG_PASSWORD"), get("IG_API_KEY")

def headers(v="2"):
    _, _, key = get_creds()
    return {
        "X-IG-API-KEY": key,
        "CST": st.session_state["cst"],
        "X-SECURITY-TOKEN": st.session_state["xsec"],
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Version": v,
    }

def ig_login():
    u, p, k = get_creds()
    if not (u and p and k):
        return False, "Missing secrets", None
    url = f"{IG_URL}/session"
    h = {
        "X-IG-API-KEY": k,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Version": "2",
    }
    body = {
        "identifier": u,
        "password": p,
        "encryptedPassword": False,
    }
    try:
        r = requests.post(url, headers=h, json=body, timeout=20)
        if r.status_code!= 200:
            try:
                e = r.json().get("errorCode", "Auth failed")
            except:
                e = r.text[:200]
            return False, e, None
        cst = r.headers.get("CST")
        xsec = r.headers.get("X-SECURITY-TOKEN")
        if not cst or not xsec:
            return False, "No tokens", None
        d = r.json()
        out = {}
        out["cst"] = cst
        out["xsec"] = xsec
        out["data"] = d
        return True, "OK", out
    except Exception as ex:
        return False, str(ex), None

def ig_search(term):
    try:
        r = requests.get(
            f"{IG_URL}/markets",
            headers=headers("1"),
            params={"searchTerm": term},
            timeout=20
        )
        if r.status_code == 401:
            return False, "Expired", []
        if r.status_code!= 200:
            return False, f"Search {r.status_code}", []
        return True, "", r.json().get("markets", [])
    except Exception as ex:
        return False, str(ex), []

def extract(m):
    epic = m.get("epic", "")
    if not epic:
        epic = m.get("instrument", {}).get("epic", "")
    name = m.get("instrumentName", "")
    if not name:
        name = m.get("instrument", {}).get("name", "")
    if not name:
        name = m.get("name", "Gold")
    stat = "UNKNOWN"
    snap = m.get("snapshot", {})
    if isinstance(snap, dict):
        stat = snap.get("marketStatus", "UNKNOWN")
    return {
        "epic": epic,
        "name": name,
        "status": stat
    }

def ig_prices(epic):
    url = f"{IG_URL}/prices/{epic}/MINUTE_15/100"
    try:
        r = requests.get(url, headers=headers("2"), timeout=30)
        if r.status_code == 401:
            return False, "Expired", None
        if r.status_code!= 200:
            return False, f"Price {r.status_code}", None
        prices = r.json().get("prices", [])
        if not prices:
            return False, "No prices", None
        rows = []
        for p in prices:
            def mid(v):
                if not isinstance(v, dict):
                    return None
                b = v.get("bid")
                a = v.get("ask")
                if b is not None and a is not None:
                    return (float(b) + float(a)) / 2
                if b is not None:
                    return float(b)
                if a is not None:
                    return float(a)
                return None
            rows.append({
                "time": p.get("snapshotTimeUTC"),
                "open": mid(p.get("openPrice", {})),
                "high": mid(p.get("highPrice", {})),
                "low": mid(p.get("lowPrice", {})),
                "close": mid(p.get("closePrice", {})),
                "vol": p.get("lastTradedVolume", 0)
            })
        df = pd.DataFrame(rows)
        df = df.dropna(subset=["time", "open"])
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df.sort_values("time").reset_index(drop=True)
        return True, "", df
    except Exception as ex:
        return False, str(ex), None

st.title("📈 QUANTUM X PRO")
st.caption("AI-Powered Analysis")

if st.session_state["connected"]:
    st.success("● SYSTEM ONLINE • IG DEMO CONNECTED")
else:
    st.success("● SYSTEM ONLINE")

with st.sidebar:
    st.header("Market Settings")
    mkt = st.selectbox("Market", list(MARKETS.keys()))
    tf = st.selectbox("Timeframe", list(TF.keys()), index=1)
    st.divider()
    st.subheader("IG Data Layer")
    if st.session_state["connected"]:
        st.success("IG Demo Connected")
        if st.button("Reconnect IG", use_container_width=True):
            ok, msg, data = ig_login()
            if ok:
                st.session_state["connected"] = True
                st.session_state["cst"] = data["cst"]
                st.session_state["xsec"] = data["xsec"]
                st.session_state["markets"] = []
                st.success("Refreshed")
            else:
                st.error(msg)
    else:
        st.warning("Not Connected")
        if st.button("Connect to IG Demo", use_container_width=True):
            with st.spinner("Connecting..."):
                ok, msg, data = ig_login()
            if ok:
                st.session_state["connected"] = True
                st.session_state["error"] = ""
                st.session_state["cst"] = data["cst"]
                st.session_state["xsec"] = data["xsec"]
                st.rerun()
            else:
                st.session_state["error"] = msg
                st.error(msg)

if st.session_state["error"]:
    st.error(st.session_state["error"])

c1, c2, c3 = st.columns(3)
c1.metric("Market", mkt)
c2.metric("TF", tf)
c3.metric("Provider", "IG Demo")

st.subheader("📊 Market Chart")
tv = MARKETS[mkt]["tv"]
itv = TF[tf]
html = f"""
<div id="tv" style="height:600px;width:100%;"></div>
<script src="https://s3.tradingview.com/tv.js"></script>
<script>
new TradingView.widget({{
"autosize": true,
"symbol": "{tv}",
"interval": "{itv}",
"timezone": "Africa/Johannesburg",
"theme": "dark",
"style": "1",
"locale": "en",
"container_id": "tv"
}});
</script>
"""
components.html(html, height=620)

st.subheader("📡 IG Market Data")

if not st.session_state["connected"]:
    st.info("Connect to IG Demo first.")
else:
    term = MARKETS[mkt]["search"]
    st.write(f"Search: {term}")
    if st.button("Find IG Instrument", use_container_width=True):
        with st.spinner(f"Searching {term}..."):
            ok, err, res = ig_search(term)
        if ok:
            found = []
            for x in res:
                if isinstance(x, dict):
                    info = extract(x)
                    if info["epic"]:
                        found.append(info)
            if found:
                st.session_state["markets"] = found
                st.success(f"Found {len(found)}")
                st.dataframe(pd.DataFrame(found), use_container_width=True)
            else:
                st.warning("No EPIC")
        else:
            st.error(err)

    if st.session_state["markets"]:
        epics = [x["epic"] for x in st.session_state["markets"]]
        labs = [f"{x['epic']} | {x['name']}" for x in st.session_state["markets"]]
        idx = st.selectbox("Select EPIC", range(len(epics)), format_func=lambda i: labs[i])
        st.session_state["epic"] = epics[idx]
        st.info(f"Selected {epics[idx]}")
        if st.button("Load 15M Candles", use_container_width=True):
            with st.spinner("Loading..."):
                ok, err, df = ig_prices(epics[idx])
            if ok:
                st.session_state["prices"] = df
                st.success(f"Loaded {len(df)} candles")
                st.dataframe(df, use_container_width=True)
                st.line_chart(df.set_index("time")[["close"]])
            else:
                st.error(err)

    if st.session_state["prices"] is not None:
        st.success("✅ PIPELINE ACTIVE")
