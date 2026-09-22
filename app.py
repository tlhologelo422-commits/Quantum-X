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
def instant_signal(df):
 if df is None or len(df)<30: return "WAIT",None,None,df,0,0,"no df"
 df=supertrend(df,10,3.0)
 if df is None: return "WAIT",None,None,df,0,0,"st fail"
 last=df.iloc[-1]
 if pd.isna(last["atr"]): return "WAIT",None,None,df,0,0,"atr nan"
 bias="BUY" if last["st_dir"]==1 else "SELL"
 sl=last["close"]-last["atr"]*2.5 if bias=="BUY" else last["close"]+last["atr"]*2.5
 tp=last["close"]+last["atr"]*3.5 if bias=="BUY" else last["close"]-last["atr"]*3.5
 return bias,sl,tp,df,last["close"],last["atr"],"ok"

def analyze_chart_with_gpt(image_bytes, market_name, openai_key):
 try:
  from openai import OpenAI
  client=OpenAI(api_key=openai_key)
  b64=base64.b64encode(image_bytes).decode('utf-8')
  prompt=f"""You are a professional scalper for {market_name}. Analyze this chart screenshot.
  Give:
    1. Trend: BULLISH or BEARISH
    2. Signal: BUY or SELL or WAIT
    3. Entry price approx
    4. Stop Loss
    5. Take Profit
    6. Confidence 0-100%
    7. Reason in 2 lines (support/resistance, supertrend, structure)
  Format as JSON: {{"trend":"", "signal":"", "entry":0, "sl":0, "tp":0, "confidence":0, "reason":""}}"""
  resp=client.chat.completions.create(
   model="gpt-4o",
   messages=[
    {"role":"user","content":[
     {"type":"text","text":prompt},
     {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}}
    ]}
   ],
   max_tokens=500
  )
  txt=resp.choices[0].message.content
  return True, txt
 except Exception as e:
  return False, str(e)

with st.sidebar:
 st.header("SOLID V16")
 mkt=st.selectbox("Market",list(YAHOO.keys()),index=3)
 if mkt!=st.session_state["last_mkt"]: st.session_state["mkts"]=[]; st.session_state["last_mkt"]=mkt
 size=st.number_input("Lot 0.01-0.15",0.01,0.15,0.05,0.01)
 openai_input=st.text_input("OpenAI API Key (GPT-8)", type="password", help="Paste sk-... or set OPENAI_API_KEY in secrets")
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
st.subheader("💰 LIVE PnL DASHBOARD")
acc=get_accounts(); pos=get_positions()
if acc:
 try:
  bal=acc.get("accounts",[])[0].get("balance",{}).get("balance",0)
  st.metric("Balance", f"${bal}")
 except: pass
if pos:
 total=0
 for p in pos:
  po=p.get("position",{}); pnl=po.get("profit",0) or 0
  try: total+=float(pnl)
  except: pass
  color="green" if float(pnl)>=0 else "red"
  st.write(f"{p.get('market',{}).get('instrumentName','')} {po.get('direction')} {po.get('size')} PnL :{color}[${pnl}]")
 st.metric("Total Open PnL", f"${total:.2f}")
else: st.write("No open positions")

