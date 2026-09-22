import os, pandas as pd, numpy as np, requests, time, base64, io, json, re
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image

st.set_page_config(page_title="SOLID V18 SMC", layout="wide")
st.title("SOLID V18 - SMC + ICT SOLID SIGNAL")
st.caption("SMC: OB + FVG + Liquidity + BOS/CHoCH + Market Structure")

IG = "https://demo-api.ig.com/gateway/deal"
YAHOO = {"XAU/USD":"GC=F","EUR/USD":"EURUSD=X","GBP/USD":"GBPUSD=X","NAS100":"^NDX","US30":"^DJI","BTC/USD":"BTC-USD"}
IG_SEARCH = {"XAU/USD":"Gold","EUR/USD":"EUR/USD","GBP/USD":"GBP/USD","NAS100":"US Tech 100","US30":"US 30","BTC/USD":"Bitcoin"}
TV = {"XAU/USD":"OANDA:XAUUSD","EUR/USD":"FX:EURUSD","GBP/USD":"FX:GBPUSD","NAS100":"OANDA:NAS100USD","US30":"OANDA:US30USD","BTC/USD":"BINANCE:BTCUSD"}

for k in ["con","cst","xsec","mkts","last_mkt","running"]:
    if k not in st.session_state:
        st.session_state[k] = False if k in ["con","running"] else [] if k == "mkts" else ""
if st.session_state["last_mkt"] is None:
    st.session_state["last_mkt"] = ""

def get_creds():
    def g(n):
        try:
            if n in st.secrets:
                return st.secrets[n]
        except:
            pass
        return os.getenv(n)
    return g("IG_USERNAME"), g("IG_PASSWORD"), g("IG_API_KEY"), g("GEMINI_API_KEY"), g("GROQ_API_KEY")

def hdrs(v="2"):
    return {"X-IG-API-KEY": get_creds()[2], "CST": st.session_state["cst"], "X-SECURITY-TOKEN": st.session_state["xsec"], "Accept":"application/json", "Content-Type":"application/json", "Version":v}

def login():
    u,p,k,_,_ = get_creds()
    h = {"X-IG-API-KEY":k, "Content-Type":"application/json", "Accept":"application/json", "Version":"2"}
    b = {"identifier":u, "password":p, "encryptedPassword":False}
    try:
        r = requests.post(f"{IG}/session", headers=h, json=b, timeout=20)
        if r.status_code!= 200:
            return False, r.text[:300], None
        return True, "OK", {"cst":r.headers.get("CST"), "xsec":r.headers.get("X-SECURITY-TOKEN")}
    except Exception as e:
        return False, str(e), None

def search(term):
    try:
        r = requests.get(f"{IG}/markets", headers=hdrs("1"), params={"searchTerm":term}, timeout=20)
        if r.status_code!= 200:
            return False, str(r.status_code), []
        d = r.json()
        return True, "", d.get("markets", [])
    except Exception as e:
        return False, str(e), []

def get_positions():
    try:
        r = requests.get(f"{IG}/positions", headers=hdrs("2"), timeout=15)
        if r.status_code!= 200:
            return []
        return r.json().get("positions", [])
    except:
        return []

def get_accounts():
    try:
        r = requests.get(f"{IG}/accounts", headers=hdrs("1"), timeout=15)
        if r.status_code!= 200:
            return None
        return r.json()
    except:
        return None

def place_solid(epic, direction, size, sl=None, tp=None):
    url = f"{IG}/positions/otc"
    pays = []
    if sl and tp:
        try:
            pays.append({"epic":epic,"expiry":"-","direction":direction,"size":str(size),"orderType":"MARKET","currencyCode":"USD","forceOpen":True,"guaranteedStop":False,"stopLevel":str(round(float(sl),1)),"limitLevel":str(round(float(tp),1))})
        except:
            pass
    pays.append({"epic":epic,"expiry":"-","direction":direction,"size":str(size),"orderType":"MARKET","currencyCode":"USD","forceOpen":True,"guaranteedStop":False})
    last = ""
    for i, pay in enumerate(pays):
        try:
            r = requests.post(url, headers=hdrs("2"), json=pay, timeout=15)
            last = r.text
            if r.status_code == 200:
                return True, f"METHOD {i+1} {direction} {size} OK", r.text
        except Exception as e:
            last = str(e)
    return False, f"IG FAIL {last[:300]}", last

