import os
import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Quantum X PRO")

BASE = "https://demo-api.ig.com/gateway/deal"

st.title("⚛️ Quantum X PRO")
st.caption("Gold Reversal Scalper")

for k in ["cst", "token"]:
    st.session_state.setdefault(k, None)


def connect():
    r = requests.post(
        BASE + "/session",
        headers={
            "X-IG-API-KEY": os.getenv("IG_API_KEY"),
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Version": "2"
        },
        json={
            "identifier": os.getenv("IG_USERNAME"),
            "password": os.getenv("IG_PASSWORD"),
            "encryptedPassword": False
        },
        timeout=20
    )

    if r.status_code != 200:
        st.error(r.text)
        return False

    st.session_state.cst = r.headers["CST"]
    st.session_state.token = r.headers["X-SECURITY-TOKEN"]

    return True


if st.button("CONNECT IG DEMO"):

    if connect():
        st.success("🟢 IG CONNECTED")
