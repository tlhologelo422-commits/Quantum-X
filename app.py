import os
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Quantum X V5 PROPER", layout="wide")
st.title("📈 QUANTUM X PRO V5 - PROPER SCAN")
st.caption("Real OB + Real FVG + Auto market clear - No more lag")

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
 "SPX500": ["OANDA:SPX500USD","US 500"],
 "GER40": ["XETRA:DAX","Germany 40"],
 "BTC/USD": ["BINANCE:BTCUSD","Bitcoin"],
 "ETH/USD": ["BINANCE:ETHUSD","Ethereum"],
 "US Oil": ["OANDA:WTICOUSD","Oil"],
}

# Init
for k in ["con","cst","xsec","mkts","last_mkt"]:
 if k not in st.session_state:
  st.session_state[k]=False if k=="con" else None if k!="mkts" else []

if st.session_state["last_mkt"] is None:
 st.session_state["last_mkt"]=""

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
  return False,"Add secrets IG_USERNAME IG_PASSWORD IG_API_KEY",None
 h={"X-IG-API-KEY":k,"Content-Type":"application/json","Accept":"application/json","Version":"2"}
 b={"identifier":u,"password":p,"encryptedPassword":False}
 try:
  r=requests.post(f"{IG}/session",headers=h,json=b,timeout=20)
  if r.status_code!=200:
   return False,r.text[:300],None
  return True,"OK",{"cst":r.headers.get("CST"),"xsec":r.headers.get("X-SECURITY-TOKEN")}
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

def get_prices(epic,res,num=150):
 url=f"{IG}/prices/{epic}/{res}/{num}"
 try:
  r=requests.get(url,headers=hdrs("2"),timeout=30)
  if r.status_code!=200:
   return None,f"{res} {r.status_code}"
  plist=r.json().get("prices",[])
  rows=[]
  for p in plist:
   t=p.get("snapshotTimeUTC") or p.get("snapshotTime") or ""
   def mid(v):
    if not isinstance(v,dict):
     return None
    b=v.get("bid")
    a=v.get("ask")
    if b is None:
     return float(a) if a is not None else None
    if a is None:
     return float(b)
    return (float(b)+float(a))/2
   rows.append({"time":t,"open":mid(p.get("openPrice")),"high":mid(p.get("highPrice")),"low":mid(p.get("lowPrice")),"close":mid(p.get("closePrice")),"vol":p.get("lastTradedVolume") or 0})
  df=pd.DataFrame(rows).dropna(subset=["open","high","low","close"])
  df["time"]=pd.to_datetime(df["time"],utc=True,errors="coerce")
  df=df.sort_values("time").reset_index(drop=True)
  return df,None
 except Exception as e:
  return None,str(e)

# --- REAL INDICATORS + REAL SMC ---
def add_ind(df):
 df=df.copy()
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
 # Swing
 df["swing_high"]=df["high"].rolling(5,center=True).max()==df["high"]
 df["swing_low"]=df["low"].rolling(5,center=True).min()==df["low"]
 return df

def find_ob_fvg(df):
 obs=[]
 fvgs=[]
 # Real Order Block: last bearish candle before bullish impulse (strong)
 for i in range(2,len(df)-2):
  # Bull OB: bearish candle, then strong bull close above
  if df["close"].iloc[i]<df["open"].iloc[i] and df["close"].iloc[i+1]>df["open"].iloc[i+1] and df["close"].iloc[i+1]>df["high"].iloc[i]:
   if df["close"].iloc[i+1]-df["open"].iloc[i+1] > df["atr"].iloc[i]*0.8:
    obs.append({"type":"BULL","idx":i,"high":df["high"].iloc[i],"low":df["low"].iloc[i],"time":df["time"].iloc[i]})
  # Bear OB
  if df["close"].iloc[i]>df["open"].iloc[i] and df["close"].iloc[i+1]<df["open"].iloc[i+1] and df["close"].iloc[i+1]<df["low"].iloc[i]:
   if df["open"].iloc[i+1]-df["close"].iloc[i+1] > df["atr"].iloc[i]*0.8:
    obs.append({"type":"BEAR","idx":i,"high":df["high"].iloc[i],"low":df["low"].iloc[i],"time":df["time"].iloc[i]})
 # Real FVG: 3 candle imbalance
 for i in range(1,len(df)-1):
  if df["low"].iloc[i+1] > df["high"].iloc[i-1]: # Bull FVG gap
   gap=df["low"].iloc[i+1]-df["high"].iloc[i-1]
   if gap>df["atr"].iloc[i]*0.3:
    fvgs.append({"type":"BULL","idx":i,"top":df["low"].iloc[i+1],"bottom":df["high"].iloc[i-1],"gap":gap,"time":df["time"].iloc[i]})
  if df["high"].iloc[i+1] < df["low"].iloc[i-1]: # Bear FVG
   gap=df["low"].iloc[i-1]-df["high"].iloc[i+1]
   if gap>df["atr"].iloc[i]*0.3:
    fvgs.append({"type":"BEAR","idx":i,"top":df["low"].iloc[i-1],"bottom":df["high"].iloc[i+1],"gap":gap,"time":df["time"].iloc[i]})
 return obs,fvgs

