import json
import os
import re

import streamlit as st
import streamlit.components.v1 as components
from google import genai
from google.genai import types
from PIL import Image


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="Quantum X PRO",
    page_icon="📊",
    layout="centered",
)

st.title("📊 Quantum X PRO")


# ============================================================
# MARKET CONFIG
# ============================================================

markets = {
    "XAUUSD Gold": "OANDA:XAUUSD",
    "EURUSD": "OANDA:EURUSD",
    "GBPUSD": "OANDA:GBPUSD",
    "BTCUSD": "BINANCE:BTCUSD",
    "NAS100": "NASDAQ:NDX",
    "US30": "DJ:DJI",
    "Custom": "CUSTOM",
}


choice = st.selectbox(
    "Market",
    list(markets.keys()),
)


if choice == "Custom":
    tv = st.text_input(
        "TradingView Symbol",
        value="OANDA:XAUUSD",
    ).strip()
else:
    tv = markets[choice]


# ============================================================
# TRADINGVIEW
# ============================================================

# Basic sanitization so a custom symbol cannot inject HTML/JS.
tv_safe = re.sub(r"[^A-Za-z0-9:_\-.]", "", tv)


tradingview_html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        html, body {{
            margin: 0;
            padding: 0;
            width: 100%;
            height: 100%;
            background: #131722;
            overflow: hidden;
        }}

        #tradingview_chart {{
            width: 100%;
            height: 350px;
        }}
    </style>
</head>

<body>

<div id="tradingview_chart"></div>

<script
    type="text/javascript"
    src="https://s3.tradingview.com/tv.js">
</script>

<script>
    new TradingView.widget({{
        "autosize": true,
        "symbol": "{tv_safe}",
        "interval": "15",
        "timezone": "Etc/UTC",
        "theme": "dark",
        "style": "1",
        "locale": "en",
        "enable_publishing": false,
        "hide_top_toolbar": false,
        "hide_legend": false,
        "allow_symbol_change": false,
        "container_id": "tradingview_chart"
    }});
</script>

</body>
</html>
"""


components.html(
    tradingview_html,
    height=380,
)


st.divider()


# ============================================================
# GEMINI API KEY
# ============================================================

st.subheader("🤖 Gemini Analysis Engine")

api = st.text_input(
    "Gemini API Key",
    type="password",
    help="Your Gemini API key is used only for the current Streamlit session.",
)


# ============================================================
# IMAGE UPLOAD
# ============================================================

up = st.file_uploader(
    f"Upload {choice} chart screenshot",
    type=["jpg", "jpeg", "png", "webp"],
)


# ============================================================
# ANALYSIS PROMPT
# ============================================================

def build_prompt(market_name: str) -> str:

    return f"""
You are analyzing a {market_name} trading-chart screenshot.

This is an image-analysis task.

Perform a structured technical analysis using exactly these
three independent confluence layers:

1. MARKET STRUCTURE
   Determine whether there is a clear and confirmed:
   - Break of Structure (BOS), or
   - Change of Character (CHoCH).

2. SMC POINT OF INTEREST
   Determine whether price is interacting with:
   - an unmitigated Order Block, or
   - an active Fair Value Gap (FVG).

3. LIQUIDITY
   Determine whether:
   - buy-side liquidity, or
   - sell-side liquidity
   was clearly swept before the current expansion.

IMPORTANT:

- Do not invent chart information.
- Only use information that is actually visible in the screenshot.
- Read price values from the visible price axis when possible.
- Do not assume a price coordinate that cannot reasonably be read.
- If a price cannot be determined reliably, use null.
- Count a confluence only when the evidence is visible.
- confluences_present must be an integer from 0 to 3.
- Do not count uncertain evidence as confirmed evidence.

For the proposed setup:

- bias must be "Bullish", "Bearish", or "Neutral".
- entry must be a numerical price or null.
- stop must be a numerical price or null.
- target must be a numerical price or null.
- reason must explain the evidence visible in the chart.

Return ONLY valid JSON.

Required structure:

