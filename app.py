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
DEFAULT_STATE = {
    "ig_connected": False,
    "bot_running": False,
    "ig_cst": None,
    "ig_security_token": None,
    "live_bid": None,
    "live_offer": None,
    "live_mid": None,
    "market_status": None,
    "min_stop": MIN_STOP_DISTANCE,
    "min_deal_size": 0.01,
    "bars": [],
    "signal": {"signal": "WAIT", "score": 0, "reason": "Waiting for data.",
               "id": None, "kind": None, "atr": None, "sl_ref": None},
    "calibration_offset": None,
    "yahoo_last_price": None,
    "last_error": None,
    "gate_msg": None,
    "log": [],
    "trade_count": 0,
    "trade_day": datetime.now(timezone.utc).date().isoformat(),
    "done_signal_ids": [],
    "cooldown_until": None,
    "entry_block_until": None,
    "equity": None,
    "day_start_equity": None,
    "account_ts": None,
    "cycle": None,
    "cycle_loaded": False,
    "active_normal": None,
    "last_order": None,
    "cfg": copy.deepcopy(DEFAULT_CFG),
}

for _key, _value in DEFAULT_STATE.items():
    if _key not in st.session_state:
        st.session_state[_key] = copy.deepcopy(_value)


class RiskRefused(RuntimeError):
    """Trade refused by the risk engine (deterministic, do not retry)."""


# ============================================================
# HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def floor_m5(value):
    ts = pd.Timestamp(value)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.floor("5min")


def safe_float(value):
    try:
        if value is None:
            return None
        result = float(value)
        return None if pd.isna(result) else result
    except Exception:
        return None


def floor2(x):
    return math.floor(x * 100 + 1e-9) / 100


def ceil2(x):
    return math.ceil(x * 100 - 1e-9) / 100


def log(message):
    stamp = now_utc().strftime("%H:%M:%S")
    st.session_state.log.insert(0, f"{stamp}  {message}")
    del st.session_state.log[80:]


def set_error(message):
    if message != st.session_state.last_error:
        log(f"⚠️ {message}")
    st.session_state.last_error = message
def reset_daily_if_needed():
    today = now_utc().date().isoformat()
    if st.session_state.trade_day != today:
        st.session_state.trade_day = today
        st.session_state.trade_count = 0
        st.session_state.day_start_equity = None
        st.session_state.done_signal_ids = []


def get_secret(name):
    try:
        value = st.secrets.get(name)
        if value:
            return str(value)
    except Exception:
        pass
    return os.environ.get(name)      # Codespaces secrets live here


# ============================================================
# IG API LAYER
# ============================================================

def ig_login():
    username = get_secret("IG_USERNAME")
    password = get_secret("IG_PASSWORD")
    api_key = get_secret("IG_API_KEY")

    if not (username and password and api_key):
        raise RuntimeError("IG credentials missing (IG_USERNAME / IG_PASSWORD / IG_API_KEY).")

    response = requests.post(
        f"{IG_BASE_URL}/session",
        headers={"X-IG-API-KEY": api_key, "Content-Type": "application/json",
                 "Accept": "application/json", "Version": "2"},
        json={"identifier": username, "password": password, "encryptedPassword": False},
        timeout=15,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"IG login failed: {response.status_code} {response.text[:300]}")

    cst = response.headers.get("CST")
    token = response.headers.get("X-SECURITY-TOKEN")
    if not cst or not token:
        raise RuntimeError("IG login OK but security tokens missing.")

    st.session_state.ig_cst = cst
    st.session_state.ig_security_token = token
    st.session_state.ig_connected = True


def ig_headers(version="2", delete_override=False):
    api_key = get_secret("IG_API_KEY")
    if not (api_key and st.session_state.ig_cst and st.session_state.ig_security_token):
        raise RuntimeError("Not logged in to IG.")
    headers = {
        "X-IG-API-KEY": api_key,
        "CST": st.session_state.ig_cst,
        "X-SECURITY-TOKEN": st.session_state.ig_security_token,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Version": version,
    }
    if delete_override:
        headers["_method"] = "DELETE"
    return headers


def ig_request(method, path, version="2", body=None, delete_override=False, _retry=True):
    response = requests.request(
        method,
        f"{IG_BASE_URL}{path}",
        headers=ig_headers(version, delete_override),
        json=body,
        timeout=15,
    )
    if response.status_code == 401 and _retry:      # token expired -> re-login once
        ig_login()
        return ig_request(method, path, version, body, delete_override, _retry=False)
    if response.status_code >= 400:
        raise RuntimeError(f"IG {method} {path}: {response.status_code} {response.text[:300]}")
    return response.json() if response.text else {}
def get_ig_market():
    data = ig_request("GET", f"/markets/{IG_EPIC}", "3")
    snapshot = data.get("snapshot", {})
    rules = data.get("dealingRules", {})

    bid = safe_float(snapshot.get("bid"))
    offer = safe_float(snapshot.get("offer"))
    if bid is None or offer is None:
        raise RuntimeError("IG returned no usable bid/offer.")

    min_stop = safe_float((rules.get("minNormalStopOrLimitDistance") or {}).get("value"))
    min_size = safe_float((rules.get("minDealSize") or {}).get("value"))
    if min_stop:
        st.session_state.min_stop = max(min_stop, 0.1)
    if min_size:
        st.session_state.min_deal_size = min_size

    st.session_state.live_bid = bid
    st.session_state.live_offer = offer
    st.session_state.live_mid = (bid + offer) / 2.0
    st.session_state.market_status = snapshot.get("marketStatus", "UNKNOWN")

    return {"bid": bid, "offer": offer, "mid": (bid + offer) / 2.0,
            "market_status": st.session_state.market_status}


