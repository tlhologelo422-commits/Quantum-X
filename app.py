import streamlit as st, json
import streamlit.components.v1 as components
import google.generativeai as genai
from PIL import Image

st.set_page_config(page_title="Quantum X PRO", page_icon="📊", layout="centered")

st.markdown("""
<style>
.card {background:#111827; border:1px solid #1F2937; border-radius:16px; padding:16px; margin-bottom:10px;}
.bull {color:#10B981; font-weight:800; font-size:28px;}
.bear {color:#EF4444; font-weight:800; font-size:28px;}
.badge {padding:4px 10px; border-radius:20px; font-size:12px; font-weight:700;}
.badge-green {background:#064E3B; color:#10B981;}
.badge-blue {background:#1E3A8A; color:#60A5FA;}
</style>
""", unsafe_allow_html=True)

st.markdown("## 📊 Quantum X Engine PRO - All Markets")

markets = {
    "XAUUSD (Gold)": "OANDA:XAUUSD",
    "EURUSD": "OANDA:EURUSD",
    "GBPUSD": "OANDA:GBPUSD",
    "USDJPY": "OANDA:USDJPY",
    "BTCUSD": "BINANCE:BTCUSD",
    "NAS100": "NASDAQ:NDX",
    "US30": "DJ:DJI",
    "Custom": "CUSTOM"
}
choice = st.selectbox("Select Market:", list(markets.keys()), index=0)
if choice == "Custom":
    custom_sym = st.text_input("Enter TradingView Symbol", value="OANDA:XAUUSD")
    tv_symbol = custom_sym
else:
    tv_symbol = markets[choice]

tv_code = f"""
<div id="tv"></div>
<script src="https://s3.tradingview.com/tv.js"></script>
<script>
new TradingView.widget({{
  "autosize": true,
  "symbol": "{tv_symbol}",
  "interval": "15",
  "timezone": "Africa/Johannesburg",
  "theme": "dark",
  "container_id": "tv",
  "height": 380
}})
</script>
"""
components.html(tv_code, height=400)

st.divider()
api = st.text_input("Gemini API Key:", type="password")
up = st.file_uploader(f"Upload {choice} Chart", type=["jpg","png","jpeg"])

if up:
    img = Image.open(up)
    st.image(img, use_container_width=True)
    if st.button("⚡ Generate Signal"):
        if not api:
            st.error("Add API Key")
        else:
            with st.spinner(f"Scanning {choice}..."):
                try:
                    genai.configure(api_key=api)
                    model = genai.GenerativeModel('gemini-1.5-flash')

                    # FIXED PROMPT - no more f-string brace bug
                    prompt = (
                        "You are an SMC trader for " + choice + ". "
                        "Analyze this chart. Return ONLY JSON like this: "
                        '{"market":"' + choice + '", "bias":"Bullish", "conf":75, "reason":"BOS+OB+FVG align", '
                        '"score":78, "grade":"A", "entry":"price", "stop":"price", "target":"price", "rr":"1:2.5", '
                        '"elements":[{"label":"Structure","detail":"BOS","status":"Confirmed"},'
                        '{"label":"Order Block","detail":"Zone","status":"Candidate"},'
                        '{"label":"FVG","detail":"Zone","status":"Partial"},'
                        '{"label":"Liquidity","detail":"Target","status":"Untaken"},'
                        '{"label":"Premium Discount","detail":"Discount","status":"Discount"}]} '
                        "RULES: score=BOS25+OB20+FVG20+LQ15+PD20. Grade A if >=70."
                    )

                    r = model.generate_content([prompt, img])
                    t = r.text.replace("```json","").replace("```","").strip()
                    d = json.loads(t)

                    cls = "bull" if "Bull" in d['bias'] else "bear"
                    st.markdown(f"<div class='card'><div style='color:#6B7280; font-size:10px;'>{d['market']} | CONF {d['conf']}% | GRADE {d['grade']}</div><div class='{cls}'>{d['bias']}</div><div style='color:#9CA3AF;'>{d['reason']} | Score {d['score']} | RR {d['rr']}</div></div>", unsafe_allow_html=True)

                    for e in d['elements']:
                        st.markdown(f"<div class='card'><b>{e['label']}</b> - {e['detail']} ({e['status']})</div>", unsafe_allow_html=True)

                    c1,c2,c3,c4 = st.columns(4)
                    c1.metric("ENTRY", d['entry'])
                    c2.metric("STOP", d['stop'])
                    c3.metric("TARGET", d['target'])
                    c4.metric("R:R", d['rr'])

                    if d['score'] < 70:
                        st.warning(f"WEAK {d['score']} - Skip")
                    else:
                        st.success(f"STRONG Grade {d['grade']} for {choice}")
                        st.balloons()
                except Exception as e:
                    st.error(f"Error: {e}")

st.caption("Risk: Verify manually. Not financial advice.")
