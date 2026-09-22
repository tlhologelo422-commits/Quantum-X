import os
import pandas as pd
import numpy as np
import requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Quantum X PRO V3 Hybrid", layout="wide")

IG = "https://demo-api.ig.com/gateway/deal"
M = {"XAU/USD": ["OANDA:XAUUSD","Gold"],"EUR/USD": ["FX:EURUSD","EUR/USD"],"GBP/USD": ["FX:GBPUSD","GBP/USD"],"US30": ["OANDA:US30USD","US 30"],}

for k,v in {"con":False,"err":"","cst":None,"xsec":None,"mkts":[],"epic":None,"df15":None,"df1h":None,"df4h":None}.items():
 if k not in st.session_state:
  st.session_state[k]=v

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
  return False,"Missing secrets",None
 h={"X-IG-API-KEY":k,"Content-Type":"application/json","Accept":"application/json","Version":"2"}
 b={"identifier":u,"password":p,"encryptedPassword":False}
 try:
  r=requests.post(f"{IG}/session",headers=h,json=b,timeout=20)
  if r.status_code!=200:
   return False,r.text[:200],None
  c=r.headers.get("CST")
  x=r.headers.get("X-SECURITY-TOKEN")
  out={}
  out["cst"]=c
  out["xsec"]=x
  out["data"]=r.json()
  return True,"OK",out
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
 epic=m.get("epic","")
 if not epic:
  epic=m.get("instrument",{}).get("epic","")
 name=m.get("instrumentName","")
 if not name:
  name=m.get("instrument",{}).get("name","Gold")
 stat="UNKNOWN"
 snap=m.get("snapshot",{})
 if isinstance(snap,dict):
  stat=snap.get("marketStatus","UNKNOWN")
 return {"epic":epic,"name":name,"status":stat}

def get_prices(epic,resolution,num=100):
 url=f"{IG}/prices/{epic}/{resolution}/{num}"
 try:
  r=requests.get(url,headers=hdrs("2"),timeout=30)
  if r.status_code!=200:
   return False,f"{resolution} {r.status_code}",None
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

# --- INDICATORS ---
def rsi_calc(series,period=14):
 delta=series.diff()
 gain=delta.where(delta>0,0)
 loss=-delta.where(delta<0,0)
 avg_gain=gain.rolling(period).mean()
 avg_loss=loss.rolling(period).mean()
 rs=avg_gain/avg_loss
 rsi=100 - (100/(1+rs))
 return rsi

def atr_calc(df,period=14):
 hl=df["high"]-df["low"]
 hc=(df["high"]-df["close"].shift()).abs()
 lc=(df["low"]-df["close"].shift()).abs()
 tr=pd.concat([hl,hc,lc],axis=1).max(axis=1)
 atr=tr.rolling(period).mean()
 return atr

def ema_calc(series,period):
 return series.ewm(span=period,adjust=False).mean()

def add_indicators(df):
 df=df.copy()
 df["sma20"]=df["close"].rolling(20).mean()
 df["ema50"]=ema_calc(df["close"],50)
 df["ema200"]=ema_calc(df["close"],200)
 df["rsi"]=rsi_calc(df["close"],14)
 df["atr"]=atr_calc(df,14)
 # Bollinger
 sma=df["close"].rolling(20).mean()
 std=df["close"].rolling(20).std()
 df["bb_upper"]=sma+2*std
 df["bb_lower"]=sma-2*std
 # MACD
 ema12=ema_calc(df["close"],12)
 ema26=ema_calc(df["close"],26)
 df["macd"]=ema12-ema26
 df["macd_sig"]=ema_calc(df["macd"],9)
 return df