def refresh_account(force=False):
    last = st.session_state.account_ts
    if not force and last and (now_utc() - last).total_seconds() < 60:
        return
    data = ig_request("GET", "/accounts", "1")
    accounts = data.get("accounts", [])
    if not accounts:
        return
    acct = next((a for a in accounts if a.get("preferred")), accounts[0])
    bal = acct.get("balance", {})
    balance = safe_float(bal.get("balance"))
    if balance is None:
        return
    equity = balance + (safe_float(bal.get("profitLoss")) or 0.0)
    st.session_state.equity = equity
    st.session_state.account_ts = now_utc()
    if st.session_state.day_start_equity is None:
        st.session_state.day_start_equity = equity


def list_positions():
    data = ig_request("GET", "/positions", "2")
    out = []
    for item in data.get("positions", []):
        pos = item.get("position", {})
        mkt = item.get("market", {})
        if (pos.get("epic") or mkt.get("epic")) != IG_EPIC:
            continue
        out.append({
            "dealId": pos.get("dealId"),
            "direction": pos.get("direction"),
            "size": safe_float(pos.get("size")),
            "level": safe_float(pos.get("level")),
            "stopLevel": safe_float(pos.get("stopLevel")),
            "limitLevel": safe_float(pos.get("limitLevel")),
        })
    return out


def confirm_deal(reference):
    time.sleep(0.5)
    data = ig_request("GET", f"/confirms/{reference}", "1")
    if data.get("dealStatus") != "ACCEPTED":
        raise RuntimeError(f"IG rejected the deal: {data.get('reason', 'unknown reason')}")
    return data


def open_position(direction, size, stop_distance=None, limit_distance=None):
    payload = {
        "epic": IG_EPIC, "expiry": "-", "direction": direction,
        "size": float(size), "orderType": "MARKET",
        "currencyCode": CURRENCY, "forceOpen": True, "guaranteedStop": False,
    }
    if stop_distance:
        payload["stopDistance"] = round(stop_distance, 2)
    if limit_distance:
        payload["limitDistance"] = round(limit_distance, 2)
    res = ig_request("POST", "/positions/otc", "2", payload)
    return confirm_deal(res["dealReference"])
def close_position(pos):
    opposite = "SELL" if pos["direction"] == "BUY" else "BUY"
    body = {"dealId": pos["dealId"], "direction": opposite,
            "size": pos["size"], "orderType": "MARKET"}
    res = ig_request("POST", "/positions/otc", "1", body, delete_override=True)
    return confirm_deal(res["dealReference"])


def close_all_positions():
    closed, failed = 0, []
    for pos in list_positions():
        try:
            close_position(pos)
            closed += 1
        except Exception as exc:
            failed.append(str(exc))
    return closed, failed


# ============================================================
# DATA: YAHOO BOOTSTRAP + IG LIVE M5 CANDLES
# ============================================================

def fetch_yahoo_m5():
    try:
        data = yf.download(YAHOO_SYMBOL, period="5d", interval="5m",
                           auto_adjust=False, progress=False, threads=False)
    except Exception as exc:
        raise RuntimeError(f"Yahoo M5 download failed: {exc}")

    if data is None or data.empty:
        raise RuntimeError("Yahoo returned no GC=F M5 data.")
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    cols = ["Open", "High", "Low", "Close"]
    if any(c not in data.columns for c in cols):
        raise RuntimeError("Yahoo data is missing OHLC columns.")

    data = data[cols].apply(pd.to_numeric, errors="coerce").dropna()
    data.index = [floor_m5(i) for i in data.index]
    data = data[~data.index.duplicated(keep="last")].sort_index()
    data = data[data.index < floor_m5(now_utc())]          # completed candles only

    if len(data) < MIN_BARS:
        raise RuntimeError(f"Yahoo gave {len(data)} completed candles, need {MIN_BARS}.")
    return data.tail(BOOTSTRAP_BARS)


def bootstrap_m5():
    if not st.session_state.ig_connected:
        raise RuntimeError("Connect to IG first.")

    market = get_ig_market()
    yahoo = fetch_yahoo_m5()

    yahoo_last = safe_float(yahoo["Close"].iloc[-1])
    if yahoo_last is None:
        raise RuntimeError("Yahoo latest close invalid.")
    offset = market["mid"] - yahoo_last
    st.session_state.yahoo_last_price = yahoo_last
    st.session_state.calibration_offset = offset

    bars = []
    for ts, row in yahoo.iterrows():
        bars.append({
            "time": pd.Timestamp(ts),
            "open": float(row["Open"]) + offset,
            "high": float(row["High"]) + offset,
            "low": float(row["Low"]) + offset,
            "close": float(row["Close"]) + offset,
            "source": "Yahoo calibrated",
        })
    st.session_state.bars = bars
    refresh_signal()
    return len(bars)
def update_live_bar(mid):
    if mid is None:
        return
    bucket = floor_m5(now_utc())
    bars = st.session_state.bars

    if bars and floor_m5(bars[-1]["time"]) == bucket:
        last = bars[-1]
        last["high"] = max(last["high"], mid)
        last["low"] = min(last["low"], mid)
        last["close"] = mid
    elif not bars or bucket > floor_m5(bars[-1]["time"]):
        bars.append({"time": bucket, "open": mid, "high": mid, "low": mid,
                     "close": mid, "source": "IG live"})

    st.session_state.bars = bars[-400:]


