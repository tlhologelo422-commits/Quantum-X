import os, pandas as pd, numpy as np, requests, time
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="FIB V11.2 FIX", layout="wide")
st.title("SUPERTREND FIB V11.2 - FIXED IG PRICE FETCH")
st.caption("Tries 5M, 15M, 1H - Debug panel - Max 0.15")

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
  if r.status_code!=200: return False,r.text[:400],None
  return True,"OK",{"cst":r.headers.get("CST"),"xsec":r.headers.get("X-SECURITY-TOKEN")}
 except Exception as e: return False,str(e),None
def search(term):
 try:
  r=requests.get(f"{IG}/markets",headers=hdrs("1"),params={"searchTerm":term},timeout=20)
  if r.status_code!=200: return False,f"Search {r.status_code} {r.text[:200]}",[]
  return True,"",r.json().get("markets",[])
 except Exception as e: return False,str(e),[]

def get_prices_robust(epic):
 # Try multiple resolutions - IG sometimes has no 5M for some epics
 for res in ["MINUTE_5","MINUTE_15","MINUTE_30","HOUR"]:
  url=f"{IG}/prices/{epic}/{res}/200"
  try:
   r=requests.get(url,headers=hdrs("3"),timeout=20) # v3 is better for prices
   if r.status_code!=200:
    # try v2
    r=requests.get(url,headers=hdrs("2"),timeout=20)
   if r.status_code!=200:
    st.write(f"Debug {res} failed {r.status_code} {r.text[:150]}")
    continue
   plist=r.json().get("prices",[])
   if not plist or len(plist)<20:
    st.write(f"Debug {res} got {len(plist)} candles - too few")
    continue
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
   if len(df)<25: continue
   df["time"]=pd.to_datetime(df["time"],utc=True,errors="coerce")
   df=df.sort_values("time").reset_index(drop=True)
   st.success(f"Got {len(df)} candles using {res}")
   return df,res
  except Exception as e:
   st.write(f"Debug {res} exception {e}")
   continue
 return None,None

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
   prev_st=st_line.iloc[i-1]
   if pd.isna(prev_st): prev_st=lower.iloc[i-1]
   if df["close"].iloc[i] <= prev_st:
    st_line.iloc[i]=upper.iloc[i]; direction.iloc[i]=-1
   else:
    st_line.iloc[i]=lower.iloc[i]; direction.iloc[i]=1
   if direction.iloc[i]==1 and not pd.isna(st_line.iloc[i-1]) and st_line.iloc[i] < st_line.iloc[i-1]:
    st_line.iloc[i]=st_line.iloc[i-1]
   if direction.iloc[i]==-1 and not pd.isna(st_line.iloc[i-1]) and st_line.iloc[i] > st_line.iloc[i-1]:
    st_line.iloc[i]=st_line.iloc[i-1]
 df["supertrend"]=st_line; df["st_dir"]=direction; df["atr"]=atr
 return df

def fib_02_03_signal(df):
 if df is None or len(df)<30: return "WAIT",None,None,None,None,None,0,0,"df empty"
 df=supertrend(df,10,3.0)
 if df is None: return "WAIT",None,None,None,None,None,0,0,"st fail"
 last=df.iloc[-1]
 if pd.isna(last["atr"]) or pd.isna(last["supertrend"]): return "WAIT",None,None,None,None,df,0,0,"nan"
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
 mkt=st.selectbox("Market",list(M.keys()),index=0)
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
term=M[mkt][1]
if st.button(f"Find {mkt} EPIC",use_container_width=True):
 ok,err,res=search(term)
 if ok:
  found=[]
  for x in res:
   epic=x.get("epic","") or x.get("instrument",{}).get("epic","")
   name=x.get("instrumentName","") or x.get("instrument",{}).get("name","")
   if epic: found.append({"epic":epic,"name":name})
  st.session_state["mkts"]=found; st.success(f"Found {len(found)}"); st.write(found[:5])
 else: st.error(err)
if not st.session_state["mkts"]: st.info("Tap Find EPIC"); st.stop()

