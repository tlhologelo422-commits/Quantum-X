import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf


st.set_page_config(
    page_title="Quantum M5 Scalper",
    page_icon="🐎",
    layout="wide",
)


APP_NAME = "Quantum M5 Scalper"


MARKETS = {
    "XAU/USD": "GC=F",
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "USD/CHF": "CHF=X",
    "AUD/USD": "AUDUSD=X",
    "USD/CAD": "CAD=X",
    "NZD/USD": "NZDUSD=X",
}


if "market_data" not in st.session_state:
    st.session_state["market_data"] = None

if "signal" not in st.session_state:
    st.session_state["signal"] = None

if "last_signal_time" not in st.session_state:
    st.session_state["last_signal_time"] = None

if "trades_today" not in st.session_state:
    st.session_state["trades_today"] = 0

if "open_trade" not in st.session_state:
    st.session_state["open_trade"] = False


def fetch_m5_data(
    ticker,
    period="5d",
):

    try:

        data = yf.download(
            ticker,
            interval="5m",
            period=period,
            auto_adjust=False,
            progress=False,
        )

        if data is None or data.empty:

            return None, "Yahoo returned no M5 data."

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

        return None, str(error)


def calculate_atr(
    data,
    period=14,
):

    df = data.copy()

    previous_close = (
        df["close"].shift(1)
    )

    true_range = pd.concat(
        [
            df["high"] - df["low"],

            (
                df["high"]
                - previous_close
            ).abs(),

            (
                df["low"]
                - previous_close
            ).abs(),
        ],
        axis=1,
    ).max(axis=1)

    df["atr"] = (
        true_range
        .rolling(period)
        .mean()
    )

    return df
