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
