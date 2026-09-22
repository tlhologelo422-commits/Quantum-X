import os, pandas as pd, numpy as np, requests, time
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="INSTANT V14 PnL", layout="wide")
st.title("INSTANT SCALPER V14 - WITH PnL + FORCE TRADE")
st.caption("Fixes trade not opening + Shows live PnL in app")

IG = "https://demo-api.ig.com/gateway/deal"
YAHOO = {"XAU/USD":"GC=F","EUR/USD":"EURUSD=X","GBP/USD":"GBPUSD=X","NAS100":"^NDX","US30":"^DJI","BTC/USD":"BTC-USD"}
IG_SEARCH = {"XAU/USD":"Gold","EUR/USD":"EUR/USD","GBP/USD":"GBP/USD","NAS100":"US Tech 100","US30":"US 30","BTC/USD":"Bitcoin"}
TV = {"XAU/USD":"OANDA:XAUUSD","EUR/USD":"FX:EURUSD","GBP/USD":"FX:GBPUSD","NAS100":"OANDA:NAS100USD","US30":"OANDA:US30USD","BTC/USD":"BINANCE:BTCUSD"}

for k in ["con","cst","xsec","mkts","last_mkt","running","last_trade_time","last_error"]:
 if k not in st.session_state: st.session_state[k]=False if k in ["con","running"] else [] if k=="mkts" else 0 if k=="last_trade_time" else None
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
  if r.status_code!=200: return False,f"{r.status_code}",[]
  return True,"",r.json().get("markets",[])
 except Exception as e: return False,str(e),[]
def get_positions():
 try:
  r=requests.get(f"{IG}/positions",headers=hdrs("2"),timeout=15)
  if r.status_code!=200: return []
  return r.json().get("positions",[])
 except: return []
def get_accounts():
 try:
  r=requests.get(f"{IG}/accounts",headers=hdrs("1"),timeout=15)
  if r.status_code!=200: return None
  return r.json()
 except: return None
def place_with_debug(epic,direction,size,sl=None,tp=None):
 url=f"{IG}/positions/otc"
 payload={"epic":epic,"expiry":"-","direction":direction,"size":str(size),"orderType":"MARKET","currencyCode":"USD","forceOpen":True,"guaranteedStop":False}
 if sl: payload["stopLevel"]=str(round(float(sl),1))
 if tp: payload["limitLevel"]=str(round(float(tp),1))
 try:
  r=requests.post(url,headers=hdrs("2"),json=payload,timeout=15)
  if r.status_code==200:
   return r.status_code,r.text,"with SL/TP OK"
  payload2={"epic":epic,"expiry":"-","direction":direction,"size":str(size),"orderType":"MARKET","currencyCode":"USD","forceOpen":True,"guaranteedStop":False}
  r2=requests.post(url,headers=hdrs("2"),json=payload2,timeout=15)
  if r2.status_code==200:
   return r2.status_code, r2.text + f" | First fail {r.text[:200]}", "without SL/TP OK"
  return r2.status_code, f"Both failed | With SL: {r.text[:300]} | Without SL: {r2.text[:300]}", "both failed"
 except Exception as e: return 0,str(e),"exception"
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

