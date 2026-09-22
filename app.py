import os, pandas as pd, numpy as np, requests, time
import streamlit as st

st.set_page_config(page_title="PURE HEDGE MARTINGALE V8", layout="wide")
st.title("PURE HEDGE MARTINGALE V8 - ANY PRICE - ONLINE")
st.caption("No HMA No OB No FVG - Enters at any price - Lots max 0.15 - Always online")

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
   if n in st.secrets: return st.secrets[n]
  except: pass
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
  if r.status_code!=200: return False,r.text[:300],None
  return True,"OK",{"cst":r.headers.get("CST"),"xsec":r.headers.get("X-SECURITY-TOKEN")}
 except Exception as e: return False,str(e),None

def search(term):
 try:
  r=requests.get(f"{IG}/markets",headers=hdrs("1"),params={"searchTerm":term},timeout=20)
  if r.status_code!=200: return False,f"{r.status_code}",[]
  return True,"",r.json().get("markets",[])
 except Exception as e: return False,str(e),[]

def get_prices(epic,res="MINUTE_5",num=100):
 url=f"{IG}/prices/{epic}/{res}/{num}"
 try:
  r=requests.get(url,headers=hdrs("2"),timeout=25)
  if r.status_code!=200: return None,None,None
  plist=r.json().get("prices",[])
  rows=[]
  for p in plist:
   def mid(v):
    if not isinstance(v,dict): return None
    b=v.get("bid"); a=v.get("ask")
    if b is None: return float(a) if a else None
    if a is None: return float(b)
    return (float(b)+float(a))/2
   rows.append({"close":mid(p.get("closePrice")),"high":mid(p.get("highPrice")),"low":mid(p.get("lowPrice"))})
  df=pd.DataFrame(rows).dropna()
  hl=df["high"]-df["low"]
  tr=hl.rolling(14).mean().iloc[-1]
  curr=df["close"].iloc[-1]
  return curr,tr,df
 except: return None,None,None

def get_positions():
 try:
  r=requests.get(f"{IG}/positions",headers=hdrs("2"),timeout=15)
  if r.status_code!=200: return []
  return r.json().get("positions",[])
 except: return []

def place(epic,direction,size):
 url=f"{IG}/positions/otc"
 payload={"epic":epic,"expiry":"-","direction":direction,"size":str(size),"orderType":"MARKET","currencyCode":"USD","forceOpen":True,"guaranteedStop":False}
 try:
  r=requests.post(url,headers=hdrs("2"),json=payload,timeout=15)
  return r.status_code,r.text
 except Exception as e: return 0,str(e)

# SIDEBAR
with st.sidebar:
 st.header("HEDGE MARTINGALE")
 mkt=st.selectbox("Market",list(M.keys()),index=3)
 if mkt!=st.session_state["last_mkt"]:
  st.session_state["mkts"]=[]
  st.session_state["last_mkt"]=mkt
 base=st.number_input("Base Lot",0.01,0.15,0.05,0.01)
 st.caption("Levels: 0.05 -> 0.08 -> 0.12 -> 0.15 MAX")
 mult=st.slider("Multiplier",1.2,2.0,1.6)
 hedge_atr=st.slider("Hedge every ATR x",0.5,3.0,1.0)
 profit_target=st.number_input("Close all when net $ profit >=",value=4.0)
 st.divider()
 if st.session_state["con"]:
  st.success("IG Connected")
 else
