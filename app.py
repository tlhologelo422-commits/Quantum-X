import os, pandas as pd, numpy as np, requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Quantum X SCALPER V6.1", layout="wide")
st.title("QUANTUM X SCALPER V6.1 - HMA + OB + FVG + AUTO")
st.caption("Hull MA filter + Real OB/FVG retest + IG Demo auto-execution")

IG = "https://demo-api.ig.com/gateway/deal"

M = {
 "XAU/USD": ["OANDA:XAUUSD","Gold"],
 "EUR/USD": ["FX:EURUSD","EUR/USD"],
 "GBP/USD": ["FX:GBPUSD","GBP/USD"],
 "NAS100": ["OANDA:NAS100USD","US Tech 100"],
 "US30": ["OANDA:US30USD","US 30"],
 "BTC/USD": ["BINANCE:BTCUSD","Bitcoin"],
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
   return False,f"Search {r.status_code}",[]
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
    if b is None:
     return float(a) if a else None
    if a is None:
     return float(b)
    return (float(b)+float(a))/2
   rows.append({"time":t,"open":mid(p.get("openPrice")),"high":mid(p.get("highPrice")),"low":mid(p.get("lowPrice")),"close":mid(p.get("closePrice"))})
  df=pd.DataFrame(rows).dropna()
  df["time"]=pd.to_datetime(df["time"],utc=True,errors="coerce")
  df=df.sort_values("time").reset_index(drop=True)
  return df,None
 except Exception as e:
  return None,str(e)

def place_trade(epic,direction,size,stop,tp):
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
 }
 payload={k:v for k,v in payload.items() if v is not None}
 try:
  r=requests.post(url,headers=hdrs("2"),json=payload,timeout=20)
  return r.status_code,r.text, r.json() if r.text else {}
 except Exception as e:
  return 0,str(e),{}

def wma(s,period):
 return s.rolling(period).apply(lambda x: np.dot(x, np.arange(1,period+1))/np.arange(1,period+1).sum(), raw=True)

def hull_ma(s,period=21):
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
 hl=df["high"]-df["low"]
 hc=(df["high"]-df["close"].shift()).abs()
 lc=(df["low"]-df["close"].shift()).abs()
 tr=pd.concat([hl,hc,lc],axis=1).max(axis=1)
 df["atr"]=tr.rolling(14).mean()
 return df

def find_real_ob_fvg(df):
 obs=[]; fvgs=[]
 for i in range(5,len(df)-3):
  is_bear=df["close"].iloc[i]<df["open"].iloc[i]
  next_bull=df["close"].iloc[i+1]>df["open"].iloc[i+1] and df["close"].iloc[i+2]>df["open"].iloc[i+2]
  impulse=df["close"].iloc[i+2]>df["high"].iloc[i] and (df["close"].iloc[i+2]-df["open"].iloc[i+1])>df["atr"].iloc[i]*1.2
  if is_bear and next_bull and impulse:
   obs.append({"type":"BULL","low":df["low"].iloc[i],"high":df["high"].iloc[i],"time":df["time"].iloc[i],"idx":i})
  is_bull=df["close"].iloc[i]>df["open"].iloc[i]
  next_bear=df["close"].iloc[i+1]<df["open"].iloc[i+1] and df["close"].iloc[i+2]<df["open"].iloc[i+2]
  impulse_b=df["close"].iloc[i+2]<df["low"].iloc[i] and (df["open"].iloc[i+1]-df["close"].iloc[i+2])>df["atr"].iloc[i]*1.2
  if is_bull and next_bear and impulse_b:
   obs.append({"type":"BEAR","low":df["low"].iloc[i],"high":df["high"].iloc[i],"time":df["time"].iloc[i],"idx":i})
  if i>1 and i<len(df)-1:
   if df["low"].iloc[i+1] > df["high"].iloc[i-1]:
    gap=df["low"].iloc[i+1]-df["high"].iloc[i-1]
    if gap>df["atr"].iloc[i]*0.25:
     fvgs.append({"type":"BULL","top":df["low"].iloc[i+1],"bottom":df["high"].iloc[i-1],"gap":gap,"time":df["time"].iloc[i],"idx":i})
   if df["high"].iloc[i+1] < df["low"].iloc[i-1]:
    gap=df["low"].iloc[i-1]-df["high"].iloc[i+1]
    if gap>df["atr"].iloc[i]*0.25:
     fvgs.append({"type":"BEAR","top":df["low"].iloc[i-1],"bottom":df["high"].iloc[i+1],"gap":gap,"time":df["time"].iloc[i],"idx":i})
 return obs,fvgs

