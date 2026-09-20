import streamlit as st
import streamlit.components.v1 as components
import google.generativeai as genai
from PIL import Image

# 1. Page settings optimized for scrolling on mobile browsers
st.set_page_config(
    page_title="Quantum X App Engine", 
    page_icon="📊", 
    layout="centered"
)

# Dark industrial header styling
st.markdown("""
    <style>
        .main-title { font-size:28px; font-weight:bold; color:#F3F4F6; margin-bottom:5px; }
        .sub-title { font-size:14px; color:#9CA3AF; margin-bottom:20px; }
    </style>
""", unsafe_allowed_html=True)

st.markdown('<div class="main-title">📊 Quantum X Engine</div>', unsafe_allowed_html=True)
st.markdown('<div class="sub-title">Multi-Factor Confluence & SMC Chart Workspace</div>', unsafe_allowed_html=True)

# 2. Free live-updating Gold (XAUUSD) Chart Widget Integration
st.markdown("### 📈 Live Market Feed")
tradingview_widget_html = """
<div class="tradingview-widget-container" style="height:380px; width:100%;">
  <div id="tradingview_quantum_x" style="height:100%; width:100%;"></div>
  <script type="text/javascript" src="https://tradingview.com"></script>
  <script type="text/javascript">
  new TradingView.widget({
    "autosize": true,
    "symbol": "FX_IDC:XAUUSD",
    "interval": "30",
    "timezone": "Africa/Johannesburg",
    "theme": "dark",
    "style": "1",
    "locale": "en",
    "enable_publishing": false,
    "hide_side_toolbar": true,
    "allow_symbol_change": true,
    "container_id": "tradingview_quantum_x"
  });
  </script>
</div>
"""
components.html(tradingview_widget_html, height=390)

st.markdown("---")

# 3. AI Processing Pipeline Setup 
st.markdown("### 🔍 Multi-Layer Confluence Scanner")

# Secure layout entry for API key directly on interface 
user_api_key = st.text_input("Enter Gemini API Key:", type="password", help="Input your free Google AI Studio token here.")

uploaded_chart = st.file_uploader("Upload Price Chart Screenshot", type=["jpg", "jpeg", "png"])

if uploaded_chart is not None:
    mobile_image = Image.open(uploaded_chart)
    st.image(mobile_image, caption="Uploaded Chart Profile", use_container_width=True)
    
    # Execution block triggered via interactive mobile button tap
    if st.button("⚡ Execute Confluence Scan"):
        if not user_api_key:
            st.error("Missing Security Credential: Please provide a valid Gemini API Key above.")
        else:
            with st.spinner("Processing structural points, gaps, and sentiment matrix..."):
                try:
                    # Injected system instruction layer parameters
                    genai.configure(api_key=user_api_key)
                    
                    structural_prompt = """
                    You are an institutional-grade risk manager specializing in Smart Money Concepts (SMC) and multi-factor confluence trading. 
                    Analyze the uploaded price chart screenshot. Provide a structured dashboard readout containing:
                    
                    1. 📈 MARKET STRUCTURE: Identify current order flow (Bullish/Bearish). Check for Break of Structure (BOS) or Change of Character (CHoCH).
                    2. 🔍 SMC ENGINES: Look for obvious Fair Value Gaps (FVG) or institutional Order Blocks (OB).
                    3. 💡 CONFLUENCE MATRIX & SCORE: Grade the setup from A to F and evaluate overall confidence (1 to 10). A setup is only high-grade if structure, key zones, and momentum lines align concurrently.
                    4. 🎯 TARGET MATRIX: Approximate an entry target level, a strict invalidation / Stop Loss line, and a logical profit target baseline.
                    """
                    
                    # Call fast multi-modal engine
                    model = genai.GenerativeModel('gemini-1.5-flash')
                    analysis_output = model.generate_content([structural_prompt, mobile_image])
                    
                    st.success("Analysis Dispatched Successfully!")
                    st.markdown("### 📊 Quantum X Evaluation Matrix")
                    st.markdown(analysis_output.text)
                    
                except Exception as system_error:
                    st.error(f"Execution Error: {system_error}")

# 4. Mandatory Risk Management Disclaimer Bar
st.markdown("---")
st.caption("⚠️ **Risk Disclosure Summary:** Vision-based AI execution tools possess clear spatial precision limitations. Mathematical coordinates and order book values displayed on dashboard evaluations must always be manually cross-verified
