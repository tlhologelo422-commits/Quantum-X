import streamlit as st
import streamlit.components.v1 as components
 google.generativeai as genai
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
    