def close_all_for_epic(epic):
    pos = get_positions()
    this = [p for p in pos if p.get("market",{}).get("epic") == epic]
    res = []
    for p in this:
        po = p.get("position",{})
        did = po.get("dealId")
        d = "SELL" if po.get("direction") == "BUY" else "BUY"
        sz = po.get("size")
        url = f"{IG}/positions/otc"
        pay = {"dealId":did,"epic":epic,"expiry":"-","direction":d,"size":str(sz),"orderType":"MARKET"}
        try:
            r = requests.delete(url, headers=hdrs("2"), json=pay, timeout=15)
            res.append(str(r.status_code))
        except Exception as e:
            res.append(str(e))
    return res

@st.cache_data(ttl=20)
def get_yahoo_candles(sym):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
        pr = {"range":"2d","interval":"5m"}
        hh = {"User-Agent":"Mozilla/5.0"}
        r = requests.get(url, params=pr, headers=hh, timeout=15)
        if r.status_code == 200:
            j = r.json()
            result = j.get("chart",{}).get("result",[])
            if result:
                res = result[0]
                ts = res.get("timestamp",[])
                q = res.get("indicators",{}).get("quote",[{}])[0]
                cl = q.get("close",[])
                rows = []
                for i in range(len(ts)):
                    if cl[i] is None:
                        continue
                    rows.append({"time":pd.to_datetime(ts[i], unit="s", utc=True),"open":q.get("open",[])[i],"high":q.get("high",[])[i],"low":q.get("low",[])[i],"close":cl[i]})
                df = pd.DataFrame(rows).dropna()
                if len(df) >= 30:
                    return df
    except:
        pass
    return None

def supertrend(df, p=10, m=3.0):
    if df is None or len(df) < p+5:
        return None
    hl2 = (df["high"] + df["low"]) / 2
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["close"].shift()).abs()
    tr3 = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([tr1,tr2,tr3], axis=1).max(axis=1)
    atr = tr.rolling(p).mean()
    upper = hl2 + m*atr
    lower = hl2 - m*atr
    stl = pd.Series(index=df.index, dtype=float)
    direction = pd.Series(index=df.index, dtype=int)
    for i in range(len(df)):
        if i == 0:
            stl.iloc[i] = lower.iloc[i] if not pd.isna(lower.iloc[i]) else hl2.iloc[i]
            direction.iloc[i] = 1
        else:
            prev = stl.iloc[i-1]
            if pd.isna(prev):
                prev = lower.iloc[i-1]
            if df["close"].iloc[i] <= prev:
                stl.iloc[i] = upper.iloc[i]
                direction.iloc[i] = -1
            else:
                stl.iloc[i] = lower.iloc[i]
                direction.iloc[i] = 1
            if direction.iloc[i]==1 and stl.iloc[i]<stl.iloc[i-1]:
                stl.iloc[i]=stl.iloc[i-1]
            if direction.iloc[i]==-1 and stl.iloc[i]>stl.iloc[i-1]:
                stl.iloc[i]=stl.iloc[i-1]
    df["supertrend"] = stl
    df["st_dir"] = direction
    df["atr"] = atr
    return df

def instant_signal(df):
    if df is None or len(df) < 30:
        return "WAIT", None, None, None, 0, 0, "no df"
    df = supertrend(df, 10, 3.0)
    if df is None:
        return "WAIT", None, None, None, 0, 0, "st fail"
    last = df.iloc[-1]
    if pd.isna(last["atr"]):
        return "WAIT", None, None, df, 0, 0, "atr nan"
    bias = "BUY" if last["st_dir"] == 1 else "SELL"
    sl = last["close"] - last["atr"]*2.5 if bias=="BUY" else last["close"] + last["atr"]*2.5
    tp = last["close"] + last["atr"]*3.5 if bias=="BUY" else last["close"] - last["atr"]*3.5
    return bias, sl, tp, df, last["close"], last["atr"], "ok"

