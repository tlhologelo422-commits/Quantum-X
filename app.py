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
def detect_swing_points(
    data,
    left_bars=3,
    right_bars=3,
):

    df = data.copy()

    df["swing_high"] = False
    df["swing_low"] = False

    for index in range(
        left_bars,
        len(df) - right_bars,
    ):

        current_high = df.loc[
            index,
            "high",
        ]

        current_low = df.loc[
            index,
            "low",
        ]

        left_highs = df.loc[
            index - left_bars:index - 1,
            "high",
        ]

        right_highs = df.loc[
            index + 1:index + right_bars,
            "high",
        ]

        left_lows = df.loc[
            index - left_bars:index - 1,
            "low",
        ]

        right_lows = df.loc[
            index + 1:index + right_bars,
            "low",
        ]

        is_swing_high = (
            current_high > left_highs.max()
            and
            current_high > right_highs.max()
        )

        is_swing_low = (
            current_low < left_lows.min()
            and
            current_low < right_lows.min()
        )

        if is_swing_high:

            df.loc[
                index,
                "swing_high",
            ] = True

        if is_swing_low:

            df.loc[
                index,
                "swing_low",
            ] = True

    return df


def classify_structure(
    data,
):

    df = data.copy()

    df["structure"] = ""
    df["structure_price"] = float("nan")

    last_swing_high = None
    last_swing_low = None

    previous_high = None
    previous_low = None

    for index in range(
        len(df)
    ):

        if df.loc[
            index,
            "swing_high",
        ]:

            current_high = df.loc[
                index,
                "high",
            ]

            if previous_high is not None:

                if current_high > previous_high:

                    df.loc[
                        index,
                        "structure",
                    ] = "HH"

                elif current_high < previous_high:

                    df.loc[
                        index,
                        "structure",
                    ] = "LH"

            previous_high = current_high
            last_swing_high = current_high

        if df.loc[
            index,
            "swing_low",
        ]:

            current_low = df.loc[
                index,
                "low",
            ]

            if previous_low is not None:

                if current_low > previous_low:

                    df.loc[
                        index,
                        "structure",
                    ] = "HL"

                elif current_low < previous_low:

                    df.loc[
                        index,
                        "structure",
                    ] = "LL"

            previous_low = current_low
            last_swing_low = current_low

        if (
            df.loc[
                index,
                "structure",
            ]
            != ""
        ):

            if df.loc[
                index,
                "structure",
            ] in ["HH", "LH"]:

                df.loc[
                    index,
                    "structure_price",
                ] = last_swing_high

            else:

                df.loc[
                    index,
                    "structure_price",
                ] = last_swing_low

    return df
def detect_structure_events(
    data,
):

    df = data.copy()

    df["bos"] = ""
    df["choch"] = ""

    last_swing_high = None
    last_swing_low = None

    market_bias = "NEUTRAL"

    high_broken = False
    low_broken = False

    for index in range(
        len(df)
    ):

        if df.loc[
            index,
            "swing_high",
        ]:

            last_swing_high = df.loc[
                index,
                "high",
            ]

            high_broken = False

        if df.loc[
            index,
            "swing_low",
        ]:

            last_swing_low = df.loc[
                index,
                "low",
            ]

            low_broken = False

        current_close = df.loc[
            index,
            "close",
        ]

        if (
            last_swing_high is not None
            and
            current_close > last_swing_high
            and
            not high_broken
        ):

            if market_bias == "BEARISH":

                df.loc[
                    index,
                    "choch",
                ] = "BULLISH CHoCH"

            else:

                df.loc[
                    index,
                    "bos",
                ] = "BULLISH BOS"

            market_bias = "BULLISH"
            high_broken = True

        if (
            last_swing_low is not None
            and
            current_close < last_swing_low
            and
            not low_broken
        ):

            if market_bias == "BULLISH":

                df.loc[
                    index,
                    "choch",
                ] = "BEARISH CHoCH"

            else:

                df.loc[
                    index,
                    "bos",
                ] = "BEARISH BOS"

            market_bias = "BEARISH"
            low_broken = True

    df["market_bias"] = market_bias

    return df
