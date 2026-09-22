import os, pandas as pd, numpy as np, requests, time
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Quantum X SCALPER V6", layout="wide")
st.title("⚡ QUANTUM X SCALPER V6 - HMA + OB + FVG + AUTO TRADE")
st.caption("Hull MA filter + Real OB/FVG retest + IG Demo auto-execution")

IG = "https://demo-api.ig.com/gateway/deal"

M = {
 "XAU/USD": ["OANDA:XAUUSD","Gold","CS.D.CFDGOLD.CFM.IP"],
 "EUR/USD": ["FX:EURUSD","EUR/USD","CS.D.EURUSD.MINI.IP"],
 "GBP/USD": ["FX:GBPUSD","GBP/USD","CS.D.GBPUSD.MINI.IP"],
 "NAS100": ["OANDA:NAS100USD","US Tech 100","CS.D.NAS100.CFM.IP"],
 "US30": ["OANDA:US30USD","US 30","CS.D.DOW.CFM.IP"],
 "BTC/USD": ["BINANCE:BTCUSD","Bitcoin","CS.D.BITCOIN.CFM.IP"],
}

for k in ["con","cst","xsec","mkts","last_mkt","auto"]:
 if k not in st.session_state:
  st.session_state[k]=False if k in ["con","auto"] else None if k!="mkts" else []

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
   return False,f"{r.status_code}",[]
  return True,"",r.json().get("markets",[])
 except Exception as e:
  return False,str(e),[]

def get_prices(epic,res,num=200):
 url=f"{IG}/prices/{epic}/{res}/{num}"
 try:
  r=requests.get(url,headers=hdrs("2"),timeout=30)
  if r.status_code!=200:
   return None,f"{res} {r.status_code}"
  plist=r.json().get("prices",[])
  rows=[]
  for p in plist:
   t=p.get("snapshotTimeUTC") or ""
   def mid(v):
    if not isinstance(v,dict):
     return None
    b=v.get("bid"); a=v.get("ask")
    if b is None: return float(a) if a else None
    if a is None: return float(b)
    return (float(b)+float(a))/2
   rows.append({"time":t,"open":mid(p.get("openPrice")),"high":mid(p.get("highPrice")),"low":mid(p.get("lowPrice")),"close":mid(p.get("closePrice"))})
  df=pd.DataFrame(rows).dropna()
  df["time"]=pd.to_datetime(df["time"],utc=True,errors="coerce")
  df=df.sort_values("time").reset_index(drop=True)
  return df,None
 except Exception as e:
  return None,str(e)

def place_trade(epic,direction,size,stop,tp):
 # direction = BUY or SELL
 url=f"{IG}/positions/otc"
 payload={
  "epic":epic,
  "expiry":"-",
  "direction":direction,
  "size":str(size),
  "orderType":"MARKET",
  "currencyCode":"USD",
  "forceOpen":True,
  "guaranteedStop":False,
  "stopLevel":str(stop) if stop else None,
  "limitLevel":str(tp) if tp else None,
  "stopDistance":None,
  "limitDistance":None
 }
 # remove None
 payload={k:v for k,v in payload.items() if v is not None}
 try:
  r=requests.post(url,headers=hdrs("2"),json=payload,timeout=20)
  return r.status_code,r.text, r.json() if r.text else {}
 except Exception as e:
  return 0,str(e),{}

# --- REAL HULL MA ---
def wma(s,period):
 return s.rolling(period).apply(lambda x: np.dot(x, np.arange(1,period+1))/np.arange(1,period+1).sum(), raw=True)