@st.cache_data(ttl=30)
def get_yahoo_candles(yahoo_symbol):
 try:
  import yfinance as yf
  df=yf.download(yahoo_symbol, period="2d", interval="5m", progress=False, auto_adjust=True)
  if df is None or len(df)<30: return None
  if isinstance(df.columns, pd.MultiIndex): df.columns=df.columns.get_level_values(0)
  df=df.reset_index()
  df=df.rename(columns={"Datetime":"time","Open":"open","High":"high","Low":"low","Close":"close"})
  df=df.dropna()
  if len(df)<30: return None
  return df
 except: return None

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
# --- END OF PART 1 ---
def instant_signal(df):
 if df is None or len(df)<30: return "WAIT",None,None,None,None,None,0,0,"no df"
 df=supertrend(df,10,3.0)
 if df is None: return "WAIT",None,None,None,None,None,0,0,"st fail"
 last=df.iloc[-1]
 if pd.isna(last["atr"]): return "WAIT",None,None,None,None,df,0,0,"atr nan"
 bias = "BUY" if last["st_dir"]==1 else "SELL"
 look=df.tail(20); sh=look["high"].max(); slw=look["low"].min(); rng=sh-slw
 if bias=="BUY":
  zt=sh - rng*0.2; zb=sh - rng*0.3
  sl=last["close"]-last["atr"]*2.5; tp=last["close"]+last["atr"]*3.5
 else:
  zb=slw + rng*0.2; zt=slw + rng*0.3
  sl=last["close"]+last["atr"]*2.5; tp=last["close"]-last["atr"]*3.5
 return bias,sl,tp,zt,zb,df,sh,slw,"instant"

with st.sidebar:
 st.header("INSTANT V14")
 mkt=st.selectbox("Market",list(YAHOO.keys()),index=3)
 if mkt!=st.session_state["last_mkt"]: st.session_state["mkts"]=[]; st.session_state["last_mkt"]=mkt
 size=st.number_input("Lot max 0.15",0.01,0.15,0.05,0.01)
 cooldown=st.slider("Cooldown sec",30,300,90)
 st.divider()
 if st.session_state["con"]: st.success("IG Connected")
 else:
  if st.button("Connect IG Demo",use_container_width=True):
   ok,msg,data=login()
   if ok: st.session_state["con"]=True; st.session_state["cst"]=data["cst"]; st.session_state["xsec"]=data["xsec"]; st.rerun()
   else: st.error(msg)

if not st.session_state["con"]: st.warning("Connect IG"); st.stop()
term=IG_SEARCH[mkt]
if st.button(f"Find {mkt} EPIC",use_container_width=True):
 ok,err,res=search(term)
 if ok:
  found=[]
  for x in res:
   epic=x.get("epic","") or x.get("instrument",{}).get("epic","")
   name=x.get("instrumentName","") or x.get("instrument",{}).get("name","")
   if epic: found.append({"epic":epic,"name":name})
  st.session_state["mkts"]=found; st.success(f"Found {len(found)}")
 else: st.error(err)
if not st.session_state["mkts"]: st.info("Tap Find EPIC"); st.stop()

epics=[x["epic"] for x in st.session_state["mkts"]]
labs=[f"{x['epic']} | {x['name']}" for x in st.session_state["mkts"]]
idx=st.selectbox("Pick EPIC",range(len(epics)),format_func=lambda i: labs[i]); epic=epics[idx]

st.divider()
st.subheader("💰 LIVE PnL DASHBOARD - No need IG app")
acc=get_accounts()
pos=get_positions()
if acc:
 try:
  bal=acc.get("accounts",[])[0].get("balance",{}).get("balance",0)
  st.metric("Account Balance", f"${bal}")
 except: pass

if pos:
 total_pnl=0
 for p in pos:
  po=p.get("position",{}); mkt_name=p.get("market",{}).get("instrumentName","")
  epic_p=p.get("market",{}).get("epic",""); direction=po.get("direction",""); size_p=po.get("size","")
  level=po.get("openLevel",""); pnl=po.get("profit",0) or 0
  try: total_pnl+=float(pnl)
  except: pass
  color="green" if float(pnl)>=0 else "red"
  st.write(f"**{mkt_name}** {epic_p} | {direction} {size_p} @ {level} | PnL: :{color}[${pnl}]")
 st.metric("Total Open PnL", f"${total_pnl:.2f}", delta=f"{total_pnl:.2f}")
 this=[p for p in pos if p.get("market",{}).get("epic")==epic]
 if this: st.info(f"{len(this)} open on {epic}")
 else: st.write(f"No open on {epic} - ready")
else:
 st.write("No open positions - ready to scalp")

