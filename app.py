import pandas as pd
import streamlit as st
import yfinance as yf
import plotly.graph_objects as go


st.set_page_config(
    page_title="Quantum X PRO",
    page_icon="📈",
    layout="wide",
)


APP_NAME = "Quantum X PRO"


MARKETS = {
    "XAU/USD": {
        "yahoo": "GC=F",
        "label": "Gold Futures",
    },
    "EUR/USD": {
        "yahoo": "EURUSD=X",
        "label": "Euro / US Dollar",
    },
    "GBP/USD": {
        "yahoo": "GBPUSD=X",
        "label": "British Pound / US Dollar",
    },
    "USD/JPY": {
        "yahoo": "JPY=X",
        "label": "US Dollar / Japanese Yen",
    },
    "USD/CHF": {
        "yahoo": "CHF=X",
        "label": "US Dollar / Swiss Franc",
    },
    "AUD/USD": {
        "yahoo": "AUDUSD=X",
        "label": "Australian Dollar / US Dollar",
    },
    "USD/CAD": {
        "yahoo": "CAD=X",
        "label": "US Dollar / Canadian Dollar",
    },
    "NZD/USD": {
        "yahoo": "NZDUSD=X",
        "label": "New Zealand Dollar / US Dollar",
    },
}


TIMEFRAMES = {
    "5M": "5m",
    "15M": "15m",
    "30M": "30m",
    "1H": "60m",
    "1D": "1d",
}


if "market_data" not in st.session_state:
    st.session_state["market_data"] = None

if "data_error" not in st.session_state:
    st.session_state["data_error"] = ""

if "last_market" not in st.session_state:
    st.session_state["last_market"] = ""

if "last_timeframe" not in st.session_state:
    st.session_state["last_timeframe"] = ""


def fetch_yahoo_data(
    ticker,
    interval,
    period="30d",
):

    try:

        data = yf.download(
            ticker,
            interval=interval,
            period=period,
            auto_adjust=False,
            progress=False,
        )

        if data is None or data.empty:
            return None, "Yahoo returned no data."

        if isinstance(
            data.columns,
            pd.MultiIndex,
        ):

            data.columns = (
                data.columns
                .get_level_values(0)
            )

        required = [
            "Open",
            "High",
            "Low",
            "Close",
            "Volume",
        ]

        missing = [
            column
            for column in required
            if column not in data.columns
        ]

        if missing:

            return (
                None,
                "Missing columns: "
                + ", ".join(missing),
            )

        data = data[required].copy()

        data = data.dropna(
            subset=[
                "Open",
                "High",
                "Low",
                "Close",
            ]
        )

        data = data.reset_index()

        if "Datetime" in data.columns:

            data = data.rename(
                columns={
                    "Datetime": "timestamp"
                }
            )

        elif "Date" in data.columns:

            data = data.rename(
                columns={
                    "Date": "timestamp"
                }
            )

        data.columns = [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]

        data["timestamp"] = pd.to_datetime(
            data["timestamp"],
            utc=True,
        )

        data = data.sort_values(
            "timestamp"
        )

        data = data.reset_index(
            drop=True
        )

        return data, ""

    except Exception as error:

        return (
            None,
            str(error),
)
def calculate_indicators(data):

    df = data.copy()

    df["ema_20"] = (
        df["close"]
        .ewm(
            span=20,
            adjust=False,
        )
        .mean()
    )

    df["ema_50"] = (
        df["close"]
        .ewm(
            span=50,
            adjust=False,
        )
        .mean()
    )

    df["ema_200"] = (
        df["close"]
        .ewm(
            span=200,
            adjust=False,
        )
        .mean()
    )

    change = df["close"].diff()

    gains = change.clip(
        lower=0
    )

    losses = -change.clip(
        upper=0
    )

    average_gain = (
        gains
        .rolling(14)
        .mean()
    )

    average_loss = (
        losses
        .rolling(14)
        .mean()
    )

    relative_strength = (
        average_gain /
        average_loss.replace(
            0,
            float("nan"),
        )
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
        .ewm(
            span=12,
            adjust=False,
        )
        .mean()
    )

    ema_26 = (
        df["close"]
        .ewm(
            span=26,
            adjust=False,
        )
        .mean()
    )

    df["macd"] = (
        ema_12 - ema_26
    )

    df["macd_signal"] = (
        df["macd"]
        .ewm(
            span=9,
            adjust=False,
        )
        .mean()
    )

    previous_close = (
        df["close"].shift(1)
    )

    true_range = pd.concat(
        [
            df["high"] -
            df["low"],

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
        .replace(
            0,
            float("nan"),
        )
    )

    return df


def create_candlestick_chart(
    data,
    market_name,
    timeframe,
):

    chart = go.Figure()

    chart.add_trace(
        go.Candlestick(
            x=data["timestamp"],
            open=data["open"],
            high=data["high"],
            low=data["low"],
            close=data["close"],
            name="Price",
            increasing_line_color="#00e676",
            decreasing_line_color="#ff5252",
        )
    )

    chart.add_trace(
        go.Scatter(
            x=data["timestamp"],
            y=data["ema_20"],
            mode="lines",
            name="EMA 20",
            line=dict(
                color="#00bcd4",
                width=1,
            ),
        )
    )

    chart.add_trace(
        go.Scatter(
            x=data["timestamp"],
            y=data["ema_50"],
            mode="lines",
            name="EMA 50",
            line=dict(
                color="#ff9800",
                width=1,
            ),
        )
    )

    chart.add_trace(
        go.Scatter(
            x=data["timestamp"],
            y=data["ema_200"],
            mode="lines",
            name="EMA 200",
            line=dict(
                color="#e91e63",
                width=1,
            ),
        )
    )

    chart.update_layout(
        title=(
            f"{market_name} • "
            f"{timeframe} • Yahoo Finance"
        ),
        template="plotly_dark",
        height=650,
        xaxis_title="Time",
        yaxis_title="Price",
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        margin=dict(
            l=20,
            r=20,
            t=60,
            b=20,
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
        ),
    )

    return chart
