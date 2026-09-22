import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
import streamlit as st

# ============================================================
# QUANTUM X PRO — ALGORITHMIC SCALPER
# ============================================================

st.set_page_config(
    page_title="Quantum X PRO",
    page_icon="⚛️",
    layout="wide",
)

# ============================================================
# CONFIGURATION
# ============================================================

INSTRUMENTS = {
    "XAUUSD": {
        "label": "Gold",
        "direction": "BUY",
    },
    "EURUSD": {
        "label": "EUR/USD",
        "direction": "SELL",
    },
    "NAS100": {
        "label": "NAS100",
        "direction": "BUY",
    },
}

TIMEFRAMES = {
    "1 Minute": "1m",
    "5 Minutes": "5m",
    "15 Minutes": "15m",
}

MIN_SCORE = 70.0


# ============================================================
# DATA MODEL
# ============================================================

@dataclass
class Signal:
    instrument: str
    direction: str
    score: float
    status: str
    entry: Optional[float]
    stop_loss: Optional[float]
    take_profit: Optional[float]
    atr: Optional[float]
    reasons: list


# ============================================================
# INDICATORS
# ============================================================

def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]

    previous_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return tr.rolling(period).mean()


def calculate_rsi(
    df: pd.DataFrame,
    period: int = 14,
) -> pd.Series:

    delta = df["close"].diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    return 100 - (100 / (1 + rs))


def calculate_ema(
    df: pd.DataFrame,
    period: int,
) -> pd.Series:
    return df["close"].ewm(
        span=period,
        adjust=False,
    ).mean()


def calculate_volume_ratio(
    df: pd.DataFrame,
    period: int = 20,
) -> pd.Series:

    average_volume = (
        df["volume"]
        .rolling(period)
        .mean()
    )

    return df["volume"] / average_volume


# ============================================================
# MARKET STRUCTURE
# ============================================================

def calculate_previous_day_levels(
    df: pd.DataFrame,
):
    if "timestamp" not in df.columns:
        return None, None

    temp = df.copy()

    temp["date"] = pd.to_datetime(
        temp["timestamp"]
    ).dt.date

    daily = (
        temp.groupby("date")
        .agg(
            day_high=("high", "max"),
            day_low=("low", "min"),
        )
    )

    if len(daily) < 2:
        return None, None

    previous_day = daily.iloc[-2]

    return (
        float(previous_day["day_high"]),
        float(previous_day["day_low"]),
    )


def detect_liquidity_sweep(
    df: pd.DataFrame,
    level_high: Optional[float],
    level_low: Optional[float],
):
    if len(df) < 2:
        return False, False

    candle = df.iloc[-1]

    swept_high = False
    swept_low = False

    if level_high is not None:
        swept_high = (
            candle["high"] > level_high
            and candle["close"] < level_high
        )

    if level_low is not None:
        swept_low = (
            candle["low"] < level_low
            and candle["close"] > level_low
        )

    return swept_high, swept_low


# ============================================================
# FAIR VALUE GAP
# ============================================================

def detect_fvg(df: pd.DataFrame):

    if len(df) < 3:
        return False, False

    c1 = df.iloc[-3]
    c3 = df.iloc[-1]

    bullish_fvg = c3["low"] > c1["high"]

    bearish_fvg = c3["high"] < c1["low"]

    return bullish_fvg, bearish_fvg


# ============================================================
# FEATURE ENGINE
# ============================================================

def calculate_features(
    df: pd.DataFrame,
    instrument: str,
):

    data = df.copy()

    data["ATR"] = calculate_atr(data)
    data["RSI"] = calculate_rsi(data)

    data["EMA20"] = calculate_ema(
        data,
        20,
    )

    data["EMA50"] = calculate_ema(
        data,
        50,
    )

    data["EMA200"] = calculate_ema(
        data,
        200,
    )

    data["VolumeRatio"] = calculate_volume_ratio(
        data
    )

    pdh, pdl = calculate_previous_day_levels(
        data
    )

    swept_high, swept_low = (
        detect_liquidity_sweep(
            data,
            pdh,
            pdl,
        )
    )

    bullish_fvg, bearish_fvg = detect_fvg(
        data
    )

    return {
        "data": data,
        "pdh": pdh,
        "pdl": pdl,
        "swept_high": swept_high,
        "swept_low": swept_low,
        "bullish_fvg": bullish_fvg,
        "bearish_fvg": bearish_fvg,
    }


# ============================================================
# QUANTUM SCORING ENGINE
# ============================================================

