import streamlit as st
import json
import streamlit.components.v1 as components
from google import genai
from PIL import Image

st.set_page_config(page_title="Quantum X PRO", layout="centered")
st.title("📊 Quantum X PRO")

markets = {
    "XAUUSD Gold": "OANDA:XAUUSD",
    "EURUSD": "OANDA:EURUSD",
    "GBPUSD": "OANDA:GBPUSD",
    "BTCUSD": "BINANCE:BTCUSD",
    "NAS100": "NASDAQ:NDX",
    "US30": "DJ:DJI",
    "Custom": "CUSTOM"
}
choice = st.selectbox("Market", list(markets.keys()))
tv = st.text_input("Symbol", value="OANDA:XAUUSD") if choice == "Custom" else markets[choice]

components.html(
    f'<div id="tv"></div><script src="https://tradingview.com"></script><script>new TradingView.widget({{"autosize":true,"symbol":"{tv}","interval":"15","theme":"dark","container_id":"tv","height":350}})</script>', 
    height=380
)

st.divider()
api = st.text_input("Paste API Key", type="password")
up = st.file_uploader("Upload " + choice + " chart", type=["jpg", "png", "jpeg"])

if up:
    img = Image.open(up)
    st.image(img, use_container_width=True)
    
    if st.button("⚡ Generate Signal"):
        if not api:
            st.error("Paste key")
        else:
            with st.spinner("Analyzing Confluences & Computing Math..."):
                try:
                    client = genai.Client(api_key=api)
                    
                    # STRICT RULESET PROMPT: Forcing the model to count active confluence factors
                    prompt = (
                        f"You are a strict institutional risk manager analyzing this {choice} chart. "
                        "Evaluate the setup using a 3-layer confluence system:\n"
                        "1. Market Structure (Is there a clear, confirmed BOS or CHoCH alignment?)\n"
                        "2. SMC POI (Is price reacting off an unmitigated Order Block or filling an active Fair Value Gap?)\n"
                        "3. Liquidity (Has sell-side or buy-side liquidity been cleanly swept before this expansion?)\n\n"
                        "Extract the exact numerical price coordinates directly from the chart's axis. "
                        "You must count how many of these 3 confluences are fully present. "
                        "Return your evaluation strictly in this JSON format, extracting numbers as float data:\n"
                        '{"bias":"Bullish or Bearish","confluences_present":2,"entry":1.2345,"stop":1.2300,"target":1.2450,"reason":"Detailed structural text explanation"}'
                    )
                    
                    models_to_try = ["gemini-2.5-flash", "gemini-2.0-flash"]
                    r = None
                    
                    for model_name in models_to_try:
                        try:
                            r = client.models.generate_content(
                                model=model_name,
                                contents=[prompt, img],
                                config={"response_mime_type": "application/json"}
                            )
                            if r and r.text:
                                break
                        except Exception:
                            continue
                    
                    if not r:
                        st.error("Engine Timeout: Backend networks failed.")
                    else:
                        d = json.loads(r.text.strip())
                        
                        # Python Backend Math Layer (Bypassing AI guesswork)
                        entry = float(d["entry"])
                        stop = float(d["stop"])
                        target = float(d["target"])
                        
                        risk = abs(entry - stop)
                        reward = abs(target - entry)
                        
                        # Guard against zero-division errors
                        rr_ratio = round(reward / risk, 2) if risk > 0 else 0
                        
                        # Dynamic Scoring Algorithm based on your strict objectives
                        base_score = 40
                        # 1. Add score weight for proven confluences
                        base_score += int(d["confluences_present"]) * 15
                        # 2. Add score weight if it meets your 1:2.5 RR rule floor
                        if rr_ratio >= 2.5:
                            base_score += 15
                            
                        # Map score to institutional grade categories
                        if base_score >= 80: grade = "A (High Precision)"
                        elif base_score >= 65: grade = "B (Moderate Edge)"
                        else: grade = "C (Low Confluence - Do Not Trade)"
                        
                        # Visual Layout Readouts
                        if base_score >= 70 and rr_ratio >= 2.5:
                            st.success(f"🔥 {d['bias']} SIGNAL ACTIVATED | GRADE {grade} | Score {base_score}")
                        else:
                            st.warning(f"⚠️ RISK WARNING: SETUP QUALITY FALLS BELOW OBJECTIVE RULES | GRADE {grade}")
                        
                        c1, c2, c3, c4 = st.columns(4)
                        c1.metric("ENTRY PRICE", f"{entry:,.5f}")
                        c2.metric("STOP LOSS", f"{stop:,.5f}")
                        c3.metric("TAKE PROFIT", f"{target:,.5f}")
                        c4.metric("TRUE RR RATIO", f"1:{rr_ratio}")
                        
                        st.markdown("### 📊 Confluence Audit Report")
                        st.write(f"**Active Confluence Layers Found:** {d['confluences_present']} of 3 verified.")
                        st.write(f"**Structural Evaluation:** {d['reason']}")
                        
                        if base_score >= 75: 
                            st.balloons()
                            
                except Exception as e:
                    st.error(f"Math/Parsing Error: {str(e)}")
