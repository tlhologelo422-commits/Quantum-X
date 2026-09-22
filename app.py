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
# CHUNK 2.1 — IG MARKET DATA TEST
# ==========================================

def test_ig_markets():

    if not st.session_state.connected:
        st.error("Connect IG Demo first.")
        return

    st.subheader("🔎 IG MARKET API TEST")

    try:

        response = requests.get(
            BASE + "/markets",
            headers=ig_headers("1"),
            params={
                "searchTerm": "XAUUSD"
            },
            timeout=20
        )

        st.write(
            "HTTP STATUS:",
            response.status_code
        )

        st.write(
            "CONTENT TYPE:",
            response.headers.get(
                "Content-Type"
            )
        )

        st.code(
            response.text[:5000]
        )

    except requests.exceptions.RequestException as e:

        st.error(
            f"Network error: {e}"
        )


# ==========================================
# BUTTON
# ==========================================

if st.session_state.connected:

    if st.button(
        "🧪 TEST IG MARKET API",
        use_container_width=True
    ):
        test_ig_markets()
