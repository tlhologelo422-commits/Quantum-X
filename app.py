import os, pandas as pd, numpy as np, requests, time, base64, io
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image

st.set_page_config(page_title="SOLID V16 GPT VISION", layout="wide")
st.title("SOLID BOT V16 - Opens Trades + GPT-8 Vision Scanner")
st.caption("Built to be solid: Direct Yahoo + IG Place + AI Screenshot")

IG = "https://demo-api.ig.com/gateway/deal"
YAHOO = {"XAU/USD":"GC=F","EUR/USD":"EURUSD=X","GBP/USD":"GBPUSD=X","NAS100":"^NDX","US30":"^DJI","BTC/USD":"BTC-USD"}
IG_SEARCH = {"XAU/USD":"Gold","EUR/USD":"EUR/USD","GBP/USD":"GBP/USD","NAS100":"US Tech 100","US30":"US 30","BTC/USD":"Bitcoin"}
TV = {"XAU/USD":"OANDA:XAUUSD","EUR/USD":"FX:EURUSD","GBP/USD":"FX:GBPUSD","NAS100":"OANDA:NAS100USD","US30":"OANDA:US30USD","BTC/USD":"BINANCE:BTCUSD"}

for k in ["con","cst","xsec","mkts","last_mkt","running","last_trade_time","ai_signal"]:
 if k not in st.session_state: st.session_state[k]=False if k in ["con","running"] else [] if k=="mkts" else 0 if k=="last_trade_time" else None
if st.session_state["last_mkt"] is None: st.session_state["last_mkt"]=""

def get_creds():
 def g(n):
  try:
   if n in st.secrets: return st.secrets[n]
  except: pass
  return os.getenv(n)
 return g("IG_USERNAME"),g("IG_PASSWORD"),g("IG_API_KEY"), g("OPENAI_API_KEY")

def hdrs(v="2"):
 _,_,k,_=get_creds()
 return {"X-IG-API-KEY":k,"CST":st.session_state["cst"],"X-SECURITY-TOKEN":st.session_state["xsec"],"Accept":"application/json","Content-Type":"application/json","Version":v}
def login():
 u,p,k,_=get_creds(); h={"X-IG-API-KEY":k,"Content-Type":"application/json","Accept":"application/json","Version":"2"}; b={"identifier":u,"password":p,"encryptedPassword":False}
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

def place_solid(epic,direction,size,sl=None,tp=None):
 url=f"{IG}/positions/otc"
 payloads=[]
 if sl and tp:
  payloads.append({"epic":epic,"expiry":"-","direction":direction,"size":str(size),"orderType":"MARKET","currencyCode":"USD","forceOpen":True,"guaranteedStop":False,"stopLevel":str(round(float(sl),1)),"limitLevel":str(round(float(tp),1))})
 payloads.append({"epic":epic,"expiry":"-","direction":direction,"size":str(size),"orderType":"MARKET","currencyCode":"USD","forceOpen":True,"guaranteedStop":False})
 for idx, pay in enumerate(payloads):
  try:
   r=requests.post(url,headers=hdrs("2"),json=pay,timeout=15)
   if r.status_code==200:
    return True, f"PLACED via method {idx+1} {direction} {size}", r.text
  except Exception as e:
   last_err=str(e)
   continue
 try:
  return False, f"IG failed: {r.text[:400]}", r.text
 except:
  return False, f"Exception {last_err}", ""

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

@st.cache_data(ttl=20)
def get_yahoo_candles(yahoo_symbol):
 try:
  url=f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
  params={"range":"2d","interval":"5m"}; headers={"User-Agent":"Mozilla/5.0"}
  r=requests.get(url,params=params,headers=headers,timeout=15)
  if r.status_code==200:
   j=r.json(); result=j.get("chart",{}).get("result",[])
   if result:
    res=result[0]; ts=res.get("timestamp",[]); quote=res.get("indicators",{}).get("quote",[{}])[0]
    closes=quote.get("close",[]); rows=[]
    for i in range(len(ts)):
     if closes[i] is None: continue
     rows.append({"time":pd.to_datetime(ts[i],unit='s',utc=True),"open":quote.get("open",[])[i],"high":quote.get("high",[])[i],"low":quote.get("low",[])[i],"close":closes[i]})
    df=pd.DataFrame(rows).dropna()
    if len(df)>=30: return df
 except: pass
 return None

def supertrend(df, period=10, multiplier=3.0):
 if df is None or len(df)<period+5: return None
 hl2=(df["high"]+df["low"])/2; tr1=df["high"]-df["low"]; tr2=(df["high"]-df["close"].shift()).abs(); tr3=(df["low"]-df["close"].shift()).abs()
 tr=pd.concat([tr1,tr2,tr3],axis=1).max(axis=1); atr=tr.rolling(period).mean()
 upper=hl2+multiplier*atr; lower=hl2-multiplier*atr
 st_line=pd.Series(index=df.index,dtype=float); direction=pd.Series(index=df.index,dtype=int)
 for i in range(len(df)):
  if i==0: st_line.iloc[i]=lower.iloc[i] if not pd.isna(lower.iloc[i]) else hl2.iloc[i]; direction.iloc[i]=1
  else:
   prev=st_line.iloc[i-1]
   if pd.isna(prev): prev=lower.iloc[i-1]
   if df["close"].iloc[i]<=prev: st_line.iloc[i]=upper.iloc[i]; direction.iloc[i]=-1
   else: st_line.iloc[i]=lower.iloc[i]; direction.iloc[i]=1
   if direction.iloc[i]==1 and st_line.iloc[i]<st_line.iloc[i-1]: st_line.iloc[i]=st_line.iloc[i-1]
   if direction.iloc[i]==-1 and st_line.iloc[i]>st_line.iloc[i-1]: st_line.iloc[i]=st_line.iloc[i-1]
 df["supertrend"]=st_line; df["st_dir"]=direction; df["atr"]=atr
 return df
# END PART 1
