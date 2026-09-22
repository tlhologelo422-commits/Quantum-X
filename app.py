import os

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf


st.set_page_config(
    page_title="Quantum X PRO",
    page_icon="📈",
    layout="wide",
)


APP_NAME = "Quantum X PRO"


MARKETS = {
    "XAU/USD": {
        "yahoo": "GC=F",
        "tradingview": "OANDA:XAUUSD",
    },
    "EUR/USD": {
        "yahoo": "EURUSD=X",
        "tradingview": "FX:EURUSD",
    },
    "GBP/USD": {
        "yahoo": "GBPUSD=X",
        "tradingview": "FX:GBPUSD",
    },
    "USD/JPY": {
        "yahoo": "JPY=X",
        "tradingview": "FX:USDJPY",
    },
    "USD/CHF": {
        "yahoo": "CHF=X",
        "tradingview": "FX:USDCHF",
    },
    "AUD/USD": {
        "yahoo": "AUDUSD=X",
        "tradingview": "FX:AUDUSD",
    },
    "USD/CAD": {
        "yahoo": "CAD=X",
        "tradingview": "FX:USDCAD",
    },
    "NZD/USD": {
        "yahoo": "NZDUSD=X",
        "tradingview": "FX:NZDUSD",
    },
}


TIMEFRAMES = {
    "5M": "5m",
    "15M": "15m",
    "30M": "30m",
    "1H": "60m",
    "1D": "1d",
}


DEFAULT_STATE = {
    "yahoo_data": None,
    "selected_market": "XAU/USD",
    "selected_timeframe": "15M",
    "scan_requested": False,
}


for key, value in DEFAULT_STATE.items():

    if key not in st.session_state:

        st.session_state[key] = value


def get_yahoo_candles(
    ticker_symbol,
    interval="15m",
    period="30d",
):

    try:

        dataframe = yf.download(
            ticker_symbol,
            interval=interval,
            period=period,
            auto_adjust=False,
            progress=False,
        )

        if dataframe is None:
            return None

        if dataframe.empty:
            return None

        if isinstance(
            dataframe.columns,
            pd.MultiIndex,
        ):

            dataframe.columns = (
                dataframe.columns
                .get_level_values(0)
            )

        required_columns = [
            "Open",
            "High",
            "Low",
            "Close",
            "Volume",
        ]

        missing_columns = [
            column
            for column in required_columns
            if column not in dataframe.columns
        ]

        if missing_columns:
            return None

        dataframe = dataframe[
            required_columns
        ].copy()

        dataframe = dataframe.dropna(
            subset=[
                "Open",
                "High",
                "Low",
                "Close",
            ]
        )

        dataframe.columns = [
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]

        dataframe = dataframe.reset_index()

        if "Datetime" in dataframe.columns:

            dataframe = dataframe.rename(
                columns={
                    "Datetime": "timestamp"
                }
            )

        elif "Date" in dataframe.columns:

            dataframe = dataframe.rename(
                columns={
                    "Date": "timestamp"
                }
            )

        if "timestamp" in dataframe.columns:

            dataframe["timestamp"] = pd.to_datetime(
                dataframe["timestamp"],
                utc=True,
            )

        return dataframe

    except Exception as error:

        st.session_state["data_error"] = str(
            error
        )

        return None
def calculate_indicators(dataframe):

    df = dataframe.copy()

    df["ema_20"] = (
        df["close"]
        .ewm(span=20, adjust=False)
        .mean()
    )

    df["ema_50"] = (
        df["close"]
        .ewm(span=50, adjust=False)
        .mean()
    )

    df["ema_200"] = (
        df["close"]
        .ewm(span=200, adjust=False)
        .mean()
    )

    change = df["close"].diff()

    gain = change.clip(lower=0)
    loss = -change.clip(upper=0)

    average_gain = (
        gain.rolling(14)
        .mean()
    )

    average_loss = (
        loss.rolling(14)
        .mean()
    )

    relative_strength = (
        average_gain /
        average_loss.replace(0, float("nan"))
    )

    df["rsi"] = (
        100 -
        (
            100 /
            (1 + relative_strength)
        )
    )

    ema_12 = (
        df["close"]
        .ewm(span=12, adjust=False)
        .mean()
    )

    ema_26 = (
        df["close"]
        .ewm(span=26, adjust=False)
        .mean()
    )

    df["macd"] = (
        ema_12 - ema_26
    )

    df["macd_signal"] = (
        df["macd"]
        .ewm(span=9, adjust=False)
        .mean()
    )

    previous_close = (
        df["close"].shift(1)
    )

    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (
                df["high"] -
                previous_close
            ).abs(),
            (
                df["low"] -
                previous_close
            ).abs(),
        ],
        axis=1,
    ).max(axis=1)

    df["atr"] = (
        true_range
        .rolling(14)
        .mean()
    )

    df["volume_average"] = (
        df["volume"]
        .rolling(20)
        .mean()
    )

    df["volume_ratio"] = (
        df["volume"] /
        df["volume_average"]
        .replace(0, float("nan"))
    )

    return df