def analyze_dual_ai(image_bytes, market, gem_key, groq_key):
    SMC_ICT_PROMPT = f"""
You are SOLID - Elite SMC + ICT Trader for {market}. You must be accurate.

READ THE CHART STEP BY STEP:

1. MARKET STRUCTURE:
- Mark Higher Highs (HH), Higher Lows (HL) for bullish, Lower Highs (LH), Lower Lows (LL) for bearish
- Is there BOS (Break of Structure) or CHoCH (Change of Character)? Where?
- Trend: bullish, bearish, or ranging?

2. ICT LIQUIDITY:
- Where is Buy Side Liquidity (above highs) and Sell Side Liquidity (below lows)?
- Did price sweep/take liquidity recently? Show it.
- Is market in Premium (above 50% of range - look for sells) or Discount (below 50% - look for buys)?

3. ORDER BLOCKS + FVG:
- Find last valid Bullish Order Block (last bearish candle before bullish impulse) and Bearish Order Block
- Find Fair Value Gaps (FVG) - 3 candle imbalance where middle candle wick not overlapping
- Has OB been mitigated or is it still valid?

4. CONFIRMATION:
- Displacement (strong impulsive move leaving OB/FVG)?
- Market structure shift at OB/FVG?
- Candlestick confirmation: engulfing, pinbar, rejection wick at zone?

STRICT RULES FOR SIGNAL:
- BUY ONLY if: Discount zone + Valid bullish OB/FVG untouched + BOS bullish + Liquidity sweep of Sell Side + Bullish displacement
- SELL ONLY if: Premium zone + Valid bearish OB/FVG untouched + BOS bearish + Liquidity sweep of Buy Side + Bearish displacement
- If you have less than 3 confluences, you MUST return WAIT. Do not force SELL/BUY. WAIT is better than false signal.
- Confidence 90+ only if 4+ confluences
- Entry = 50% of OB or 50% of FVG retest
- SL = 10 pips beyond OB/FVG, beyond sweep
- TP = Next opposing liquidity level, minimum 1:2 RR

RETURN ONLY VALID JSON like this, no other text outside JSON:
{{"trend":"BULLISH","structure":"BOS bullish after CHoCH","signal":"BUY","entry":4330.5,"sl":4320.0,"tp":4350.0,"confidence":88,"reason":"Discount + Bullish OB 4325-4330 + FVG 4328-4332 + Sell side liquidity swept + BOS","ob_zone":"4325-4330","fvg":"4328-4332","liquidity":"Sell side taken below 4320","premium_discount":"Discount"}}
"""

    gem_err = "not tried"
    groq_err = "not tried"
    if gem_key:
        try:
            from google import genai
            client = genai.Client(api_key=gem_key)
            img = Image.open(io.BytesIO(image_bytes))
            for mdl in ["gemini-3.6-flash","gemini-2.5-flash","gemini-3-flash-preview","gemini-flash-latest"]:
                try:
                    resp = client.models.generate_content(model=mdl, contents=[SMC_ICT_PROMPT, img])
                    if resp and resp.text:
                        return True, resp.text, f"GEMINI {mdl} SMC"
                except Exception as e:
                    gem_err = str(e)[:600]
                    continue
        except Exception as e:
            gem_err = str(e)[:600]
    else:
        gem_err = "No Gemini key"

    if groq_key:
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            b64 = base64.b64encode(image_bytes).decode("utf-8")
            for gmdl in ["qwen/qwen3.6-27b","meta-llama/llama-4-scout-17b-16e-instruct","meta-llama/llama-4-maverick-17b-128e-instruct"]:
                try:
                    comp = client.chat.completions.create(model=gmdl, messages=[{"role":"user","content":[{"type":"text","text":SMC_ICT_PROMPT},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}}]}], max_tokens=800)
                    return True, comp.choices[0].message.content, f"GROQ {gmdl} SMC"
                except Exception as e:
                    groq_err = str(e)[:600]
                    continue
            return False, f"Gemini:{gem_err} | Groq last:{groq_err}", "NONE"
        except Exception as e:
            return False, f"Gemini:{gem_err} | Groq:{e}", "NONE"
    return False, f"Add FREE keys. Gemini:{gem_err}", "NONE"

