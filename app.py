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
