import streamlit as st, json, streamlit.components.v1 as components
from google import genai
from PIL import Image

st.set_page_config(page_title="Quantum X PRO", layout="centered")
st.title("📊 Quantum X PRO")

markets = {"XAUUSD Gold":"OANDA:XAUUSD","EURUSD":"OANDA:EURUSD","GBPUSD":"OANDA:GBPUSD","BTCUSD":"BINANCE:BTCUSD","NAS100":"NASDAQ:NDX","US30":"DJ:DJI","Custom":"CUSTOM"}
choice = st.selectbox("Market", list(markets.keys()))
tv = st.text_input("Symbol", value="OANDA:XAUUSD") if choice=="Custom" else markets[choice]

components.html('<div id="tv"></div><script src="https://s3.tradingview.com/tv.js"></script><script>new TradingView.widget({"autosize":true,"symbol":"'+tv+'","interval":"15","theme":"dark","container_id":"tv","height":350})</script>', height=380)

st.divider()
api = st.text_input("Paste AQ. Key", type="password")
up = st.file_uploader("Upload "+choice+" chart", type=["jpg","png","jpeg"])

if up:
    img = Image.open(up)
    st.image(img, use_container_width=True)
    if st.button("⚡ Generate Signal"):
        if not api:
            st.error("Paste key")
        else:
            with st.spinner("Scanning..."):
                try:
                    client = genai.Client(api_key=api)
                    prompt = "SMC trader "+choice+" Return ONLY JSON {\"bias\":\"Bullish\",\"conf\":78,\"reason\":\"BOS OB FVG\",\"score\":80,\"grade\":\"A\",\"entry\":\"price\",\"stop\":\"price\",\"target\":\"price\",\"rr\":\"1:2.5\"}"
                    # try newest models first
                    for model in ["gemini-3.6-flash","gemini-3.5-flash","gemini-2.5-flash-lite","gemini-2.0-flash"]:
                        try:
                            r = client.models.generate_content(model=model, contents=[prompt, img])
                            break
                        except:
                            continue
                    d = json.loads(r.text.replace("```json","").replace("```","").strip())
                    st.success(f"{d['bias']} | CONF {d['conf']}% | GRADE {d['grade']} | Score {d['score']}")
                    c1,c2,c3,c4 = st.columns(4)
                    c1.metric("ENTRY",d["entry"]); c2.metric("STOP",d["stop"]); c3.metric("TARGET",d["target"]); c4.metric("RR",d["rr"])
                    st.write(d["reason"])
                    if d["score"]>=70: st.balloons()
                except Exception as e:
                    st.error(str(e))
