import os, pandas as pd, numpy as np, requests, time
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="SUPERTREND FIB 0.2-0.3 V11", layout="wide")
st.title("SUPERTREND + FIB 0.2-0.3 SCALPER V11 - Mathematical")
st.caption("Supertrend direction + Fib 20-30% block entry + Max 0.15 + CLOSE button")

IG = "https://demo-api.ig.com/gateway/deal"
M = {"XAU/USD":["OANDA:XAUUSD","Gold"],"EUR/USD":["FX:EURUSD","EUR/USD"],"GBP/USD":["FX:GBPUSD","GBP/USD"],"NAS100":["OANDA:NAS100USD","US Tech 100"],"US30":["OANDA:US30USD","US 30"],"BTC/USD":["BINANCE:BTCUSD","Bitcoin"]}

for k in ["con","cst","xsec","mkts","last_mkt","running"]:
 if k not in st.session_state: st.session_state[k]=False if k in ["con","running"] else [] if k=="mkts" else None
if st.session_state["last_mkt"] is None: st.session_state["last_mkt"]=""

def get_creds():
 def g(n):
  try:
   if n in st.secrets: return st.secrets[n]
  except: pass
  return os.getenv(n)
 return g("IG_USERNAME"),g("IG_PASSWORD"),g("IG_API_KEY")
def hdrs(v="2"):
 _,_,k=get_creds()
 return {"X-IG-API-KEY":k,"CST":st.session_state["cst"],"X-SECURITY-TOKEN":st.session_state["xsec"],"Accept":"application/json","Content-Type":"application/json","Version":v}
def login():
 u,p,k=get_creds(); h={"X-IG-API-KEY":k,"Content-Type":"application/json","Accept":"application/json","Version":"2"}; b={"identifier":u,"password":p,"encryptedPassword":False}
 try:
  r=requests.post(f"{IG}/session",headers=h,json=b,timeout=20)
  if r.status_code!=200: return False,r.text[:300],None
  return True,"OK",{"cst":r.headers.get("CST"),"xsec":r.headers.get("X-SECURITY-TOKEN")}
 except Exception as e: return False,str(e),None
def search(term):
 try:
  r=requests.get(f"{IG}/markets",headers=hdrs("1"),params={"searchTerm":term},timeout=20)
  if r.status_code!=200: return False,f"{r.status_code}",[]
  return True,"",r.json().get("markets",[])
 except Exception as e: return False,str(e),[]
def get_prices(epic,res="MINUTE_5",num=200):
 url=f"{IG}/prices/{epic}/{res}/{num}"
 try:
  r=requests.get(url,headers=hdrs("2"),timeout=25)
  if r.status_code!=200: return None
  plist=r.json().get("prices",[])
  rows=[]
  for p in plist:
   def mid(v):
    if not isinstance(v,dict): return None
    b=v.get("bid"); a=v.get("ask")
    if b is None: return float(a) if a else None
    if a is None: return float(b)
    return (float(b)+float(a))/2
   rows.append({"time":p.get("snapshotTimeUTC"),"open":mid(p.get("openPrice")),"high":mid(p.get("highPrice")),"low":mid(p.get("lowPrice")),"close":mid(p.get("closePrice"))})
  df=pd.DataFrame(rows).dropna()
  df["time"]=pd.to_datetime(df["time"],utc=True,errors="coerce")
  df=df.sort_values("time").reset_index(drop=True)
  return df
 except: return None
def get_positions():
 try:
  r=requests.get(f"{IG}/positions",headers=hdrs("2"),timeout=15)
  if r.status_code!=200: return []
  return r.json().get("positions",[])
 except: return []
def place(epic,direction,size,sl=None,tp=None):
 url=f"{IG}/positions/otc"
 payload={"epic":epic,"expiry":"-","direction":direction,"size":str(size),"orderType":"MARKET","currencyCode":"USD","forceOpen":True,"guaranteedStop":False}
 if sl: payload["stopLevel"]=str(sl)
 if tp: payload["limitLevel"]=str(tp)
 try:
  r=requests.post(url,headers=hdrs("2"),json=payload,timeout=15)
  return r.status_code,r.text
 except Exception as e: return 0,str(e)