def analyze_proper(df15,df1h,df4h,market_name):
 df15=add_ind(df15)
 if df4h is not None:
  df4h=add_ind(df4h)
 if df1h is not None:
  df1h=add_ind(df1h)
 obs,fvgs=find_ob_fvg(df15)
 last=df15.iloc[-1]
 notes=[]
 score=0
 # HTF
 bias="NEUTRAL"
 if df4h is not None and len(df4h)>20:
  l4=df4h.iloc[-1]
  if l4["close"]>l4["ema50"] and l4["ema50"]>l4["ema200"]:
   bias="BULLISH"
   score+=15
   notes.append(f"4H BULL: {l4['close']:.2f} > EMA50 > EMA200")
  elif l4["close"]<l4["ema50"] and l4["ema50"]<l4["ema200"]:
   bias="BEARISH"
   score+=15
   notes.append(f"4H BEAR: {l4['close']:.2f} < EMA50 < EMA200")
  else:
   notes.append("4H RANGE")
 # RSI
 rsi=last["rsi"]
 if pd.notna(rsi):
  if bias=="BULLISH" and 45<rsi<68:
   score+=15
   notes.append(f"RSI {rsi:.1f} healthy bull")
  elif bias=="BEARISH" and 32<rsi<55:
   score+=15
   notes.append(f"RSI {rsi:.1f} healthy bear")
  else:
   notes.append(f"RSI {rsi:.1f}")
 # BOS
 last_high=df15["high"].rolling(15).max().iloc[-20:-1].max()
 last_low=df15["low"].rolling(15).min().iloc[-20:-1].min()
 bos=None
 if last["close"]>last_high:
  bos="BULL BOS"
  score+=15
  notes.append(f"BULL BOS broke {last_high:.2f} | Now {last['close']:.2f}")
 elif last["close"]<last_low:
  bos="BEAR BOS"
  score+=15
  notes.append(f"BEAR BOS broke {last_low:.2f} | Now {last['close']:.2f}")
 # OB proximity
 recent_obs=[o for o in obs if o["idx"]>len(df15)-20]
 if recent_obs:
  notes.append(f"Found {len(recent_obs)} recent OBs")
  last_ob=recent_obs[-1]
  if last_ob["type"]=="BULL" and bias=="BULLISH":
   score+=15
   notes.append(f"BULL OB at {last_ob['low']:.2f}-{last_ob['high']:.2f} {last_ob['time']}")
  if last_ob["type"]=="BEAR" and bias=="BEARISH":
   score+=15
   notes.append(f"BEAR OB at {last_ob['low']:.2f}-{last_ob['high']:.2f} {last_ob['time']}")
 else:
  notes.append("No recent OB in last 20 candles")
 # FVG
 recent_fvg=[f for f in fvgs if f["idx"]>len(df15)-15]
 if recent_fvg:
  last_fvg=recent_fvg[-1]
  if (last_fvg["type"]=="BULL" and bias=="BULLISH") or (last_fvg["type"]=="BEAR" and bias=="BEARISH"):
   score+=15
   notes.append(f"{last_fvg['type']} FVG {last_fvg['bottom']:.2f}-{last_fvg['top']:.2f} gap {last_fvg['gap']:.2f}")
 else:
  notes.append("No recent FVG")
 # Price reading check
 notes.append(f"PRICE READ: {market_name} Close {last['close']:.5f} Open {last['open']:.5f} ATR {last['atr']:.5f}")

 atr=last["atr"]
 if pd.isna(atr):
  atr=last["close"]*0.002

 signal="WAIT"
 entry=sl=tp=None
 if score>=65 and bos:
  if bias=="BULLISH" and bos=="BULL BOS":
   signal="BUY"
   entry=last["close"]
   sl=entry-atr*1.8
   tp=entry+atr*3.5
  elif bias=="BEARISH" and bos=="BEAR BOS":
   signal="SELL"
   entry=last["close"]
   sl=entry+atr*1.8
   tp=entry-atr*3.5

 return {"bias":bias,"bos":bos,"signal":signal,"conf":min(score,95),"entry":entry,"sl":sl,"tp":tp,"notes":notes,"obs":obs,"fvgs":fvgs,"df":df15}

