import os
import pandas as pd
import numpy as np
import requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Quantum X V4.1", layout="wide")

# ALWAYS VISIBLE HEADER
st.title("📈 QUANTUM X PRO V4.1 - ALL MARKETS")
st.caption("If you see this, app is running - check logs if blank after this")

IG = "https://demo-api.ig.com/gateway/deal"

M = {
 "XAU/USD": ["OANDA:XAUUSD","Gold"],
 "XAG/USD": ["OANDA:XAGUSD","Silver"],
 "EUR/USD": ["FX:EURUSD","EUR/USD"],
 "GBP/USD": ["FX:GBPUSD","GBP/USD"],
 "USD/JPY": ["FX:USDJPY","USD/JPY"],
 "AUD/USD": ["FX:AUDUSD","AUD/USD"],
 "US30": ["OANDA:US30USD","US 30"],
 "NAS100": ["OANDA:NAS100USD","US Tech 100"],
 "BTC/USD": ["BINANCE:BTCUSD","Bitcoin"],
 "ETH/USD": ["BINANCE:ETHUSD","Ethereum"],
}

# Init session
if "con" not in st.session_state:
 st.session_state["con"]=False
if "err" not in st.session_state:
 st.session_state["err"]=""
if "cst" not in st.session_state:
 st.session_state["cst"]=None
if "xsec" not in st.session_state:
 st.session_state["xsec"]=None
if "mkts" not in st.session_state:
 st.session_state["mkts"]=[]
if "epic" not in st.session_state:
 st.session_state["epic"]=None
if "df15" not in st.session_state:
 st.session_state["df15"]=None

def get_creds():
 def g(n):
  try:
   if n in st.secrets:
    return st.secrets[n]
  except:
   pass
  return os.getenv(n)
 return g("IG_USERNAME"),g("IG_PASSWORD"),g("IG_API_KEY")

def hdrs(v="2"):
 _,_,k=get_creds()
 return {"X-IG-API-KEY":k,"CST":st.session_state["cst"],"X-SECURITY-TOKEN":st.session_state["xsec"],"Accept":"application/json","Content-Type":"application/json","Version":v}

def login():
 u,p,k=get_creds()
 if not (u and p and k):
  return False,"Missing secrets - add IG_USERNAME IG_PASSWORD IG_API_KEY in Secrets",None
 h={"X-IG-API-KEY":k,"Content-Type":"application/json","Accept":"application/json","Version":"2"}
 b={"identifier":u,"password":p,"encryptedPassword":False}
 try:
  r=requests.post(f"{IG}/session",headers=h,json=b,timeout=20)
  if r.status_code!=200:
   return False,r.text[:300],None
  c=r.headers.get("CST")
  x=r.headers.get("X-SECURITY-TOKEN")
  if not c or not x:
   return False,"No CST/X-SEC",None
  return True,"OK",{"cst":c,"xsec":x}
 except Exception as e:
  return False,str(e),None

def search(term):
 try:
  r=requests.get(f"{IG}/markets",headers=hdrs("1"),params={"searchTerm":term},timeout=20)
  if r.status_code!=200:
   return False,f"Search {r.status_code}",[]
  return True,"",r.json().get("markets",[])
 except Exception as e:
  return False,str(e),[]

def extract(m):
 epic=m.get("epic","") or m.get("instrument",{}).get("epic","")
 name=m.get("instrumentName","") or m.get("instrument",{}).get("name","Gold")
 return {"epic":epic,"name":name}

