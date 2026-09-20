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

# === ALL MARKETS SELECTOR ===
markets = {
    "XAUUSD (Gold)": "OANDA:XAUUSD",
    "EURUSD": "OANDA:EURUSD",
    "GBPUSD": "OANDA:GBPUSD",
    "USDJPY": "OANDA:USDJPY",
    "BTCUSD": "BINANCE:BTCUSD",
    "NAS100": "NASDAQ:NDX",
    "US30": "DJ:DJI",
    "GER40": "XETR:DAX",
    "Custom": "CUSTOM"
}
choice = st.selectbox("Select Market:", list(markets.keys()), index=0)
if choice == "Custom":
    custom_sym = st.text_input("Enter TradingView Symbol (e.g. BINANCE:ETHUSD)", value="OANDA:XAUUSD")
    tv_symbol = custom_sym
else:
    tv_symbol = markets[choice]

# Live chart that changes with market
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
  "style": "1",
  "locale": "en",
  "hide_side_toolbar": true,
  "allow_symbol_change": true,
  "container_id": "tv",
  "height": 380
}})
</script>
"""
components.html(tv_code, height=400)

st.divider()
st.markdown(f"### 🔍 AI Signal Scanner - {choice}")

api = st.text_input("Gemini API Key:", type="password", help="Free from aistudio.google.com")
up = st.file_uploader(f"Upload {choice} Chart Screenshot", type=["jpg","png","jpeg"])

if up:
    img = Image.open(up)
    st.image(img, use_container_width=True)

    if st.button("⚡ Generate Signal"):
        if not api:
            st.error("Add API Key")
        else:
            with st.spinner(f"Scanning {choice} for BOS + OB + FVG..."):
                try:
                    genai.configure(api_key=api)
                    model = genai.GenerativeModel('gemini-1.5-flash')

                    prompt = f"""
                    You are an SMC institutional trader for {choice} ({tv_symbol}).
                    Analyze this {choice} chart. Return ONLY valid JSON:
                    {{
                      "market": "{choice}",
                      "bias": "Bullish or Bearish or Neutral",
                      "conf": 75,
                      "reason": "Why