def close_all_for_epic(epic):
 pos=get_positions(); this=[p for p in pos if p.get("market",{}).get("epic")==epic]; res=[]
 for p in this:
  po=p.get("position",{}); dealId=po.get("dealId"); direction=po.get("direction"); size=po.get("size")
  close_dir="SELL" if direction=="BUY" else "BUY"
  url=f"{IG}/positions/otc"; payload={"dealId":dealId,"epic":epic,"expiry":"-","direction":close_dir,"size":str(size),"orderType":"MARKET"}
  try:
   r=requests.delete(url,headers=hdrs("2"),json=payload,timeout=15)
   res.append(f"{direction} {size} -> {r.status_code}")
  except Exception as e: res.append(str(e))
 return res

# SUPERTREND
def supertrend(df, period=10, multiplier=3.0):
 hl2 = (df["high"] + df["low"]) / 2
 tr1 = df["high"] - df["low"]
 tr2 = (df["high"] - df["close"].shift()).abs()
 tr3 = (df["low"] - df["close"].shift()).abs()
 tr = pd.concat([tr1,tr2,tr3],axis=1).max(axis=1)
 atr = tr.rolling(period).mean()
 upper = hl2 + multiplier*atr
 lower = hl2 - multiplier*atr
 st_line = pd.Series(index=df.index,dtype=float)
 direction = pd.Series(index=df.index,dtype=int)
 for i in range(len(df)):
  if i==0:
   st_line.iloc[i]=lower.iloc[i]; direction.iloc[i]=1
  else:
   if df["close"].iloc[i] <= st_line.iloc[i-1]:
    st_line.iloc[i]=upper.iloc[i]; direction.iloc[i]=-1
   else:
    st_line.iloc[i]=lower.iloc[i]; direction.iloc[i]=1
   # adjust
   if direction.iloc[i]==1 and st_line.iloc[i] < st_line.iloc[i-1]:
    st_line.iloc[i]=st_line.iloc[i-1]
   if direction.iloc[i]==-1 and st_line.iloc[i] > st_line.iloc[i-1]:
    st_line.iloc[i]=st_line.iloc[i-1]
 df["supertrend"]=st_line
 df["st_dir"]=direction
 df["atr"]=atr
 return df

def fib_02_03_signal(df):
 df=supertrend(df,10,3.0)
 last=df.iloc[-1]
 # Last 20 candle swing
 look=df.tail(20)
 swing_high=look["high"].max()
 swing_low=look["low"].min()
 swing_range=swing_high-swing_low
 if swing_range==0: return "WAIT",None,None,None,None,df,0,0

 # Fib 0.2-0.3 block
 if last["st_dir"]==1: # BULL supertrend - buy dip 20-30% from high
  zone_top=swing_high - swing_range*0.2
  zone_bottom=swing_high - swing_range*0.3
  zone_type="BULL 0.2-0.3"
  signal="BUY" if zone_bottom <= last["close"] <= zone_top else "WAIT"
  sl=last["close"]-last["atr"]*1.5
  tp=last["close"]+last["atr"]*2.0
 else: # BEAR
  zone_bottom=swing_low + swing_range*0.2
  zone_top=swing_low + swing_range*0.3
  zone_type="BEAR 0.2-0.3"
  signal="SELL" if zone_bottom <= last["close"] <= zone_top else "WAIT"
  sl=last["close"]+last["atr"]*1.5
  tp=last["close"]-last["atr"]*2.0

 return signal,sl,tp,zone_top,zone_bottom,df,swing_high,swing_low

# SIDEBAR
with st.sidebar:
 st.header("SCALPER SETTINGS")
 mkt=st.selectbox("Market",list(M.keys()),index=0)
 if mkt!=st.session_state["last_mkt"]: st.session_state["mkts"]=[]; st.session_state["last_mkt"]=mkt
 size=st.number_input("Lot Size max 0.15",0.01,0.15,0.05,0.01)
 st.divider()
 if st.session_state["con"]: st.success("IG Connected")
 else:
  if st.button("Connect IG Demo",use_container_width=True):
   ok,msg,data=login()
   if ok: st.session_state["con"]=True; st.session_state["cst"]=data["cst"]; st.session_state["xsec"]=data["xsec"]; st.rerun()
   else: st.error(msg)

if not st.session_state["con"]: st.warning("Connect IG sidebar"); st.stop()

