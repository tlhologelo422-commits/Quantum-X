import os, pandas as pd, numpy as np, requests, time, base64, io, json, re
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image

st.set_page_config(page_title="SOLID V17 FIXED", layout="wide")
st.title("SOLID V17.1 - DUAL FREE AI FIXED")
st.caption("Gemini 2.0 Flash FREE + Groq FREE + Always Opens")

IG="https://demo-api.ig.com/gateway/deal"
YAHOO={"XAU/USD":"GC=F","EUR/USD":"EURUSD=X","GBP/USD":"GBPUSD=X","NAS100":"^NDX","US30":"^DJI","BTC/USD":"BTC-USD"}
IG_SEARCH={"XAU/USD":"Gold","EUR/USD":"EUR/USD","GBP/USD":"GBP/USD","NAS100":"US Tech 100","US30":"US 30","BTC/USD":"Bitcoin"}
TV={"XAU/USD":"OANDA:XAUUSD","EUR/USD":"FX:EURUSD","GBP/USD":"FX:GBPUSD","NAS100":"OANDA:NAS100USD","US30":"OANDA:US30USD","BTC/USD":"BINANCE:BTCUSD"}

for k in ["con","cst","xsec","mkts","last_mkt","running"]:
 if k not in st.session_state: st.session_state[k]=False if k in ["con","running"] else [] if k=="mkts" else ""
if st.session_state["last_mkt"] is None: st.session_state["last_mkt"]=""

def get_creds():
 def g(n):
  try:
   if n in st.secrets: return st.secrets[n]
  except: pass
  return os.getenv(n)
 return g("IG_USERNAME"),g("IG_PASSWORD"),g("IG_API_KEY"),g("GEMINI_API_KEY"),g("GROQ_API_KEY")

def hdrs(v="2"):
 u,p,k,_,_=get_creds()
 return {"X-IG-API-KEY":k,"CST":st.session_state["cst"],"X-SECURITY-TOKEN":st.session_state["xsec"],"Accept":"application/json","Content-Type":"application/json","Version":v}

def login():
 u,p,k,_,_=get_creds()
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

def place_solid(epic,direction,size,sl=None,tp=None):
 url=f"{IG}/positions/otc"
 pays=[]
 if sl and tp:
  pays.append({"epic":epic,"expiry":"-","direction":direction,"size":str(size),"orderType":"MARKET","currencyCode":"USD","forceOpen":True,"guaranteedStop":False,"stopLevel":str(round(float(sl),1)),"limitLevel":str(round(float(tp),1))})
 pays.append({"epic":epic,"expiry":"-","direction":direction,"size":str(size),"orderType":"MARKET","currencyCode":"USD","forceOpen":True,"guaranteedStop":False})
 last=""
 for i,pay in enumerate(pays):
  try:
   r=requests.post(url,headers=hdrs("2"),json=pay,timeout=15)
   last=r.text
   if r.status_code==200: return True,f"METHOD {i+1} {direction} {size} OK",r.text
  except Exception as e: last=str(e)
 return False,f"IG FAIL {last[:300]}",last

def close_all_for_epic(epic):
 pos=get_positions()
 this=[p for p in pos if p.get("market",{}).get("epic")==epic]
 res=[]
 for p in this:
  po=p.get("position",{})
  did=po.get("dealId")
  d="SELL" if po.get("direction")=="BUY" else "BUY"
  sz=po.get("size")
  url=f"{IG}/positions/otc"
  pay={"dealId":did,"epic":epic,"expiry":"-","direction":d,"size":str(sz),"orderType":"MARKET"}
  try:
   r=requests.delete(url,headers=hdrs("2"),json=pay,timeout=15)
   res.append(f"{r.status_code}")
  except Exception as e: res.append(str(e))
 return res

@st.cache_data(ttl=20)
def get_yahoo_candles(sym):
 try:
  url=f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
  pr={"range":"2d","interval":"5m"}
  hh={"User-Agent":"Mozilla/5.0"}
  r=requests.get(url,params=pr,headers=hh,timeout=15)
  if r.status_code==200:
   j=r.json()
   result=j.get("chart",{}).get("result",[])
   if result:
    res=result[0]
    ts=res.get("timestamp",[])
    q=res.get("indicators",{}).get("quote",[{}])[0]
    cl=q.get("close",[])
    rows=[]
    for i in range(len(ts)):
     if cl[i] is None: continue
     rows.append({"time":pd.to_datetime(ts[i],unit='s',utc=True),"open":q.get("open",[])[i],"high":q.get("high",[])[i],"low":q.get("low",[])[i],"close":cl[i]})
    df=pd.DataFrame(rows).dropna()
    if len(df)>=30: return df
 except: pass
 return None

def supertrend(df,p=10,m=3.0):
 if df is None or len(df)<p+5: return None
 hl2=(df["high"]+df["low"])/2
 tr1=df["high"]-df["low"]
 tr2=(df["high"]-df["close"].shift()).abs()
 tr3=(df["low"]-df["close"].shift()).abs()
 tr=pd.concat([tr1,tr2,tr3],axis=1).max(axis=1)
 atr=tr.rolling(p).mean()
 upper=hl2+m*atr
 lower=hl2-m*atr
 stl=pd.Series(index=df.index,dtype=float)
 direction=pd.Series(index=df.index,dtype=int)
 for i in range(len(df)):
  if i==0:
   stl.iloc[i]=lower.iloc[i] if not pd.isna(lower.iloc[i]) else hl2.iloc[i]
   direction.iloc[i]=1
  else:
   prev=stl.iloc[i-1]
   if pd.isna(prev): prev=lower.iloc[i-1]
   if df["close"].iloc[i]<=prev:
    stl.iloc[i]=upper.iloc[i]
    direction.iloc[i]=-1
   else:
    stl.iloc[i]=lower.iloc[i]
    direction.iloc[i]=1
   if direction.iloc[i]==1 and stl.iloc[i]<stl.iloc[i-1]: stl.iloc[i]=stl.iloc[i-1]
   if direction.iloc[i]==-1 and stl.iloc[i]>stl.iloc[i-1]: stl.iloc[i]=stl.iloc[i-1]
 df["supertrend"]=stl
 df["st_dir"]=direction
 df["atr"]=atr
 return df
# END PART1