def bars_dataframe():
    if not st.session_state.bars:
        return pd.DataFrame()
    df = pd.DataFrame(st.session_state.bars)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    for c in ["open", "high", "low", "close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)


def completed_df():
    """Only fully closed candles — signals never repaint."""
    df = bars_dataframe()
    if df.empty:
        return df
    return df[df["time"] < floor_m5(now_utc())].reset_index(drop=True)


# ============================================================
# INDICATORS
# ============================================================

def add_indicators(df):
    d = df.copy()
    ema_fast = d["close"].ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = d["close"].ewm(span=MACD_SLOW, adjust=False).mean()
    d["macd"] = ema_fast - ema_slow
    d["sig"] = d["macd"].ewm(span=MACD_SIGNAL, adjust=False).mean()
    d["hist"] = d["macd"] - d["sig"]
    d["ema"] = d["close"].ewm(span=EMA_TREND, adjust=False).mean()

    prev_close = d["close"].shift(1)
    true_range = pd.concat([
        (d["high"] - d["low"]).abs(),
        (d["high"] - prev_close).abs(),
        (d["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    d["atr"] = true_range.rolling(ATR_PERIOD, min_periods=5).mean()
    return d


def find_pivots(values, kind):
    out = []
    for i in range(PIVOT_LEFT, len(values) - PIVOT_RIGHT):
        v = values[i]
        left = values[i - PIVOT_LEFT:i]
        right = values[i + 1:i + 1 + PIVOT_RIGHT]
        if kind == "low" and all(v <= x for x in left) and all(v < x for x in right):
            out.append(i)
        if kind == "high" and all(v >= x for x in left) and all(v > x for x in right):
            out.append(i)
    return out


def macd_cross(d, side, lookback=3):
    diff = (d["macd"] - d["sig"]).values
    for k in range(1, lookback + 1):
        now, prev = diff[-k], diff[-k - 1]
        if side == "BUY" and prev <= 0 < now:
            return True
        if side == "SELL" and prev >= 0 > now:
            return True
    return False
def trend_flags(d):
    close = float(d["close"].iloc[-1])
    ema_now = float(d["ema"].iloc[-1])
    ema_old = float(d["ema"].iloc[-6])
    return (close > ema_now and ema_now > ema_old,
            close < ema_now and ema_now < ema_old)


# ============================================================
# MACD DIVERGENCE + CROSS SETUPS
# ============================================================

def detect_divergences(d, atr):
    n = len(d)
    low, high = d["low"].values, d["high"].values
    macd = d["macd"].values
    times = d["time"]
    min_move = 0.05 * atr
    trend_up, trend_dn = trend_flags(d)
    found = []

    lows = find_pivots(low, "low")
    if lows and (n - 1 - lows[-1]) <= SIGNAL_MAX_AGE:
        p2 = lows[-1]
        for p1 in reversed(lows[-4:-1]):
            gap = p2 - p1
            if not (PIVOT_MIN_GAP <= gap <= PIVOT_MAX_GAP):
                continue
            stamp = times.iloc[p2].isoformat()
            if low[p2] < low[p1] - min_move and macd[p2] > macd[p1] and macd[p1] < 0:
                found.append({"side": "BUY", "kind": "Regular bullish divergence", "type": "div",
                              "sl_ref": float(low[p2]), "id": f"BUY:RD:{stamp}"})
                break
            if trend_up and low[p2] > low[p1] + min_move and macd[p2] < macd[p1]:
                found.append({"side": "BUY", "kind": "Hidden bullish divergence", "type": "div",
                              "sl_ref": float(low[p2]), "id": f"BUY:HD:{stamp}"})
                break

    highs = find_pivots(high, "high")
    if highs and (n - 1 - highs[-1]) <= SIGNAL_MAX_AGE:
        p2 = highs[-1]
        for p1 in reversed(highs[-4:-1]):
            gap = p2 - p1
            if not (PIVOT_MIN_GAP <= gap <= PIVOT_MAX_GAP):
                continue
            stamp = times.iloc[p2].isoformat()
            if high[p2] > high[p1] + min_move and macd[p2] < macd[p1] and macd[p1] > 0:
                found.append({"side": "SELL", "kind": "Regular bearish divergence", "type": "div",
                              "sl_ref": float(high[p2]), "id": f"SELL:RD:{stamp}"})
                break
            if trend_dn and high[p2] < high[p1] - min_move and macd[p2] > macd[p1]:
                found.append({"side": "SELL", "kind": "Hidden bearish divergence", "type": "div",
                              "sl_ref": float(high[p2]), "id": f"SELL:HD:{stamp}"})
                break
    return found


def detect_cross_setups(d):
    """Trend-pullback MACD cross: cross back with the trend, on the pullback side of zero."""
    out = []
    macd_now = float(d["macd"].iloc[-1])
    stamp = d["time"].iloc[-1].isoformat()
    trend_up, trend_dn = trend_flags(d)

    if trend_up and macd_now < 0 and macd_cross(d, "BUY", 2):
        out.append({"side": "BUY", "kind": "MACD pullback cross", "type": "cross",
                    "sl_ref": float(d["low"].iloc[-3:].min()), "id": f"BUY:X:{stamp}"})
    if trend_dn and macd_now > 0 and macd_cross(d, "SELL", 2):
        out.append({"side": "SELL", "kind": "MACD pullback cross", "type": "cross",
                    "sl_ref": float(d["high"].iloc[-3:].max()), "id": f"SELL:X:{stamp}"})
    return out
def evaluate_signal(d, bid, offer, cfg):
    def wait(reason, **extra):
        base = {"signal": "WAIT", "score": 0, "reason": reason, "id": None,
                "kind": None, "atr": None, "sl_ref": None}
        base.update(extra)
        return base

    if len(d) < MIN_BARS:
        return wait(f"Need {MIN_BARS} closed candles, have {len(d)}.")

    atr = safe_float(d["atr"].iloc[-1])
    if atr is None:
        return wait("ATR not ready.")
    if atr < ATR_MIN:
        return wait(f"Volatility too low (ATR {atr:.2f}).", atr=atr)
    if atr > ATR_MAX:
        return wait(f"Volatility too extreme (ATR {atr:.2f}).", atr=atr)

    c = d.iloc[-1]
    hist = d["hist"].values

    cands = detect_divergences(d, atr)
    if "cross" in cfg["signal_mode"].lower():
        cands += detect_cross_setups(d)
    if not cands:
        return wait("No divergence / MACD setup on the last closed candle.", atr=atr)

    best, near_miss = None, None
    for cand in cands:
        s = 1 if cand["side"] == "BUY" else -1
        candle_ok = (c["close"] - c["open"]) * s > 0
        turn_ok = (hist[-1] - hist[-2]) * s > 0
        if not (candle_ok and turn_ok):
            near_miss = cand
            continue

        score = 3
        if cand["type"] == "div" and macd_cross(d, cand["side"], 3):
            score += 1
        if (hist[-1] - hist[-2]) * s > 0 and (hist[-2] - hist[-3]) * s > 0:
            score += 1
        if abs(c["close"] - c["open"]) >= 0.5 * atr:
            score += 1
        cand["score"] = score
        if best is None or score > best["score"]:
            best = cand

    if best is None:
        return wait(f"{near_miss['kind']} spotted ({near_miss['side']}), waiting for a "
                    f"confirming candle + histogram turn.", atr=atr)

    if best["score"] < cfg["min_score"]:
        return wait(f"{best['kind']} ({best['side']}) score {best['score']}/6 "
                    f"< required {cfg['min_score']}.", atr=atr, score=best["score"])

    if bid is not None and offer is not None:
        chased = (offer - c["close"]) if best["side"] == "BUY" else (c["close"] - bid)
        if chased > MAX_CHASE_ATR * atr:
            return wait(f"{best['kind']} but price already ran {chased:.2f} "
                        f"(> {MAX_CHASE_ATR} ATR). Not chasing.", atr=atr, score=best["score"])

    return {
        "signal": best["side"], "score": best["score"], "kind": best["kind"],
        "id": best["id"], "atr": atr, "sl_ref": best["sl_ref"],
        "reason": f"{best['kind']} • score {best['score']}/6 • "
                  f"MACD {c['macd']:.3f} • hist {c['hist']:.3f} • ATR {atr:.2f}",
    }


def refresh_signal(cfg=None):
    cfg = cfg or st.session_state.cfg
    d = completed_df()
    if d.empty or len(d) < MIN_BARS:
        sig = {"signal": "WAIT", "score": 0, "id": None, "kind": None, "atr": None,
               "sl_ref": None, "reason": f"Need {MIN_BARS} closed candles."}
    else:
        sig = evaluate_signal(add_indicators(d), st.session_state.live_bid,
                              st.session_state.live_offer, cfg)
    st.session_state.signal = sig
    return sig


# ============================================================
# PERSISTENCE (survives browser refresh / Streamlit restart)
# ============================================================

def save_runtime():
    ss = st.session_state
    try:
        with open(STATE_FILE, "w") as fh:
            json.dump({"cycle": ss.cycle, "active_normal": ss.active_normal}, fh, default=str)
    except Exception as exc:
        log(f"State save failed: {exc}")

def load_runtime():
    ss = st.session_state
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as fh:
                data = json.load(fh)
            ss.cycle = data.get("cycle")
            ss.active_normal = data.get("active_normal")
    except Exception as exc:
        log(f"State load failed: {exc}")
    ss.cycle_loaded = True

# ============================================================
# RISK ENGINE
# ============================================================

def opposite(direction):
    return "SELL" if direction == "BUY" else "BUY"


def legs_points(legs, price_buy_mark, price_sell_mark):
    """P&L in (size x points). BUY legs marked at bid, SELL legs at offer."""
    total = 0.0
    for leg in legs:
        if leg["direction"] == "BUY":
            total += (price_buy_mark - leg["level"]) * leg["size"]
        else:
            total += (leg["level"] - price_sell_mark) * leg["size"]
    return total


def entry_gate(cfg, positions):
    ss = st.session_state
    reset_daily_if_needed()

    if not ss.bot_running:
        return False, "Bot is stopped (no new entries)."
    if ss.market_status != "TRADEABLE":
        return False, f"Market not tradeable ({ss.market_status})."
    if ss.live_bid is None or ss.live_offer is None:
        return False, "No live quote."
    spread = ss.live_offer - ss.live_bid
    if spread > MAX_SPREAD:
        return False, f"Spread too wide ({spread:.2f})."
    if ss.trade_count >= cfg["max_trades"]:
        return False, f"Daily trade limit {cfg['max_trades']} reached."
    if ss.cooldown_until and now_utc() < ss.cooldown_until:
        wait = int((ss.cooldown_until - now_utc()).total_seconds())
        return False, f"Cooldown ({wait}s left)."
    if ss.equity is not None and ss.day_start_equity is not None:
        lost = ss.day_start_equity - ss.equity
        if lost >= cfg["max_daily_loss"]:
            return False, f"Daily loss limit hit ({lost:.2f} >= {cfg['max_daily_loss']:.2f})."
    if ss.cycle or ss.active_normal:
        return False, "A trade/cycle is already being managed."
    if positions:
        return False, "Unmanaged XAU position open — close it or press 'Close ALL'."
    return True, "OK"


def start_cooldown(cfg):
    st.session_state.cooldown_until = now_utc() + timedelta(minutes=cfg["cooldown_min"])


# ---------------- NORMAL MODE --------------------------------

def update_position(deal_id, stop_level, limit_level):
    body = {"stopLevel": round(stop_level, 2), "trailingStop": False}
    if limit_level:
        body["limitLevel"] = round(limit_level, 2)
    res = ig_request("PUT", f"/positions/otc/{deal_id}", "2", body)
    return confirm_deal(res["dealReference"])
def open_normal(sig, cfg):
    ss = st.session_state
    m = get_ig_market()
    direction, atr = sig["signal"], sig["atr"]
    entry = m["offer"] if direction == "BUY" else m["bid"]

    floor_dist = max(ss.min_stop * 1.2, MIN_STOP_DISTANCE)
    dist = abs(entry - sig["sl_ref"]) + 0.25 * atr      # stop just beyond the swing
    if dist > max(2.5 * atr, floor_dist):
        raise RiskRefused(f"Structure stop too wide ({dist:.2f} > 2.5 ATR). Skipped.")
    dist = max(dist, floor_dist)
    limit = max(dist * cfg["rr"], ss.min_stop * 1.2)

    size = min(cfg["size"], floor2(cfg["risk_usd"] / (dist * cfg["contract"])))
    if size < ss.min_deal_size - 1e-9:
        raise RiskRefused(f"Risk ${cfg['risk_usd']:.2f} too small for stop {dist:.2f} "
                          f"at min size {ss.min_deal_size}. Skipped.")
    size = round(size, 2)

    res = open_position(direction, size, dist, limit)
    level = safe_float(res.get("level")) or entry
    ss.active_normal = {
        "dealId": res.get("dealId"), "direction": direction, "size": size,
        "entry": level, "stop_dist": dist, "limit_dist": limit,
        "be_done": False, "equity_at_open": ss.equity,
        "signal": sig.get("reason"), "opened": now_utc().isoformat(),
    }
    ss.trade_count += 1
    ss.last_order = {"mode": "Normal", **ss.active_normal}
    log(f"NORMAL {direction} {size} @ {level:.2f} | SL {dist:.2f} | TP {limit:.2f} "
        f"| risk=${dist * size * cfg['contract']:.2f}")
    save_runtime()    

def manage_normal(positions, m, cfg):
    ss = st.session_state
    a = ss.active_normal
    pos = next((p for p in positions if p["dealId"] == a["dealId"]), None)

    if pos is None:                                   # closed by SL / TP / manual
        refresh_account(force=True)
        before = a.get("equity_at_open")
        delta = (ss.equity - before) if (ss.equity is not None and before is not None) else None
        note = f" | result ≈ {delta:+.2f}" if delta is not None else ""
        log(f"Normal trade closed{note}")
        ss.active_normal = None
        start_cooldown(cfg)
        save_runtime()
        return

    if not a["be_done"]:                              # move stop to break-even (+10% of risk)
        if a["direction"] == "BUY":
            favourable = m["bid"] - a["entry"]
        else:
            favourable = a["entry"] - m["offer"]
        if favourable >= cfg["be_trigger"] * a["stop_dist"]:
            sign = 1 if a["direction"] == "BUY" else -1
            new_stop = a["entry"] + sign * 0.1 * a["stop_dist"]
            gap = (m["bid"] - new_stop) if sign == 1 else (new_stop - m["offer"])
            if gap >= ss.min_stop * 1.2:
                update_position(a["dealId"], new_stop, pos.get("limitLevel"))
                a["be_done"] = True
                log(f"Break-even set at {new_stop:.2f}")
                save_runtime()


# ---------------- HEDGING MARTINGALE (ZONE RECOVERY) -------------
#
#  First trade at E (say BUY). Zone = [L, U] = [E - Z, E].
#  Price falls to L  -> open SELL hedge (bigger size).
#  Price rises to U  -> open BUY  (bigger again) ... alternating.
#  Every new leg is sized so that a move of T beyond the zone in its
#  favour clears the whole basket PLUS the base profit target.
#  Basket closes at target. HARD STOPS:
#     * max_legs
#     * max_cycle_risk : a leg is only opened if the loss locked at the
#       opposite edge stays below the cap; otherwise the cycle is CUT.
#     * disaster stop on every leg in case the bot dies.

def plan_next_leg(legs, U, L, Z, T, cfg):
    """Return (size, reason). size=None means: do NOT hedge, cut the cycle."""
    if len(legs) >= cfg["max_legs"]:
        return None, f"max legs ({cfg['max_legs']}) reached"

    s0 = legs[0]["size"]
    new_dir = opposite(legs[-1]["direction"])
    open_level = L if new_dir == "SELL" else U
    far_level = U if new_dir == "SELL" else L

    p_now = legs_points(legs, open_level, open_level)
    buys = sum(x["size"] for x in legs if x["direction"] == "BUY")
    sells = sum(x["size"] for x in legs if x["direction"] == "SELL")
    net_against = (buys - sells) if new_dir == "SELL" else (sells - buys)

    needed = (s0 * T - p_now) / T + net_against
    size = max(needed, legs[-1]["size"] * cfg["mult"])
    size = ceil2(size)

    new_leg = {"direction": new_dir, "size": size, "level": open_level}
    p_far = legs_points(legs + [new_leg], far_level, far_level)
    locked_loss = -p_far * cfg["contract"]
    if locked_loss > cfg["max_cycle_risk"]:
        return None, (f"next leg {size:.2f} would lock ${locked_loss:.2f} "
                      f" > cycle cap ${cfg['max_cycle_risk']:.2f}")
    return size, "ok"

def fit_first_size(direction, Z, T, cfg, min_size):
    """Largest size <= cfg size for which at least one hedge fits the cycle cap."""
    U, L = (0.0, -Z) if direction == "BUY" else (Z, 0.0)
    level0 = U if direction == "BUY" else L
    size = cfg["size"]
    while size >= min_size - 1e-9:
        legs = [{"direction": direction, "size": size, "level": level0}]
        nxt, _ = plan_next_leg(legs, U, L, Z, T, cfg)
        if nxt is not None:
            return round(size, 2)
        smaller = floor2(size * 0.9)
        size = smaller if smaller < size else round(size - 0.01, 2)
    return None

def open_cycle(sig, cfg):
    ss = st.session_state
    m = get_ig_market()
    direction, atr = sig["signal"], sig["atr"]
    Z = max(cfg["zone_mult"] * atr, MIN_STOP_DISTANCE, ss.min_stop * 1.2)
    T = Z * cfg["rr"]
    s0 = fit_first_size(direction, Z, T, cfg, ss.min_deal_size)
    if s0 is None:
        raise RiskRefused(f"Cycle cap ${cfg['max_cycle_risk']:.2f} too small for zone {Z:.2f}: cannot even afford one hedge at min size. Skipped.")
    res = open_position(direction, s0, Z * 1.5, Z * 3)
    level = safe_float(res.get("level")) or (m["offer"] if direction == "BUY" else m["bid"])
    U, L = (level, level - Z) if direction == "BUY" else (level + Z, level)
    ss.cycle = {
        "direction": direction, "Z": Z, "T": T, "U": U, "L": L,
        "target_usd": s0 * T * cfg["contract"],
        "opened": now_utc().isoformat(), "disaster": disaster,
        "equity_at_open": ss.equity,
        "legs": [{"dealId": res.get("dealId"), "direction": direction, "size": s0, "level": level}],
    }
    ss.trade_count += 1
    ss.last_order = {"mode": "Hedging Martingale", **ss.cycle}
    log(f"CYCLE {direction} {s0} @ {level:.2f} | zone {L:.2f}-{U:.2f} | target ${ss.cycle['target_usd']:.2f} | cap ${cfg['max_cycle_risk']:.2f}")
    save_runtime()

def finish_cycle(reason, cfg):
    ss = st.session_state
    close_all_positions()
    remaining = list_positions()
    if remaining:
        set_error(f"Could not close {len(remaining)} position(s) - retrying next tick.")
        return False
    refresh_account(force=True)
    before = (ss.cycle or {}).get("equity_at_open")
    delta = (ss.equity - before) if (ss.equity is not None and before is not None) else None
    note = f" | result ~ ${delta:+.2f}" if delta is not None else ""
    log(f"Cycle closed: {reason}{note}")
    ss.cycle = None
    start_cooldown(cfg)
    save_runtime()
    return True

def manage_cycle(positions, m, cfg):
    ss = st.session_state
    c = ss.cycle
    legs = c["legs"]
    by_id = {p["dealId"]: p for p in positions}

    if len(positions) != len(legs) or any(l["dealId"] not in by_id for l in legs):
        finish_cycle("positions out of sync (stop hit / manual close) - flattened", cfg)
        return

    for leg in legs:  # use real fill levels
        real = by_id[leg["dealId"]]
        if real.get("level"):
            leg["level"] = real["level"]

    pnl_usd = legs_points(legs, m["bid"], m["offer"]) * cfg["contract"]
    c["pnl_usd"] = pnl_usd
    ss = st.session_state
    m = get_ig_market()
    direction, atr = sig["signal"], sig["atr"]

    Z = max(cfg["zone_mult"] * atr, MIN_STOP_DISTANCE, ss.min_stop * 1.2)
    T = Z * cfg["rr"]
    s0 = fit_first_size(direction, Z, T, cfg, ss.min_deal_size)
    if s0 is None:
        raise RiskRefused(f"Cycle cap ${cfg['max_cycle_risk']:.2f} too small for zone {Z:.2f}: "
                          f"cannot even afford one hedge at min size. Skipped.")

    res = open_position(direction, s0, Z * 1.5, Z * 3)
    level = safe_float(res.get("level")) or (m["offer"] if direction == "BUY" else m["bid"])
    U, L = (level, level - Z) if direction == "BUY" else (level + Z, level)

    ss.cycle = {
        "direction": direction, "Z": Z, "T": T, "U": U, "L": L,
        "target_usd": s0 * T * cfg["contract"],
        "opened": now_utc().isoformat(), "disaster": disaster,
        "equity_at_open": ss.equity,
        "legs": [{"dealId": res.get("dealId"), "direction": direction,
                 "size": s0, "level": level}],
    }
    ss.trade_count += 1
    ss.last_order = {"mode": "Hedging Martingale", **ss.cycle}
    log(f"CYCLE {direction} {s0} @ {level:.2f} | zone {L:.2f}-{U:.2f} | "
        f"target ${ss.cycle['target_usd']:.2f} | cap ${cfg['max_cycle_risk']:.2f}")
    save_runtime()

def finish_cycle(reason, cfg):
    ss = st.session_state
    close_all_positions()
    remaining = list_positions()
    if remaining:
        set_error(f"Could not close {len(remaining)} position(s) - retrying next tick.")
        return False
    refresh_account(force=True)
    before = (ss.cycle or {}).get("equity_at_open")
    delta = (ss.equity - before) if (ss.equity is not None and before is not None) else None
    note = f" | result ~ ${delta:+.2f}" if delta is not None else ""
    log(f"Cycle closed: {reason}{note}")
    ss.cycle = None
    start_cooldown(cfg)
    save_runtime()
    return True       
  def manage_cycle(positions, m, cfg):
    ss = st.session_state
    c = ss.cycle
    legs = c["legs"]
    by_id = {p["dealId"]: p for p in positions}

    if len(positions) != len(legs) or any(l["dealId"] not in by_id for l in legs):
        finish_cycle("positions out of sync (stop hit / manual close) — flattened", cfg)
        return

    for leg in legs:                                   # use real fill levels
        real = by_id[leg["dealId"]]
        if real.get("level"):
            leg["level"] = real["level"]

    pnl_usd = legs_points(legs, m["bid"], m["offer"]) * cfg["contract"]
    c["pnl_usd"] = pnl_usd

    if pnl_usd >= c["target_usd"]:
        finish_cycle(f"TARGET reached (+${pnl_usd:.2f})", cfg)
        return

    age_min = (now_utc() - datetime.fromisoformat(c["opened"])).total_seconds() / 60
    if age_min > CYCLE_MAX_MIN:
        finish_cycle(f"time stop after {age_min:.0f} min ({pnl_usd:+.2f})", cfg)
        return

    new_dir = opposite(legs[-1]["direction"])
    triggered = (m["bid"] <= c["L"]) if new_dir == "SELL" else (m["offer"] >= c["U"])
    if not triggered:
        return

    size, why = plan_next_leg(legs, c["U"], c["L"], c["Z"], c["T"], cfg)
    if size is None:
        finish_cycle(f"CUT — {why} (floating {pnl_usd:+.2f})", cfg)
        return

    res = open_position(new_dir, size, stop_distance=c["disaster"])
    fill = safe_float(res.get("level")) or (m["bid"] if new_dir == "SELL" else m["offer"])
    legs.append({"dealId": res.get("dealId"), "direction": new_dir, "size": size, "level": fill})
    log(f"HEDGE leg {len(legs)}: {new_dir} {size} @ {fill:.2f} (basket {pnl_usd:+.2f})")
    save_runtime()


# ============================================================
# ENTRY + AUTOMATION LOOP
# ============================================================

def try_entry(sig, cfg, positions):
    ss = st.session_state
    if sig["signal"] not in ("BUY", "SELL") or sig["id"] in ss.done_signal_ids:
        return

    ok, why = entry_gate(cfg, positions)
    ss.gate_msg = None if ok else why
    if not ok:
        return

    try:
        if cfg["mode"] == "Normal":
            open_normal(sig, cfg)
        else:
            open_cycle(sig, cfg)
        ss.done_signal_ids.append(sig["id"])
        ss.done_signal_ids = ss.done_signal_ids[-200:]
    except RiskRefused as exc:
        ss.done_signal_ids.append(sig["id"])
        ss.gate_msg = str(exc)
        log(f"Trade refused: {exc}")


def automation_tick():
    """Runs whenever IG is connected. Open trades are ALWAYS managed;
    'bot_running' only controls whether NEW entries are allowed."""
    ss = st.session_state
    cfg = ss.cfg
    try:
        reset_daily_if_needed()
        if not ss.cycle_loaded:
            load_runtime()
        m = get_ig_market()
        update_live_bar(m["mid"])
        refresh_account()
        sig = refresh_signal(cfg)
        positions = list_positions()
        ss.gate_msg = None

        if ss.cycle:
            manage_cycle(positions, m, cfg)
        elif ss.active_normal:
            manage_normal(positions, m, cfg)
        elif ss.bot_running:
            try_entry(sig, cfg, positions)
        ss.last_error = None
    except Exception as exc:
        set_error(str(exc))
# ============================================================
# DASHBOARD
# ============================================================

def render_dashboard():
    ss = st.session_state
    cfg = ss.cfg
    reset_daily_if_needed()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Mode", cfg["mode"])
    c2.metric("Trades today", f"{ss.trade_count}/{cfg['max_trades']}")
    c3.metric("Entries", "ACTIVE" if ss.bot_running else "STOPPED")
    sig = ss.signal
    c4.metric("Signal", {"BUY": "🟢 BUY", "SELL": "🔴 SELL"}.get(sig["signal"], "⏳ WAIT"))

    p1, p2, p3, p4 = st.columns(4)
    p1.metric("XAU/USD", f"{ss.live_mid:.2f}" if ss.live_mid else "—")
    p2.metric("Bid / Offer", f"{ss.live_bid:.2f} / {ss.live_offer:.2f}" if ss.live_bid else "—")
    p3.metric("Equity", f"{ss.equity:,.2f}" if ss.equity is not None else "—")
    day = (ss.equity - ss.day_start_equity) if (ss.equity is not None and ss.day_start_equity is not None) else None
    p4.metric("Day P&L", f"{day:+.2f}" if day is not None else "—",
              delta=f"limit -{cfg['max_daily_loss']:.0f}", delta_color="off")

    if ss.market_status:
        st.caption(f"IG market status: `{ss.market_status}`  •  min stop `{ss.min_stop}`  •  "
                   f"min size `{ss.min_deal_size}`")
    st.divider()

    st.subheader("🤖 Signal engine")
    text = f"{sig['signal']} — {sig['reason']}"
    (st.success if sig["signal"] == "BUY" else st.error if sig["signal"] == "SELL" else st.info)(text)
    if ss.gate_msg:
        st.warning(f"Gate: {ss.gate_msg}")

    if ss.cycle:
        c = ss.cycle
        st.subheader("♟️ Active hedging cycle")
        st.write(f"Zone **{c['L']:.2f} – {c['U']:.2f}**  •  target **${c['target_usd']:.2f}**  •  "
                 f"floating **{c.get('pnl_usd', 0):+.2f}**  •  legs **{len(c['legs'])}/{cfg['max_legs']}**")
        st.dataframe(pd.DataFrame(c["legs"])[["direction", "size", "level", "dealId"]],
                     use_container_width=True, hide_index=True)
    elif ss.active_normal:
        a = ss.active_normal
        st.subheader("🎯 Active trade")
        st.write(f"**{a['direction']} {a['size']}** @ {a['entry']:.2f}  •  SL dist {a['stop_dist']:.2f}  •  "
                 f"TP dist {a['limit_dist']:.2f}  •  BE {'✅' if a['be_done'] else '—'}")

    if ss.last_error:
        st.error("⚠️ " + ss.last_error)

    st.subheader("📜 Log")
    st.code("\n".join(ss.log[:15]) if ss.log else "No events yet.", language=None)

    st.subheader("📊 Last closed candles + MACD")
    d = completed_df()
    if len(d) >= MIN_BARS:
        view = add_indicators(d).tail(15)[["time", "open", "high", "low", "close", "macd", "sig", "hist"]].copy()
        view["time"] = view["time"].dt.strftime("%H:%M")
        st.dataframe(view.round(3), use_container_width=True, hide_index=True)
    else:
        st.info(f"{len(d)}/{MIN_BARS} closed candles.")


# ============================================================
# PAGE + SIDEBAR
# ============================================================

st.title("🐎 Quantum X PRO")
st.caption("IG Demo • XAU/USD M5 • MACD divergence scalper • Normal & Hedging Martingale")
with st.sidebar:
    cfg = st.session_state.cfg
    st.header("⚙️ Strategy")

    cfg["mode"] = st.radio("Bot mode", ["Normal", "Hedging Martingale"],
                           index=0 if cfg["mode"] == "Normal" else 1)
    cfg["signal_mode"] = st.selectbox(
        "Signals", ["Divergence + MACD cross", "Divergence only"],
        index=0 if cfg["signal_mode"] == "Divergence + MACD cross" else 1)
    cfg["min_score"] = st.slider("Min signal score (higher = stricter)", 3, 6, int(cfg["min_score"]))
    cfg["size"] = st.number_input("Max trade size", 0.01, 100.0, float(cfg["size"]), 0.01, format="%.2f")
    cfg["rr"] = st.number_input("Risk / Reward", 0.5, 5.0, float(cfg["rr"]), 0.1, format="%.1f")
    cfg["contract"] = st.number_input("USD per point per 1.0 size", 0.01, 1000.0, float(cfg["contract"]),
                                      help="Check your IG contract: P&L for a 1.0 size, 1 point move.")

    st.header("🛡️ Risk")
    cfg["max_daily_loss"] = st.number_input("Max daily loss (USD)", 1.0, 100000.0, float(cfg["max_daily_loss"]))
    cfg["max_trades"] = int(st.number_input("Max trades / cycles per day", 1, 100, int(cfg["max_trades"])))
    cfg["cooldown_min"] = int(st.number_input("Cooldown after close (min)", 0, 120, int(cfg["cooldown_min"])))

    if cfg["mode"] == "Normal":
        cfg["risk_usd"] = st.number_input("Risk per trade (USD)", 0.5, 10000.0, float(cfg["risk_usd"]))
        cfg["be_trigger"] = st.slider("Break-even at (× risk)", 0.3, 1.5, float(cfg["be_trigger"]), 0.1)
    else:
        st.warning("Martingale sizing grows every leg. The hard cap below is what protects you.")
        cfg["max_cycle_risk"] = st.number_input("HARD max loss per cycle (USD)", 1.0, 100000.0,
                                                float(cfg["max_cycle_risk"]))
        cfg["max_legs"] = int(st.slider("Max legs per cycle", 2, 6, int(cfg["max_legs"])))
        cfg["mult"] = st.slider("Min size growth per leg", 1.2, 3.0, float(cfg["mult"]), 0.1)
        cfg["zone_mult"] = st.slider("Zone width (× ATR)", 0.6, 3.0, float(cfg["zone_mult"]), 0.1)

    st.divider()
    ss = st.session_state
    st.caption(f"Epic: `{IG_EPIC}`  •  max spread {MAX_SPREAD}")

    if st.button("🔌 Connect IG Demo", use_container_width=True):
        try:
            ig_login()
            count = bootstrap_m5()
            load_runtime()
            refresh_account(force=True)
            ss.last_error = None
            st.success(f"Connected • {count} M5 candles loaded.")
        except Exception as exc:
            ss.ig_connected = False
            ss.last_error = str(exc)
    st.success("🟢 IG DEMO CONNECTED") if ss.ig_connected else st.warning("🔴 IG NOT CONNECTED")

    if st.button("🚀 Start Bot", use_container_width=True, disabled=not ss.ig_connected):
        try:
            if len(ss.bars) < MIN_BARS:
                bootstrap_m5()
            ss.bot_running = True
            ss.last_error = None
            ss.cooldown_until = None
            automation_tick()               # evaluate + trade RIGHT NOW, no waiting
        except Exception as exc:
            ss.last_error = str(exc)

    if st.button("🛑 Stop Bot (no new entries)", use_container_width=True):
        ss.bot_running = False
    st.success("🟢 BOT RUNNING") if ss.bot_running else st.info("⏹️ BOT STOPPED")
    st.caption("Stopping only blocks NEW entries. Open trades/cycles keep being managed.")

    if st.button("❌ Close ALL XAU positions", use_container_width=True, disabled=not ss.ig_connected):
        try:
            ss.bot_running = False
            closed, failed = close_all_positions()
            ss.cycle, ss.active_normal = None, None
            save_runtime()
            log(f"Manual close: {closed} closed, {len(failed)} failed")
            if failed:
                ss.last_error = failed[0]
        except Exception as exc:
            ss.last_error = str(exc)


@st.fragment(run_every=POLL_SECONDS)
def live_automation():
    if st.session_state.ig_connected:
        automation_tick()
    render_dashboard()


live_automation()
