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
# CHUNK 2 — GOLD MARKET DISCOVERY
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


def find_gold():

    if not st.session_state.connected:
        st.error("Connect IG Demo first.")
        return

    try:

        response = requests.get(
            BASE + "/markets",
            headers=ig_headers("1"),
            params={"searchTerm": "Gold"},
            timeout=20
        )

        if response.status_code != 200:
            st.error("IG Gold search failed.")
            st.code(response.text)
            return

        markets = response.json().get(
            "markets", []
        )

        if not markets:
            st.warning(
                "IG returned no markets for Gold."
            )
            return

        rows = []

        for market in markets:

            instrument = market.get(
                "instrument", {}
            )

            snapshot = market.get(
                "snapshot", {}
            )

            epic = instrument.get("epic")
            name = instrument.get("name", "")
            status = snapshot.get(
                "marketStatus",
                "UNKNOWN"
            )

            if epic:

                rows.append({
                    "EPIC": epic,
                    "Name": name,
                    "Status": status
                })

        if not rows:
            st.warning(
                "Gold search returned no usable EPIC."
            )
            return

        st.subheader("🥇 IG Gold Markets")

        st.dataframe(
            rows,
            use_container_width=True,
            hide_index=True
        )

        # Prefer a tradeable Gold market
        selected = None

        for row in rows:

            text = (
                row["EPIC"] + " " +
                row["Name"]
            ).lower()

            if (
                ("gold" in text or "xau" in text)
                and row["Status"] == "TRADEABLE"
            ):
                selected = row
                break

        # Fallback to first Gold/XAU result
        if selected is None:

            for row in rows:

                text = (
                    row["EPIC"] + " " +
                    row["Name"]
                ).lower()

                if "gold" in text or "xau" in text:
                    selected = row
                    break

        if selected is None:
            st.warning(
                "No Gold/XAU EPIC identified."
            )
            return

        st.session_state.gold_epic = (
            selected["EPIC"]
        )

        st.session_state.gold_name = (
            selected["Name"]
        )

        st.success(
            "🥇 Gold EPIC selected."
        )

        st.code(
            st.session_state.gold_epic
        )

    except requests.exceptions.RequestException as e:

        st.error(
            f"Gold search network error: {e}"
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