st.divider()
st.subheader("📸 AI CHART SCANNER - GPT-8 Vision")
st.write("Upload TradingView screenshot, AI will give BUY/SELL + SL/TP")
uploaded=st.file_uploader("Upload chart screenshot", type=["png","jpg","jpeg"])
if uploaded:
 st.image(uploaded, caption="Your chart", use_column_width=True)
 if st.button("🔍 SCAN WITH GPT-8", type="primary", use_container_width=True):
  _,_,_,secret_key=get_creds()
  key_to_use=openai_input or secret_key
  if not key_to_use:
   st.error("Add OpenAI API key in sidebar or secrets OPENAI_API_KEY")
  else:
   with st.spinner("GPT-8 analyzing chart..."):
    ok, result=analyze_chart_with_gpt(uploaded.getvalue(), mkt, key_to_use)
    if ok:
     st.success("AI Analysis Done")
     st.code(result, language="json")
     st.session_state["ai_signal"]=result
     # Try to parse signal
     import json, re
     try:
      m=re.search(r'\{.*\}', result, re.DOTALL)
      if m: j=json.loads(m.group())
      else: j={}
      sig=j.get("signal","WAIT"); entry=j.get("entry"); sl=j.get("sl"); tp=j.get("tp"); conf=j.get("confidence",0)
      st.write(f"**AI says: {sig} | Confidence {conf}% | Entry {entry} SL {sl} TP {tp}**")
      if sig in ["BUY","SELL"]:
       if st.button(f"TRADE AI SIGNAL {sig}", use_container_width=True):
        ok2, msg2, _=place_solid(epic, sig, size, sl, tp)
        if ok2: st.success(msg2); st.balloons()
        else: st.error(msg2)
     except Exception as e: st.write(f"Parse note: {e}")
    else:
     st.error(f"GPT error: {result}")

st.divider()
try:
 sym=TV[mkt]
 components.html(f"""<div id="tv" style="height:350px;"></div><script src="https://s3.tradingview.com/tv.js"></script><script>new TradingView.widget({{"autosize":true,"symbol":"{sym}","interval":"5","timezone":"Africa/Johannesburg","theme":"dark","style":"1","container_id":"tv"}});</script>""",height=370)
except: pass

st.subheader(f"Auto Scalper {mkt} {epic}")
b1,b2,b3,b4=st.columns(4)
with b1: scan=st.button("TRADE NOW",use_container_width=True,type="primary")
with b2: force=st.button("FORCE TRADE",use_container_width=True)
with b3: auto=st.button("START AUTO",use_container_width=True)
with b4: close_btn=st.button("CLOSE ALL",use_container_width=True)

if close_btn:
 r=close_all_for_epic(epic); st.success(f"Closed {r}"); time.sleep(1); st.rerun()

def run_trade():
 df=get_yahoo_candles(YAHOO[mkt])
 if df is None: st.error("Yahoo no candles - Try XAU/USD"); return
 bias,sl,tp,full,price,atr,msg=instant_signal(df)
 if full is None: st.error(msg); return
 c1,c2,c3=st.columns(3)
 c1.metric("Price",f"{price:.2f}"); c2.metric("Bias",bias); c3.metric("ATR",f"{atr:.2f}")
 pos=get_positions(); this=[p for p in pos if p.get("market",{}).get("epic")==epic]
 if this: st.warning("Already has position - close first");
 else:
  ok,msg2,_=place_solid(epic,bias,size,sl,tp)
  if ok: st.success(f"✅ {msg2} SL {sl:.2f} TP {tp:.2f}"); st.balloons()
  else: st.error(msg2)
 if full is not None: st.line_chart(full.set_index("time")[["close","supertrend"]].tail(100))

if scan or force: run_trade()
if auto:
 st.session_state["running"]=True
 run_trade()
if st.session_state["running"]:
 st.info("AUTO ON - trades every 60s")
 ph=st.empty()
 while st.session_state["running"]:
  time.sleep(60)
  df=get_yahoo_candles(YAHOO[mkt])
  if df is None: continue
  bias,sl,tp,full,price,atr,msg=instant_signal(df)
  pos=get_positions(); this=[p for p in pos if p.get("market",{}).get("epic")==epic]
  if this:
   with ph.container(): st.write(f"{time.strftime('%H:%M:%S')} Has position")
   continue
  ok,msg2,_=place_solid(epic,bias,size,sl,tp)
  with ph.container(): st.write(f"{time.strftime('%H:%M:%S')} AUTO {bias} {price:.2f} -> {msg2}")
  st.rerun()