def hull_ma(s,period=21):
 # HMA = WMA(2*WMA(n/2)-WMA(n), sqrt(n))
 half=wma(s,period//2)
 full=wma(s,period)
 diff=2*half-full
 hma=wma(diff,int(np.sqrt(period)))
 return hma

def add_all(df):
 df=df.copy()
 df["hma"]=hull_ma(df["close"],21)
 df["ema50"]=df["close"].ewm(span=50,adjust=False).mean()
 df["ema200"]=df["close"].ewm(span=200,adjust=False).mean()
 df["sma20"]=df["close"].rolling(20).mean()
 # ATR for SL
 hl=df["high"]-df["low"]
 hc=(df["high"]-df["close"].shift()).abs()
 lc=(df["low"]-df["close"].shift()).abs()
 tr=pd.concat([hl,hc,lc],axis=1).max(axis=1)
 df["atr"]=tr.rolling(14).mean()
 return df

def find_real_ob_fvg(df):
 obs=[]; fvgs=[]
 for i in range(5,len(df)-3):
  # Bull OB: bearish candle, next 2 strong bullish, close above high
  is_bear=df["close"].iloc[i]<df["open"].iloc[i]
  next_bull=df["close"].iloc[i+1]>df["open"].iloc[i+1] and df["close"].iloc[i+2]>df["open"].iloc[i+2]
  impulse=df["close"].iloc[i+2]>df["high"].iloc[i] and (df["close"].iloc[i+2]-df["open"].iloc[i+1])>df["atr"].iloc[i]*1.2
  if is_bear and next_bull and impulse:
   obs.append({"type":"BULL","low":df["low"].iloc[i],"high":df["high"].iloc[i],"time":df["time"].iloc[i],"idx":i})
  # Bear OB: bullish candle, next 2 strong bearish
  is_bull=df["close"].iloc[i]>df["open"].iloc[i]
  next_bear=df["close"].iloc[i+1]<df["open"].iloc[i+1] and df["close"].iloc[i+2]<df["open"].iloc[i+2]
  impulse_b=df["close"].iloc[i+2]<df["low"].iloc[i] and (df["open"].iloc[i+1]-df["close"].iloc[i+2])>df["atr"].iloc[i]*1.2
  if is_bull and next_bear and impulse_b:
   obs.append({"type":"BEAR","low":df["low"].iloc[i],"high":df["high"].iloc[i],"time":df["time"].iloc[i],"idx":i})
  # FVG - real imbalance
  if i>1 and i<len(df)-1:
   if df["low"].iloc[i+1] > df["high"].iloc[i-1]: # Bull FVG
    gap=df["low"].iloc[i+1]-df["high"].iloc[i-1]
    if gap>df["atr"].iloc[i]*0.25:
     fvgs.append({"type":"BULL","top":df["low"].iloc[i+1],"bottom":df["high"].iloc[i-1],"gap":gap,"time":df["time"].iloc[i],"idx":i})
   if df["high"].iloc[i+1] < df["low"].iloc[i-1]: # Bear FVG
    gap=df["low"].iloc[i-1]-df["high"].iloc[i+1]
    if gap>df["atr"].iloc[i]*0.25:
     fvgs.append({"type":"BEAR","top":df["low"].iloc[i-1],"bottom":df["high"].iloc[i+1],"gap":gap,"time":df["time"].iloc[i],"idx":i})
 return obs,fvgs

def scalper_signal(df,market):
 df=add_all(df)
 obs,fvgs=find_real_ob_fvg(df)
 last=df.iloc[-1]
 prev=df.iloc[-5:]
 notes=[]
 # HULL FILTER - THIS IS KEY
 hma=last["hma"]; e50=last["ema50"]; e200=last["ema200"]; close=last["close"]
 notes.append(f"PRICE: {close:.5f} | HMA21: {hma:.5f} | EMA50: {e50:.5f} | EMA200: {e200:.5f} | ATR: {last['atr']:.5f}")
 trend=None
 if close>hma and hma>e50 and e50>e200:
  trend="BULL"
  notes.append(f"TREND BULL: Close > HMA > EMA50 > EMA200 - ONLY BUYS")
 elif close< hma and hma< e50 and e50< e200:
  trend="BEAR"
  notes.append(f"TREND BEAR: Close < HMA < EMA50 < EMA200 - ONLY SELLS")
 else:
  notes.append(f"TREND MIXED - NO TRADE - Wait for alignment")
  return {"trend":None,"signal":"WAIT","conf":0,"notes":notes,"obs":obs,"fvgs":fvgs,"df":df}

 # Check retest into OB/FVG
 recent_obs=[o for o in obs if o["idx"]>len(df)-25]
 recent_fvg=[f for f in fvgs if f["idx"]>len(df)-25]
 notes.append(f"Found {len(recent_obs)} OBs and {len(recent_fvg)} FVGs in last 25 candles")
 signal="WAIT"; entry=None; sl=None; tp=None; conf=0
 atr=last["atr"]
 if trend=="BEAR":
  # Look for price retesting bear OB/FVG
  for ob in reversed(recent_obs):
   if ob["type"]=="BEAR" and ob["low"]<=close<=ob["high"]*1.001:
    signal="SELL"
    conf=80
    notes.append(f"🔴 SELL RETEST: Price {
