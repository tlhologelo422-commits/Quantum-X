import os
from typing import Any

import numpy as np
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components


# ============================================================
# QUANTUM X PRO
# Quantitative Scalping Engine V1
# IG DEMO DATA ONLY
# ============================================================

st.set_page_config(
    page_title="Quantum X PRO",
    page_icon="⚡",
    layout="wide",
)


# ============================================================
# IG CONFIG
# ============================================================

IG_BASE_URL = "https://demo-api.ig.com/gateway/deal"


# ============================================================
# QUANTUM X PRO MARKETS
# ============================================================

MARKETS = {
    "XAU/USD": {
        "tradingview": "OANDA:XAUUSD",
        "ig_search": "Gold",
        "allowed_direction": "BUY",
    },
    "EUR/USD": {
        "tradingview": "FX:EURUSD",
        "ig_search": "EUR/USD",
        "allowed_direction": "SELL",
    },
    "NAS100": {
        "tradingview": "OANDA:NAS100USD",
        "ig_search": "US Tech 100",
        "allowed_direction": "BUY",
    },
}


# ============================================================
# TIMEFRAMES
# ============================================================

TIMEFRAMES = {
    "1M": "1",
    "5M": "5",
    "15M": "15",
    "30M": "30",
    "1H": "60",
    "4H": "240",
    "1D": "D",
}


IG_RESOLUTIONS = {
    "1M": "MINUTE",
    "5M": "MINUTE_5",
    "15M": "MINUTE_15",
    "30M": "MINUTE_30",
    "1H": "HOUR",
    "4H": "HOUR_4",
    "1D": "DAY",
}


# ============================================================
# SESSION STATE
# ============================================================

DEFAULT_STATE = {
    "ig_connected": False,
    "ig_error": "",
    "ig_cst": None,
    "ig_security_token": None,
    "ig_account": None,
    "selected_epics": {},
    "last_scan": None,
}

for key, value in DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# IG CREDENTIALS
# ============================================================

def get_ig_credentials():

    return (
        os.getenv("IG_USERNAME"),
        os.getenv("IG_PASSWORD"),
        os.getenv("IG_API_KEY"),
    )


def build_ig_headers(version="2"):

    _, _, api_key = get_ig_credentials()

    return {
        "X-IG-API-KEY": api_key,
        "CST": st.session_state["ig_cst"],
        "X-SECURITY-TOKEN": st.session_state[
            "ig_security_token"
        ],
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Version": version,
    }


# ============================================================
# CONNECT TO IG
# ============================================================

def connect_to_ig():

    username, password, api_key = (
        get_ig_credentials()
    )

    missing = []

    if not username:
        missing.append("IG_USERNAME")

    if not password:
        missing.append("IG_PASSWORD")

    if not api_key:
        missing.append("IG_API_KEY")

    if missing:

        return (
            False,
            "Missing Codespaces secret(s): "
            + ", ".join(missing),
            None,
        )

    url = f"{IG_BASE_URL}/session"

    headers = {
        "X-IG-API-KEY": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Version": "2",
    }

    payload = {
        "identifier": username,
        "password": password,
        "encryptedPassword": False,
    }

    try:

        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=20,
        )

        if response.status_code != 200:

            try:
                data = response.json()

                error_code = data.get(
                    "errorCode",
                    "IG authentication failed.",
                )

            except ValueError:

                error_code = (
                    "IG authentication failed."
                )

            return False, error_code, None

        cst = response.headers.get("CST")

        security_token = response.headers.get(
            "X-SECURITY-TOKEN"
        )

        if not cst or not security_token:

            return (
                False,
                "IG login succeeded but session tokens were not returned.",
                None,
            )

        try:
            account_data = response.json()
        except ValueError:
            account_data = {}

        return (
            True,
            "IG Demo connection successful.",
            {
                "cst": cst,
                "security_token": security_token,
                "account_data": account_data,
            },
        )

    except requests.exceptions.Timeout:

        return (
            False,
            "IG connection timed out.",
            None,
        )

    except requests.exceptions.ConnectionError:

        return (
            False,
            "Could not reach IG Demo.",
            None,
        )

    except requests.exceptions.RequestException as exc:

        return (
            False,
            f"Unexpected network error: {exc}",
            None,
        )


# ============================================================
# SEARCH IG MARKETS
# ============================================================

def search_ig_markets(search_term):

    url = f"{IG_BASE_URL}/markets"

    params = {
        "searchTerm": search_term
    }

    try:

        response = requests.get(
            url,
            headers=build_ig_headers("1"),
            params=params,
            timeout=20,
        )

        if response.status_code == 401:

            return (
                False,
                "IG session expired. Please reconnect.",
                [],
            )

        if response.status_code != 200:

            try:

                data = response.json()

                error_code = data.get(
                    "errorCode",
                    "IG market search failed.",
                )

            except ValueError:

                error_code = (
                    "IG market search failed."
                )

            return False, error_code, []

        data = response.json()

        markets = data.get(
            "markets",
            []
        )

        if not isinstance(markets, list):

            markets = []

        return True, "", markets

    except requests.exceptions.RequestException as exc:

        return (
            False,
            f"Market search network error: {exc}",
            [],
        )


