import streamlit as st, json
import streamlit.components.v1 as components
import google.generativeai as genai
from PIL import Image

st.set_page_config(page_title="Quantum X PRO", page_icon="📊", layout="centered")

st.markdown("## 📊 Quantum X Engine PRO")

tv = """<div id="tv"></div><script src="https://s3.tradingview.com/tv.js"></script><script>new TradingView.widget({"autosize":true,"symbol":"OANDA:XAUUSD","interval":"15","timezone":"Africa/Johannesburg","theme":"dark","container_id":"tv","height":380})</script>"""
components.html(tv, height=400)

api = st.text_input("Gemini API Key:", type="password")
up = st.file_uploader("Upload Chart", type=["jpg","png","jpeg"])

if up:
    img = Image.open(up)
    st.image(img, use_container_width=True)
    if st.button("⚡ Scan"):
        if not api:
            st.error("Add API Key")
        else:
            with st.spinner("Scanning..."):
                genai.configure(api_key=api)
                model = genai.GenerativeModel('gemini-1.5-flash')
                prompt = "You are SMC trader. Return ONLY JSON: {\"bias\":\"Bullish\",\"conf\":75,\"reason\":\"BOS+OB+FVG align\",\"score\":78,\"grade\":\"A\",\"entry\":\"4360\",\"stop\":\"4338\",\"target\":\"4396\",\"elements\":[{\"label\":\"BOS break\",\"detail\":\"High broken\",\"status\":\"Confirmed\"},{\"label\":\"Order Block\",\"detail\":\"4348-4368\",\"status\":\"Candidate\"},{\"label\":\"FVG\",\"detail\":\"4360-4378\",\"status\":\"Partial\"},{\"label\":\"Liquidity\",\"detail\":\"4395\",\"status\":\"Untaken\"}]} RULES: score=BOS25+OB20+FVG20+LQ15+PD20. Grade A if >=70."
                r = model.generate_content([prompt, img])
                t = r.text.replace("```json","").replace("```","").strip()
                d = json.loads(t)
                st.metric("Bias", f"{d['bias']} {d['conf']}%")
                st.write(f"Score: {d['score']} Grade {d['grade']} - {d['reason']}")
                for e in d['elements']:
                    st.write(f"**{e['label']}** - {e['detail']} ({e['status']})")
                c1,c2,c3 = st.columns(3)
                c1.metric("ENTRY", d['entry'])
                c2.metric("STOP", d['stop'])
                c3.metric("TARGET", d['target'])
                if d['score']<70:
                    st.warning("Weak - Skip")
                else:
                    st.success("Strong Setup")

st.caption("Risk: Verify levels manually. Not financial advice.")
