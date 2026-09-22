import os
import requests
import streamlit as st

st.set_page_config(
    page_title="Quantum X PRO",
    page_icon="⚛️",
    layout="wide"
)

BASE = "https://demo-api.ig.com/gateway/deal"

# -----------------------------
# SESSION STATE
# -----------------------------

st.session_state.setdefault("cst", None)
st.session_state.setdefault("token", None)
st.session_state.setdefault("connected", False)

# -----------------------------
# IG CONNECTION
# -----------------------------

def connect_ig():

    username = os.getenv("IG_USERNAME")
    password = os.getenv("IG_PASSWORD")
    api_key = os.getenv("IG_API_KEY")

    if not username or not password or not api_key:
        return False, "Missing IG Codespaces secrets."

    try:

        response = requests.post(
            BASE + "/session",
            headers={
                "X-IG-API-KEY": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Version": "2"
            },
            json={
                "identifier": username,
                "password": password,
                "encryptedPassword": False
            },
            timeout=20
        )

        if response.status_code != 200:

            try:
                error = response.json().get(
                    "errorCode",
                    "IG login failed."
                )
            except Exception:
                error = "IG login failed."

            return False, error

        st.session_state.cst = response.headers.get("CST")
        st.session_state.token = response.headers.get(
            "X-SECURITY-TOKEN"
        )

        if not st.session_state.cst:
            return False, "CST token missing."

        if not st.session_state.token:
            return False, "Security token missing."

        st.session_state.connected = True

        return True, "IG Demo connected successfully."

    except requests.exceptions.Timeout:
        return False, "IG connection timed out."

    except requests.exceptions.RequestException as e:
        return False, f"Network error: {e}"


# -----------------------------
# USER INTERFACE
# -----------------------------

st.title("⚛️ Quantum X PRO")

st.caption(
    "Mathematical Gold Scalping Engine"
)

st.divider()

if st.button(
    "🔌 CONNECT IG DEMO",
    use_container_width=True
):

    success, message = connect_ig()

    if success:
        st.success(message)
    else:
        st.error(message)

if st.session_state.connected:

    st.success("🟢 IG DEMO ONLINE")

else:

    st.info("Connect your IG Demo account to continue.")
# ==========================================
# CHUNK 2 — GOLD MARKET DISCOVERY V2
# ==========================================

st.session_state.setdefault("gold_epic", None)
st.session_state.setdefault("gold_name", None)


def ig_headers(version="1"):
    return {
        "X-IG-API-KEY": os.getenv("IG_API_KEY", ""),
        "CST": st.session_state.cst,
        "X-SECURITY-TOKEN": st.session_state.token,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Version": version
    }


def search_gold(term):

    try:

        response = requests.get(
            BASE + "/markets",
            headers=ig_headers("1"),
            params={"searchTerm": term},
            timeout=20
        )

        if response.status_code != 200:
            return []

        return response.json().get(
            "markets", []
        )

    except Exception:
        return []


def find_gold():

    if not st.session_state.connected:
        st.error("Connect IG Demo first.")
        return

    terms = [
        "Gold",
        "XAU",
        "XAUUSD",
        "Spot Gold"
    ]

    all_markets = {}

    for term in terms:

        markets = search_gold(term)

        for market in markets:

            instrument = market.get(
                "instrument", {}
            )

            snapshot = market.get(
                "snapshot", {}
            )

            epic = instrument.get("epic")

            if epic:

                all_markets[epic] = {
                    "EPIC": epic,
                    "Name": instrument.get(
                        "name", ""
                    ),
                    "Status": snapshot.get(
                        "marketStatus",
                        "UNKNOWN"
                    ),
                    "Instrument": instrument.get(
                        "type", ""
                    )
                }

    rows = list(all_markets.values())

    if not rows:

        st.error(
            "❌ IG returned no usable markets."
        )

        st.info(
            "The connection works, but IG is "
            "not returning market search results."
        )

        return

    st.subheader(
        "🔎 IG Market Search Results"
    )

    st.dataframe(
        rows,
        use_container_width=True,
        hide_index=True
    )

    # --------------------------------------
    # FIND GOLD / XAU
    # --------------------------------------

    gold_rows = []

    for row in rows:

        text = (
            str(row["EPIC"]) + " " +
            str(row["Name"])
        ).lower()

        if (
            "gold" in text
            or "xau" in text
        ):
            gold_rows.append(row)

    if not gold_rows:

        st.warning(
            "⚠️ IG returned markets, but none "
            "contain Gold/XAU in the EPIC or name."
        )

        st.info(
            "Check the table above. "
            "We will use the exact IG EPIC returned."
        )

        return

    # Prefer tradeable Gold
    selected = None

    for row in gold_rows:

        if row["Status"] == "TRADEABLE":
            selected = row
            break

    # Otherwise use first Gold result
    if selected is None:
        selected = gold_rows[0]

    st.session_state.gold_epic = selected[
        "EPIC"
    ]

    st.session_state.gold_name = selected[
        "Name"
    ]

    st.success(
        "🥇 GOLD MARKET FOUND"
    )

    st.write(
        "EPIC:",
        st.session_state.gold_epic
    )

    st.write(
        "Name:",
        st.session_state.gold_name
    )

    st.write(
        "Status:",
        selected["Status"]
    )


# ==========================================
# BUTTON
# ==========================================

if st.session_state.connected:

    if st.button(
        "🥇 FIND GOLD",
        use_container_width=True
    ):
        find_gold()    