def get_prices(epic,res,num=100):
 url=f"{IG}/prices/{epic}/{res}/{num}"
 try:
  r=requests.get(url,headers=hdrs("2"),timeout=30)
  if r.status_code!=200:
   return False,f"{res} {r.status_code}",None
  plist=r.json().get("prices",[])
  rows=[]
  for p in plist:
   t=p.get("snapshotTimeUTC") or p.get("snapshotTime") or ""
   def mid(v):
    if not isinstance(v,dict):
     return None
    b=v.get("bid")
    a=v.get("ask")
    if b is not None and a is not None:
     return (float(b)+float(a))/2
    if b is not None:
     return float(b)
    if a is not None:
     return float(a)
    return None
   rows.append({"time":t,"open":mid(p.get("openPrice")),"high":mid(p.get("highPrice")),"low":mid(p.get("lowPrice")),"close":mid(p.get("closePrice")),"vol":p.get("lastTradedVolume") or 0})
  df=pd.DataFrame(rows).dropna(subset=["open","high","low","close"])
  df["time"]=pd.to_datetime(df["time"],utc=True,errors="coerce")
  df=df.sort_values("time").reset_index(drop=True)
  return True,"",df
 except Exception as e:
  return False,str(e),None

def add_ind(df):
 try:
  df=df.copy()
  df["sma20"]=df["close"].rolling(20).mean()
  df["ema50"]=df["close"].ewm(span=50,adjust=False).mean()
  df["ema200"]=df["close"].ewm(span=200,adjust=False).mean()
  delta=df["close"].diff()
  gain=delta.where(delta>0,0).rolling(14).mean()
  loss=(-delta.where(delta<0,0)).rolling(14).mean()
  rs=gain/loss
  df["rsi"]=100 - (100/(1+rs))
  hl=df["high"]-df["low"]
  hc=(df["high"]-df["close"].shift()).abs()
  lc=(df["low"]-df["close"].shift()).abs()
  tr=pd.concat([hl,hc,lc],axis=1).max(axis=1)
  df["atr"]=tr.rolling(14).mean()
  return df
 except Exception as e:
  st.error(f"Indicator error {e}")
  return df

def analyze(df15):
 if df15 is None or len(df15)<20:
  return {"bias":"NEUTRAL","signal":"WAIT","conf":0,"notes":["No data"]}
 try:
  df=add_ind(df15)
  last=df.iloc[-1]
  notes=[]
  score=0
  bias="NEUTRAL"
  if last["close"]>last["ema50"]:
   bias="BULLISH"
   score+=20
   notes.append("Price > EMA50 Bull")
  else:
   bias="BEARISH"
   score+=20
   notes.append("Price < EMA50 Bear")
  rsi=last["rsi"]
  if 40<rsi<70 and bias=="BULLISH":
   score+=20
   notes.append(f"RSI {rsi:.1f} OK")
  elif 30<rsi<60 and bias=="BEARISH":
   score+=20
   notes.append(f"RSI {rsi:.1f} OK")
  else:
   notes.append(f"RSI {rsi:.1f}")
  last_high=df["high"].rolling(10).max().iloc[-15:-1].max()
  last_low=df["low"].rolling(10).min().iloc[-15:-1].min()
  bos=None
  if last["close"]>last_high:
   bos="BULL BOS"
   score+=20
   notes.append(f"BOS Bull {last_high:.2f}")
  elif last["close"]<last_low:
   bos="BEAR BOS"
   score+=20
   notes.append(f"BOS Bear {last_low:.2f}")
  atr=last["atr"]
  if pd.isna(atr):
   atr=last["close"]*0.002
  signal="WAIT"
  entry=sl=tp=None
  if score>=60:
   if bias=="BULLISH" and bos=="BULL BOS":
    signal="BUY"
    entry=last["close"]
    sl=entry-atr*1.5
    tp=entry+atr*3
   elif bias=="BEARISH" and bos=="BEAR BOS":
    signal="SELL"
    entry=last["close"]
    sl=entry+atr*1.5
    tp=entry-atr*3
  return {"bias":bias,"bos":bos,"signal":signal,"conf":score,"entry":entry,"sl":sl,"tp":tp,"rsi":rsi,"atr":atr,"notes":notes,"df":df}
 except Exception as e:
  return {"bias":"ERROR","signal":"WAIT","conf":0,"notes":[f"Analyze error {e}"]}

