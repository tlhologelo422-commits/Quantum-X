import os
from typing import Any

import numpy as np
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components


# ============================================================
# QUANTUM X PRO
# IG DEMO + QUANTITATIVE SCALPER ENGINE V1
# ============================================================

st.set_page_config(
    page_title="Quantum X PRO",
    page_icon="⚡",
    layout="wide",
)

IG_BASE_URL = "https://demo-api.ig.com/gateway/deal"


# ============================================================
# MARKET CONFIGURATION
# ============================================================

MARKETS = {
    "XAU/USD": {
        "tradingview": "OANDA:XAUUSD",
        "ig_search": "Gold",
        "allowed_direction": "BUY",
        "label": "GOLD",
    },
    "EUR/USD": {
        "tradingview": "FX:EURUSD",
        "ig_search": "EUR/USD",
        "allowed_direction": "SELL",
        "label": "EURUSD",
    },
    "NAS100": {
        "tradingview": "OANDA:NAS100USD",
        "ig_search": "US Tech 100",
        "allowed_direction": "BUY",
        "label": "NAS100",
    },
}


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
    "ig_markets": [],
    "ig_selected_epic": None,
    "ig_selected_market_name": None,
    "ig_prices": None,
    "last_scan": None,
}

for key, value in DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# IG AUTHENTICATION
# ============================================================

def get_ig_credentials():
    return (
        os.getenv("IG_USERNAME"),
        os.getenv("IG_PASSWORD"),
        os.getenv("IG_API_KEY"),
    )


def credentials_available():
    username, password, api_key = get_ig_credentials()
    return bool(username and password and api_key)


def build_ig_headers(version="2"):
    _, _, api_key = get_ig_credentials()

    return {
        "X-IG-API-KEY": api_key,
        "CST": st.session_state["ig_cst"],
        "X-SECURITY-TOKEN": st.session_state["ig_security_token"],
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Version": version,
    }


def connect_to_ig():

    username, password, api_key = get_ig_credentials()

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
                error_code = "IG authentication failed."

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
        return False, "IG connection timed out.", None

    except requests.exceptions.ConnectionError:
        return False, "Could not reach IG Demo.", None

    except requests.exceptions.RequestException as exc:
        return False, f"Unexpected network error: {exc}", None


# ============================================================
# IG MARKET SEARCH
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
                error_code = "IG market search failed."

            return False, error_code, []

        data = response.json()

        markets = data.get("markets", [])

        if not isinstance(markets, list):
            markets = []

        return True, "", markets

    except requests.exceptions.RequestException as exc:

        return (
            False,
            f"Market search network error: {exc}",
            [],
        )


def extract_market_info(market: dict[str, Any]):

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

    market_status = (
        snapshot.get("marketStatus")
        or market.get("marketStatus")
        or "UNKNOWN"
    )

    market_id = (
        instrument.get("marketId")
        or market.get("marketId")
        or ""
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
        "market_status": market_status,
        "market_id": market_id,
        "bid": bid,
        "offer": offer,
    }


# ============================================================
# IG PRICE DATA
# ============================================================