def generate_signal(
    df: pd.DataFrame,
    instrument: str,
) -> Signal:

    config = INSTRUMENTS[instrument]

    allowed_direction = config["direction"]

    features = calculate_features(
        df,
        instrument,
    )

    data = features["data"]

    latest = data.iloc[-1]

    price = float(latest["close"])

    atr = float(latest["ATR"])

    rsi = float(latest["RSI"])

    ema20 = float(latest["EMA20"])
    ema50 = float(latest["EMA50"])
    ema200 = float(latest["EMA200"])

    volume_ratio = float(
        latest["VolumeRatio"]
    )

    buy_score = 0.0
    sell_score = 0.0

    reasons = []

    # --------------------------------------------------------
    # TREND
    # --------------------------------------------------------

    if price > ema20:
        buy_score += 10

    if price < ema20:
        sell_score += 10

    if ema20 > ema50:
        buy_score += 10

    if ema20 < ema50:
        sell_score += 10

    if ema50 > ema200:
        buy_score += 10

    if ema50 < ema200:
        sell_score += 10

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    if rsi > 50:
        buy_score += 10

    if rsi < 50:
        sell_score += 10

    # Avoid blindly buying extreme RSI.
    if rsi < 70:
        buy_score += 5

    if rsi > 30:
        sell_score += 5

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    if volume_ratio >= 1.2:
        if latest["close"] > latest["open"]:
            buy_score += 10
        elif latest["close"] < latest["open"]:
            sell_score += 10

    # --------------------------------------------------------
    # LIQUIDITY
    # --------------------------------------------------------

    if features["swept_low"]:
        buy_score += 15
        reasons.append("PDL liquidity sweep")

    if features["swept_high"]:
        sell_score += 15
        reasons.append("PDH liquidity sweep")

    # --------------------------------------------------------
    # FVG
    # --------------------------------------------------------

    if features["bullish_fvg"]:
        buy_score += 10
        reasons.append("Bullish FVG")

    if features["bearish_fvg"]:
        sell_score += 10
        reasons.append("Bearish FVG")

    # --------------------------------------------------------
    # DIRECTION RESTRICTION
    # --------------------------------------------------------

    if allowed_direction == "BUY":
        final_score = buy_score
        direction = "BUY"

    else:
        final_score = sell_score
        direction = "SELL"

    final_score = min(
        100.0,
        float(final_score),
    )

    # --------------------------------------------------------
    # SIGNAL STATUS
    # --------------------------------------------------------

    if final_score >= MIN_SCORE:
        status = "QUALIFIED"
    else:
        status = "WAIT"

    entry = None
    stop_loss = None
    take_profit = None

    # --------------------------------------------------------
    # RISK MODEL
    # --------------------------------------------------------

    if (
        status == "QUALIFIED"
        and not math.isnan(atr)
        and atr > 0
    ):

        entry = price

        if direction == "BUY":

            stop_loss = entry - (
                atr * 1.5
            )

            take_profit = entry + (
                atr * 2.5
            )

        else:

            stop_loss = entry + (
                atr * 1.5
            )

            take_profit = entry - (
                atr * 2.5
            )

    return Signal(
        instrument=instrument,
        direction=direction,
        score=final_score,
        status=status,
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        atr=atr,
        reasons=reasons,
    )


# ============================================================
# IG DATA ADAPTER
# ============================================================

def get_market_data(
    instrument: str,
    timeframe: str,
) -> pd.DataFrame:
    """
    Connect this function to the existing IG demo
    market-data function.

    REQUIRED columns:

        timestamp
        open
        high
        low
        close
        volume

    Example:

        return your_existing_ig_function(
            instrument,
            timeframe
        )
    """

    # --------------------------------------------------------
    # TEMPORARY GUARD
    # --------------------------------------------------------
    #
    # Do NOT generate fake trading data here.
    # The application should use the actual IG feed.
    #

    return pd.DataFrame(
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    )


# ============================================================
# UI
# ============================================================

st.title("⚛️ Quantum X PRO")

st.caption(
    "Algorithmic multi-factor scalping engine • IG Demo"
)

# ------------------------------------------------------------
# SIDEBAR
# ------------------------------------------------------------

st.sidebar.header("Market")

instrument = st.sidebar.selectbox(
    "Instrument",
    list(INSTRUMENTS.keys()),
)

timeframe_name = st.sidebar.selectbox(
    "Execution timeframe",
    list(TIMEFRAMES.keys()),
)

timeframe = TIMEFRAMES[
    timeframe_name
]

st.sidebar.divider()

st.sidebar.write(
    f"**Allowed direction:** "
    f"{INSTRUMENTS[instrument]['direction']}"
)

