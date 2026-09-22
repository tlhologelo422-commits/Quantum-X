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
        st.success("🟢 IG CONNECTED")def headers():
    return {
        "X-IG-API-KEY": os.getenv("IG_API_KEY"),
        "CST": st.session_state.cst,
        "X-SECURITY-TOKEN": st.session_state.token,
        "Accept": "application/json",
        "Version": "1"
    }


def find_gold():

    search_terms = [
        "XAU",
        "XAUUSD",
        "Spot Gold",
        "Gold"
    ]

    for term in search_terms:

        response = requests.get(
            BASE + "/markets",
            headers=headers(),
            params={"searchTerm": term},
            timeout=20
        )

        if response.status_code != 200:
            continue

        markets = response.json().get("markets", [])

        if not markets:
            continue

        rows = []

        for market in markets:

            instrument = market.get(
                "instrument",
                {}
            )

            snapshot = market.get(
                "snapshot",
                {}
            )

            epic = instrument.get("epic")

            if epic:

                rows.append({
                    "EPIC": epic,
                    "Name": instrument.get("name"),
                    "Status": snapshot.get(
                        "marketStatus"
                    )
                })

        if rows:

            st.subheader(
                f"🥇 IG Results: {term}"
            )

            st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True,
                hide_index=True
            )

            return


    st.error(
        "No Gold/XAU markets found."
    )


if st.session_state.cst:

    if st.button("🥇 FIND GOLD"):

        find_gold()