# ============================================================
# EXTRACT IG MARKET
# ============================================================

def extract_market_info(
    market: dict[str, Any]
):

    instrument = market.get(
        "instrument",
        {}
    )

    if not isinstance(instrument, dict):

        instrument = {}

    snapshot = market.get(
        "snapshot",
        {}
    )

    if not isinstance(snapshot, dict):

        snapshot = {}

    epic = (
        instrument.get("epic")
        or market.get("epic")
    )

    name = (
        instrument.get("name")
        or market.get("name")
        or "Unknown market"
    )

    status = (
        snapshot.get("marketStatus")
        or market.get("marketStatus")
        or "UNKNOWN"
    )

    bid = (
        snapshot.get("bid")
        or market.get("bid")
    )

    offer = (
        snapshot.get("offer")
        or snapshot.get("ask")
        or market.get("offer")
        or market.get("ask")
    )

    return {
        "epic": epic,
        "name": name,
        "status": status,
        "bid": bid,
        "offer": offer,
    }


# ============================================================
# GET IG PRICES
# ============================================================

def get_ig_prices(
    epic,
    resolution,
    num_points=250,
):

    url = (
        f"{IG_BASE_URL}/prices/"
        f"{epic}/{resolution}/{num_points}"
    )

    try:

        response = requests.get(
            url,
            headers=build_ig_headers("2"),
            timeout=30,
        )

        if response.status_code == 401:

            return (
                False,
                "IG session expired. Please reconnect.",
                None,
            )

        if response.status_code != 200:

            try:

                data = response.json()

                error_code = data.get(
                    "errorCode",
                    "IG price request failed.",
                )

            except ValueError:

                error_code = (
                    "IG price request failed."
                )

            return False, error_code, None

        data = response.json()

        prices = data.get(
            "prices",
            []
        )

        if not prices:

            return (
                False,
                "IG returned no historical prices.",
                None,
            )

        rows = []

        for candle in prices:

            open_price = candle.get(
                "openPrice",
                {}
            )

            high_price = candle.get(
                "highPrice",
                {}
            )

            low_price = candle.get(
                "lowPrice",
                {}
            )

            close_price = candle.get(
                "closePrice",
                {}
            )

            def mid_price(value):

                if not isinstance(
                    value,
                    dict
                ):
                    return np.nan

                bid = value.get("bid")
                ask = value.get("ask")

                if (
                    bid is not None
                    and ask is not None
                ):

                    return (
                        float(bid)
                        + float(ask)
                    ) / 2.0

                if bid is not None:
                    return float(bid)

                if ask is not None:
                    return float(ask)

                return np.nan

            rows.append(
                {
                    "timestamp": candle.get(
                        "snapshotTimeUTC",
                        candle.get(
                            "snapshotTime"
                        ),
                    ),
                    "open": mid_price(
                        open_price
                    ),
                    "high": mid_price(
                        high_price
                    ),
                    "low": mid_price(
                        low_price
                    ),
                    "close": mid_price(
                        close_price
                    ),
                    "volume": candle.get(
                        "lastTradedVolume",
                        0,
                    ),
                }
            )

        df = pd.DataFrame(rows)

        if df.empty:

            return (
                False,
                "No usable candles returned.",
                None,
            )

        df["timestamp"] = pd.to_datetime(
            df["timestamp"],
            utc=True,
            errors="coerce",
        )

        numeric_columns = [
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]

        for column in numeric_columns:

            df[column] = pd.to_numeric(
                df[column],
                errors="coerce",
            )

        df = df.dropna(
            subset=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
            ]
        )

        df = df.sort_values(
            "timestamp"
        ).reset_index(
            drop=True
        )

        if df.empty:

            return (
                False,
                "IG returned candles but they were invalid.",
                None,
            )

        return True, "", df

    except requests.exceptions.RequestException as exc:

        return (
            False,
            f"Price request failed: {exc}",
            None,
        )

    except Exception as exc:

        return (
            False,
            f"Could not process IG data: {exc}",
            None,
        )


# ============================================================
# EMA
# ============================================================

def ema(series, period):

    return series.ewm(
        span=period,
        adjust=False
    ).mean()


# ============================================================
# RSI
# ============================================================

def rsi(series, period=14):

    delta = series.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    average_gain = gain.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    average_loss = loss.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    rs = (
        average_gain
        / average_loss.replace(
            0,
            np.nan
        )
    )

    return 100 - (
        100 / (1 + rs)
    )


# ============================================================
# ATR
# ============================================================

def atr(df, period=14):

    previous_close = (
        df["close"].shift(1)
    )

    range_one = (
        df["high"]
        - df["low"]
    )

    range_two = (
        df["high"]
        - previous_close
    ).abs()

    range_three = (
        df["low"]
        - previous_close
    ).abs()

    true_range = pd.concat(
        [
            range_one,
            range_two,
            range_three,
        ],
        axis=1,
    ).max(axis=1)

    return true_range.ewm(
        alpha=1