with st.sidebar:
    st.header("SOLID V18 SMC")
    mkt = st.selectbox("Market", list(YAHOO.keys()), index=3)
    if mkt!= st.session_state["last_mkt"]:
        st.session_state["mkts"] = []
        st.session_state["last_mkt"] = mkt
    size = st.number_input("Lot 0.01-0.15", 0.01, 0.15, 0.05, 0.01)
    st.divider()
    st.subheader("FREE AI Keys")
    gem_input = st.text_input("Gemini FREE key", type="password")
    groq_input = st.text_input("Groq FREE key", type="password")
    st.divider()
    if st.session_state["con"]:
        st.success("IG Connected")
    else:
        if st.button("Connect IG", use_container_width=True):
            ok, msg, data = login()
            if ok:
                st.session_state["con"] = True
                st.session_state["cst"] = data["cst"]
                st.session_state["xsec"] = data["xsec"]
                st.rerun()
            else:
                st.error(msg)

if not st.session_state["con"]:
    st.warning("Connect IG first")
    st.stop()

term = IG_SEARCH[mkt]
if st.button(f"Find {mkt} EPIC", use_container_width=True):
    ok, err, res = search(term)
    if ok:
        found = []
        for x in res:
            epic = x.get("epic","") or x.get("instrument",{}).get("epic","")
            name = x.get("instrumentName","") or x.get("instrument",{}).get("name","")
            if epic:
                found.append({"epic":epic, "name":name})
        st.session_state["mkts"] = found
        st.success(f"Found {len(found)}")
    else:
        st.error(err)

if not st.session_state["mkts"]:
    st.info("Tap Find EPIC")
    st.stop()

epics = [x["epic"] for x in st.session_state["mkts"]]
labs = [f"{x['epic']} | {x['name']}" for x in st.session_state["mkts"]]
idx = st.selectbox("Pick EPIC", range(len(epics)), format_func=lambda i: labs[i])
epic = epics[idx]

st.divider()
st.subheader("LIVE PnL")
acc = get_accounts()
pos = get_positions()
if acc:
    try:
        bal = acc.get("accounts",[])[0].get("balance",{}).get("balance",0)
        st.metric("Balance", f"${bal}")
    except:
        pass
if pos:
    tot = 0
    for p in pos:
        po = p.get("position",{})
        pnl = po.get("profit",0) or 0
        try:
            tot += float(pnl)
        except:
            pass
        st.write(f"{p.get('market',{}).get('instrumentName','')} {po.get('direction')} {po.get('size')} PnL ${pnl}")
    st.metric("Total PnL", f"${tot:.2f}")
else:
    st.write("No positions")