def scalper_signal(df,market):
 df=add_all(df)
 obs,fvgs=find_real_ob_fvg(df)
 last=df.iloc[-1]
 notes=[]
 hma=last["hma"]; e50=last["ema50"]; e200=last["ema200"]; close=last["close"]
 notes.append(f"PRICE {close:.5f} HMA {hma:.5f} EMA50 {e50:.5f} EMA200 {e200:.5f} ATR {last['atr']:.5f}")
 trend=None
 if close>hma and hma>e50 and e50>e200:
  trend="BULL"
  notes.append("TREND BULL: Close>HMA>EMA50>EMA200 ONLY BUYS")
 elif close<hma and hma<e50 and e50<e200:
  trend="BEAR"
  notes.append("TREND BEAR: Close<HMA<EMA50<EMA200 ONLY SELLS")
 else:
  notes.append("TREND MIXED - NO TRADE - Wait alignment")
  return {"trend":None,"signal":"WAIT","conf":0,"notes":notes,"obs":obs,"fvgs":fvgs,"df":df}
 recent_obs=[o for o in obs if o["idx"]>len(df)-25]
 recent_fvg=[f for f in fvgs if f["idx"]>len(df)-25]
 notes.append(f"Found {len(recent_obs)} OBs and {len(recent_fvg)} FVGs last 25")
 signal="WAIT"; entry=None; sl=None; tp=None; conf=0
 atr=last["atr"]
 if trend=="BEAR":
  for ob in reversed(recent_obs):
   if ob["type"]=="BEAR" and ob["low"]<=close<=ob["high"]*1.001:
    signal="SELL"
    conf=80
    txt=f"SELL RETEST: Price {close:.2f} inside BEAR OB {ob['low']:.2f}-{ob['high']:.2f}"
    notes.append(txt)
    break
  if signal=="WAIT":
   for fvg in reversed(recent_fvg):
    if fvg["type"]=="BEAR" and fvg["bottom"]<=close<=fvg["top"]:
     signal="SELL"
     conf=75
     txt=f"SELL RETEST: Price {close:.2f} inside BEAR FVG {fvg['bottom']:.2f}-{fvg['top']:.2f}"
     notes.append(txt)
     break
  if signal=="SELL":
   entry=close
   sl=entry+atr*1.5
   tp=entry-atr*2.5
 elif trend=="BULL":
  for ob in reversed(recent_obs):
   if ob["type"]=="BULL" and ob["low"]*0.999<=close<=ob["high"]:
    signal="BUY"
    conf=80
    txt=f"BUY RETEST: Price {close:.2f} inside BULL OB {ob['low']:.2f}-{ob['high']:.2f}"
    notes.append(txt)
    break
  if signal=="WAIT":
   for fvg in reversed(recent_fvg):
    if fvg["type"]=="BULL" and fvg["bottom"]<=close<=fvg["top"]:
     signal="BUY"
     conf=75
     txt=f"BUY RETEST: Price {close:.2f} inside BULL FVG {fvg['bottom']:.2f}-{fvg['top']:.2f}"
     notes.append(txt)
     break
  if signal=="BUY":
   entry=close
   sl=entry-atr*1.5
   tp=entry+atr*2.5
 if signal=="WAIT":
  notes.append("Price not retesting OB/FVG yet - WAIT pullback")
  conf=30
 return {"trend":trend,"signal":signal,"conf":conf,"entry":entry,"sl":sl,"tp":tp,"notes":notes,"obs":obs,"fvgs":fvgs,"df":df}

