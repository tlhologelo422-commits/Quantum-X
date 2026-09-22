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
def display_market_metrics(data):

    latest = data.iloc[-1]

    price = latest["close"]
    rsi = latest["rsi"]
    atr = latest["atr"]
    volume_ratio = latest["volume_ratio"]

    col1, col2, col3, col4 = (
        st.columns(4)
    )

    with col1:

        st.metric(
            "Current Price",
            f"{price:.5f}",
        )

    with col2:

        st.metric(
            "RSI",
            f"{rsi:.2f}",
        )

    with col3:

        st.metric(
            "ATR",
            f"{atr:.5f}",
        )

    with col4:

        st.metric(
            "Volume Ratio",
            f"{volume_ratio:.2f}x",
        )


def display_technical_data(data):

    latest = data.iloc[-1]

    st.subheader(
        "📐 Technical Evidence"
    )

    col1, col2 = (
        st.columns(2)
    )

    with col1:

        st.write(
            f"**EMA 20:** "
            f"{latest['ema_20']:.5f}"
        )

        st.write(
            f"**EMA 50:** "
            f"{latest['ema_50']:.5f}"
        )

        st.write(
            f"**EMA 200:** "
            f"{latest['ema_200']:.5f}"
        )

    with col2:

        st.write(
            f"**MACD:** "
            f"{latest['macd']:.5f}"
        )

        st.write(
            f"**MACD Signal:** "
            f"{latest['macd_signal']:.5f}"
        )

        st.write(
            f"**Volume:** "
            f"{latest['volume']:.0f}"
        )


def display_volume_chart(data):

    st.subheader(
        "📊 Volume"
    )

    volume_colors = []

    for index in range(
        len(data)
    ):

        if (
            data["close"].iloc[index]
            >=
            data["open"].iloc[index]
        ):

            volume_colors.append(
                "#00e676"
            )

        else:

            volume_colors.append(
                "#ff5252"
            )

    volume_chart = go.Figure()

    volume_chart.add_trace(
        go.Bar(
            x=data["timestamp"],
            y=data["volume"],
            marker_color=volume_colors,
            name="Volume",
        )
    )

    volume_chart.update_layout(
        template="plotly_dark",
        height=250,
        xaxis_rangeslider_visible=False,
        margin=dict(
            l=20,
            r=20,
            t=20,
            b=20,
        ),
        showlegend=False,
    )

    st.plotly_chart(
        volume_chart,
        use_container_width=True,
    )


def show_engine_status():

    st.subheader(
        "🧠 Quantum X Analysis Engine"
    )

    col1, col2, col3 = (
        st.columns(3)
    )

    with col1:

        st.markdown(
            "### 📐 Technical Analysis"
        )

        st.success(
            "ONLINE"
        )

    with col2:

        st.markdown(
            "### 🧱 Smart Money Concepts"
        )

        st.warning(
            "NEXT MODULE"
        )

    with col3:

        st.markdown(
            "### 🤖 AI Reasoning"
        )

        st.info(
            "GPT-5.6 Sol • NEXT"
        )
st.title("📈 QUANTUM X PRO")

st.caption(
    "AI-Powered Market Analysis • "
    "Live Yahoo Finance Candles • "
    "Smart Money Concepts"
)

st.success("● SYSTEM ONLINE")


with st.sidebar:

    st.header("⚙️ Market Settings")

    selected_market = st.selectbox(
        "Market",
        list(MARKETS.keys()),
        index=0,
    )

    selected_timeframe = st.selectbox(
        "Setup Timeframe",
        list(TIMEFRAMES.keys()),
        index=1,
    )

    analysis_mode = st.radio(
        "Analysis Mode",
        [
            "Manual Analysis",
            "Automatic Scanner",
        ],
    )

    st.divider()

    st.subheader("📡 Data Layer")

    st.success(
        "Yahoo Finance • OHLCV"
    )

    st.caption(
        "IG remains available for "
        "the broker layer."
    )


st.subheader("🕯️ Live Market Chart")


ticker = MARKETS[
    selected_market
]["yahoo"]

interval = TIMEFRAMES[
    selected_timeframe
]


refresh = st.button(
    "🔄 Refresh Market Data",
    use_container_width=True,
)


if (
    refresh
    or
    st.session_state["market_data"] is None
    or
    st.session_state["last_market"]
    != selected_market
    or
    st.session_state["last_timeframe"]
    != selected_timeframe
):

    with st.spinner(
        "Loading live market candles..."
    ):

        data, error = fetch_yahoo_data(
            ticker=ticker,
            interval=interval,
            period="30d",
        )

    if data is None:

        st.session_state[
            "market_data"
        ] = None

        st.session_state[
            "data_error"
        ] = error

    else:

        st.session_state[
            "market_data"
        ] = data

        st.session_state[
            "last_market"
        ] = selected_market

        st.session_state[
            "last_timeframe"
        ] = selected_timeframe

        st.session_state[
            "data_error"
        ] = ""
data = st.session_state["market_data"]


if data is None:

    st.error(
        "❌ Market data could not be loaded."
    )

    if st.session_state["data_error"]:

        st.warning(
            st.session_state["data_error"]
        )

else:

    analysed_data = calculate_indicators(
        data
    )

    st.success(
        f"✅ {selected_market} "
        f"{selected_timeframe} data loaded"
    )


    chart = create_candlestick_chart(
        analysed_data,
        selected_market,
        selected_timeframe,
    )

    st.plotly_chart(
        chart,
        use_container_width=True,
    )


    display_market_metrics(
        analysed_data
    )


    display_volume_chart(
        analysed_data
    )


    display_technical_data(
        analysed_data
    )


    st.subheader(
        "🕯️ Recent Candles"
    )


    recent_data = (
        analysed_data[
            [
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]
        ]
        .tail(10)
        .sort_values(
            "timestamp",
            ascending=False,
        )
    )


    st.dataframe(
        recent_data,
        use_container_width=True,
        hide_index=True,
    )


show_engine_status()


st.subheader(
    "🎯 AI Signal"
)


st.info(
    "AI signal generation will be connected "
    "after the SMC and confluence engines "
    "are completed."
)


st.divider()


st.caption(
    "QUANTUM X PRO • "
    "Yahoo Finance • "
    "OHLCV • "
    "Candlestick Engine • "
    "IG Broker Layer • "
    "GPT-5.6 Sol"
)
