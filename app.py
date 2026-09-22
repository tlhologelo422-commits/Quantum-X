import os, pandas as pd, numpy as np, requests, time
import streamlit as st

st.set_page_config(page_title="PURE HEDGE V8.1", layout="wide")
st.title("PURE HEDGE MARTINGALE V8.1 - ANY PRICE - ONLINE - MAX 0.15")
st.caption("No indicators - Enters at any price - Hedging as market moves")

IG = "https://demo-api.ig.com/gateway/deal"

M = {
 "XAU/USD": "Gold",
 "EUR/USD": "EUR/USD",
 "GBP/USD": "GBP/USD",
 "NAS100": "US Tech 100",
 "US30": "US 30",
 "BTC/USD": "Bitcoin",
}

for k in ["con","cst","xsec","mkts","last_mkt","running"]:
 if k not in st.session_state:
  st.session_state[k]=False if k in ["con","running"] else None if k not in ["mkts"] else []

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

def get_prices(epic,res="MINUTE_5",num=100):
 url=f"{IG}/prices/{epic}/{res}/{num}"
 try:
  r=requests.get(url,headers=hdrs("2"),timeout=25)
  if r.status_code!=200:
   return None,None,None
  plist=r.json().get("prices",[])
  rows=[]
  for p in plist:
   def mid(v):
    if not isinstance(v,dict):
     return None
    b=v.get("bid")
    a=v.get("ask")
    if b is None:
     return float(a) if a else None
    if a is None:
     return float(b)
    return (float(b)+float(a))/2
   rows.append({"close":mid(p.get("closePrice")),"high":mid(p.get("highPrice")),"low":mid(p.get("lowPrice"))})
  df=pd.DataFrame(rows).dropna()
  if len(df)<15:
   return None,None,None
  hl=df["high"]-df["low"]
  tr=hl.rolling(14).mean().iloc[-1]
  curr=df["close"].iloc[-1]
  return curr,tr,df
 except:
  return None,None,None

def get_positions():
 try:
  r=requests.get(f"{IG}/positions",headers=hdrs("2"),timeout=15)
  if r.status_code!=200:
   return []
  return r.json().get("positions",[])
 except:
  return []

def place(epic,direction,size):
 url=f"{IG}/positions/otc"
 payload={"epic":epic,"expiry":"-","direction":direction,"size":str(size),"orderType":"MARKET","currencyCode":"USD","forceOpen":True,"guaranteedStop":False}
 try:
  r=requests.post(url,headers=hdrs("2"),json=payload,timeout=15)
  return r.status_code,r.text
 except Exception as e:
  return 0,str(e)

with st.sidebar:
 st.header("HEDGE SETTINGS")
 mkt=st.selectbox("Market",list(M.keys()),index=3)
 if mkt!=st.session_state["last_mkt"]:
  st.session_state["mkts"]=[]
  st.session_state["last_mkt"]=mkt
 base=st.number_input("Base Lot",0.01,0.15,0.05,0.01)
 st.caption("Levels: 0.05 -> 0.08 -> 0.12 -> 0.15 MAX")
 mult=st.slider("Multiplier",1.2,2.0,1.6)
 hedge_atr=st.slider("Hedge every ATR x",0.5,3.0,1.0)
 profit_target=st.number_input("Close all when net profit >=",value=4.0)
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

if not st.session_state["con"]:
 st.warning("Connect IG Demo in sidebar")
 st.stop()

term=M[mkt]
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

if not st.session_state["mkts"]:
 st.info(f"Tap Find {mkt} EPIC first")
 st.stop()

epics=[x["epic"] for x in st.session_state["mkts"]]
labs=[f"{x['epic']} | {x['name']}" for x in st.session_state["mkts"]]
idx=st.selectbox("Pick EPIC",range(len(epics)),format_func=lambda i: labs[i],key=f"epic_{mkt}")
epic=epics[idx]
st.info(f"Bot will trade {epic} Lots max 0.15 - ANY PRICE")

col1,col2,col3=st.columns(3)
with col1:
 start=st.button("START BOT ONLINE",type="primary",use_container_width=True)
with col2:
 stop=st.button("STOP BOT",use_container_width=True)
with col3:
 check=st.button("CHECK POSITIONS",use_container_width=True)