def get_ig_prices(
    epic,
    resolution="MINUTE_15",
    num_points=200,
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
                error_code = "IG price request failed."

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

        for price in prices:

            open_price = price.get(
                "openPrice",
                {}
            )

            high_price = price.get(
                "highPrice",
                {}
            )

            low_price = price.get(
                "lowPrice",
                {}
            )

            close_price = price.get(
                "closePrice",
                {}
            )

            def mid_price(value):

                if not isinstance(value, dict):
                    return None

                bid = value.get("bid")
                ask = value.get("ask")

                if bid is not None and ask is not None:

                    return (
                        float(bid)
                        + float(ask)
                    ) / 2.0

                if bid is not None:
                    return float(bid)

                if ask is not None:
                    return float(ask)

                return None

            rows.append(
                {
                    "timestamp": price.get(
                        "snapshotTimeUTC",
                        price.get(
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
                    "volume": price.get(
                        "lastTradedVolume",
                        0,
                    ),
                }
            )

        dataframe = pd.DataFrame(rows)

        dataframe = dataframe.dropna(
            subset=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
            ]
        )

        if dataframe.empty:

            return (
                False,
                "IG returned candles, but they could not be converted into OHLC data.",
                None,
            )

        dataframe["timestamp"] = pd.to_datetime(
            dataframe["timestamp"],
            utc=True,
        )

        dataframe = dataframe.sort_values(
            "timestamp"
        )

        dataframe = dataframe.reset_index(
            drop=True
        )

        return True, "", dataframe

    except requests.exceptions.RequestException as exc:

        return (
            False,
            f"Price request network error: {exc}",
            None,
        )

    except (ValueError, TypeError) as exc:

        return (
            False,
            f"Could not parse IG price data: {exc}",
            None,
        )


# ============================================================
# INDICATORS
# ============================================================

def calculate_ema(series, period):

    return series.ewm(
        span=period,
        adjust=False
    ).mean()


def calculate_rsi(series, period=14):

    delta = series.diff()

    gains = delta.clip(
        lower=0
    )

    losses = -delta.clip(
        upper=0
    )

    avg_gain = gains.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    avg_loss = losses.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(
        0,
        np.nan
    )

    return 100 - (
        100 / (1 + rs)
    )


def calculate_atr(df, period=14):

    previous_close = df["close"].shift(1)

    tr1 = (
        df["high"]
        - df["low"]
    )

    tr2 = (
        df["high"]
        - previous_close
    ).abs()

    tr3 = (
        df["low"]
        - previous_close
    ).abs()

    true_range = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    return true_range.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()


def calculate_indicators(df):

    df = df.copy()

    df["ema20"] = calculate_ema(
        df["close"],
        20
    )

    df["ema50"] = calculate_ema(
        df["close"],
        50
    )

    df["ema200"] = calculate_ema(
        df["close"],
        200
    )

    df["rsi"] = calculate_rsi(
        df["close"],
        14
    )

    df["atr"] = calculate_atr(
        df,
        14
    )

    df["atr_percent"] = (
        df["atr"]
        / df["close"]
    ) * 100

    df["roc"] = (
        df["close"]
        .pct_change(10)
        * 100
    )

    df["ema20_slope"] = (
        df["ema20"]
        .diff(5)
    )

    df["ema50_slope"] = (
        df["ema50"]
        .diff(5)
    )

    volume_ma = (
        df["volume"]
        .rolling(20)
        .mean()
    )

    df["volume_ratio"] = (
        df["volume"]
        / volume_ma.replace(
            0,
            np.nan
        )
    )

    df["body"] = (
        df["close"]
        - df["open"]
    ).abs()

    df["range"] = (
        df["high"]
        - df["low"]
    )

    df["body_ratio"] = (
        df["body"]
        / df["range"].replace(
            0,
            np.nan
        )
    )

    return df


# ============================================================
# DAILY LEVELS
# ============================================================

def calculate_daily_levels(df):

    working = df.copy()

    working["date"] = (
        working["timestamp"]
        .dt.date
    )

    daily = (
        working
        .groupby("date")
        .agg(
            daily_high=("high", "max"),
            daily_low=("low", "min"),
        )
        .reset_index()
    )

    daily["previous_high"] = (
        daily["daily_high"]
        .shift(1)
    )

    daily["previous_low"] = (
        daily["daily_low"]
        .shift(1)
    )

    daily["daily_mid"] = (
        daily["daily_high"]
        + daily["daily_low"]
    ) / 2

    working = working.merge(
        daily[
            [
                "date",
                "previous_high",
                "previous_low",
                "daily_mid",
            ]
        ],
        on="date",
        how="left",
    )

    return working


# ============================================================
# LIQUIDITY SWEEPS
# ============================================================

def detect_liquidity(df, lookback=20):

    df = df.copy()

    rolling_high = (
        df["high"]
        .shift(1)
        .rolling(lookback)
        .max()
    )

    rolling_low = (
        df["low"]
        .shift(1)
        .rolling(lookback)
        .min()
    )

    df["sweep_high"] = (
        (df["high"] > rolling_high)
        & (df["close"] < rolling_high)
    )

    df["sweep_low"] = (
        (df["low"] < rolling_low)
        & (df["close"] > rolling_low)
    )

    df["pdh_sweep"] = (
        (df["high"] > df["previous_high"])
        & (
            df["close"]
            < df["previous_high"]
        )
    )

    df["pdl_sweep"] = (
        (df["low"] < df["previous_low"])
        & (
            df["close"]
            > df["previous_low"]
        )
    )

    return df


# ============================================================
# FAIR VALUE GAP
# ============================================================

def detect_fvg(df):

    df = df.copy()

    df["bullish_fvg"] = False
    df["bearish_fvg"] = False

    for i in range(2, len(df)):

        previous_high = df.iloc[i - 2]["high"]
        previous_low = df.iloc[i - 2]["low"]

        current_low = df.iloc[i]["low"]
        current_high = df.iloc[i]["high"]

        if current_low > previous_high:

            df.iloc[
                i,
                df.columns.get_loc(
                    "bullish_fvg"
                )
            ] = True

        if current_high < previous_low:

            df.iloc[
                i,
                df.columns.get_loc(
                    "bearish_fvg"
                )
            ] = True

    return df


# ============================================================
# DISPLACEMENT
# ============================================================

def detect_displacement(df):

    df = df.copy()

    average_body = (
        df["body"]
        .rolling(20)
        .mean()
    )

    df["bullish_displacement"] = (
        (df["close"] > df["open"])
        & (
            df["body"]
            > average_body * 1.5
        )
        & (
            df["body_ratio"]
            > 0.60
        )
    )

    df["bearish_displacement"] = (
        (df["close"] < df["open"])
        & (
            df["body"]
            > average_body * 1.5
        )
        & (
            df["body_ratio"]
            > 0.60
        )
    )

    return df


# ============================================================
# COMPLETE FEATURE ENGINE
# ============================================================

def build_feature_engine(df):

    df = calculate_indicators(df)

    df = calculate_daily_levels(df)

    df = detect_liquidity(df)

    df = detect_fvg(df)

    df = detect_displacement(df)

    return df


# ============================================================
# QUANTUM SIGNAL ENGINE
# ============================================================

def calculate_signal(
    df,
    market_name,
):

    if df is None or len(df) < 50:

        return {
            "signal": "WAIT",
            "score": 0,
            "direction": "NONE",
            "entry": None,
            "stop": None,
            "target": None,
            "reasons": [
                "Not enough IG candles."
            ],
        }

    row = df.iloc[-1]

    market = MARKETS[market_name]

    allowed_direction = market[
        "allowed_direction"
    ]

    score = 0

    reasons = []

    # --------------------------------------------------------
    # PRICE
    # --------------------------------------------------------

    price = float(row["close"])

    atr = float(row["atr"])

    if not np.isfinite(atr) or atr <= 0:

        return {
            "signal": "WAIT",
            "score": 0,
            "direction": "NONE",
            "entry": None,
            "stop": None,
            "target": None,
            "reasons": [
                "ATR unavailable."
            ],
        }

    # --------------------------------------------------------
    # TREND
    # --------------------------------------------------------

    ema20 = row["ema20"]
    ema50 = row["ema50"]
    ema200 = row["ema200"]

    if allowed_direction == "BUY":

        if (
            price > ema20
            and ema20 > ema50
            and ema50 > ema200
        ):
            score += 20
            reasons.append(
                "Bullish EMA alignment."
            )

        elif price > ema50:
            score += 8
            reasons.append(
                "Price above medium-term EMA."
            )

        if row["ema20_slope"] > 0:
            score += 8
            reasons.append(
                "EMA20 slope is bullish."
            )

    else:

        if (
            price < ema20
            and ema20 < ema50
            and ema50 < ema200
     