# ------------------------------------------------------------
# GET IG DATA
# ------------------------------------------------------------

df = get_market_data(
    instrument,
    timeframe,
)

if df.empty:

    st.warning(
        "Waiting for IG market data..."
    )

    st.stop()

# ------------------------------------------------------------
# VALIDATE DATA
# ------------------------------------------------------------

required_columns = {
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
}

missing = (
    required_columns
    - set(df.columns)
)

if missing:

    st.error(
        "Missing market-data columns: "
        + ", ".join(missing)
    )

    st.stop()

df = df.copy()

df["timestamp"] = pd.to_datetime(
    df["timestamp"]
)

df = df.sort_values(
    "timestamp"
).reset_index(drop=True)

# ------------------------------------------------------------
# SIGNAL
# ------------------------------------------------------------

signal = generate_signal(
    df,
    instrument,
)

# ============================================================
# DASHBOARD
# ============================================================

latest_price = float(
    df.iloc[-1]["close"]
)

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        "Instrument",
        instrument,
    )

with col2:
    st.metric(
        "Price",
        f"{latest_price:.5f}",
    )

with col3:
    st.metric(
        "Quantum Score",
        f"{signal.score:.1f}/100",
    )

with col4:
    st.metric(
        "Signal",
        signal.status,
    )

# ============================================================
# CHART
# ============================================================

st.subheader(
    f"{instrument} • {timeframe_name}"
)

chart_data = df[
    ["timestamp", "close"]
].set_index("timestamp")

st.line_chart(
    chart_data,
    height=450,
)

# ============================================================
# INDICATOR PANEL
# ============================================================

st.subheader("Market Intelligence")

features = calculate_features(
    df,
    instrument,
)

latest = features["data"].iloc[-1]

c1, c2, c3, c4 = st.columns(4)

with c1:
    st.metric(
        "ATR",
        f"{latest['ATR']:.5f}",
    )

with c2:
    st.metric(
        "RSI",
        f"{latest['RSI']:.2f}",
    )

with c3:
    st.metric(
        "EMA 20",
        f"{latest['EMA20']:.5f}",
    )

with c4:
    st.metric(
        "Volume Ratio",
        f"{latest['VolumeRatio']:.2f}x",
    )

# ============================================================
# LEVELS
# ============================================================

st.subheader("Liquidity Levels")

l1, l2, l3 = st.columns(3)

with l1:
    st.metric(
        "PDH",
        (
            f"{features['pdh']:.5f}"
            if features["pdh"] is not None
            else "N/A"
        ),
    )

with l2:
    st.metric(
        "PDL",
        (
            f"{features['pdl']:.5f}"
            if features["pdl"] is not None
            else "N/A"
        ),
    )

with l3:

    if (
        features["pdh"] is not None
        and features["pdl"] is not None
    ):

        equilibrium = (
            features["pdh"]
            + features["pdl"]
        ) / 2

        st.metric(
            "Daily 50%",
            f"{equilibrium:.5f}",
        )

    else:
        st.metric(
            "Daily 50%",
            "N/A",
        )

# ============================================================
# SIGNAL PANEL
# ============================================================

st.subheader("Quantum Signal")

if signal.status == "QUALIFIED":

    st.success(
        f"⚡ {signal.direction} "
        f"QUALIFIED — "
        f"{signal.score:.1f}/100"
    )

    s1, s2, s3 = st.columns(3)

    with s1:
        st.metric(
            "Entry",
            (
                f"{signal.entry:.5f}"
                if signal.entry
                else "N/A"
            ),
        )

    with s2:
        st.metric(
            "Stop Loss",
            (
                f"{signal.stop_loss:.5f}"
                if signal.stop_loss
                else "N/A"
            ),
        )

    with s3:
        st.metric(
            "Take Profit",
            (
                f"{signal.take_profit:.5f}"
                if signal.take_profit
                else "N/A"
            ),
        )

else:

    st.info(
        f"⏳ NO TRADE — "
        f"{signal.direction} score "
        f"{signal.score:.1f}/100"
    )

# ============================================================
# REASONS
# ============================================================

st.subheader("Engine Evidence")

if signal.reasons:

    for reason in signal.reasons:
        st.write(
            f"✓ {reason}"
        )

else:

    st.write(
        "No qualifying structural confirmation yet."
    )

# ============================================================
# SYSTEM STATUS
# ============================================================

st.divider()

st.caption(
    "QUANTUM X PRO • IG DEMO • "
    "ORDER EXECUTION DISABLED"
)
