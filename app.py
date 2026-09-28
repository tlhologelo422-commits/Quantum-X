import copy
import json
import math
import os
import time
from datetime import datetime, timezone, timedelta

import pandas as pd
import requests
import streamlit as st
import yfinance as yf


# ============================================================
# QUANTUM X PRO — VERSION 3
# MACD + DIVERGENCE SCALPER  |  IG DEMO  XAU/USD  M5
#
# MODES
#   1) NORMAL             : one trade, hard SL + TP, break-even move
#   2) HEDGING MARTINGALE : zone-recovery. Opposite hedge legs with
#                           escalating size, closed together at a
#                           basket profit target. HARD leg cap and
#                           HARD money cap per cycle.
#
# DATA
#   Yahoo GC=F  -> M5 history (bootstrap, calibrated to IG price)
#   IG XAU/USD  -> live price + execution (execution authority)
# ============================================================

st.set_page_config(page_title="Quantum X PRO", page_icon="🐎", layout="wide")

IG_BASE_URL = "https://demo-api.ig.com/gateway/deal"
IG_EPIC = "CS.D.IN_GOLD.MFI.IP"
YAHOO_SYMBOL = "GC=F"
CURRENCY = "USD"

POLL_SECONDS = 5
BOOTSTRAP_BARS = 300
MIN_BARS = 60                     # MACD(26+9) + EMA50 warm-up

# --- indicators -------------------------------------------------
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
EMA_TREND = 50
ATR_PERIOD = 14

# --- divergence -------------------------------------------------
PIVOT_LEFT, PIVOT_RIGHT = 3, 2    # swing = 3 bars left, 2 bars right
PIVOT_MIN_GAP, PIVOT_MAX_GAP = 4, 40
SIGNAL_MAX_AGE = 8                # bars since 2nd pivot (freshness)

# --- filters ----------------------------------------------------
ATR_MIN, ATR_MAX = 0.6, 10.0      # skip dead / spiking markets
MAX_CHASE_ATR = 0.6               # do not chase price away from signal
MAX_SPREAD = 1.50
MIN_STOP_DISTANCE = 2.0           # floor, IG's own minimum also applied
CYCLE_MAX_MIN = 120               # hedging cycle time stop
STATE_FILE = "quantum_x_state.json"

DEFAULT_CFG = {
    "mode": "Normal",
    "size": 0.01,
    "rr": 1.5,
    "min_score": 5,
    "signal_mode": "Divergence + MACD cross",
    "risk_usd": 10.0,           # Normal: max loss per trade
    "max_daily_loss": 60.0,     # both modes: stop opening new entries
    "max_cycle_risk": 30.0,     # Hedging: worst-case loss per cycle
    "max_trades": 10,
    "cooldown_min": 5,
    "be_trigger": 0.7,          # Normal: move SL to BE at 0.7R
    "zone_mult": 1.2,           # Hedging: zone width = 1.2 x ATR
    "mult": 2.0,                # Hedging: hedge size multiplier
    "max_legs": 4,              # Hedging: hard leg cap
    "contract": 1.0,            # USD per 1.0 point per 1.0 size (verify!)
}