if check:
 pos=get_positions()
 this=[p for p in pos if p.get("market",{}).get("epic")==epic]
 if not this:
  st.info("No positions for this epic")
 else:
  net=0
  for p in this:
   d=p.get("position",{}).get("direction")
   s=p.get("position",{}).get("size")
   lvl=p.get("position",{}).get("level")
   pnl=p.get("position",{}).get("profit")
   if pnl is not None:
    net+=float(pnl)
   st.write(f"{d} {s} Entry {lvl} PnL {pnl}")
  st.metric("Net PnL",f"${net:.2f}")

if stop:
 st.session_state["running"]=False
 st.warning("Bot stopped")

if start:
 st.session_state["running"]=True

if st.session_state["running"]:
 st.success(f"BOT ONLINE - {mkt} - Lots max 0.15 - Press STOP to stop")
 placeholder=st.empty()
 log_box=st.empty()
 logs=[]

 while st.session_state["running"]:
  curr,atr,df=get_prices(epic)
  if curr is None:
   placeholder.error("Price fail - retrying")
   time.sleep(5)
   continue

  all_pos=get_positions()
  this_pos=[p for p in all_pos if p.get("market",{}).get("epic")==epic]

  net_pnl=0
  total_buy=0
  total_sell=0
  avg_buy=0
  avg_sell=0

  for p in this_pos:
   pos=p.get("position",{})
   pnl=float(pos.get("profit") or 0)
   net_pnl+=pnl
   size=float(pos.get("size") or 0)
   level=float(pos.get("level") or curr)
   if pos.get("direction")=="BUY":
    total_buy+=size
    avg_buy+=level*size
   else:
    total_sell+=size
    avg_sell+=level*size

  if total_buy>0:
   avg_buy=avg_buy/total_buy
  if total_sell>0:
   avg_sell=avg_sell/total_sell

  levels=[base, round(base*mult,2), round(base*mult*1.4,2), 0.15]
  levels=[min(l,0.15) for l in levels]
  next_lot=levels[min(len(this_pos), len(levels)-1)]

  with placeholder.container():
   c1,c2,c3,c4=st.columns(4)
   c1.metric(f"{mkt} Price",f"{curr:.5f}")
   c2.metric("ATR",f"{atr:.5f}" if atr else "0")
   c3.metric("Net PnL",f"${net_pnl:.2f}")
   c4.metric("Positions",f"{len(this_pos)} Buy {total_buy} Sell {total_sell}")
   st.write(f"Avg BUY {avg_buy:.5f} Avg SELL {avg_sell:.5f} Next lot {next_lot} Levels {levels}")

  action="HOLD"

  if len(this_pos)==0:
   action=f"NO POS - ENTER BUY {base} AT ANY PRICE {curr:.2f}"
   code,txt=place(epic,"BUY",base)
   logs.append(f"{time.strftime('%H:%M:%S')} {action} -> {code}")
   log_box.write("\n".join(logs[-10:]))

  elif net_pnl>=profit_target:
   action=f"PROFIT ${net_pnl:.2f} >= ${profit_target} - CLOSE ALL MANUALLY IN IG APP"
   logs.append(f"{time.strftime('%H:%M:%S')} {action}")
   log_box.write("\n".join(logs[-10:]))

  else:
   if total_buy>total_sell:
    distance=avg_buy-curr
    if atr and distance>=(atr*hedge_atr):
     action=f"Price dropped {distance:.5f} vs avg buy - HEDGE SELL {next_lot}"
     code,txt=place(epic,"SELL",next_lot)
     logs.append(f"{time.strftime('%H:%M:%S')} {action} -> {code}")
     log_box.write("\n".join(logs[-10:]))
   elif total_sell>total_buy:
    distance=curr-avg_sell
    if atr and distance>=(atr*hedge_atr):
     action=f"Price rose {distance:.5f} vs avg sell - HEDGE BUY {next_lot}"
     code,txt=place(epic,"BUY",next_lot)
     logs.append(f"{time.strftime('%H:%M:%S')} {action} -> {code}")
     log_box.write("\n".join(logs[-10:]))
   else:
    action="Balanced hedge - waiting profit target"

  time.sleep(15)
  st.rerun()

else:
 st.info("Bot offline - Press START BOT ONLINE - It will enter at any price and hedge as market moves - Lots max 0.15")
