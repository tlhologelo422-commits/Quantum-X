import os
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Quantum X PRO", layout="wide")

IG = "https://demo-api.ig.com/gateway/deal"

M = {
 "XAU/USD": ["OANDA:XAUUSD","Gold"],
 "EUR/USD": ["FX:EURUSD","EUR/USD"],
 "GBP/USD": ["FX:GBPUSD","GBP/USD"],
 "US30": ["OANDA:US30USD","US 30"],
}

TF = {"5M":"5","15M":"15","1H":"60","4H":"240","1D":"D"}

for k,v in {"con":False,"err":"","cst":None,"xsec":None,"mkts":[],"epic":None,"prs":None}.items():
 if k not in st.session_state:
  st.session_state[k]=v

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
 if not (u and p and k):
  return False,"Missing secrets",None
 h={"X-IG-API-KEY":k,"Content-Type":"application/json","Accept":"application/json","Version":"2"}
 b={"identifier":u,"password":p,"encryptedPassword":False}
 try:
  r=requests.post(f"{IG}/session",headers=h,json=b,timeout=20)
  if r.status_code!=200:
   return False,r.text[:200],None
  c=r.headers.get("CST")
  x=r.headers.get("X-SECURITY-TOKEN")
  if not c or not x:
   return False,"No tokens",None
  out={}
  out["cst"]=c
  out["xsec"]=x
  out["data"]=r.json()
  return True,"OK",out
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

def extract(m):
 epic=m.get("epic","")
 if not epic:
  epic=m.get("instrument",{}).get("epic","")
 name=m.get("instrumentName","")
 if not name:
  name=m.get("instrument",{}).get("name","Gold")
 stat="UNKNOWN"
 snap=m.get("snapshot",{})
 if isinstance(snap,dict):
  stat=snap.get("marketStatus","UNKNOWN")
 return {"epic":epic,"name":name,"status":stat}

def prices(epic):
 url=f"{IG}/prices/{epic}/MINUTE_15/100"
 try:
  r=requests.get(url,headers=hdrs("2"),timeout=30)
  if r.status_code!=200:
   return False,f"Price {r.status_code} {r.text[:120]}",None
  j=r.json()
  plist=j.get("prices",[])
  if not plist:
   return False,"No prices array",None
  rows=[]
  for p in plist:
   t=p.get("snapshotTimeUTC")
   if not t:
    t=p.get("snapshotTime")
   if not t:
    t=p.get("snapshotTimeLocal")
   def mid(v):
    if v is None:
     return None
    if isinstance(v,dict):
     b=v.get("bid")
     a=v.get("ask")
     if b is not None and a is not None:
      try:
       return (float(b)+float(a))/2
      except:
       return None
     if b is not None:
      try:
       return float(b)
      except:
       return None
     if a is not None:
      try:
       return float(a)
      except:
       return None
     return None
    try:
     return float(v)
    except:
     return None
   o=mid(p.get("openPrice"))
   h=mid(p.get("highPrice"))
   l=mid(p.get("lowPrice"))
   c=mid(p.get("closePrice"))
   rows.append({"time":t,"open":o,"high":h,"low":l,"close":c,"vol":p.get("lastTradedVolume") or 0})
  df=pd.DataFrame(rows)
  # debug if still empty
  if df.empty:
   return False,"DataFrame empty after parse",None
  df=df.dropna(subset=["open","high","low","close"])
  if df.empty:
   return False,f"All OHLC None - sample {rows[0] if rows else 'no rows'}",None
  df["time"]=pd.to_datetime(df["time"],utc=True,errors="coerce")
  df=df.sort_values("time").reset_index(drop=True)
  return True,"",df
 except Exception as e:
  return False,str(e),None

st.title("📈 QUANTUM X PRO")
st.caption("AI-Powered Market Analysis")

if st.session_state["con"]:
 st.success("● SYSTEM ONLINE • IG DEMO CONNECTED")
else:
 st.success("● SYSTEM ONLINE")

with st.sidebar:
 st.header("Market Settings")
 mkt=st.selectbox("Market",list(M.keys()))
 tf=st.selectbox("TF",list(TF.keys()),index=1)
 st.divider()
 if st.session_state["con"]:
  st.success("IG Connected")
  if st.button("Reconnect"):
   ok,msg,data=login()
   if ok:
    st.session_state["con"]=True
    st.session_state["cst"]=data["cst"]
    st.session_state["xsec"]=data["xsec"]
    st.success("Refreshed")
   else:
    st.error(msg)
 else:
  st.warning("Not Connected")
  if st.button("Connect to IG Demo"):
   with st.spinner("Connecting..."):
    ok,msg,data=login()
   if ok:
    st.session_state["con"]=True
    st.session_state["err"]=""
    st.session_state["cst"]=data["cst"]
    st.session_state["xsec"]=data["xsec"]
    st.rerun()
   else:
    st.session_state["err"]=msg
    st.error(msg)

if st.session_state["err"]:
 st.error(st.session_state["err"])

c1,c2,c3=st.columns(3)
c1.metric("Market",mkt)
c2.metric("TF",tf)
c3.metric("Provider","IG Demo")

st.subheader("📊 Market Chart")
sym=M[mkt][0]
interval=TF[tf]
html=f"""
<div id="chart" style="height:600px;width:100%;"></div>
<script src="https://s3.tradingview.com/tv.js"></script>
<script>
new TradingView.widget({{"autosize":true,"symbol":"{sym}","interval":"{interval}","timezone":"Africa/Johannesburg","theme":"dark","style":"1","locale":"en","container_id":"chart"}});
</script>
"""
components.html(html,height=620)

st.subheader("📡 IG Market Data")

if not st.session_state["con"]:
 st.info("Connect first")
else:
 term=M[mkt][1]
 st.write(f"Search: {term}")
 if st.button("Find IG Instrument"):
  with st.spinner(f"Searching {term}..."):
   ok,err,res=search(term)
  if ok:
   found=[]
   for x in res:
    if isinstance(x,dict):
     info=extract(x)
     if info["epic"]:
      found.append(info)
   if found:
    st.session_state["mkts"]=found
    st.success(f"Found {len(found)}")
    st.dataframe(pd.DataFrame(found),use_container_width=True)
   else:
    st.warning("No EPIC")
  else:
   st.error(err)

 if st.session_state["mkts"]:
  epics=[x["epic"] for x in st.session_state["mkts"]]
  labs=[f"{x['epic']} | {x['name']}" for x in st.session_state["mkts"]]
  idx=st.selectbox("Select EPIC",range(len(epics)),format_func=lambda i: labs[i])
  st.session_state["epic"]=epics[idx]
  st.info(f"Selected {epics[idx]}")
  if st.button("Load 15M Candles"):
   with st.spinner("Loading..."):
    ok,err,df=prices(epics[idx])
   if ok:
    st.session_state["prs"]=df
    st.success(f"Loaded {len(df)} candles")
    st.dataframe(df,use_container_width=True)
    st.line_chart(df.set_index("time")[["close"]])
   else:
    st.error(err)

 if st.session_state["prs"] is not None:
  st.success("✅ PIPELINE ACTIVE - READY FOR SMC BRAIN")