{{
    "bias": "Bullish",
    "confluences_present": 2,
    "entry": 1234.50,
    "stop": 1225.00,
    "target": 1255.00,
    "reason": "Detailed explanation of the visible structure, POI and liquidity evidence."
}}
"""


# ============================================================
# GEMINI ANALYSIS
# ============================================================

def analyze_chart(
    client: genai.Client,
    image: Image.Image,
    market_name: str,
):
    """
    Send the chart image to Gemini and return structured JSON.
    """

    prompt = build_prompt(market_name)

    response_schema = {
        "type": "object",
        "properties": {
            "bias": {
                "type": "string",
                "enum": [
                    "Bullish",
                    "Bearish",
                    "Neutral",
                ],
            },
            "confluences_present": {
                "type": "integer",
                "minimum": 0,
                "maximum": 3,
            },
            "entry": {
                "type": [
                    "number",
                    "null",
                ],
            },
            "stop": {
                "type": [
                    "number",
                    "null",
                ],
            },
            "target": {
                "type": [
                    "number",
                    "null",
                ],
            },
            "reason": {
                "type": "string",
            },
        },
        "required": [
            "bias",
            "confluences_present",
            "entry",
            "stop",
            "target",
            "reason",
        ],
    }

    # Convert image to RGB for consistent JPEG encoding.
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    # Keep uploaded screenshots reasonably small.
    max_width = 2000

    if image.width > max_width:
        ratio = max_width / image.width

        new_size = (
            max_width,
            int(image.height * ratio),
        )

        image = image.resize(
            new_size,
            Image.Resampling.LANCZOS,
        )

    # Encode image into memory.
    import io

    image_buffer = io.BytesIO()

    image.save(
        image_buffer,
        format="JPEG",
        quality=92,
    )

    image_bytes = image_buffer.getvalue()

    # Gemini current stable multimodal model.
    response = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=[
            types.Part.from_bytes(
                data=image_bytes,
                mime_type="image/jpeg",
            ),
            prompt,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=response_schema,
        ),
    )

    if not response or not response.text:
        raise RuntimeError(
            "Gemini returned an empty response."
        )

    raw_text = response.text.strip()

    # Structured output should already be JSON, but this
    # protects against accidental markdown fences.
    if raw_text.startswith("```"):
        raw_text = re.sub(
            r"^```(?:json)?\s*",
            "",
            raw_text,
            flags=re.IGNORECASE,
        )

        raw_text = re.sub(
            r"\s*```$",
            "",
            raw_text,
        )

    try:
        data = json.loads(raw_text)

    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Gemini returned invalid JSON: {raw_text}"
        ) from exc

    return data


# ============================================================
# SIGNAL BUTTON
# ============================================================

if up:

    try:
        img = Image.open(up)

        # Force image loading while the uploaded file is alive.
        img.load()

        st.image(
            img,
            caption=f"{choice} chart",
            use_container_width=True,
        )

    except Exception as exc:

        st.error(
            f"Could not read the uploaded image: {exc}"
        )

        st.stop()


    if st.button(
        "⚡ Generate Signal",
        type="primary",
        use_container_width=True,
    ):

        if not api.strip():

            st.error(
                "Please enter your Gemini API key."
            )

            st.stop()


        # ====================================================
        # GEMINI CLIENT
        # ====================================================

        try:

            client = genai.Client(
                api_key=api.strip()
            )

        except Exception as exc:

            st.error(
                f"Could not initialize Gemini: {exc}"
            )

            st.stop()


        # ====================================================
        # ANALYSIS
        # ====================================================

        with st.spinner(
            "🤖 Gemini is analyzing the chart..."
        ):

            try:

                data = analyze_chart(
                    client=client,
                    image=img,
                    market_name=choice,
                )

            except Exception as exc:

                st.error(
                    "❌ Gemini analysis failed."
                )

                # Show the actual error instead of hiding it.
                with st.expander(
                    "Technical error details"
                ):
                    st.code(
                        str(exc)
                    )

                st.info(
                    "Check that your Gemini API key is valid, "
                    "the Gemini API is enabled, and your account "
                    "has available quota."
                )

                st.stop()


        # ====================================================
        # VALIDATE MODEL RESPONSE
        # ====================================================

        required_fields = [
            "bias",
            "confluences_present",
            "entry",
            "stop",
            "target",
            "reason",
        ]

        missing = [
            field
            for field in required_fields
            if field not in data
        ]

        if missing:

            st.error(
                "Gemini response is missing: "
                + ", ".join(missing)
            )

            st.json(data)

            st.stop()


        # ====================================================
        # BASIC VALIDATION
        # ====================================================

        bias = str(
            data["bias"]
        )

        confluences = int(
            data["confluences_present"]
        )

        reason = str(
            data["reason"]
        )

        entry_raw = data["entry"]
        stop_raw = data["stop"]
        target_raw = data["target"]


        if not 0 <= confluences <= 3:

            st.error(
                "Invalid confluence count returned by Gemini."
            )

            st.stop()


        # ====================================================
        # NO VALID TRADE SETUP
        # ====================================================

        if (
            entry_raw is None
            or stop_raw is None
            or target_raw is None
        ):

            st.warning(
                "⚠️ Gemini could not reliably determine "
                "all three price coordinates from the screenshot."
            )

            st.write(
                f"**Bias:** {bias}"
            )

            st.write(
                f"**Confluences:** "
                f"{confluences}/3"
            )

            st.write(
                f"**Analysis:** {reason}"
            )

            st.stop()


        # ====================================================
        # PRICE MATH
        # ====================================================

        try:

            entry = float(entry_raw)
            stop = float(stop_raw)
            target = float(target_raw)

        except (
            TypeError,
            ValueError,
        ):

            st.error(
                "Gemini returned invalid price values."
            )

            st.json(data)

            st.stop()


        risk = abs(
            entry - stop
        )

        reward = abs(
            target - entry
        )


        if risk <= 0:

            st.error(
                "Invalid setup: entry and stop are identical."
            )

            st.stop()


        rr_ratio = reward / risk


        # ====================================================
        # SCORE
        # ====================================================

        base_score = 40

        base_score += (
            confluences * 15
        )

        if rr_ratio >= 2.5:
            base_score += 15


        base_score = min(
            base_score,
            100,
        )


        # ====================================================
        # GRADE
        # ====================================================

        if base_score >= 80:

            grade = "A"

        elif base_score >= 65:

            grade = "B"

        else:

            grade = "C"


        # ====================================================
        # SIGNAL STATUS
        # ====================================================

        signal_confirmed = (
            base_score >= 70
            and rr_ratio >= 2.5
            and confluences >= 2
        )


        if signal_confirmed:

            st.success(
                f"🔥 {bias} SETUP | "
                f"GRADE {grade} | "
                f"SCORE {base_score}/100"
            )

        else:

            st.warning(
                f"⚠️ SETUP DOES NOT MEET "
                f"THE DEFINED RULES | "
                f"GRADE {grade} | "
                f"SCORE {base_score}/100"
            )


        # ====================================================
        # PRICE METRICS
        # ====================================================

        c1, c2, c3, c4 = st.columns(4)


        c1.metric(
            "ENTRY",
            f"{entry:,.5f}",
        )

        c2.metric(
            "STOP LOSS",
            f"{stop:,.5f}",
        )

        c3.metric(
            "TARGET",
            f"{target:,.5f}",
        )

        c4.metric(
            "RISK / REWARD",
            f"1:{rr_ratio:.2f}",
        )


        # ====================================================
        # AUDIT
        # ====================================================

        st.markdown(
            "### 📊 Confluence Audit Report"
        )


        a1, a2 = st.columns(2)


        a1.metric(
            "Confluences",
            f"{confluences}/3",
        )

        a2.metric(
            "Objective Score",
            f"{base_score}/100",
        )


        st.write(
            f"**Bias:** {bias}"
        )

        st.write(
            f"**Structural Evaluation:** {reason}"
        )


        # ====================================================
        # RAW GEMINI RESPONSE
        # ====================================================

        with st.expander(
            "🔍 View Gemini JSON"
        ):

            st.json(data)


        # ====================================================
        # RISK WARNING
        # ====================================================

        st.caption(
            "This output is an automated chart analysis, "
            "not a guarantee of future market movement. "
            "Verify price levels and risk independently "
            "before taking any position."
)