def analyze_hybrid(df15,df1h,df4h):
 if df15 is None or len(df15)<30:
  return {"bias":"NEUTRAL","signal":"WAIT","conf":0,"notes":["No data"]}
 df15=add_indicators(df15)
 last=df15.iloc[-1]
 prev=df15.iloc[-2]
 notes=[]
 score=0
 max_score=100

 # 1. HTF Bias - 4H
 bias="NEUTRAL"
 if df4h is not None and len(df4h)>=20:
  df4h=add_indicators(df4h)
  l4=df4h.iloc[-1]
  if l4["close"]>l4["ema50"] and l4["ema50"]>l4["ema200"]:
   bias="BULLISH"
   score+=15
   notes.append(f"4H Bull Trend: close > EMA50 > EMA200")
  elif l4["close"]<l4["ema50"] and l4["ema50"]<l4["ema200"]:
   bias="BEARISH"
   score+=15
   notes.append(f"4H Bear Trend: close < EMA50 < EMA200")
  else:
   notes.append(f"4H Ranging")

 # 2. RSI Filter
 rsi=last["rsi"]
 if bias=="BULLISH" and 40<rsi<70:
  score+=15
  notes.append(f"RSI Healthy Bull {rsi:.1f}")
 elif bias=="BEARISH" and 30<rsi<60:
  score+=15
  notes.append(f"RSI Healthy Bear {rsi:.1f}")
 elif rsi>70:
  notes.append(f"RSI Overbought {rsi:.1f} - caution")
 elif rsi<30:
  notes.append(f"RSI Oversold {rsi:.1f} - caution")

 # 3. EMA Alignment 15M
 if last["close"]>last["ema50"] and last["ema50"]>last["ema200"]:
  if bias=="BULLISH":
   score+=10
   notes.append("15M EMA Bull aligned")
 elif last["close"]<last["ema50"] and last["ema50"]<last["ema200"]:
  if bias=="BEARISH":
   score+=10
   notes.append("15M EMA Bear aligned")

 # 4. MACD Momentum
 if last["macd"]>last["macd_sig"] and bias=="BULLISH":
  score+=10
  notes.append("MACD Bull cross")
 elif last["macd"]<last["macd_sig"] and bias=="BEARISH":
  score+=10
  notes.append("MACD Bear cross")

 # 5. Bollinger - not overextended
 if last["bb_lower"]<last["close"]<last["bb_upper"]:
  score+=5
  notes.append("Inside Bollinger - good")

 # 6. SMC - BOS
 last_high=df15["high"].rolling(10).max().iloc[-15:-1].max()
 last_low=df15["low"].rolling(10).min().iloc[-15:-1].min()
 bos=None
 if last["close"]>last_high:
  bos="BULL BOS"
  score+=15
  notes.append(f"BULL BOS broke {last_high:.2f}")
 elif last["close"]<last_low:
  bos="BEAR BOS"
  score+=15
  notes.append(f"BEAR BOS broke {last_low:.2f}")

 # 7. FVG
 fvg_count=0
 fvg_signal=None
 for i in range(1,len(df15)-1):
  if df15["low"].iloc[i+1] > df15["high"].iloc[i-1]:
   fvg_count+=1
   if i>len(df15)-10:
    fvg_signal="BULL FVG"
  if df15["high"].iloc[i+1] < df15["low"].iloc[i-1]:
   fvg_count+=1
   if i>len(df15)-10:
    fvg_signal="BEAR FVG"
 if fvg_count>0:
  notes.append(f"{fvg_count} FVGs - recent {fvg_signal}")
  if (bias=="BULLISH" and fvg_signal=="BULL FVG") or (bias=="BEARISH" and fvg_signal=="BEAR FVG"):
   score+=10

 # 8. Volume confirmation
 vol_avg=df15["vol"].rolling(20).mean().iloc[-1]
 if last["vol"]>vol_avg:
  score+=5
  notes.append(f"Volume spike {last['vol']} > avg {vol_avg:.0f}")

 # 9. ATR for SL/TP
 atr=last["atr"]
 if pd.isna(atr):
  atr=2.5

 # Final Signal
 signal="WAIT"
 entry=sl=tp=None
 if score>=70:
  if bias=="BULLISH" and bos=="BULL BOS":
   signal="BUY"
   entry=last["close"]
   sl=entry - atr*1.5
   tp=entry + atr*3
  elif bias=="BEARISH" and bos=="BEAR BOS":
   signal="SELL"
   entry=last["close"]
   sl=entry + atr*1.5
   tp=entry - atr*3
 else:
  signal="WAIT"

 return {"bias":bias,"bos":bos,"signal":signal,"conf":min(score,95),"entry":entry,"sl":sl,"tp":tp,"rsi":rsi,"atr":atr,"notes":notes,"fvg":fvg_signal,"df":df15}

# --- UI ---
st.title("📈 QUANTUM X PRO V3 - HYBRID")
st.caption("SMC + RSI + ATR + EMA + MACD + Bollinger")

if st.session_state["con"]:
 st.success("● SYSTEM ONLINE • IG DEMO • HYBRID ENGINE")
else:
 st.success("● SYSTEM ONLINE")

