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
