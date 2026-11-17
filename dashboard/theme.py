"""Farben, Symbole und das eigene CSS des Dashboards."""
import streamlit as st

SENTIMENT_COLOR = {"positive": "#00C853", "negative": "#D50000", "neutral": "#2979FF"}
SENTIMENT_ICON  = {"positive": "🟢",       "negative": "🔴",       "neutral":  "🔵"}

# Elliott-Wellen: Farbe folgt der Richtung (bullisch/bärisch), nicht dem
# Wellentyp — Impuls/Korrektur wird stattdessen über den Linienstil (solid/
# dash) unterschieden. Bewusst blassere, entsättigte Töne im Vergleich zu den
# Candle-/News-Farben, damit die Wellen-Overlays nicht mit den Kursdaten
# konkurrieren, sondern sich als eigene Analyseebene abheben.
WAVE_DIRECTION_COLOR = {"bullish": "#6FD9A0", "bearish": "#E8877D"}


def hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


# ─── Custom Look & Feel ──────────────────────────────────────────────────────
# Reine CSS-/Chrome-Politur (Terminal-artiges Dark-UI, angelehnt an TradingView/
# Bloomberg-Layouts) — greift über data-testid/data-baseweb-Selektoren global,
# ohne die bestehenden st.metric()/st.tabs()-Aufrufe in den einzelnen Tabs
# anfassen zu müssen.
def inject_custom_css() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
        html, body, [class*="css"] {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        }

        [data-testid="stAppViewContainer"] > .main {
            background: radial-gradient(circle at 12% 0%, #131722 0%, #0B0E14 55%) fixed;
        }

        section[data-testid="stSidebar"] {
            background: linear-gradient(180deg, #10131C 0%, #0B0E14 100%);
            border-right: 1px solid rgba(255,255,255,0.06);
        }

        /* ── Header-Banner ─────────────────────────────────────────────── */
        .fi-header {
            display: flex; align-items: center; justify-content: space-between;
            flex-wrap: wrap; gap: 10px;
            padding: 18px 26px; margin-bottom: 20px;
            border-radius: 14px;
            background: linear-gradient(120deg, rgba(46,125,247,0.16), rgba(19,23,34,0.35));
            border: 1px solid rgba(46,125,247,0.25);
        }
        .fi-header h1 {
            font-size: 1.55rem; font-weight: 800; margin: 0; letter-spacing: -0.02em;
            background: linear-gradient(90deg, #ffffff, #9fc4ff);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        .fi-header p { margin: 3px 0 0 0; color: #8B93A7; font-size: 0.85rem; }
        .fi-header .fi-meta { text-align: right; font-size: 0.78rem; color: #8B93A7; line-height: 1.5; }
        .fi-header .fi-meta b { color: #E6E9F0; }

        /* ── Metric-Karten ─────────────────────────────────────────────── */
        div[data-testid="stMetric"] {
            background: #131722;
            border: 1px solid rgba(255,255,255,0.07);
            border-left: 3px solid #2E7DF7;
            border-radius: 10px;
            padding: 12px 16px 10px 16px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.25);
        }
        div[data-testid="stMetricLabel"] {
            color: #8B93A7; font-size: 0.76rem;
            text-transform: uppercase; letter-spacing: 0.05em;
        }
        div[data-testid="stMetricValue"] { font-weight: 700; }

        /* ── Tabs ───────────────────────────────────────────────────────── */
        div[data-baseweb="tab-list"] {
            gap: 4px;
            border-bottom: 1px solid rgba(255,255,255,0.08);
        }
        button[data-baseweb="tab"] {
            background: transparent;
            border-radius: 8px 8px 0 0;
            padding: 8px 16px;
            font-weight: 600;
            color: #8B93A7;
        }
        button[data-baseweb="tab"][aria-selected="true"] {
            color: #ffffff;
            background: rgba(46,125,247,0.12);
            border-bottom: 2px solid #2E7DF7;
        }

        /* ── Buttons ────────────────────────────────────────────────────── */
        .stButton > button {
            border-radius: 8px;
            border: 1px solid rgba(46,125,247,0.35);
            font-weight: 600;
            transition: all .15s ease;
        }
        .stButton > button:hover {
            border-color: #2E7DF7;
            color: #2E7DF7;
        }

        /* ── Expander / bordered containers / dataframes ───────────────── */
        div[data-testid="stExpander"] {
            border-radius: 10px !important;
            border: 1px solid rgba(255,255,255,0.07) !important;
            background: #10131C;
        }
        div[data-testid="stVerticalBlockBorderWrapper"] {
            border-radius: 12px !important;
            border-color: rgba(255,255,255,0.09) !important;
        }
        div[data-testid="stDataFrame"] {
            border-radius: 10px;
            overflow: hidden;
            border: 1px solid rgba(255,255,255,0.07);
        }

        /* ── Sidebar-Branding ──────────────────────────────────────────── */
        .fi-sidebar-brand {
            padding: 4px 0 14px 0;
            margin-bottom: 6px;
            border-bottom: 1px solid rgba(255,255,255,0.08);
        }
        .fi-sidebar-brand h2 {
            margin: 0; font-size: 1.25rem; font-weight: 800; letter-spacing: -0.02em;
        }
        .fi-sidebar-brand p {
            margin: 2px 0 0 0; font-size: 0.75rem; color: #8B93A7;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
CONTINENT_EMOJI = {
    "Nordamerika": "🇺🇸",
    "Europa":      "🇪🇺",
    "Asien":       "🏮",
    "Südamerika":  "🌎",
    "Afrika & NO": "🌍",
    "Ozeanien":    "🌏",
    "Global":      "🌐",
    "Alle":        "🗺️",
}