# SIDEBAR
with st.sidebar:
 st.header("Market Settings")
 mkt=st.selectbox("Market",list(M.keys()),index=0)
 st.write(f"IG search: {M[mkt][1]}")
 st.divider()
 if st.session_state["con"]:
  st.success("IG Connected")
 else:
  st.warning("Not Connected")
  if st.button("Connect IG Demo"):
   with st.spinner("Connecting..."):
    ok,msg,data=login()
   if ok:
    st.session_state["con"]=True
    st.session_state["cst"]=data["cst"]
    st.session_state["xsec"]=data["xsec"]
    st.session_state["err"]=""
    st.success("Connected")
    st.rerun()
   else:
    st.session_state["err"]=msg
    st.error(msg)

if st.session_state["err"]:
 st.error(st.session_state["err"])

# CHART - SAFE
try:
 sym=M[mkt][0]
 st.subheader(f"📊 {mkt} Chart")
 html=f"""
<div id="tv" style="height:450px;"></div>
<script src="https://s3.tradingview.com/tv.js"></script>
<script>
new TradingView.widget({{"autosize":true,"symbol":"{sym}","interval":"15","timezone":"Africa/Johannesburg","theme":"dark","style":"1","container_id":"tv"}});
</script>
"""
 components.html(html,height=470)
except Exception as e:
 st.error(f"Chart error {e}")
 st.write(f"Market {mkt} symbol {M[mkt][0]}")

# IG SECTION
st.subheader(f"📡 {mkt} IG Data")

if not st.session_state["con"]:
 st.info("Connect IG Demo in sidebar first")
else:
 term=M[mkt][1]
 if st.button(f"Find {mkt} EPIC"):
  with st.spinner(f"Searching {term}..."):
   ok,err,res=search(term)
  if ok:
   found=[]
   for x in res:
    if isinstance(x,dict):
     info=extract(x)
     if info["epic"]:
      found.append(info)
   st.session_state["mkts"]=found
   if found:
    st.success(f"Found {len(found)} for {mkt}")
    st.dataframe(pd.DataFrame(found),use_container_width=True)
   else:
    st.warning(f"No results for {term}")
  else:
   st.error(err)

 if st.session_state["mkts"]:
  epics=[x["epic"] for x in st.session_state["mkts"]]
  labs=[f"{x['epic']} | {x['name']}" for x in st.session_state["mkts"]]
  idx=st.selectbox("Select EPIC",range(len(epics)),format_func=lambda i: labs[i])
  epic=epics[idx]
  st.session_state["epic"]=epic
  st.info(f"Selected {epic}")

  if st.button(f"🚀 ANALYZE {mkt}",use_container_width=True):
   with st.spinner(f"Pulling {mkt}..."):
    ok15,_,df15=get_prices(epic,"MINUTE_15",100)
   if ok15:
    st.session_state["df15"]=df15
    st.success(f"Loaded {len(df15)} candles for {mkt}")
   else:
    st.error("Failed to load")

# ANALYSIS - ALWAYS SHOWS
if st.session_state["df15"] is not None:
 try:
  st.divider()
  st.subheader(f"🧠 {mkt} HYBRID ANALYSIS")
  res=analyze(st.session_state["df15"])
  c1,c2,c3,c4=st.columns(4)
  c1.metric("BIAS",res["bias"])
  c2.metric("BOS",res["bos"] or "None")
  c3.metric("SIGNAL",res["signal"])
  c4.metric("CONF",f"{res['conf']}%")

  if res["signal"]!="WAIT":
   st.success(f"✅ {mkt} {res['signal']} Entry {res['entry']:.5f} SL {res['sl']:.5f} TP {res['tp']:.5f}")
  else:
   st.warning(f"⏳ WAIT - {res['conf']}% need 60%+")

  for n in res["notes"]:
   st.write(f"• {n}")

  with st.expander("Candles + Indicators"):
   st.dataframe(res["df"].tail(20),use_container_width=True)
  st.line_chart(res["df"].set_index("time")[["close","ema50"]])
 except Exception as e:
  st.error(f"Analysis render error {e}")
