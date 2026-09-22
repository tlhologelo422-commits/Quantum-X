import os
import pandas as pd
import numpy as np
import requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Quantum X PRO V4 All Markets", layout="wide")

IG = "https://demo-api.ig.com/gateway/deal"

# --- ALL MARKETS ---
M = {
 # Gold & Metals
 "XAU/USD": ["OANDA:XAUUSD","Gold"],
 "XAG/USD": ["OANDA:XAGUSD","Silver"],
 # Forex Majors
 "EUR/USD": ["FX:EURUSD","EUR/USD"],
 "GBP/USD": ["FX:GBPUSD","GBP/USD"],
 "USD/JPY": ["FX:USDJPY","USD/JPY"],
 "AUD/USD": ["FX:AUDUSD","AUD/USD"],
 "USD/CAD": ["FX:USDCAD","USD/CAD"],
 "NZD/USD": ["FX:NZDUSD","NZD/USD"],
 # Forex Minors
 "EUR/GBP": ["FX:EURGBP","EUR/GBP"],
 "EUR/JPY": ["FX:EURJPY","EUR/JPY"],
 "GBP/JPY": ["FX:GBPJPY","GBP/JPY"],
 # Indices
 "US30": ["OANDA:US30USD","US 30"],
 "NAS100": ["OANDA:NAS100USD","US Tech 100"],
 "SPX500": ["OANDA:SPX500USD","US 500"],
 "GER40": ["XETRA:DAX","Germany 40"],
 "UK100": ["OANDA:UK100GBP","FTSE 100"],
 # Crypto
 "BTC/USD": ["BINANCE:BTCUSD","Bitcoin"],
 "ETH/USD": ["BINANCE:ETHUSD","Ethereum"],
 # Oil
 "US Oil": ["OANDA:WTICOUSD","Oil"],
}

for k,v in {"con":False,"err":"","cst":None,"xsec":None,"mkts":[],"epic":None,"df15":None,"df1h":None,"df4h":None}.items():
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
  out={"cst":c,"xsec":x,"data":r.json()}
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
 epic=m.get("epic","") or m.get("instrument",{}).get("epic","")
 name=m.get("instrumentName","") or m.get("instrument",{}).get("name","")
 stat="UNKNOWN"
 snap