# SIDEBAR
with st.sidebar:
 st.header("Market")
 mkt=st.selectbox("Select Market",list(M.keys()),index=0)
 # AUTO CLEAR when market changes - FIXES LAG BUG
 if mkt!=st.session_state["last_mkt"]:
  st.session_state["mkts"]=[]
  st.session_state["last_mkt"]=mkt
  st.info(f"Switched to {mkt} - cleared old data")
 st.write(f"IG term: {M[mkt][1]}")
 st.divider()
 if st.session_state["con"]:
  st.success("IG Connected")
  if st.button("Disconnect"):
   st.session_state["con"]=False
   st.rerun()
 else:
  if st.button("Connect IG Demo"):
   with st.spinner("Connecting..."):
    ok,msg,data=login()
   if ok:
    st.session_state["con"]=True
    st.session_state["cst"]=data["cst"]
    st.session_state["xsec"]=data["xsec"]
    st.success("Connected")
    st.rerun()
   else:
    st.error(msg)

# CHART
try:
 sym=M[mkt][0]
 st.subheader(f"{mkt} Chart {sym}")
 html=f"""<div id="tv" style="height:400px;"></div><script src="https://s3.tradingview.com/tv.js"></script><script>new TradingView.widget({{"autosize":true,"symbol":"{sym}","interval":"15","timezone":"Africa/Johannesburg","theme":"dark","style":"1","container_id":"tv"}});</script>"""
 components.html(html,height=420)
except Exception as e:
 st.write(f"Chart err {e}")

# MAIN
st.subheader(f"📡 {mkt} IG Scanner")

if not st.session_state["con"]:
 st.warning("Connect IG Demo in sidebar")
 st.stop()

term=M[mkt][1]
col1,col2=st.columns(2)
with col1:
 if st.button(f"Find {mkt} EPIC",use_container_width=True):
  with st.spinner(f"Searching {term}..."):
   ok,err,res=search(term)
  if ok:
   found=[]
   for x in res:
    if isinstance(x,dict):
     epic=x.get("epic","") or x.get("instrument",{}).get("epic","")
     name=x.get("instrumentName","") or x.get("instrument",{}).get("name","")
     if epic:
      found.append({"epic":epic,"name":name})
   st.session_state["mkts"]=found
   if found:
    st.success(f"Found {len(found)} for {mkt}")
  else:
   st.error(err)

if st.session_state["mkts"]:
 st.dataframe(pd.DataFrame(st.session_state["mkts"]),use_container_width=True)
 epics=[x["epic"] for x in st.session_state["mkts"]]
 labs=[f"{x['epic']} | {x['name']}" for x in st.session_state["mkts"]]
 idx=st.selectbox("Pick EPIC",range(len(epics)),format_func=lambda i: labs[i],key=f"epic_{mkt}")
 epic=epics[idx]

 if st.button(f"🚀 SCAN {mkt} NOW",use_container_width=True,type="primary"):
  with st.spinner(f"Scanning {mkt}... pulling 15M 1H 4H"):
   df15,err15=get_prices(epic,"MINUTE_15",150)
   df1h,_=get_prices(epic,"HOUR",80)
   df4h,_=get_prices(epic,"HOUR_4",50)
  if df15 is None:
   st.error(f"Failed {err15}")
  else:
   res=analyze_proper(df15,df1h,df4h,mkt)
   st.divider()
   st.subheader(f"🧠 {mkt} PROPER ANALYSIS")
   c1,c2,c3,c4=st.columns(4)
   c1.metric("BIAS",res["bias"])
   c2.metric("BOS",res["bos"] or "None")
   c3.metric("SIGNAL",res["signal"])
   c4.metric("CONF",f"{res['conf']}%")
   if res["signal"]!="WAIT":
    st.success(f"✅ {mkt} {res['signal']} @ {res['entry']:.5f} SL {res['sl']:.5f} TP {res['tp']:.5f}")
    if res["conf"]>=75:
     st.balloons()
   else:
    st.warning(f"⏳ {mkt} WAIT {res['conf']}% need BOS + OB + FVG")
   st.write("**Price + OB + FVG Log:**")
   for n in res["notes"]:
    st.write(f"• {n}")
   if res["obs"]:
    st.write(f"**Last 3 Order Blocks:**")
    for ob in res["obs"][-3:]:
     st.write(f" - {ob['type']} OB {ob['low']:.2f}-{ob['high']:.2f} at {ob['time']}")
   if res["fvgs"]:
    st.write(f"**Last 3 FVGs:**")
    for f in res["fvgs"][-3:]:
     st.write(f" - {f['type']} FVG {f['bottom']:.2f}-{f['top']:.2f} gap {f['gap']:.4f}")
   with st.expander(f"{mkt} Candles"):
    st.dataframe(res["df"].tail(30),use_container_width=True)
   st.line_chart(res["df"].set_index("time")[["close","ema50","ema200"]])