epics=[x["epic"] for x in st.session_state["mkts"]]
labs=[f"{x['epic']} | {x['name']}" for x in st.session_state["mkts"]]
idx=st.selectbox("Pick EPIC",range(len(epics)),format_func=lambda i: labs[i]); epic=epics[idx]
st.info(f"Selected {epic}")

try:
 sym=M[mkt][0]
 components.html(f"""<div id="tv" style="height:350px;"></div><script src="https://s3.tradingview.com/tv.js"></script><script>new TradingView.widget({{"autosize":true,"symbol":"{sym}","interval":"5","timezone":"Africa/Johannesburg","theme":"dark","style":"1","container_id":"tv"}});</script>""",height=370)
except: pass

st.subheader(f"SUPERTREND FIB {mkt}")

b1,b2,b3,b4=st.columns(4)
with b1: scan=st.button("SCAN NOW",use_container_width=True,type="primary")
with b2: auto=st.button("START AUTO",use_container_width=True)
with b3: stop=st.button("STOP BOT",use_container_width=True)
with b4: close_btn=st.button("CLOSE ALL",use_container_width=True)

if close_btn:
 r=close_all_for_epic(epic); st.success(f"Closed {r}"); st.balloons()
if stop: st.session_state["running"]=False; st.warning("Stopped")

def run_scan():
 st.write(f"Trying to fetch candles for {epic}...")
 df,res=get_prices_robust(epic)
 if df is None:
  st.error(f"IG returned 0 candles for {epic}. Try different EPIC from list - pick one ending with.IP or.CFD. Also wait 30 sec (IG rate limit).")
  st.info("TIP: In Find results, pick EPIC with MINI or CFD in name - those have 5M data")
  return
 sig,sl,tp,zt,zb,full,sh,slw,msg=fib_02_03_signal(df)
 if full is None:
  st.error(f"Calc failed {msg}"); return
 last=full.iloc[-1]
 st.divider()
 c1,c2,c3,c4=st.columns(4)
 c1.metric("Price",f"{last['close']:.2f}"); c2.metric("Supertrend", "BULL" if last["st_dir"]==1 else "BEAR"); c3.metric("Signal",sig); c4.metric("TF used",res)
 st.write(f"Swing High {sh:.2f} Low {slw:.2f}")
 if zt: st.write(f"Fib 0.2-0.3 Block {zb:.2f} to {zt:.2f}")
 if sig!="WAIT":
  st.success(f"INSIDE BLOCK! {sig} Entry {last['close']:.2f} SL {sl:.2f} TP {tp:.2f}")
  pos=get_positions(); this=[p for p in pos if p.get("market",{}).get("epic")==epic]
  if not this:
   code,txt=place(epic,sig,size,sl,tp)
   if code==200: st.success(f"OPENED {sig} {size}"); st.balloons()
   else: st.error(f"Failed {code} {txt[:300]}")
  else: st.warning(f"Already {len(this)} open")
 else:
  st.warning(f"WAIT - Price {last['close']:.2f} outside block {zb:.2f}-{zt:.2f}")
 st.line_chart(full.set_index("time")[["close","supertrend"]].tail(100))

if scan: run_scan()
if auto:
 st.session_state["running"]=True
 run_scan()

if st.session_state["running"]:
 st.info("BOT ONLINE every 20 sec")
 ph=st.empty()
 while st.session_state["running"]:
  time.sleep(20)
  df,res=get_prices_robust(epic)
  if df is None: continue
  sig,sl,tp,zt,zb,full,sh,slw,msg=fib_02_03_signal(df)
  if full is None: continue
  pos=get_positions(); this=[p for p in pos if p.get("market",{}).get("epic")==epic]
  if not this and sig!="WAIT":
   code,txt=place(epic,sig,size,sl,tp)
   with ph.container(): st.write(f"{time.strftime('%H:%M:%S')} AUTO {sig} {full.iloc[-1]['close']:.2f} -> {code}")
  st.rerun()
