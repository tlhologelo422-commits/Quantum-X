import os, pandas as pd, numpy as np, requests, time
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="FIB V12 YAHOO", layout="wide")
st.title("SUPERTREND FIB V12 - YAHOO CANDLES - NO IG LIMIT")
st.caption("Candles from Yahoo FREE + Trade on IG + Max 0.15 + CLOSE")

IG = "https://demo-api.ig.com/gateway/deal"

# Yahoo mapping to avoid IG 403
YAHOO = {
 "XAU/USD":"GC=F", # Gold futures = same price as XAU
 "EUR/USD":"EURUSD=X",
 "GBP/USD":"GBPUSD=X",
 "NAS100":"^NDX", # Nasdaq 100
 "US30":"^DJI",
 "BTC/USD":"BTC-USD"
}
IG_SEARCH = {"XAU/USD":"Gold","EUR/USD":"EUR/USD","GBP/USD":"GBP/USD","NAS100":"US Tech 100","US30":"US 30","BTC/USD":"Bitcoin"}
TV = {"XAU/USD":"OANDA:XAUUSD","EUR/USD":"FX:EURUSD","GBP/USD":"FX:GBPUSD","NAS100":"OANDA:NAS100USD","US30":"OANDA:US30USD","BTC/USD":"BINANCE:BTCUSD"}

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
  if r.status_code!=200: return False,r.text[:400],None
  return True,"OK",{"cst":r.headers.get("CST"),"xsec":r.headers.get("X-SECURITY-TOKEN")}
 except Exception as e: return False,str(e),None
def search(term):
 try:
  r=requests.get(f"{IG}/markets",headers=hdrs("1"),params={"searchTerm":term},timeout=20)
  if r.status_code!=200: return False,f"Search {r.status_code}",[]
  return True,"",r.json().get("markets",[])
 except Exception as e: return False,str(e),[]
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

# YAHOO CANDLES - NO IG LIMIT
@st.cache_data(ttl=60)
def get_yahoo_candles(yahoo_symbol):
 try:
  import yfinance as yf
  df=yf.download(yahoo_symbol, period="5d", interval="5m", progress=False, auto_adjust=True)
  if df is None or len(df)<30: return None
  if isinstance(df.columns, pd.MultiIndex): df.columns=df.columns.get_level_values(0)
  df=df.reset_index()
  # yfinance returns Datetime, Open, High, Low, Close
  df=df.rename(columns={"Datetime":"time","Open":"open","High":"high","Low":"low","Close":"close"})
  # ensure columns
  for c in ["open","high","low","close"]:
   if c not in df.columns: return None
  df=df.dropna()
  if len(df)<30: return None
  return df
 except Exception as e:
  st.write(f"Yahoo error {e} - installing yfinance...")
  return None

def supertrend(df, period=10, multiplier=3.0):
 if df is None or len(df)<period+5: return None
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
   st_line.iloc[i]=lower.iloc[i] if not pd.isna(lower.iloc[i]) else hl2.iloc[i]
   direction.iloc[i]=1
  else:
   prev=st_line.iloc[i-1]
   if pd.isna(prev): prev=lower.iloc[i-1]
   if df["close"].iloc[i] <= prev:
    st_line.iloc[i]=upper.iloc[i]; direction.iloc[i]=-1
   else:
    st_line.iloc[i]=lower.iloc[i]; direction.iloc[i]=1
   if direction.iloc[i]==1 and st_line.iloc[i] < st_line.iloc[i-1]: st_line.iloc[i]=st_line.iloc[i-1]
   if direction.iloc[i]==-1 and st_line.iloc[i] > st_line.iloc[i-1]: st_line.iloc[i]=st_line.iloc[i-1]
 df["supertrend"]=st_line; df["st_dir"]=direction; df["atr"]=atr
 return df

def fib_signal(df):
 if df is None or len(df)<30: return "WAIT",None,None,None,None,None,0,0,"no df"
 df=supertrend(df,10,3.0)
 if df is None: return "WAIT",None,None,None,None,None,0,0,"st fail"
 last=df.iloc[-1]
 if pd.isna(last["atr"]): return "WAIT",None,None,None,None,df,0,0,"atr nan"
 look=df.tail(20); sh=look["high"].max(); slw=look["low"].min(); rng=sh-slw
 if rng==0: return "WAIT",None,None,None,None,df,0,0,"range 0"
 if last["st_dir"]==1:
  zt=sh - rng*0.2; zb=sh - rng*0.3
  sig="BUY" if zb <= last["close"] <= zt else "WAIT"
  sl=last["close"]-last["atr"]*1.5; tp=last["close"]+last["atr"]*2.0
 else:
  zb=slw + rng*0.2; zt=slw + rng*0.3
  sig="SELL" if zb <= last["close"] <= zt else "WAIT"
  sl=last["close"]+last["atr"]*1.5; tp=last["close"]-last["atr"]*2.0
 return sig,sl,tp,zt,zb,df,sh,slw,"ok"