def detect_market_structure(dataframe):

    df = dataframe.copy()

    df["swing_high"] = (
        df["high"]
        .rolling(5, center=True)
        .max()
    )

    df["swing_low"] = (
        df["low"]
        .rolling(5, center=True)
        .min()
    )

    latest = df.iloc[-1]

    previous_high = (
        df["high"]
        .iloc[-20:-1]
        .max()
    )

    previous_low = (
        df["low"]
        .iloc[-20:-1]
        .min()
    )

    if latest["close"] > previous_high:

        structure = "Bullish BOS"

    elif latest["close"] < previous_low:

        structure = "Bearish BOS"

    elif latest["close"] > latest["ema_50"]:

        structure = "Bullish Structure"

    elif latest["close"] < latest["ema_50"]:

        structure = "Bearish Structure"

    else:

        structure = "Neutral Structure"

    return structure


def build_market_snapshot(dataframe):

    latest = dataframe.iloc[-1]

    structure = detect_market_structure(
        dataframe
    )

    snapshot = {
        "price": float(
            latest["close"]
        ),
        "ema_20": float(
            latest["ema_20"]
        ),
        "ema_50": float(
            latest["ema_50"]
        ),
        "ema_200": float(
            latest["ema_200"]
        ),
        "rsi": float(
            latest["rsi"]
        ),
        "macd": float(
            latest["macd"]
        ),
        "macd_signal": float(
            latest["macd_signal"]
        ),
        "atr": float(
            latest["atr"]
        ),
        "volume_ratio": float(
            latest["volume_ratio"]
        ),
        "structure": structure,
    }

    return snapshot
def render_tradingview_chart(
    market_name,
    timeframe,
):

    symbol = MARKETS[
        market_name
    ]["tradingview"]

    interval = TIMEFRAMES[
        timeframe
    ]

    chart_html = f"""
    <div
        id="tradingview_chart"
        style="
            height:600px;
            width:100%;
        ">
    </div>

    <script
        src="https://s3.tradingview.com/tv.js">
    </script>

    <script>

    new TradingView.widget({{
        "autosize": true,
        "symbol": "{symbol}",
        "interval": "{interval}",
        "timezone": "Africa/Johannesburg",
        "theme": "dark",
        "style": "1",
        "locale": "en",
        "enable_publishing": false,
        "hide_top_toolbar": false,
        "hide_legend": false,
        "save_image": false,
        "container_id":
            "tradingview_chart"
    }});

    </script>
    """

    components.html(
        chart_html,
        height=620,
        scrolling=False,
    )


def display_market_snapshot(
    snapshot,
):

    price = snapshot["price"]

    st.subheader(
        "📊 Market Intelligence"
    )

    col1, col2, col3, col4 = (
        st.columns(4)
    )

    with col1:

        st.metric(
            "Price",
            f"{price:.5f}",
        )

    with col2:

        st.metric(
            "RSI",
            f"{snapshot['rsi']:.2f}",
        )

    with col3:

        st.metric(
            "ATR",
            f"{snapshot['atr']:.5f}",
        )

    with col4:

        st.metric(
            "Structure",
            snapshot["structure"],
        )

    st.subheader(
        "📐 Technical Evidence"
    )

    tech_col1, tech_col2 = (
        st.columns(2)
    )

    with tech_col1:

        st.write(
            f"EMA 20: "
            f"{snapshot['ema_20']:.5f}"
        )

        st.write(
            f"EMA 50: "
            f"{snapshot['ema_50']:.5f}"
        )

        st.write(
            f"EMA 200: "
            f"{snapshot['ema_200']:.5f}"
        )

    with tech_col2:

        st.write(
            f"MACD: "
            f"{snapshot['macd']:.5f}"
        )

        st.write(
            f"MACD Signal: "
            f"{snapshot['macd_signal']:.5f}"
        )

        st.write(
            f"Volume Ratio: "
            f"{snapshot['volume_ratio']:.2f}x"
        )


def show_analysis_engine():

    st.subheader(
        "🧠 Analysis Engine"
    )

    col1, col2, col3 = (
        st.columns(3)
    )

    with col1:

        st.markdown(
            "### 📐 Technical Analysis"
        )

        st.write(
            "EMA, RSI, MACD, ATR "
            "and volume analysis."
        )

    with col2:

        st.markdown(
            "### 🧱 Smart Money Concepts"
        )

        st.write(
            "Market structure, BOS, "
            "liquidity and future SMC "
            "modules."
        )

    with col3:

        st.markdown(
            "### 🤖 AI Reasoning"
        )

        st.write(
            "GPT-5.6 Sol will reason "
            "over deterministic "
            "market evidence."
    )