def run_smc_engine(data):

    df = detect_swing_points(
        data,
        left_bars=3,
        right_bars=3,
    )

    df = classify_structure(
        df
    )

    df = detect_structure_events(
        df
    )

    return df


def get_structure_summary(data):

    latest_bias = data[
        "market_bias"
    ].iloc[-1]

    bos_events = data[
        data["bos"] != ""
    ]

    choch_events = data[
        data["choch"] != ""
    ]

    swing_highs = data[
        data["swing_high"]
    ]

    swing_lows = data[
        data["swing_low"]
    ]

    return {
        "bias": latest_bias,
        "bos_count": len(bos_events),
        "choch_count": len(choch_events),
        "swing_high_count": len(
            swing_highs
        ),
        "swing_low_count": len(
            swing_lows
        ),
    }
def create_smc_chart(
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
            x=data.loc[
                data["swing_high"],
                "timestamp",
            ],
            y=data.loc[
                data["swing_high"],
                "high",
            ],
            mode="markers",
            name="Swing High",
            marker=dict(
                symbol="triangle-down",
                size=9,
                color="#ff5252",
            ),
        )
    )


    chart.add_trace(
        go.Scatter(
            x=data.loc[
                data["swing_low"],
                "timestamp",
            ],
            y=data.loc[
                data["swing_low"],
                "low",
            ],
            mode="markers",
            name="Swing Low",
            marker=dict(
                symbol="triangle-up",
                size=9,
                color="#00e676",
            ),
        )
    )


    bullish_bos = data[
        data["bos"] == "BULLISH BOS"
    ]


    bearish_bos = data[
        data["bos"] == "BEARISH BOS"
    ]


    bullish_choch = data[
        data["choch"] == "BULLISH CHoCH"
    ]


    bearish_choch = data[
        data["choch"] == "BEARISH CHoCH"
    ]


    chart.add_trace(
        go.Scatter(
            x=bullish_bos[
                "timestamp"
            ],
            y=bullish_bos[
                "high"
            ],
            mode="markers+text",
            name="Bullish BOS",
            text=["BOS"] * len(
                bullish_bos
            ),
            textposition="top center",
            marker=dict(
                symbol="diamond",
                size=11,
                color="#00e676",
            ),
        )
    )


    chart.add_trace(
        go.Scatter(
            x=bearish_bos[
                "timestamp"
            ],
            y=bearish_bos[
                "low"
            ],
            mode="markers+text",
            name="Bearish BOS",
            text=["BOS"] * len(
                bearish_bos
            ),
            textposition="bottom center",
            marker=dict(
                symbol="diamond",
                size=11,
                color="#ff5252",
            ),
        )
    )


    chart.add_trace(
        go.Scatter(
            x=bullish_choch[
                "timestamp"
            ],
            y=bullish_choch[
                "low"
            ],
            mode="markers+text",
            name="Bullish CHoCH",
            text=["CHoCH"] * len(
                bullish_choch
            ),
            textposition="bottom center",
            marker=dict(
                symbol="star",
                size=12,
                color="#00bcd4",
            ),
        )
    )


    chart.add_trace(
        go.Scatter(
            x=bearish_choch[
                "timestamp"
            ],
            y=bearish_choch[
                "high"
            ],
            mode="markers+text",
            name="Bearish CHoCH",
            text=["CHoCH"] * len(
                bearish_choch
            ),
            textposition="top center",
            marker=dict(
                symbol="star",
                size=12,
                color="#ff9800",
            ),
        )
    )


    chart.update_layout(
        title=(
            f"{market_name} • "
            f"{timeframe} • "
            f"SMC Structure"
        ),
        template="plotly_dark",
        height=700,
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