with st.sidebar:
 st.header("Settings")
 mkt=st.selectbox("Market",list(M.keys()))
 st.divider()
 if st.session_state["con"]:
  st.success("IG Connected")
  if st.button("Reconnect"):
   ok,msg,data=login()
   if ok:
    st.session_state["con"]=True
    st.session_state["cst"]=data["cst"]
    st.session_state["xsec"]=data["xsec"]
 else:
  if st.button("Connect IG Demo"):
   with st.spinner("Connecting..."):
    ok,msg,data=login()
   if ok:
    st.session_state["con"]=True
    st.session_state["err"]=""
    st.session_state["cst"]=data["cst"]
    st.session_state["xsec"]=data["xsec"]
    st.rerun()
   else:
    st.error(msg)

if st.session_state["err"]:
 st.error(st.session_state["err"])

sym=M[mkt][0]
html=f"""
<div id="chart" style="height:450px;width:100%;"></div>
<script src="https://s3.tradingview.com/tv.js"></script>
<script>
new TradingView.widget({{"autosize":true,"symbol":"{sym}","interval":"15","timezone":"Africa/Johannesburg","theme":"dark","style":"1","container_id":"chart"}});
</script>
"""
components.html(html,height=470)

st.subheader("📡 IG Data + Hybrid Analysis")

if not st.session_state["con"]:
 st.info("Connect IG first")
else:
 term=M[mkt][1]
 if st.button("Find Gold EPIC"):
  ok,err,res=search(term)
  if ok:
   found=[]
   for x in res:
    if isinstance(x,dict):
     info=extract(x)
     if info["epic"]:
      found.append(info)
   st.session_state["mkts"]=found
   st.success(f"Found {len(found)}")
   st.dataframe(pd.DataFrame(found),use_container_width=True)
  else:
   st.error(err)

 if st.session_state["mkts"]:
  epics=[x["epic"] for x in st.session_state["mkts"]]
  labs=[f"{x['epic']} | {x['name']}" for x in st.session_state["mkts"]]
  idx=st.selectbox("Select EPIC",range(len(epics)),format_func=lambda i: labs[i])
  epic=epics[idx]
  st.session_state["epic"]=epic
  st.info(f"Selected {epic}")
  if st.button("🚀 LOAD + HYBRID ANALYZE",use_container_width=True):
   with st.spinner("Pulling 4H 1H 15M..."):
    ok4,_,df4h=get_prices(epic,"HOUR_4",50)
    ok1,_,df1h=get_prices(epic,"HOUR",80)
    ok15,_,df15=get_prices(epic,"MINUTE_15",120)
   if ok15:
    st.session_state["df15"]=df15
    st.session_state["df1h"]=df1h if ok1 else None
    st.session_state["df4h"]=df4h if ok4 else None
    st.success(f"15M {len(df15)} | 1H {len(df1h) if df1h is not None else 0} | 4H {len(df4h) if df4h is not None else 0}")

 if st.session_state["df15"] is not None:
  st.divider()
  st.subheader("🧠 HYBRID CONFLUENCE ANALYSIS")
  res=analyze_hybrid(st.session_state["df15"],st.session_state["df1h"],st.session_state["df4h"])
  c1,c2,c3,c4=st.columns(4)
  c1.metric("HTF BIAS",res["bias"])
  c2.metric("STRUCTURE",res["bos"] or "No BOS")
  c3.metric("SIGNAL",res["signal"])
  c4.metric("CONFLUENCE",f"{res['conf']}%")

  # Indicator Dashboard
  d1,d2,d3,d4=st.columns(4)
  d1.metric("RSI 14",f"{res['rsi']:.1f}" if res['rsi'] else "N/A")
  d2.metric("ATR 14",f"{res['atr']:.2f}")
  d3.metric("FVG",res["fvg"] or "None")
  d4.metric("EMA", "Aligned" if res["conf"]>=70 else "Not Aligned")

  if res["signal"]!="WAIT":
   st.success(f"✅ HIGH PROBABILITY {res['signal']} | Entry {res['entry']:.2f} | SL {res['sl']:.2f} | TP {res['tp']:.2f} | ATR SL/TP")
   st.balloons()
  else:
   st.warning(f"⏳ WAIT - Score {res['conf']}% - Need 70%+ for signal")

  st.subheader("📋 Confluence Breakdown")
  for n in res["notes"]:
   st.write(f"• {n}")

  with st.expander("📊 View 15M + Indicators Table"):
   st.dataframe(res["df"].tail(30),use_container_width=True)

  st.line_chart(res["df"].set_index("time")[["close","sma20","ema50"]])
