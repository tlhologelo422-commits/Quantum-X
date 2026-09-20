import streamlit as st
import json
import streamlit.components.v1 as components
from google import genai
from PIL import Image

st.set_page_config(page_title="Quantum X PRO", page_icon="📊", layout="centered")

st.title("📊 Quantum X PRO - All Markets")
st.caption("Works with new AQ. keys + all markets")

markets = {
"XAUUSD Gold": "OANDA:XAUUSD",
"EURUSD": "OANDA:EURUSD",
"GBPUSD": "OANDA:GBPUSD",
"USDJPY": "OANDA:USDJPY",
"BTCUSD": "BINANCE:BTCUSD",
"NAS100": "NASDAQ:NDX",
"US30": "DJ:DJI",
"Custom": "CUSTOM"
}

choice = st.selectbox("Market", list(markets.keys()), index=0)

if choice == "Custom":
    tv = st.text_input("Type TradingView symbol", value="OANDA:XAUUSD")
else:
    tv = markets[choice]

# Live chart
code = '<div id="tv"></div><script src="https://s3.tradingview.com/tv.js"></script><script>new TradingView.widget({"autosize":true,"symbol":"'+tv+'","interval":"15","theme":"dark","container_id":"tv","height":350})</script>'
components.html(code, height=380)

st.divider()

api = st.text_input("Paste AQ. API Key", type="password")
up = st.file_uploader("Upload "+choice+" Chart", type=["jpg","png","jpeg"])

if up:
    img = Image.open(up)
    st.image(img, use_container_width=True)

    if st.button("⚡ Generate Signal"):
        if not api:
            st.error("Paste key first")
        else:
            with st.spinner("AI scanning "+choice+"..."):
                try:
                    client = genai.Client(api_key=api)
                    prompt = "You are SMC trader for "+choice+". Return ONLY JSON: {\"market\":\""+choice+"\",\"bias\":\"Bullish or Bearish\",\"conf\":75,\"reason\":\"BOS OB FVG\",\"score\":78,\"grade\":\"A\",\"entry\":\"price\",\"stop\":\"price\",\"target\":\"price\",\"rr\":\"1:2.5\"} Rules score BOS25 OB20 FVG20 LQ15 PD20 Grade A if 70+"
                    res = client.models.generate_content(model="gemini-2.5-flash", contents=[prompt, img])
                    txt = res.text.replace("```json","").replace("```","").strip()
                    d = json.loads(txt)

                    if "Bull" in d["bias"]:
                        st.success(d["bias"]+" "+d["market"]+" | CONF "+str(d["conf"])+"% | GRADE "+d["grade"]+" | Score "+str(d["score"]))
                    else:
                        st.error(d["bias"]+" "+d["market"]+" | CONF "+str(d["conf"])+"% | GRADE "+d["grade"]+" | Score "+str(d["score"]))

                    c1,c2,c3,c4 = st.columns(4)
                    c1.metric("ENTRY", d["entry"])
                    c2.metric("STOP", d["stop"])
                    c3.metric("TARGET", d["target"])
                    c4.metric("RR", d["rr"])
                    st.write(d["reason"])

                    if d["score"] >= 70:
                        st.balloons()
                        st.success("STRONG SIGNAL READY")
                    else:
                        st.warning("WEAK SIGNAL - SKIP")

                except Exception as e:
                    st.error(str(e))

st.caption("Not financial advice")