term=M[mkt][1]
if st.button(f"Find {mkt} EPIC",use_container_width=True):
 ok,err,res=search(term)
 if ok:
  found=[]
  for x in res:
   epic=x.get("epic","") or x.get("instrument",{}).get("epic","")
   name=x.get("instrumentName","") or x.get("instrument",{}).get("name","")
   if epic: found.append({"epic":epic,"name":name})
  st.session_state["mkts"]=found; st.success(f"Found {len(found)}")
if not st.session_state["mkts"]: st.info("Tap Find EPIC"); st.stop()

epics=[x["epic"] for x in st.session_state["mkts"]]
labs=[f"{x['epic']} | {x['name']}" for x in st.session_state["mkts"]]
idx=st.selectbox("Pick EPIC",range(len(epics)),format_func=lambda i: labs[i]); epic=epics[idx]

try:
 sym=M[mkt][0]
 components.html(f"""<div id="tv" style="height:350px;"></div><script src="https://s3.tradingview.com/tv.js"></script><script>new TradingView.widget({{"autosize":true,"symbol":"{sym}","interval":"5","timezone":"Africa/Johannesburg","theme":"dark","style":"1","container_id":"tv"}});</script>""",height=370)
except: pass

st.subheader(f"SUPERTREND FIB SCALPER {mkt}")

b1,b2,b3,b4=st.columns(4)
with b1: scan=st.button("SCAN NOW",use_container_width=True,type="primary")
with b2: auto=st.button("START AUTO",use_container_width=True)
with b3: stop=st.button("STOP BOT",use_container_width=True)
with b4: close_btn=st.button("CLOSE ALL",use_container_width=True)

if close_btn:
 r=close_all_for_epic(epic); st.success(f"Closed {r}"); st.balloons()
if stop: st.session_state["running"]=False; st.warning("Bot stopped")

def run_scan():
 df=get_prices(epic)
 if df is None: st.error("Price fail"); return
 signal,sl,tp,zt,zb,full,sh,slw=fib_02_03_signal(df)
 last=full.iloc[-1]
 st.divider()
 c1,c2,c3,c4=st.columns(4)
 c1.metric("Price",f"{last['close']:.2f}"); c2.metric("Supertrend", "BULL BUY ONLY" if last["st_dir"]==1 else "BEAR SELL ONLY")
 c3.metric("Signal",signal); c4.metric("ATR",f"{last['atr']:.4f}")
 st.write(f"Swing High {sh:.2f} Low {slw:.2f} Range {sh-slw:.2f}")
 st.write(f"Fib 0.2-0.3 Block: {zb:.2f} to {zt:.2f} (20-30% zone in supertrend dir)")
 if signal!="WAIT":
  st.success(f"INSIDE FIB BLOCK! {signal} NOW Entry {last['close']:.2f} SL {sl:.2f} TP {tp:.2f}")
  pos=get_positions(); this=[p for p in pos if p.get("market",{}).get("epic")==epic]
  if not this:
   code,txt=place(epic,signal,size,sl,tp)
   if code==200: st.success(f"SCALP OPENED {signal} {size}"); st.balloons()
   else: st.error(f"Failed {code} {txt[:200]}")
  else: st.warning(f"Already {len(this)} pos open - close first")
 else:
  st.warning(f"WAIT - Price {last['close']:.2f} not inside Fib 0.2-0.3 block {zb:.2f}-{zt:.2f} yet - but this comes fast for scalper")
 st.line_chart(full.set_index("time")[["close","supertrend"]].tail(100))

if scan: run_scan()
if auto:
 st.session_state["running"]=True
 run_scan()

if st.session_state["running"]:
 st.info("BOT ONLINE - Scanning every 15 sec - Mathematical scalps")
 ph=st.empty()
 while st.session_state["running"]:
  time.sleep(15)
  df=get_prices(epic)
  if df is None: continue
  signal,sl,tp,zt,zb,full,sh,slw=fib_02_03_signal(df)
  last=full.iloc[-1]
  pos=get_positions(); this=[p for p in pos if p.get("market",{}).get("epic")==epic]
  if not this and signal!="WAIT":
   code,txt=place(epic,signal,size,sl,tp)
   with ph.container(): st.write(f"{time.strftime('%H:%M:%S')} AUTO SCALP {signal} {last['close']:.2f} inside Fib block {zb:.2f}-{zt:.2f} -> {code}")
  st.rerun()