with st.sidebar:
 st.header("SCALPER")
 mkt=st.selectbox("Market",list(YAHOO.keys()),index=3)
 if mkt!=st.session_state["last_mkt"]: st.session_state["mkts"]=[]; st.session_state["last_mkt"]=mkt
 size=st.number_input("Lot max 0.15",0.01,0.15,0.05,0.01)
 st.divider()
 if st.session_state["con"]: st.success("IG Connected")
 else:
  if st.button("Connect IG Demo",use_container_width=True):
   ok,msg,data=login()
   if ok: st.session_state["con"]=True; st.session_state["cst"]=data["cst"]; st.session_state["xsec"]=data["xsec"]; st.rerun()
   else: st.error(msg)

if not st.session_state["con"]: st.warning("Connect IG"); st.stop()
term=IG_SEARCH[mkt]
if st.button(f"Find {mkt} EPIC for trading",use_container_width=True):
 ok,err,res=search(term)
 if ok:
  found=[]
  for x in res:
   epic=x.get("epic","") or x.get("instrument",{}).get("epic","")
   name=x.get("instrumentName","") or x.get("instrument",{}).get("name","")
   if epic: found.append({"epic":epic,"name":name})
  st.session_state["mkts"]=found; st.success(f"Found {len(found)}")
 else: st.error(err)
if not st.session_state["mkts"]: st.info("Tap Find EPIC first - need EPIC to trade, candles from Yahoo"); st.stop()

epics=[x["epic"] for x in st.session_state["mkts"]]
labs=[f"{x['epic']} | {x['name']}" for x in st.session_state["mkts"]]
idx=st.selectbox("Pick IG EPIC to trade",range(len(epics)),format_func=lambda i: labs[i]); epic=epics[idx]
st.info(f"Trading {epic} | Candles from Yahoo {YAHOO[mkt]} (no 403)")

try:
 sym=TV[mkt]
 components.html(f"""<div id="tv" style="height:350px;"></div><script src="https://s3.tradingview.com/tv.js"></script><script>new TradingView.widget({{"autosize":true,"symbol":"{sym}","interval":"5","timezone":"Africa/Johannesburg","theme":"dark","style":"1","container_id":"tv"}});</script>""",height=370)
except: pass

st.subheader(f"SUPERTREND FIB {mkt} - Yahoo fix")

b1,b2,b3,b4=st.columns(4)
with b1: scan=st.button("SCAN NOW",use_container_width=True,type="primary")
with b2: auto=st.button("START AUTO",use_container_width=True)
with b3: stop=st.button("STOP BOT",use_container_width=True)
with b4: close_btn=st.button("CLOSE ALL",use_container_width=True)

if close_btn:
 r=close_all_for_epic(epic); st.success(f"Closed {r}"); st.balloons()
if stop: st.session_state["running"]=False; st.warning("Stopped")

def run_scan():
 ysym=YAHOO[mkt]
 st.write(f"Getting candles from Yahoo {ysym} (free, no IG limit)...")
 df=get_yahoo_candles(ysym)
 if df is None or len(df)<30:
  st.error("Yahoo failed - check internet - trying pip install yfinance")
  # try install hint
  st.code("Add to requirements.txt:\nyfinance\npandas")
  return
 sig,sl,tp,zt,zb,full,sh,slw,msg=fib_signal(df)
 if full is None:
  st.error(f"Calc failed {msg}"); return
 last=full.iloc[-1]
 st.divider()
 c1,c2,c3,c4=st.columns(4)
 c1.metric("Price Yahoo",f"{last['close']:.2f}"); c2.metric("Supertrend", "BULL" if last["st_dir"]==1 else "BEAR"); c3.metric("Signal",sig); c4.metric("Candles",len(full))
 st.write(f"Swing High {sh:.2f} Low {slw:.2f}")
 if zt: st.write(f"Fib 0.2-0.3 Block {zb:.2f} to {zt:.2f}")
 if sig!="WAIT":
  st.success(f"INSIDE BLOCK! {sig} NOW SL {sl:.2f} TP {tp:.2f}")
  pos=get_positions(); this=[p for p in pos if p.get("market",{}).get("epic")==epic]
  if not this:
   code,txt=place(epic,sig,size,sl,tp)
   if code==200: st.success(f"OPENED {sig} {size} on IG {epic}"); st.balloons()
   else: st.error(f"IG trade failed {code} {txt[:300]}")
  else: st.warning(f"Already {len(this)} open")
 else:
  st.warning(f"WAIT - Price {last['close']:.2f} outside block {zb:.2f}-{zt:.2f} - scalper catches next candle")
 st.line_chart(full.set_index("time" if "time" in full.columns else full.index)[["close","supertrend"]].tail(100))

if scan: run_scan()
if auto:
 st.session_state["running"]=True
 run_scan()

if st.session_state["running"]:
 st.info("BOT ONLINE every 30 sec - Yahoo candles")
 ph=st.empty()
 while st.session_state["running"]:
  time.sleep(30)
  df=get_yahoo_candles(YAHOO[mkt])
  if df is None: continue
  sig,sl,tp,zt,zb,full,sh,slw,msg=fib_signal(df)
  if full is None: continue
  pos=get_positions(); this=[p for p in pos if p.get("market",{}).get("epic")==epic]
  if not this and sig!="WAIT":
   code,txt=place(epic,sig,size,sl,tp)
   with ph.container(): st.write(f"{time.strftime('%H:%M:%S')} AUTO {sig} {full.iloc[-1]['close']:.2f} -> {code}")
  st.rerun()