with st.sidebar:
 st.header("Scalper Settings")
 mkt=st.selectbox("Market",list(M.keys()),index=0)
 if mkt!=st.session_state["last_mkt"]:
  st.session_state["mkts"]=[]
  st.session_state["last_mkt"]=mkt
 size=st.number_input("Trade Size lots",value=0.5,min_value=0.1,step=0.1)
 st.divider()
 if st.session_state["con"]:
  st.success("IG Connected")
 else:
  if st.button("Connect IG Demo"):
   ok,msg,data=login()
   if ok:
    st.session_state["con"]=True
    st.session_state["cst"]=data["cst"]
    st.session_state["xsec"]=data["xsec"]
    st.rerun()
   else:
    st.error(msg)
 auto=st.checkbox("AUTO TRADE ON (Real Demo)",value=False)
 st.session_state["auto"]=auto
 if auto:
  st.error("LIVE - Will open real demo trades!")

try:
 sym=M[mkt][0]
 html=f"""<div id="tv" style="height:380px;"></div><script src="https://s3.tradingview.com/tv.js"></script><script>new TradingView.widget({{"autosize":true,"symbol":"{sym}","interval":"5","timezone":"Africa/Johannesburg","theme":"dark","style":"1","container_id":"tv"}});</script>"""
 components.html(html,height=400)
except:
 st.write(f"{mkt} chart")

st.subheader(f"SCANNER {mkt}")

if not st.session_state["con"]:
 st.warning("Connect IG first")
 st.stop()

term=M[mkt][1]
if st.button(f"Find {mkt} EPIC"):
 ok,err,res=search(term)
 if ok:
  found=[]
  for x in res:
   epic=x.get("epic","") or x.get("instrument",{}).get("epic","")
   name=x.get("instrumentName","") or x.get("instrument",{}).get("name","")
   if epic:
    found.append({"epic":epic,"name":name})
  st.session_state["mkts"]=found
  st.success(f"Found {len(found)}")

if st.session_state["mkts"]:
 epics=[x["epic"] for x in st.session_state["mkts"]]
 labs=[f"{x['epic']} | {x['name']}" for x in st.session_state["mkts"]]
 idx=st.selectbox("EPIC",range(len(epics)),format_func=lambda i: labs[i],key=f"epic_{mkt}")
 epic=epics[idx]
 st.info(f"Trading {epic} Size {size}")

 if st.button(f"SCAN & SCALP {mkt}",use_container_width=True,type="primary"):
  df,_=get_prices(epic,"MINUTE_5",200)
  if df is None:
   st.error("Failed price")
  else:
   res=scalper_signal(df,mkt)
   st.divider()
   c1,c2,c3,c4=st.columns(4)
   c1.metric("TREND",res["trend"] or "MIXED")
   c2.metric("SIGNAL",res["signal"])
   c3.metric("CONF",f"{res['conf']}%")
   c4.metric("MODE","AUTO" if st.session_state["auto"] else "MANUAL")
   for n in res["notes"]:
    st.write(f"- {n}")
   if res["obs"]:
    st.write("Recent OBs:")
    for ob in res["obs"][-4:]:
     st.write(f" - {ob['type']} {ob['low']:.2f}-{ob['high']:.2f} {ob['time']}")
   if res["fvgs"]:
    st.write("Recent FVGs:")
    for f in res["fvgs"][-4:]:
     st.write(f" - {f['type']} {f['bottom']:.2f}-{f['top']:.2f} gap {f['gap']:.4f}")
   if res["signal"]!="WAIT":
    st.success(f"{res['signal']} Entry {res['entry']:.5f} SL {res['sl']:.5f} TP {res['tp']:.5f}")
    if st.session_state["auto"]:
     with st.spinner(f"Placing {res['signal']} on IG Demo..."):
      code,txt,js=place_trade(epic,res["signal"],size,res["sl"],res["tp"])
     if code==200:
      st.balloons()
      st.success(f"TRADE OPENED IG ref: {js}")
      st.json(js)
     else:
      st.error(f"Trade failed {code} {txt[:500]}")
    else:
     st.warning("AUTO OFF - Tick checkbox to open trade")
   else:
    st.warning(f"WAIT - No retest")
   with st.expander("Candles + HMA"):
    st.dataframe(res["df"].tail(30),use_container_width=True)
   st.line_chart(res["df"].set_index("time")[["close","hma","ema50","ema200"]])