st.divider()
st.subheader("SMC + ICT SCANNER")
up = st.file_uploader("Upload chart", type=["png","jpg","jpeg"])
if up:
    st.image(up, caption="Chart", use_container_width=True)
    if st.button("SCAN SMC SOLID", type="primary", use_container_width=True):
        _,_,_,sec_gem,sec_groq = get_creds()
        gk = sec_gem or gem_input
        gqk = sec_groq or groq_input
        if not gk and not gqk:
            st.error("Add Gemini FREE key from aistudio.google.com/apikey")
        else:
            with st.spinner("SMC + ICT analyzing..."):
                ok, txt, model_used = analyze_dual_ai(up.getvalue(), mkt, gk, gqk)
                if ok:
                    st.success(f"Done via {model_used}")
                    st.code(txt)
                    try:
                        m = re.search(r'\{.*\}', txt, re.DOTALL)
                        if m:
                            j = json.loads(m.group())
                        else:
                            j = {}
                        sig = j.get("signal","WAIT")
                        ent = j.get("entry")
                        sl = j.get("sl")
                        tp = j.get("tp")
                        conf = j.get("confidence",0)
                        reason = j.get("reason","")
                        ob = j.get("ob_zone","")
                        fvg = j.get("fvg","")
                        liq = j.get("liquidity","")
                        pd_status = j.get("premium_discount","")
                        st.markdown(f"**{model_used}: {sig} | Conf {conf}%**")
                        st.write(f"Entry {ent} SL {sl} TP {tp}")
                        st.write(f"Trend: {j.get('trend')} | Structure: {j.get('structure')}")
                        st.write(f"OB: {ob} | FVG: {fvg} | Liq: {liq} | {pd_status}")
                        st.info(f"Reason: {reason}")
                        if sig in ["BUY","SELL"]:
                            if conf < 75:
                                st.warning("Low confidence - wait for better")
                            if st.button(f"TRADE SOLID {sig}", use_container_width=True):
                                ok2, msg2, _ = place_solid(epic, sig, size, sl, tp)
                                if ok2:
                                    st.success(msg2)
                                    st.balloons()
                                else:
                                    st.error(msg2)
                        else:
                            st.warning("SOLID says WAIT - no solid confluence")
                    except Exception as e:
                        st.write(f"Raw: {txt[:800]}")
                        st.write(f"Parse err: {e}")
                else:
                    st.error(txt)

st.divider()
try:
    sym = TV[mkt]
    components.html(f"""<div id="tv" style="height:350px;"></div><script src="https://s3.tradingview.com/tv.js"></script><script>new TradingView.widget({{"autosize":true,"symbol":"{sym}","interval":"5","timezone":"Africa/Johannesburg","theme":"dark","style":"1","container_id":"tv"}});</script>""", height=370)
except:
    pass

st.subheader(f"Auto {mkt} {epic}")
b1,b2,b3 = st.columns(3)
with b1:
    trade_btn = st.button("TRADE NOW", use_container_width=True, type="primary")
with b2:
    close_btn = st.button("CLOSE ALL", use_container_width=True)
with b3:
    auto_btn = st.button("START AUTO", use_container_width=True)

if close_btn:
    r = close_all_for_epic(epic)
    st.success(f"Closed {r}")
    time.sleep(1)
    st.rerun()

def run_trade():
    df = get_yahoo_candles(YAHOO[mkt])
    if df is None:
        st.error("Yahoo no candles - Try XAU/USD")
        return
    bias, sl, tp, full, price, atr, msg = instant_signal(df)
    if full is None:
        st.error(msg)
        return
    st.metric("Bias", bias)
    st.write(f"Price {price:.2f} SL {sl:.2f} TP {tp:.2f}")
    pos = get_positions()
    this = [p for p in pos if p.get("market",{}).get("epic") == epic]
    if this:
        st.warning("Already has position")
    else:
        ok, msg2, _ = place_solid(epic, bias, size, sl, tp)
        if ok:
            st.success(msg2)
            st.balloons()
        else:
            st.error(msg2)
    if full is not None:
        st.line_chart(full.set_index("time")[["close","supertrend"]].tail(100))

if trade_btn:
    run_trade()
if auto_btn:
    st.session_state["running"] = True
    run_trade()

if st.session_state["running"]:
    st.info("AUTO ON every 60s")
    ph = st.empty()
    while st.session_state["running"]:
        time.sleep(60)
        df = get_yahoo_candles(YAHOO[mkt])
        if df is None:
            continue
        bias, sl, tp, full, price, atr, msg = instant_signal(df)
        pos = get_positions()
        this = [p for p in pos if p.get("market",{}).get("epic") == epic]
        if this:
            with ph.container():
                st.write(f"{time.strftime('%H:%M:%S')} Has pos")
            continue
        ok, msg2, _ = place_solid(epic, bias, size, sl, tp)
        with ph.container():
            st.write(f"{time.strftime('%H:%M:%S')} AUTO {bias} {msg2}")
        st.rerun()