st.divider()
try:
 sym=TV[mkt]
 components.html(f"""<div id="tv" style="height:350px;"></div><script src="https://s3.tradingview.com/tv.js"></script><script>new TradingView.widget({{"autosize":true,"symbol":"{sym}","interval":"5","timezone":"Africa/Johannesburg","theme":"dark","style":"1","container_id":"tv"}});</script>""",height=370)
except: pass

st.subheader(f"Scalper {mkt} - {epic}")
b1,b2,b3,b4,b5=st.columns(5)
with b1: scan=st.button("TRADE NOW",use_container_width=True,type="primary")
with b2: force=st.button("FORCE TRADE (no SL)",use_container_width=True)
with b3: auto=st.button("START AUTO",use_container_width=True)
with b4: stop=st.button("STOP",use_container_width=True)
with b5: close_btn=st.button("CLOSE ALL",use_container_width=True)

if close_btn:
 r=close_all_for_epic(epic); st.success(f"Closed {r}"); st.balloons(); time.sleep(1); st.rerun()
if stop: st.session_state["running"]=False; st.warning("Stopped")

def run_trade(force_no_sl=False):
 ysym=YAHOO[mkt]
 df=get_yahoo_candles(ysym)
 if df is None: st.error("Yahoo no candles"); return
 bias,sl,tp,zt,zb,full,sh,slw,msg=instant_signal(df)
 if full is None: st.error(f"Calc fail {msg}"); return
 last=full.iloc[-1]
 c1,c2,c3,c4=st.columns(4)
 c1.metric("Price",f"{last['close']:.2f}"); c2.metric("Bias", "BULL BUY" if last["st_dir"]==1 else "BEAR SELL")
 c3.metric("ATR", f"{last['atr']:.2f}"); c4.metric("Signal", bias)
 st.write(f"Fib Block {zb:.2f}-{zt:.2f} | SL {sl:.2f} TP {tp:.2f}")
 now=time.time()
 if now - st.session_state["last_trade_time"] < 20:
  st.warning(f"Wait {int(20-(now - st.session_state['last_trade_time']))}s anti-spam")
 else:
  pos=get_positions(); this=[p for p in pos if p.get("market",{}).get("epic")==epic]
  if this:
   st.warning(f"Already have trade - close first")
  else:
   if force_no_sl: sl=None; tp=None; st.warning("FORCING WITHOUT SL/TP")
   code,txt,method=place_with_debug(epic,bias,size,sl,tp)
   st.write(f"Debug: {method}"); st.write(f"IG {code}: {txt[:500]}")
   if code==200:
    st.success(f"✅ TRADE PLACED {bias} {size}"); st.session_state["last_trade_time"]=now; st.balloons()
   else:
    st.error(f"❌ Failed {code}"); st.session_state["last_error"]=txt
 if full is not None:
  st.line_chart(full.set_index("time")[["close","supertrend"]].tail(100))

if scan: run_trade(force_no_sl=False)
if force: run_trade(force_no_sl=True)
if auto:
 st.session_state["running"]=True
 run_trade(force_no_sl=False)

if st.session_state["running"]:
 st.info("BOT AUTO ONLINE - Trading every 30s if no position")
 ph=st.empty()
 while st.session_state["running"]:
  time.sleep(30)
  df=get_yahoo_candles(YAHOO[mkt])
  if df is None: continue
  bias,sl,tp,zt,zb,full,sh,slw,msg=instant_signal(df)
  if full is None: continue
  pos=get_positions(); this=[p for p in pos if p.get("market",{}).get("epic")==epic]
  if this:
   with ph.container(): st.write(f"{time.strftime('%H:%M:%S')} Has position - waiting")
   continue
  now=time.time()
  if now - st.session_state["last_trade_time"] < 90: continue
  code,txt,method=place_with_debug(epic,bias,size,sl,tp)
  with ph.container(): st.write(f"{time.strftime('%H:%M:%S')} AUTO {bias} {full.iloc[-1]['close']:.2f} {method} -> {code}")
  st.session_state["last_trade_time"]=now
  st.rerun()
