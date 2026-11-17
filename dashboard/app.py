"""
Finance Intelligence Dashboard
Start: streamlit run dashboard/app.py   (from project root)
"""
import bisect
import sys
from pathlib import Path
from collections import Counter
from urllib.parse import quote

import streamlit as st
import streamlit.components.v1 as components
import plotly.graph_objects as go
import pandas as pd
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.db import get_connection, init_db
from src.watchlist_updater import ALL_CONTINENTS

# ─── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Finance Intelligence",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

SENTIMENT_COLOR = {"positive": "#00C853", "negative": "#D50000", "neutral": "#2979FF"}
SENTIMENT_ICON  = {"positive": "🟢",       "negative": "🔴",       "neutral":  "🔵"}

# Elliott-Wellen: Farbe folgt der Richtung (bullisch/bärisch), nicht dem
# Wellentyp — Impuls/Korrektur wird stattdessen über den Linienstil (solid/
# dash) unterschieden. Bewusst blassere, entsättigte Töne im Vergleich zu den
# Candle-/News-Farben, damit die Wellen-Overlays nicht mit den Kursdaten
# konkurrieren, sondern sich als eigene Analyseebene abheben.
WAVE_DIRECTION_COLOR = {"bullish": "#6FD9A0", "bearish": "#E8877D"}


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


# ─── Custom Look & Feel ──────────────────────────────────────────────────────
# Reine CSS-/Chrome-Politur (Terminal-artiges Dark-UI, angelehnt an TradingView/
# Bloomberg-Layouts) — greift über data-testid/data-baseweb-Selektoren global,
# ohne die bestehenden st.metric()/st.tabs()-Aufrufe in den einzelnen Tabs
# anfassen zu müssen.
def _inject_custom_css() -> None:
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

# ─── DB helpers ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=120)
def _load_watchlist() -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql(
        "SELECT * FROM watchlist ORDER BY continent, asset_type, ticker", conn
    )
    conn.close()
    return df


@st.cache_data(ttl=120)
def _load_prices(ticker: str, start: str, end: str) -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql(
        """SELECT date, open, high, low, close, volume
           FROM stock_prices
           WHERE ticker=? AND date BETWEEN ? AND ?
           ORDER BY date""",
        conn, params=[ticker, start, end],
    )
    conn.close()
    return df


@st.cache_data(ttl=120)
def _load_news_for_ticker(ticker: str, start: str, end: str) -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql(
        """SELECT ni.id, ni.title,
                  DATE(ni.published_at) AS date,
                  ni.published_at,
                  ni.summary, ni.url,
                  ni.sentiment_label,
                  ni.sentiment_score,
                  s.name AS source, s.region
           FROM news_items ni
           JOIN news_tickers nt ON ni.id = nt.news_id
           JOIN sources s      ON ni.source_id = s.id
           WHERE nt.ticker = ?
             AND DATE(ni.published_at) BETWEEN ? AND ?
           GROUP BY ni.id
           ORDER BY ni.published_at DESC""",
        conn, params=[ticker, start, end],
    )
    conn.close()
    return df


@st.cache_data(ttl=120)
def _load_insider_trades_for_ticker(ticker: str, start: str, end: str) -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql(
        """SELECT source, trader, ticker, company,
                  trade_date, trade_type,
                  amount_min, amount_max, shares_delta, fund
           FROM insider_trades
           WHERE ticker = ?
             AND trade_date BETWEEN ? AND ?
           ORDER BY trade_date DESC""",
        conn, params=[ticker, start, end],
    )
    conn.close()
    return df


@st.cache_data(ttl=120)
def _insider_bias(ticker: str, days: int = 180) -> dict:
    """Aggregiert Insider-Trades (Congress + ARK) der letzten `days` Tage zu
    einer groben Richtungseinschätzung — dient als unabhängiger Abgleich für
    Elliott-Wellen-Einstiegssignale (bestätigt die Richtung oder widerspricht ihr).
    bias: 'bullish' (mehr Käufe), 'bearish' (mehr Verkäufe), 'mixed' (Gleichstand,
    aber Trades vorhanden) oder None (keine Trades im Zeitraum)."""
    end   = str(datetime.today().date())
    start = str(datetime.today().date() - timedelta(days=days))
    df = _load_insider_trades_for_ticker(ticker, start, end)
    buy_count  = int((df["trade_type"] == "buy").sum())
    sell_count = int((df["trade_type"] == "sell").sum())
    if df.empty:
        bias = None
    elif buy_count > sell_count:
        bias = "bullish"
    elif sell_count > buy_count:
        bias = "bearish"
    else:
        bias = "mixed"
    return {"bias": bias, "buy_count": buy_count, "sell_count": sell_count, "trades": df}


@st.cache_data(ttl=3600)
def _load_fundamentals(ticker: str) -> dict:
    """Fundamentaldaten ändern sich langsam — 1h Cache statt 120s wie bei Kursen."""
    from src.fetchers.stock_fetcher import get_ticker_info
    return get_ticker_info(ticker)


@st.cache_data(ttl=3600)
def _load_fundamental_scores(tickers: tuple) -> pd.DataFrame:
    """Piotroski-F-Score/Gross-Profitability/Fama-French-Proxy für die
    übergebenen Ticker, aus dem DB-Cache (fundamentals_raw) — löst KEINEN
    Live-Abruf aus (das wäre bei hunderten Tickern zu langsam), siehe dazu
    _fundamentals_refresh_button(). Die Perzentile (Value/Profitabilität/
    Investment/Quality) werden relativ zu genau dieser Ticker-Menge
    berechnet — deshalb möglichst dieselbe (kontinent-)gefilterte Menge
    übergeben, die gerade angezeigt/gescannt wird, nicht in jedem Tab neu
    zusammengewürfelt."""
    from src.analytics.fundamentals import load_scores
    conn = get_connection()
    df = load_scores(conn, list(tickers))
    conn.close()
    return df


def _fundamentals_refresh_button(tickers: list, key: str) -> None:
    """Rendert einen Button, der fundamentals_raw für die übergebenen
    (Aktien-)Ticker live nachlädt — nur was älter als 7 Tage oder noch nie
    abgerufen ist (siehe refresh_fundamentals()). Für die komplette
    Watchlist ist 'python main.py fundamentals' der schnellere Weg
    (kein Streamlit-Rerun-Overhead); der Button hier ist für einen
    schnellen Nachschlag der aktuell relevanten Teilmenge gedacht."""
    from src.analytics.fundamentals import refresh_fundamentals
    if st.button(f"🔄 Fundamentaldaten aktualisieren ({len(tickers)} Aktien)", key=key):
        with st.spinner("Lade Bilanz-/GuV-Daten (yfinance, ein Request je Aktie) …"):
            conn = get_connection()
            result = refresh_fundamentals(conn, tickers)
            conn.close()
        _load_fundamental_scores.clear()
        st.toast(
            f"✅ {result['refreshed']} aktualisiert · {result['skipped_fresh']} bereits aktuell "
            f"· {result['no_data']} ohne auswertbare Bilanzdaten",
            icon="✅",
        )
        st.rerun()


def _fundamental_badge(score) -> str:
    if score is None or (isinstance(score, float) and pd.isna(score)):
        return "➖ n/a"
    if score >= 70:
        return f"🟢 Stark ({score:.0f}/100)"
    if score >= 40:
        return f"🟡 Mittel ({score:.0f}/100)"
    return f"🔴 Schwach ({score:.0f}/100)"


def _fund_score_for(scores: pd.DataFrame, ticker: str):
    """fundamental_score für einen einzelnen Ticker aus einer
    _load_fundamental_scores()-Tabelle, None wenn nicht vorhanden/keine
    Bilanzdaten (ETF/Index/Krypto)."""
    if scores.empty:
        return None
    row = scores[scores["ticker"] == ticker]
    if row.empty:
        return None
    v = row.iloc[0]["fundamental_score"]
    return None if (v is None or (isinstance(v, float) and pd.isna(v))) else v


def _fundamental_prefilter_controls(wl_filtered: pd.DataFrame, key_prefix: str):
    """Gemeinsame UI (Checkbox + Mindestscore-Slider + Refresh-Button) für den
    Fundamental-Vorfilter, genutzt von Wellen-Scanner, Signale und
    Insider-Papier-Trades — dieselben drei Studien wie im Kurschart-Tab
    (Fama-French/Piotroski/Novy-Marx), hier aber um VOR dem eigentlichen
    Elliott-Wellen-Scan auszusieben statt nur anzuzeigen. Liest nur den
    DB-Cache (kein Live-Abruf), damit ein Scan über hunderte Ticker nicht an
    yfinance-Latenz hängt. Gibt (aktiv, Mindestscore, Scores-Tabelle) zurück."""
    stock_tickers = wl_filtered[wl_filtered["asset_type"] == "stock"]["ticker"].tolist()
    universe = tuple(sorted(stock_tickers))
    scores = _load_fundamental_scores(universe)

    with st.expander(
        "📐 Fundamental-Vorfilter (Fama-French/Piotroski-F-Score/Novy-Marx Gross Profitability)",
        expanded=False,
    ):
        st.caption(
            "Schließt Aktien mit schwachem Fundamentalscore VOR dem Elliott-Wellen-Scan aus, "
            "statt sie nur anzuzeigen — hält Papier-Trades/Vorhersagen/Signale auf fundamental "
            "vielversprechendere Titel fokussiert. ETFs, Indizes und Krypto haben keine "
            "auswertbaren Bilanzdaten und sind von der Filterung IMMER ausgenommen (die drei "
            "Studien gelten nur für Einzelaktien)."
        )
        fc1, fc2 = st.columns([1, 2])
        active = fc1.checkbox("Aktiv", value=True, key=f"{key_prefix}_fund_on")
        min_score = fc2.slider(
            "Mindest-Fundamentalscore", min_value=0, max_value=100, value=30, step=5,
            key=f"{key_prefix}_fund_min", disabled=not active,
        )
        n_ok = int(scores.attrs.get("n_universe", 0)) if not scores.empty else 0
        st.caption(f"{n_ok} von {len(stock_tickers)} Aktien mit gecachten Fundamentaldaten.")
        _fundamentals_refresh_button(stock_tickers, key=f"{key_prefix}_fund_refresh")

    return active, float(min_score), scores


def _apply_fundamental_prefilter(
    wl_filtered: pd.DataFrame, active: bool, min_score: float, scores: pd.DataFrame,
) -> tuple:
    """Wendet den Fundamental-Vorfilter auf eine Watchlist-Teilmenge an.
    Ticker ohne Fundamentaldaten (nicht im scores-Universum, z.B. ETF/Index/
    Krypto) bleiben immer erhalten. Gibt (gefilterte Watchlist, Anzahl
    ausgeschlossen) zurück."""
    if not active or scores.empty:
        return wl_filtered, 0
    from src.analytics.fundamentals import filter_promising
    included, _ = filter_promising(scores, min_score)
    included_set  = set(included)
    scored_tickers = set(scores["ticker"])
    keep = wl_filtered["ticker"].isin(included_set) | ~wl_filtered["ticker"].isin(scored_tickers)
    return wl_filtered[keep], int((~keep).sum())


@st.cache_data(ttl=120)
def _load_observations(ticker: str) -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql(
        """SELECT id, obs_date, note, color, created_at
           FROM chart_observations
           WHERE ticker=?
           ORDER BY obs_date DESC""",
        conn, params=[ticker],
    )
    conn.close()
    return df


@st.cache_data(ttl=120)
def _load_all_news(limit: int = 400) -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql(
        """SELECT ni.id, ni.title,
                  DATE(ni.published_at) AS date,
                  ni.summary, ni.url,
                  ni.sentiment_label, ni.sentiment_score,
                  s.name AS source, s.region,
                  GROUP_CONCAT(DISTINCT nt.ticker) AS tickers
           FROM news_items ni
           JOIN sources s ON ni.source_id = s.id
           LEFT JOIN news_tickers nt ON ni.id = nt.news_id
           GROUP BY ni.id
           ORDER BY ni.published_at DESC
           LIMIT ?""",
        conn, params=[limit],
    )
    conn.close()
    return df


# ─── Krypto Top 50 — Helper ──────────────────────────────────────────────────
from src.fetchers.crypto_fetcher import CRYPTO_PROXY_TICKERS  # noqa: E402 (zentrale Liste, siehe Moduldoc dort)


@st.cache_data(ttl=300)
def _load_crypto_top50() -> pd.DataFrame:
    """Top 50 Kryptowährungen nach Marktkapitalisierung, live von CoinGecko.
    5-Minuten-Cache statt 120s wie bei Kursdaten — Ranking/Preise ändern sich
    für diesen Überblick nicht schnell genug, um kürzer zu cachen, und schont
    das Free-Tier-Rate-Limit."""
    from src.fetchers.crypto_fetcher import fetch_top50_markets
    return fetch_top50_markets()


def _sync_crypto_watchlist(top50: pd.DataFrame) -> int:
    """Trägt neue Top-50-Coins in die Watchlist ein (asset_type='crypto',
    Kontinent 'Global' — dieselbe Gruppe wie die bereits vorhandenen
    BTC-USD/ETH-USD-Einträge), damit sie automatisch auch in den generischen
    Tabs (Wellen-Scanner, Signale, Vergleich, …) bei Kontinent 'Global'/'Alle'
    auftauchen. Add-only, wie update_watchlist() — ändert nie bestehende
    Einträge. Gibt die Anzahl neu hinzugefügter Coins zurück."""
    if top50.empty:
        return 0
    conn = get_connection()
    existing = {r["ticker"] for r in conn.execute("SELECT ticker FROM watchlist").fetchall()}
    added = 0
    for _, r in top50.iterrows():
        if r["ticker"] not in existing:
            conn.execute(
                "INSERT OR IGNORE INTO watchlist (ticker,name,asset_type,continent) VALUES (?,?,?,?)",
                (r["ticker"], r["name"], "crypto", "Global"),
            )
            added += 1
    conn.commit()
    conn.close()
    if added:
        _load_watchlist.clear()
    return added


def _ensure_crypto_price_history(top50: pd.DataFrame, status=None) -> int:
    """Lädt für jeden Top-50-Coin ohne aktuelle Kurs-Historie (siehe
    _prices_need_update) die Tageshistorie über crypto_fetcher nach — bewusst
    CoinGecko statt yfinance (siehe Modul-Doc in crypto_fetcher.py:
    uneinheitliche/gesuffixte Yahoo-Ticker für viele Altcoins). Die
    Rate-Limit-Pause zwischen Requests steckt in
    crypto_fetcher.fetch_top50_history_batch(), nicht hier verdoppelt.
    `status` (st.status-Objekt) optional für Fortschrittsausgabe. Gibt die
    Anzahl aktualisierter Coins zurück."""
    from src.fetchers.crypto_fetcher import fetch_top50_history_batch
    from src.database.db import store_prices

    stale = top50[top50["ticker"].apply(_prices_need_update)]
    if stale.empty:
        return 0

    def _progress(i, n, ticker):
        if status:
            status.write(f"⏳ ({i + 1}/{n}) {ticker}")

    results = fetch_top50_history_batch(stale, progress_cb=_progress)

    conn = get_connection()
    updated = 0
    for df in results.values():
        if not df.empty:
            store_prices(conn, df)
            conn.commit()
            updated += 1
    conn.close()
    if updated:
        _load_prices.clear()
    return updated


def _crypto_proxy_insider_bias(days: int = 180) -> dict:
    """Wie _insider_bias(), aber über mehrere Ticker (CRYPTO_PROXY_TICKERS)
    aggregiert — die Krypto-Proxy-Variante des Insider-Abgleichs, da Coins
    selbst keine SEC-Insider-Meldungen haben."""
    end   = str(datetime.today().date())
    start = str(datetime.today().date() - timedelta(days=days))
    frames = [_load_insider_trades_for_ticker(t, start, end) for t in CRYPTO_PROXY_TICKERS]
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    buy_count  = int((df["trade_type"] == "buy").sum())  if not df.empty else 0
    sell_count = int((df["trade_type"] == "sell").sum()) if not df.empty else 0
    if df.empty:
        bias = None
    elif buy_count > sell_count:
        bias = "bullish"
    elif sell_count > buy_count:
        bias = "bearish"
    else:
        bias = "mixed"
    return {"bias": bias, "buy_count": buy_count, "sell_count": sell_count, "trades": df}


def _fmt_crypto_price(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    v = float(v)
    return f"${v:,.2f}" if v >= 1 else f"${v:.6f}"


def _fmt_large_usd(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    v = float(v)
    if v >= 1e9:
        return f"${v / 1e9:,.2f} Mrd."
    if v >= 1e6:
        return f"${v / 1e6:,.1f} Mio."
    return f"${v:,.0f}"


def _pct_badge(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    icon = "🟢" if v >= 0 else "🔴"
    return f"{icon} {v:+.2f} %"


def _crypto_technical_metrics(ticker: str, sensitivity: float) -> dict:
    """Kombinierte technische Kennzahlen für die Krypto-Top-50-Übersicht —
    lädt die Kurshistorie einmal (_load_prices, gecacht) und leitet daraus
    sowohl die Elliott-Wellen-Tendenz (elliott_wave.py) als auch den RSI(14)
    (das bislang ungenutzte indicators.py-Modul — bisher nur für Aktien
    gebaut, aber genauso rein preisbasiert auf Krypto anwendbar) ab, statt
    dieselben Kurse zweimal zu laden.

    'has_data'=False heißt 'noch keine ausreichende Kurshistorie' (siehe
    'Kurs-Historie aktualisieren'); 'wave'=None bei has_data=True heißt
    'Kurshistorie vorhanden, aber aktuell keine erkennbare Wellenstruktur' —
    zwei unterschiedliche Situationen, die nicht dasselbe Badge verdienen."""
    from src.analytics.elliott_wave import analyze_current_wave
    from src.analytics.indicators import rsi as _rsi
    end   = str(datetime.today().date())
    start = str(datetime.today().date() - timedelta(days=365))
    prices = _load_prices(ticker, start, end)
    if len(prices) < 20:
        return {"has_data": False, "wave": None, "rsi": None}

    wave = analyze_current_wave(prices, threshold_pct=sensitivity)
    rsi_series = _rsi(prices.sort_values("date")["close"], period=14)
    rsi_value = float(rsi_series.iloc[-1]) if not rsi_series.empty and pd.notna(rsi_series.iloc[-1]) else None
    return {"has_data": True, "wave": wave, "rsi": rsi_value}


def _tendency_badge(metrics: dict) -> str:
    """Formatiert die Elliott-Wellen-Tendenz aus _crypto_technical_metrics()
    als 'wie viel Prozent sicher ist eine bullische/bärische Phase'-Badge —
    die Konfidenz aus analyze_current_wave() ist genau das: eine Fibonacci-
    Konformitäts-basierte Sicherheit der erkannten Wellenstruktur, nicht der
    Richtung an sich."""
    if not metrics["has_data"]:
        return "➖ keine Daten"
    state = metrics["wave"]
    if state is None:
        return "⚪ kein Muster"
    icon  = "🟢" if state["direction"] == "bullish" else "🔴"
    label = "Bullisch" if state["direction"] == "bullish" else "Bärisch"
    return f"{icon} {state['confidence'] * 100:.0f}% {label}"


def _rsi_badge(metrics: dict) -> str:
    """RSI(14)-Badge — Standard-Schwellen 70/30 für überkauft/überverkauft.
    Unabhängig von der Elliott-Wellen-Tendenz berechnet (reiner Preis-
    Momentum-Indikator), dient als zweite, davon unabhängige Einschätzung."""
    if not metrics["has_data"] or metrics["rsi"] is None:
        return "—"
    v = metrics["rsi"]
    if v >= 70:
        return f"🔴 {v:.0f} (überkauft)"
    if v <= 30:
        return f"🟢 {v:.0f} (überverkauft)"
    return f"⚪ {v:.0f}"


def _ath_distance_badge(ath_change_pct) -> str:
    """Abstand vom Allzeithoch (CoinGecko liefert ath_change_percentage
    bereits mit — bislang ungenutzt). Grobe Einordnung: nah am ATH (<5%
    entfernt) = 🚀, moderater Rückgang (5-30%) = 🟡, tiefer Rückgang (>30%)
    = 🔴 — je tiefer, desto mehr 'Erholungspotenzial', aber auch desto mehr
    ist bereits gegen den Coin gelaufen; keine Kauf-/Verkaufsempfehlung."""
    if ath_change_pct is None or pd.isna(ath_change_pct):
        return "—"
    v = float(ath_change_pct)
    icon = "🚀" if v >= -5 else ("🟡" if v >= -30 else "🔴")
    return f"{icon} {v:+.1f} %"


def _vol_mcap_badge(volume_24h, market_cap) -> str:
    """Handelsvolumen (24h) im Verhältnis zur Marktkapitalisierung, in % —
    grobes Liquiditäts-/Handelsinteresse-Maß: ein sehr niedriger Wert deutet
    auf dünnen Handel hin (Kurs leichter durch einzelne große Orders zu
    bewegen), ein sehr hoher auf aktuell überdurchschnittliches Interesse
    (z.B. während starker Kursbewegungen oder News)."""
    if not volume_24h or not market_cap or pd.isna(volume_24h) or pd.isna(market_cap) or market_cap == 0:
        return "—"
    ratio = float(volume_24h) / float(market_cap) * 100
    icon = "🟢" if ratio >= 5 else ("⚪" if ratio >= 1 else "🔴")
    return f"{icon} {ratio:.1f} %"


@st.cache_data(ttl=1800)
def _load_fear_greed() -> list[dict]:
    """Crypto Fear & Greed Index — 30-Min.-Cache, da der Index nur einmal
    täglich aktualisiert wird (siehe crypto_fetcher.fetch_fear_greed)."""
    from src.fetchers.crypto_fetcher import fetch_fear_greed
    return fetch_fear_greed(limit=2)


@st.cache_data(ttl=300)
def _load_funding_rates() -> dict:
    """Funding-Rates aller Binance-Futures-Perpetuals, EIN Bulk-Request für
    alle Top-50-Coins zusammen (siehe crypto_fetcher.fetch_funding_rates) —
    5-Min.-Cache wie die Top-50-Marktdaten."""
    from src.fetchers.crypto_fetcher import fetch_funding_rates
    return fetch_funding_rates()


def _crypto_btc_correlation(ticker: str, days: int = 90) -> float | None:
    """Pearson-Korrelation der täglichen Kursrenditen zu Bitcoin über die
    letzten `days` Tage — rein lokal aus bereits vorhandenen Kursdaten
    berechnet (_load_prices, gecacht), kein zusätzlicher Live-Abruf. None
    für BTC-USD selbst (Referenz) oder wenn nicht genug überlappende
    Handelstage vorliegen."""
    if ticker == "BTC-USD":
        return None
    end   = str(datetime.today().date())
    start = str(datetime.today().date() - timedelta(days=days))
    p_coin = _load_prices(ticker, start, end)
    p_btc  = _load_prices("BTC-USD", start, end)
    if len(p_coin) < 10 or len(p_btc) < 10:
        return None
    merged = pd.merge(
        p_coin[["date", "close"]], p_btc[["date", "close"]],
        on="date", suffixes=("_coin", "_btc"),
    )
    if len(merged) < 10:
        return None
    ret_coin = merged["close_coin"].pct_change().dropna()
    ret_btc  = merged["close_btc"].pct_change().dropna()
    if len(ret_coin) < 5:
        return None
    corr = ret_coin.corr(ret_btc)
    return float(corr) if pd.notna(corr) else None


def _correlation_badge(corr: float | None, is_btc: bool = False) -> str:
    if is_btc:
        return "🔗 Referenz"
    if corr is None:
        return "—"
    if corr >= 0.7:
        icon = "🔗"   # stark an BTC gekoppelt
    elif corr >= 0.3:
        icon = "🔸"   # moderat gekoppelt
    elif corr >= -0.3:
        icon = "⚪"   # kaum Zusammenhang
    else:
        icon = "🔀"   # läuft BTC entgegen
    return f"{icon} {corr:+.2f}"


def _funding_rate_badge(rate: float | None) -> str:
    """Funding-Rate-Badge — >0.02%/8h gilt grob als 'Markt überwiegend long
    positioniert' (🔴, potenziell überhitzt/Long-Squeeze-Risiko), <-0.02%
    'überwiegend short' (🟢, potenziell Short-Squeeze-Risiko). Die Richtung
    ist ein Crowding-Signal, KEINE direkte Kursprognose — 🔴 heißt nicht
    automatisch 'bearish', sondern 'in diese Richtung positioniert und damit
    anfälliger für eine Gegenbewegung'."""
    if rate is None:
        return "—"
    pct = rate * 100
    icon = "🔴" if pct > 0.02 else ("🟢" if pct < -0.02 else "⚪")
    return f"{icon} {pct:+.3f} %"


def _fng_badge(entry: dict) -> str:
    icon = {
        "Extreme Fear": "🟥", "Fear": "🟧", "Neutral": "⬜",
        "Greed": "🟩", "Extreme Greed": "🟢",
    }.get(entry["classification"], "⬜")
    return f"{icon} {entry['value']}/100 — {entry['classification']}"


# ─── Chart-Link (neuer Tab, per Ticker) ──────────────────────────────────────

def _chart_link(ticker: str) -> str:
    """Relativer Link, der die App neu lädt und im Kurschart-Tab (erster Tab,
    daher standardmäßig aktiv) den gewünschten Ticker vorauswählt."""
    return f"?ticker={quote(str(ticker))}"


def _apply_query_param_ticker() -> None:
    """Übernimmt einen per URL übergebenen Ticker (?ticker=XYZ) einmalig als
    Vorauswahl für den Kurschart-Tab. Viele Insider-Trade-Ticker (Congress/ARK)
    sind nicht Teil der kuratierten Watchlist — ohne Auto-Add würde der Link
    dort stillschweigend ins Leere laufen, weil die Ticker-Auswahl im Chart-Tab
    nur Watchlist-Ticker anbietet. Grobe Formatprüfung, da der Wert aus der
    URL kommt (keine ungeprüfte Übernahme beliebiger Strings in die DB)."""
    q_ticker = st.query_params.get("ticker")
    if not q_ticker or "ticker_sel" in st.session_state:
        return
    q_ticker = str(q_ticker).strip().upper()
    if not (1 <= len(q_ticker) <= 12 and all(c.isalnum() or c in ".-^=" for c in q_ticker)):
        return

    wl = _load_watchlist()
    if q_ticker not in set(wl["ticker"].tolist()):
        conn = get_connection()
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (ticker,name,asset_type,continent) VALUES (?,?,?,?)",
            (q_ticker, q_ticker, "stock", "Global"),
        )
        conn.commit()
        conn.close()
        _load_watchlist.clear()
        st.session_state["_chart_link_auto_added"] = q_ticker

    # Kontinent-Filter auf "Alle" zurücksetzen, damit der Ticker unabhängig vom
    # zuletzt gewählten Kontinent sichtbar ist (sonst würde die Ticker-Auswahl
    # im Chart-Tab den Wert ablehnen, falls er zu einem gefilterten Kontinent
    # nicht passt).
    st.session_state["continent_sel"] = f"{CONTINENT_EMOJI.get('Alle', '')} Alle"
    st.session_state["ticker_sel"] = q_ticker


# ─── Nearest trading-day helper ──────────────────────────────────────────────

def _nearest_price_date(target: str, sorted_dates: list[str]) -> str | None:
    """Map any date to the nearest preceding trading day with price data."""
    if not sorted_dates:
        return None
    if target in sorted_dates:
        return target
    idx = bisect.bisect_right(sorted_dates, target)
    if idx > 0:
        return sorted_dates[idx - 1]
    return sorted_dates[0]


# ─── Chart builder ───────────────────────────────────────────────────────────

def build_price_chart(
    prices:  pd.DataFrame,
    news:    pd.DataFrame,
    obs:     pd.DataFrame,
    ticker:  str,
    chart_type: str,
    insider: pd.DataFrame | None = None,
    elliott: list[dict] | None = None,
    current_wave: dict | None = None,
) -> go.Figure:
    fig = go.Figure()

    if prices.empty:
        fig.add_annotation(
            text="Keine Kursdaten verfügbar (Ticker evtl. ungültig oder Börse geschlossen)",
            showarrow=False, font=dict(size=18, color="#888"),
            xref="paper", yref="paper", x=0.5, y=0.5,
        )
        fig.update_layout(template="plotly_dark", height=540,
                          paper_bgcolor="rgba(0,0,0,0)")
        return fig

    sorted_dates = sorted(prices["date"].tolist())
    price_high   = prices.set_index("date")["high"].to_dict()
    price_low    = prices.set_index("date")["low"].to_dict()

    # ── Preis-Trace ──────────────────────────────────────────────────────────
    if chart_type == "Candlestick":
        fig.add_trace(go.Candlestick(
            x=prices["date"],
            open=prices["open"], high=prices["high"],
            low=prices["low"],   close=prices["close"],
            name="Kurs",
            increasing_line_color="#00C853",  decreasing_line_color="#D50000",
            increasing_fillcolor="#00C853",   decreasing_fillcolor="#D50000",
            whiskerwidth=0.3,
        ))
    else:
        fig.add_trace(go.Scatter(
            x=prices["date"], y=prices["close"],
            mode="lines", name="Schlusskurs",
            line=dict(color="#2979FF", width=2),
            fill="tozeroy",
            fillcolor="rgba(41,121,255,0.07)",
        ))

    # ── Volumen ──────────────────────────────────────────────────────────────
    vol_colors = [
        "#00C853" if c >= o else "#D50000"
        for c, o in zip(prices["close"], prices["open"])
    ]
    fig.add_trace(go.Bar(
        x=prices["date"], y=prices["volume"],
        name="Volumen", marker_color=vol_colors,
        opacity=0.22, yaxis="y2",
    ))

    # ── News-Marker ───────────────────────────────────────────────────────────
    if not news.empty:
        m_x, m_y, m_color, m_text, m_size, m_url = [], [], [], [], [], []

        for date, grp in news.groupby("date"):
            mapped = _nearest_price_date(date, sorted_dates)
            if mapped is None:
                continue
            y_high = price_high[mapped]

            grp_sorted = grp.sort_values("sentiment_score", ascending=False)
            parts, sentiments = [], []
            for _, row in grp_sorted.iterrows():
                icon    = SENTIMENT_ICON.get(row["sentiment_label"], "⚪")
                summary = str(row["summary"] or "")[:220]
                parts.append(
                    f"<b>{icon} {row['source']}</b>  ·  <i>{date}</i><br>"
                    f"<b>{row['title'][:80]}{'…' if len(row['title'])>80 else ''}</b><br>"
                    f"<span style='color:#ccc;font-size:11px'>{summary}</span><br>"
                    f"Sentiment: {row['sentiment_label'].upper()} "
                    f"({row['sentiment_score']:+.2f})"
                )
                sentiments.append(row["sentiment_label"])

            dominant  = Counter(sentiments).most_common(1)[0][0]
            top_url   = str(grp_sorted.iloc[0]["url"] or "")
            click_hint = "<br><br><i>🖱️ Klicken zum Öffnen des Artikels</i>" if top_url else ""
            hover    = (
                f"<b>📰 {len(grp)} Artikel  |  {date}</b>"
                + "<br>──────────────────────<br>"
                + "<br>──────────────────────<br>".join(parts)
                + click_hint
            )
            m_x.append(mapped)
            m_y.append(y_high * 1.018)
            m_color.append(SENTIMENT_COLOR[dominant])
            m_text.append(hover)
            m_size.append(min(14 + len(grp) * 2, 22))
            m_url.append(top_url)

        if m_x:
            fig.add_trace(go.Scatter(
                x=m_x, y=m_y,
                mode="markers",
                marker=dict(
                    symbol="triangle-down",
                    size=m_size,
                    color=m_color,
                    line=dict(width=1.5, color="white"),
                    opacity=0.95,
                ),
                text=m_text,
                customdata=m_url,
                hovertemplate="%{text}<extra></extra>",
                name="📰 News",
            ))

    # ── Insider-Trade-Marker (Congress + ARK/Cathie Wood + Trump-Familie) ────
    # Einheitliches gelbes Info-Zeichen (ℹ) statt Kauf/Verkauf-Einfärbung —
    # Kauf/Verkauf-Richtung steht weiterhin im Hover-Text pro Trade.
    if insider is not None and not insider.empty:
        i_x, i_y, i_text, i_size = [], [], [], []

        for date, grp in insider.groupby("trade_date"):
            mapped = _nearest_price_date(str(date), sorted_dates)
            if mapped is None:
                continue
            y_low = price_low[mapped]

            parts = []
            for _, row in grp.iterrows():
                icon   = TRADE_TYPE_ICON.get(row["trade_type"], "⚪")
                origin = SOURCE_LABEL.get(row["source"], row["source"])
                if row["shares_delta"] not in (None, 0) and not pd.isna(row["shares_delta"]):
                    detail = f"{abs(row['shares_delta']):,.0f} Aktien"
                else:
                    detail = _fmt_amount(row["amount_min"], row["amount_max"])
                parts.append(
                    f"{icon} <b>{row['trader']}</b>  ·  <i>{origin}</i><br>"
                    f"<span style='color:#ccc;font-size:11px'>"
                    f"{row['trade_type'].upper()} · {detail}</span>"
                )

            hover = (
                f"<b>ℹ️ {len(grp)} Insider-Trade(s)  |  {date}</b>"
                + "<br>──────────────────────<br>"
                + "<br>──────────────────────<br>".join(parts)
            )
            i_x.append(mapped)
            i_y.append(y_low * 0.991)
            i_text.append(hover)
            i_size.append(min(16 + len(grp) * 2, 24))

        if i_x:
            fig.add_trace(go.Scatter(
                x=i_x, y=i_y,
                mode="markers+text",
                marker=dict(
                    symbol="circle",
                    size=i_size,
                    color="#FFD600",
                    line=dict(width=1.5, color="white"),
                    opacity=0.95,
                ),
                text=["ℹ"] * len(i_x),
                textfont=dict(color="#1a1a1a", size=12, family="Arial Black"),
                textposition="middle center",
                customdata=i_text,
                hovertemplate="%{customdata}<extra></extra>",
                name="🏛️ Insider",
            ))

    # ── Beobachtungs-Marker ───────────────────────────────────────────────────
    if not obs.empty:
        o_x, o_y, o_color, o_text = [], [], [], []
        for _, row in obs.iterrows():
            mapped = _nearest_price_date(str(row["obs_date"]), sorted_dates)
            if mapped is None:
                continue
            y_low = price_low[mapped] * 0.982
            o_x.append(mapped)
            o_y.append(y_low)
            o_color.append(row["color"] or "#FFD600")
            o_text.append(
                f"<b>📌 Beobachtung  |  {row['obs_date']}</b><br>"
                f"{row['note']}<br>"
                f"<i>Erstellt: {str(row['created_at'])[:10]}</i>"
            )

        if o_x:
            fig.add_trace(go.Scatter(
                x=o_x, y=o_y,
                mode="markers",
                marker=dict(
                    symbol="star",
                    size=16,
                    color=o_color,
                    line=dict(width=1.5, color="white"),
                    opacity=1.0,
                ),
                text=o_text,
                hovertemplate="%{text}<extra></extra>",
                name="📌 Beobachtungen",
            ))

    # ── Elliott-Wellen-Overlay ───────────────────────────────────────────────
    # Farbe = Richtung (bullisch/bärisch), Linienstil = Wellentyp (solid=Impuls,
    # dash=Korrektur) — so lassen sich Trendrichtung und Struktur unabhängig
    # voneinander ablesen.
    if elliott:
        for n, pattern in enumerate(elliott):
            pts   = pattern["points"]
            color = WAVE_DIRECTION_COLOR.get(pattern["direction"], "#6FD9A0")
            dash  = "solid" if pattern["type"] == "impulse" else "dash"
            dir_label = "bullisch ↑" if pattern["direction"] == "bullish" else "bärisch ↓"
            fig.add_trace(go.Scatter(
                x=[pt["date"] for pt in pts],
                y=[pt["price"] for pt in pts],
                mode="lines+markers+text",
                text=[pt["label"] for pt in pts],
                textposition="top center",
                textfont=dict(size=13, color=color),
                line=dict(color=color, width=1.5, dash=dash),
                marker=dict(size=6, color=color),
                name=f"🌊 {'Impuls' if pattern['type']=='impulse' else 'Korrektur'} "
                     f"({dir_label}, {pattern['confidence']*100:.0f}%)",
                hovertemplate=(
                    f"<b>Welle %{{text}}</b><br>%{{x}} · %{{y:.2f}}<br>"
                    f"Konfidenz: {pattern['confidence']*100:.0f}%<extra></extra>"
                ),
                showlegend=True,
                legendgroup=f"elliott{n}",
            ))

    # ── Aktuell laufende Welle + Ausblick (noch unbestätigt) ──────────────────
    # Historie bleibt kräftig/solid; alles, was in die Zukunft zeigt (Ziellinie
    # + Zielzone), wird bewusst blasser/transparenter gerendert, damit auf den
    # ersten Blick klar ist: das ist eine Prognose, keine Kursdaten.
    if current_wave:
        from src.analytics.elliott_wave import STATE_LABELS
        cw = current_wave
        cw_color = WAVE_DIRECTION_COLOR.get(cw["direction"], "#6FD9A0")
        cw_label = STATE_LABELS.get(cw["state"], cw["state"])

        # Bereits bestätigte Pivots der laufenden Struktur (z.B. 0/1/2 bei
        # "Welle 3 in Bildung") — dieselbe Darstellung wie abgeschlossene
        # Elliott-Muster, damit auf einen Blick klar ist, worauf sich die
        # Prognose stützt.
        cw_points = cw.get("points") or []
        if cw_points:
            fig.add_trace(go.Scatter(
                x=[pt["date"] for pt in cw_points],
                y=[pt["price"] for pt in cw_points],
                mode="lines+markers+text",
                text=[pt["label"] for pt in cw_points],
                textposition="top center",
                textfont=dict(size=13, color=cw_color),
                line=dict(color=cw_color, width=1.5),
                marker=dict(size=6, color=cw_color),
                name=f"🌊 Bestätigt: {cw_label}",
                hovertemplate=(
                    f"<b>Welle %{{text}}</b><br>%{{x}} · %{{y:.2f}}<extra></extra>"
                ),
                showlegend=True,
                legendgroup="current_wave",
            ))

        # Laufendes Wellenbein: letzter bestätigter Pivot -> aktueller Kurs
        fig.add_trace(go.Scatter(
            x=[cw["last_pivot_date"], cw["current_date"]],
            y=[cw["last_pivot_price"], cw["current_price"]],
            mode="lines+markers",
            line=dict(color=cw_color, width=2, dash="dot"),
            marker=dict(size=7, color=cw_color, symbol="circle"),
            name=f"🎯 {cw_label} ({cw['confidence']*100:.0f}%)",
            hovertemplate=(
                f"<b>{cw_label}</b><br>{cw['expectation_text']}<extra></extra>"
            ),
            showlegend=True,
            legendgroup="current_wave",
        ))

        # Trennlinie "Heute" — markiert den Übergang von Kursdaten zur Prognose.
        # add_shape/add_annotation statt add_vline: add_vline berechnet die
        # Default-Annotationsposition intern über einen Mittelwert der x-Achse,
        # was bei String-Datumsachsen (kein numerischer/datetime dtype) crasht.
        fig.add_shape(
            type="line", xref="x", yref="paper",
            x0=cw["current_date"], x1=cw["current_date"], y0=0, y1=1,
            line=dict(color="rgba(255,255,255,0.25)", width=1, dash="dot"),
        )
        fig.add_annotation(
            x=cw["current_date"], y=1, xref="x", yref="paper",
            text="Heute", showarrow=False, yanchor="bottom",
            font=dict(size=10, color="rgba(255,255,255,0.5)"),
        )

        # Projizierter Ausblick: Ziellinie + Zielzone rechts vom aktuellen Kurs,
        # abgeleitet aus der Elliott-Wellenberechnung (Fibonacci-Ziel des
        # laufenden Wellenbeins) — blasse/transparente Darstellung, da Prognose.
        if cw["target_low"] is not None:
            d0     = datetime.strptime(sorted_dates[0],  "%Y-%m-%d")
            d_last = datetime.strptime(sorted_dates[-1], "%Y-%m-%d")
            span_days = max((d_last - d0).days, 1)
            extend    = max(5, int(span_days * 0.06))
            future_date = (
                datetime.strptime(cw["current_date"], "%Y-%m-%d") + timedelta(days=extend)
            ).strftime("%Y-%m-%d")
            target_mid = (cw["target_low"] + cw["target_high"]) / 2
            zone_fill  = _hex_to_rgba(cw_color, 0.12)

            # Ziellinie (Ausblick): aktueller Kurs -> Fibonacci-Zielmitte
            fig.add_trace(go.Scatter(
                x=[cw["current_date"], future_date],
                y=[cw["current_price"], target_mid],
                mode="lines",
                line=dict(color=_hex_to_rgba(cw_color, 0.55), width=1.5, dash="dashdot"),
                name="🔮 Ausblick (Elliott-Projektion)",
                hovertemplate=(
                    f"<b>Prognose</b><br>Zielmitte ≈ {target_mid:.2f}<extra></extra>"
                ),
                showlegend=True,
                legendgroup="current_wave",
            ))

            fig.add_trace(go.Scatter(
                x=[cw["current_date"], future_date, future_date, cw["current_date"]],
                y=[cw["target_low"], cw["target_low"], cw["target_high"], cw["target_high"]],
                mode="lines",
                line=dict(width=0),
                fill="toself",
                fillcolor=zone_fill,
                name=f"🎯 Zielzone {cw['target_low']:.2f}–{cw['target_high']:.2f}",
                hovertemplate=(
                    f"<b>Projizierte Zielzone</b><br>{cw['target_low']:.2f} – {cw['target_high']:.2f}"
                    "<extra></extra>"
                ),
                showlegend=True,
                legendgroup="current_wave",
            ))

            # Weitere Wellenbeine derselben Struktur (z.B. 4+5 nach Welle 3,
            # oder A+B+C nach Welle 5) — zeitlich weiter verkettet, mit
            # abnehmender Deckkraft je weiter in der Zukunft (= spekulativer,
            # da auf einer noch unbestätigten Zielzone aufbauend statt auf
            # echten Kursdaten).
            prev_date, prev_price = future_date, target_mid
            for step, pw in enumerate(cw.get("projected_waves") or [], start=2):
                pw_future_date = (
                    datetime.strptime(cw["current_date"], "%Y-%m-%d")
                    + timedelta(days=extend * step)
                ).strftime("%Y-%m-%d")
                pw_mid    = (pw["target_low"] + pw["target_high"]) / 2
                line_fade = max(0.15, 0.55 - 0.15 * (step - 1))
                zone_fade = max(0.03, 0.12 - 0.03 * (step - 1))

                fig.add_trace(go.Scatter(
                    x=[prev_date, pw_future_date],
                    y=[prev_price, pw_mid],
                    mode="lines",
                    line=dict(color=_hex_to_rgba(cw_color, line_fade), width=1.2, dash="dashdot"),
                    name=f"🔮 Welle {pw['label']} (Projektion)",
                    hovertemplate=(
                        f"<b>Welle {pw['label']}</b><br>{pw['note']}<br>"
                        f"Zielzone ≈ {pw['target_low']:.2f}–{pw['target_high']:.2f}<extra></extra>"
                    ),
                    showlegend=True,
                    legendgroup="current_wave",
                ))
                fig.add_trace(go.Scatter(
                    x=[prev_date, pw_future_date, pw_future_date, prev_date],
                    y=[pw["target_low"], pw["target_low"], pw["target_high"], pw["target_high"]],
                    mode="lines",
                    line=dict(width=0),
                    fill="toself",
                    fillcolor=_hex_to_rgba(cw_color, zone_fade),
                    name=f"🎯 Welle {pw['label']} Zielzone {pw['target_low']:.2f}–{pw['target_high']:.2f}",
                    hovertemplate=(
                        f"<b>Welle {pw['label']} — Projizierte Zielzone</b><br>"
                        f"{pw['target_low']:.2f} – {pw['target_high']:.2f}<extra></extra>"
                    ),
                    showlegend=True,
                    legendgroup="current_wave",
                ))
                fig.add_annotation(
                    x=pw_future_date, y=pw["target_high"], xref="x", yref="y",
                    text=pw["label"], showarrow=False, yanchor="bottom",
                    font=dict(size=11, color=_hex_to_rgba(cw_color, min(1.0, line_fade + 0.3))),
                )
                prev_date, prev_price = pw_future_date, pw_mid

    # ── Layout ────────────────────────────────────────────────────────────────
    fig.update_layout(
        title=dict(text=f"<b>{ticker}</b>", font=dict(size=20, color="#eee")),
        height=560,
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(10,12,20,1)",
        xaxis=dict(
            showgrid=True,
            gridcolor="rgba(255,255,255,0.05)",
            rangeslider_visible=False,
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor="rgba(255,255,255,0.06)",
            side="right",
            title="Kurs",
        ),
        yaxis2=dict(
            overlaying="y", side="left",
            showgrid=False, showticklabels=False,
            range=[0, prices["volume"].max() * 7],
        ),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="left", x=0,
            bgcolor="rgba(0,0,0,0.4)",
            font=dict(size=11),
        ),
        hovermode="closest",
        margin=dict(l=10, r=70, t=65, b=20),
        # list(fig.layout.annotations) statt [...]: sonst überschreibt dieser
        # Call die zuvor per add_annotation gesetzte "Heute"-Markierung, da
        # update_layout(annotations=[...]) die komplette Liste ersetzt statt
        # zu ergänzen.
        annotations=list(fig.layout.annotations) + [dict(
            text=(
                "▼ <span style='color:#00C853'>Pos. News</span>  "
                "▼ <span style='color:#D50000'>Neg. News</span>  "
                "▼ <span style='color:#2979FF'>Neutr. News</span>  "
                "◆ <span style='color:#00C853'>Insider-Kauf</span>  "
                "◆ <span style='color:#D50000'>Insider-Verkauf</span>  "
                "★ Beobachtung"
                + (
                    "&nbsp;&nbsp;·&nbsp;&nbsp;"
                    f"— <span style='color:{WAVE_DIRECTION_COLOR['bullish']}'>bullische Welle</span>  "
                    f"— <span style='color:{WAVE_DIRECTION_COLOR['bearish']}'>bärische Welle</span>  "
                    "(solid=Impuls, gestrichelt=Korrektur, gepunktet=Ausblick)"
                    if (elliott or current_wave) else ""
                )
            ),
            xref="paper", yref="paper", x=0.01, y=1.055,
            showarrow=False, font=dict(size=11), align="left",
        )],
    )
    return fig


# ─── Chart-Rendering mit Klick-auf-News-Flag ────────────────────────────────

def render_chart_with_news_link(fig: go.Figure, height: int = 560) -> None:
    """Rendert den Plotly-Chart und öffnet beim Klick auf einen News-Marker
    den zugehörigen Original-Artikel in einem neuen Tab."""
    chart_html = fig.to_html(include_plotlyjs="cdn", full_html=False, div_id="price-chart-div")
    click_handler = """
    <script>
    (function () {
        var gd = document.getElementById("price-chart-div");
        if (!gd) return;
        gd.on("plotly_click", function (evt) {
            var pt = evt.points && evt.points[0];
            if (!pt) return;
            if (pt.data.name === "📰 News" && pt.customdata) {
                window.open(pt.customdata, "_blank");
            }
        });
    })();
    </script>
    """
    components.html(chart_html + click_handler, height=height + 40, scrolling=False)


# ─── Sidebar ─────────────────────────────────────────────────────────────────

def render_sidebar() -> tuple[str, str, str]:
    st.sidebar.markdown(
        """
        <div class="fi-sidebar-brand">
            <h2>📈 Finance Intel</h2>
            <p>Markets · News · Insider Trades</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Zeitraum
    st.sidebar.subheader("Zeitraum")
    today     = datetime.today().date()
    default_s = today - timedelta(days=180)
    c1, c2    = st.sidebar.columns(2)
    start = c1.date_input("Von", value=default_s, key="dt_s")
    end   = c2.date_input("Bis", value=today,      key="dt_e")

    # Kontinent-Filter
    st.sidebar.subheader("Kontinent")
    continent_options = [f"{CONTINENT_EMOJI.get(c, '')} {c}" for c in ALL_CONTINENTS]
    selected_label = st.sidebar.selectbox(
        "Kontinent wählen", continent_options,
        index=0, key="continent_sel",
    )
    selected_continent = selected_label.split(" ", 1)[1].strip()

    # Watchlist verwalten
    st.sidebar.subheader("Watchlist")
    with st.sidebar.expander("+ Ticker hinzufügen"):
        new_t = st.text_input("Ticker (z.B. AAPL, WES.AX)", key="add_t").upper().strip()
        new_n = st.text_input("Name",                         key="add_n")
        new_c = st.selectbox("Kontinent", ALL_CONTINENTS[1:], key="add_c")
        new_a = st.selectbox("Typ", ["stock","etf","index","crypto","commodity"], key="add_a")
        if st.button("Hinzufügen", key="btn_add"):
            if new_t:
                conn = get_connection()
                conn.execute(
                    "INSERT OR IGNORE INTO watchlist (ticker,name,asset_type,continent) VALUES (?,?,?,?)",
                    (new_t, new_n or new_t, new_a, new_c),
                )
                conn.commit()
                conn.close()
                st.cache_data.clear()
                st.rerun()

    # Daten abrufen
    st.sidebar.divider()
    st.sidebar.subheader("Daten abrufen")
    if st.sidebar.button("📰  News laden", use_container_width=True):
        _run_news_fetch()
    if st.sidebar.button("📊  Kurse laden", use_container_width=True):
        _run_price_fetch()
    if st.sidebar.button("🔄  Watchlist sync", use_container_width=True,
                          help="Neue Kontinent-Aktien hinzufügen (löscht nichts)"):
        from src.watchlist_updater import update_watchlist
        added = update_watchlist()
        st.cache_data.clear()
        st.sidebar.success(f"{added} neue Ticker hinzugefügt" if added else "Alles aktuell")
    if st.sidebar.button("🏛️  Insider laden", use_container_width=True,
                          help="Congress-Trades + ARK Holdings aktualisieren"):
        _run_insider_fetch()

    return str(start), str(end), selected_continent


# ─── Data fetch helpers (run inside sidebar status widget) ───────────────────

def _run_news_fetch():
    from src.processors.text_processor import ensure_nltk_data, process_item
    from src.fetchers.news_fetcher import fetch_feed
    import time as _t

    ensure_nltk_data()
    conn    = get_connection()
    sources = conn.execute("SELECT * FROM sources WHERE active=1").fetchall()
    conn.close()

    with st.sidebar.status("News laden …", expanded=True) as status:
        total = 0
        for src in sources:
            st.write(f"⏳ {src['name']}")
            items = fetch_feed(src["feed_url"])
            new_c = 0
            conn  = get_connection()
            for item in items:
                if not item["url"]:
                    continue
                if conn.execute("SELECT id FROM news_items WHERE url=?", (item["url"],)).fetchone():
                    continue
                proc = process_item(item["title"], item["raw_content"], src["language"])
                cur  = conn.execute(
                    """INSERT OR IGNORE INTO news_items
                           (source_id,title,url,published_at,raw_content,
                            summary,sentiment_label,sentiment_score)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (src["id"], item["title"], item["url"], item["published_at"],
                     item["raw_content"], proc["summary"],
                     proc["sentiment_label"], proc["sentiment_score"]),
                )
                if cur.rowcount > 0:
                    nid = cur.lastrowid
                    for ticker, cname in proc["tickers"]:
                        conn.execute(
                            "INSERT INTO news_tickers (news_id,ticker,company_name) VALUES (?,?,?)",
                            (nid, ticker, cname),
                        )
                    new_c += 1
            conn.commit()
            conn.close()
            total += new_c
            _t.sleep(0.3)
        status.update(label=f"✅ {total} neue Artikel geladen", state="complete")
    st.cache_data.clear()


def _fetch_and_store_prices(ticker: str) -> int:
    """Fetch + store prices for one ticker. Returns number of new rows."""
    from src.database.db import store_prices
    from src.fetchers.stock_fetcher import fetch_prices
    df = fetch_prices(ticker, days=400)
    if df.empty:
        return 0
    conn = get_connection()
    inserted = store_prices(conn, df)
    conn.commit()
    conn.close()
    return inserted


def _prices_need_update(ticker: str) -> bool:
    """True if ticker has no prices or latest date is before today."""
    from datetime import date
    conn   = get_connection()
    latest = conn.execute(
        "SELECT MAX(date) FROM stock_prices WHERE ticker=?", (ticker,)
    ).fetchone()[0]
    conn.close()
    if latest is None:
        return True
    return latest < date.today().strftime("%Y-%m-%d")


def _run_price_fetch():
    """Refresh prices only for tickers that already have data (not all 131)."""
    from src.database.db import store_prices
    from src.database.price_history import needs_full_reload
    from src.fetchers.stock_fetcher import fetch_prices_batch, UPDATE_DAYS

    conn = get_connection()
    # Only refresh tickers already in price DB — not the full 131-ticker universe
    tickers_with_data = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT ticker FROM stock_prices ORDER BY ticker"
        ).fetchall()
    ]
    crypto = {r[0] for r in conn.execute("SELECT ticker FROM watchlist WHERE asset_type='crypto'").fetchall()}
    conn.close()

    BATCH_SIZE = 40
    with st.sidebar.status(f"Kurse für {len(tickers_with_data)} Ticker aktualisieren …",
                           expanded=True) as status:
        for i in range(0, len(tickers_with_data), BATCH_SIZE):
            chunk = tickers_with_data[i:i + BATCH_SIZE]
            st.write(f"⏳ Batch {i // BATCH_SIZE + 1}: {', '.join(chunk)}")
            results = fetch_prices_batch(chunk, days=UPDATE_DAYS)
            conn = get_connection()
            for t in chunk:
                df = results.get(t)
                if df is None or df.empty:
                    continue
                # Geänderte Kursbasis (Split) nicht überschreiben — sonst wäre
                # sie für den Abgleich in main.fetch_all_prices() unsichtbar,
                # der dann die komplette Historie ersetzt.
                if t not in crypto and needs_full_reload(conn, t, df):
                    st.write(f"⚠️ {t}: Kursbasis geändert (Split?) — wird beim nächsten "
                             "`python main.py prices` komplett neu geladen")
                    continue
                store_prices(conn, df, overwrite=t not in crypto)
            conn.commit()
            conn.close()
        status.update(label="✅ Kurse aktualisiert", state="complete")
    st.cache_data.clear()


# ─── Tab 1: Kurschart ────────────────────────────────────────────────────────

def tab_chart(start: str, end: str, continent: str):
    wl = _load_watchlist()
    if wl.empty:
        st.info("Watchlist leer — bitte Ticker hinzufügen oder 'Watchlist sync' klicken.")
        return

    # Kontinent-Filter
    if continent != "Alle":
        wl_filtered = wl[wl["continent"] == continent]
    else:
        wl_filtered = wl

    if wl_filtered.empty:
        st.info(f"Keine Ticker für Kontinent '{continent}' in der Watchlist.")
        return

    # Header-Zeile: Ticker-Auswahl + Chart-Optionen
    col1, col2, col3, col4, col5 = st.columns([3, 1, 1, 1, 1])
    ticker = col1.selectbox(
        "Ticker",
        wl_filtered["ticker"].tolist(),
        format_func=lambda t: (
            f"{t}  —  "
            f"{wl_filtered[wl_filtered['ticker']==t]['name'].values[0]}  "
            f"[{wl_filtered[wl_filtered['ticker']==t]['asset_type'].values[0].upper()}]"
        ),
        key="ticker_sel",
    )
    chart_type    = col2.selectbox("Charttyp",            ["Candlestick", "Linie"], key="ct")
    show_news     = col3.checkbox("News einblenden",      value=True,               key="sn")
    show_insider  = col4.checkbox("Insider einblenden",   value=True,               key="si")
    show_elliott  = col5.checkbox("🌊 Elliott-Wellen",     value=True,               key="sew")

    # ── Zeitraum-Schnellauswahl (überschreibt die Sidebar-Daten nur für diesen Chart) ──
    RANGE_PRESETS = {
        "1T": 1, "1W": 7, "1M": 30, "3M": 90,
        "6M": 182, "1J": 365, "3J": 1095,
    }
    range_choice = st.segmented_control(
        "Zeitraum",
        options=list(RANGE_PRESETS.keys()) + ["Benutzerdefiniert"],
        default="6M",
        key="chart_range_sel",
    )
    if range_choice and range_choice != "Benutzerdefiniert":
        chart_end   = datetime.today().date()
        chart_start = chart_end - timedelta(days=RANGE_PRESETS[range_choice])
        start, end  = str(chart_start), str(chart_end)
    # "Benutzerdefiniert" → fällt zurück auf den Zeitraum aus der Sidebar (Von/Bis)

    elliott_sensitivity = 5.0
    if show_elliott:
        with st.expander("🌊 Elliott-Wellen-Einstellungen", expanded=False):
            elliott_sensitivity = st.slider(
                "Zigzag-Empfindlichkeit (%)", min_value=1.0, max_value=20.0,
                value=5.0, step=0.5, key="ew_thresh",
                help="Kleinere Werte erkennen mehr (kleinere) Schwenks, aber auch mehr "
                     "Rauschen. Größere Werte filtern auf die dominanten Trendbewegungen.",
            )
            st.caption(
                "Regelbasierte Struktur-Erkennung (kein ML): sucht 5-Wellen-Impulse "
                "und ABC-Korrekturen, die die klassischen Elliott-Regeln erfüllen "
                "(Welle 2 nicht über Start von Welle 1, Welle 3 nie die kürzeste, "
                "Welle 4 ohne Überschneidung mit Welle 1). Konfidenz = Nähe der "
                "Wellenverhältnisse zu Fibonacci-Levels."
            )

    # Auto-fetch: Kurse direkt laden wenn fehlend oder veraltet — kein Button nötig
    if _prices_need_update(ticker):
        with st.spinner(f"Lade Kurse für **{ticker}** …"):
            _fetch_and_store_prices(ticker)
            _load_prices.clear()   # nur diesen Cache-Eintrag invalidieren

    prices  = _load_prices(ticker, start, end)
    news    = _load_news_for_ticker(ticker, start, end) if show_news else pd.DataFrame()
    obs     = _load_observations(ticker)
    insider = _load_insider_trades_for_ticker(ticker, start, end) if show_insider else pd.DataFrame()

    # KPI-Zeile
    if not prices.empty:
        last  = prices["close"].iloc[-1]
        prev  = prices["close"].iloc[-2] if len(prices) > 1 else last
        delta = (last - prev) / prev * 100
        k1, k2, k3, k4, k5, k6 = st.columns(6)
        k1.metric("Letzter Kurs",     f"{last:.2f}",                f"{delta:+.2f} %")
        k2.metric("52W Hoch",         f"{prices['high'].max():.2f}")
        k3.metric("52W Tief",         f"{prices['low'].min():.2f}")
        k4.metric("Ø Volumen",
                  f"{prices['volume'].mean()/1e6:.1f}M"
                  if prices["volume"].mean() > 1e6
                  else f"{int(prices['volume'].mean()):,}")
        k5.metric("News im Zeitraum", f"{len(news)}")
        k6.metric("Insider-Trades",   f"{len(insider)}")

    # Fundamentaldaten (Einzelaktien — Indizes/ETFs/Krypto/Futures haben i.d.R. keine)
    fund = _load_fundamentals(ticker)
    if fund and fund.get("market_cap"):
        with st.expander("📊 Fundamentaldaten", expanded=False):
            cur = fund.get("currency", "")
            f1, f2, f3, f4, f5 = st.columns(5)

            mcap = fund.get("market_cap")
            mcap_str = f"{mcap/1e9:.1f} Mrd. {cur}" if mcap >= 1e9 else f"{mcap/1e6:.0f} Mio. {cur}"
            f1.metric("Marktkapitalisierung", mcap_str)

            pe_t = fund.get("pe_trailing")
            f2.metric("KGV (trailing)", f"{pe_t:.1f}" if pe_t else "—")

            pe_f = fund.get("pe_forward")
            f3.metric("KGV (forward)", f"{pe_f:.1f}" if pe_f else "—")

            div = fund.get("dividend_yield")
            f4.metric("Dividendenrendite", f"{div:.2f} %" if div is not None else "—")

            tgt = fund.get("target_mean")
            f5.metric("Analysten-Kursziel", f"{tgt:.2f} {cur}" if tgt else "—")

            rec = (fund.get("recommendation") or "—").upper()
            st.caption(
                f"Sektor: {fund.get('sector') or '—'}  ·  "
                f"Branche: {fund.get('industry') or '—'}  ·  "
                f"Analysten-Empfehlung: {rec}"
            )

            asset_type = wl_filtered[wl_filtered["ticker"] == ticker]["asset_type"].values[0]
            if asset_type == "stock":
                st.divider()
                universe = tuple(sorted(wl_filtered[wl_filtered["asset_type"] == "stock"]["ticker"]))
                scores = _load_fundamental_scores(universe)
                row = scores[scores["ticker"] == ticker]
                if row.empty or not bool(row.iloc[0]["data_ok"]):
                    st.caption(
                        "📐 Fundamentalscore (Piotroski/Gross-Profitability/Fama-French) noch nicht "
                        "im Cache oder keine auswertbare Bilanzhistorie für diesen Ticker."
                    )
                    _fundamentals_refresh_button([ticker], key="chart_fund_refresh")
                else:
                    r = row.iloc[0]
                    if pd.notna(r["fundamental_score"]):
                        st.markdown(
                            f"📐 **Fundamentalscore:** {_fundamental_badge(r['fundamental_score'])}  "
                            f"·  Piotroski-F-Score **{int(r['f_score'])}/9**  "
                            f"·  Quality (Gross Profitability) Perzentil **{r['quality_pctl']:.0f}**  "
                            f"·  Fama-French-Proxy Perzentil **{r['ff_composite']:.0f}**"
                        )
                    else:
                        st.caption(
                            f"Piotroski-F-Score {int(r['f_score'])}/9 — Perzentile brauchen "
                            f"mindestens 2 Aktien im Vergleichs-Universum "
                            f"({int(scores.attrs.get('n_universe', 0))} aktuell verfügbar)."
                        )
                    st.caption(
                        f"Perzentile relativ zu {int(scores.attrs.get('n_universe', 0))} Aktien der "
                        f"aktuell gefilterten Watchlist (Kontinent-Filter) — je höher, desto besser "
                        f"im Vergleich zu diesem Universum, nicht zum Gesamtmarkt."
                    )
                    _fundamentals_refresh_button([ticker], key="chart_fund_refresh")

    # Elliott-Wellen-Erkennung
    elliott_patterns = None
    current_wave = None
    if show_elliott and not prices.empty:
        from src.analytics.elliott_wave import (
            detect_elliott_waves, analyze_current_wave, STATE_LABELS, STRUCTURE_LABELS,
        )
        # Keine Protokollierung hier: der Chart-Zeitraum ist frei wählbar und
        # würde andere Pivots liefern als der Standard-Scan (siehe
        # wave_tracker.is_canonical_scan).
        elliott_patterns = detect_elliott_waves(prices, threshold_pct=elliott_sensitivity)
        current_wave = analyze_current_wave(prices, threshold_pct=elliott_sensitivity)
        if not elliott_patterns and not current_wave:
            st.info("🌊 Keine gültige Elliott-Wellen-Struktur im gewählten Zeitraum gefunden "
                     "— Empfindlichkeit anpassen oder anderen Zeitraum wählen.")
        elif current_wave:
            cw        = current_wave
            richtung  = "🟢 Bullish" if cw["direction"] == "bullish" else "🔴 Bearish"
            zielzone  = (f"{cw['target_low']:.2f} – {cw['target_high']:.2f}"
                         if cw["target_low"] is not None else "—")
            struktur  = STRUCTURE_LABELS.get(cw.get("structure_type"), "")
            st.markdown(
                f"##### 🌊 Aktueller Wellen-Zustand: {STATE_LABELS.get(cw['state'], cw['state'])} "
                f"· {struktur} · {richtung} · Konfidenz {cw['confidence']*100:.0f}%"
            )
            st.caption(
                f"🎯 {cw['expectation_text'].split(' Danach:')[0].split(' Danach (')[0]}  \n"
                f"Nächste erwartete Zielzone: **{zielzone}** · "
                f"Letzter bestätigter Pivot: {cw['last_pivot_price']:.2f} ({cw['last_pivot_date']})"
            )
            if cw.get("projected_waves"):
                st.caption("**Weitere erwartete Wellenbeine derselben Struktur:**")
                for pw in cw["projected_waves"]:
                    st.caption(
                        f"→ Welle {pw['label']}: {pw['target_low']:.2f} – {pw['target_high']:.2f} "
                        f"— {pw['note']}"
                    )

    # Chart
    fig = build_price_chart(prices, news, obs, ticker, chart_type, insider,
                             elliott_patterns, current_wave)
    render_chart_with_news_link(fig)
    if not news.empty:
        st.caption("🖱️ Auf ein 📰-Dreieck klicken öffnet den zugehörigen Artikel in einem neuen Tab.")

    # ── Beobachtungspunkte verwalten ──────────────────────────────────────────
    st.subheader("📌 Beobachtungspunkte")
    with st.expander("Neuen Beobachtungspunkt hinzufügen", expanded=False):
        bc1, bc2, bc3, bc4 = st.columns([1, 3, 1, 1])
        obs_date  = bc1.date_input("Datum", value=datetime.today().date(), key="obs_d")
        obs_note  = bc2.text_input("Notiz / Beobachtung", key="obs_n",
                                    placeholder="z.B. Earnings Beat, Fed-Entscheidung, Kaufsignal …")
        obs_color = bc3.color_picker("Farbe", value="#FFD600", key="obs_c")
        bc4.write("")
        bc4.write("")
        if bc4.button("💾 Speichern", key="obs_save"):
            if obs_note.strip():
                conn = get_connection()
                conn.execute(
                    "INSERT INTO chart_observations (ticker,obs_date,note,color) VALUES (?,?,?,?)",
                    (ticker, str(obs_date), obs_note.strip(), obs_color),
                )
                conn.commit()
                conn.close()
                st.cache_data.clear()
                st.rerun()
            else:
                st.warning("Bitte eine Notiz eingeben.")

    # Bestehende Beobachtungen
    if not obs.empty:
        st.markdown("**Bestehende Beobachtungen:**")
        for _, row in obs.iterrows():
            c_dot, c_date, c_note, c_del = st.columns([0.3, 1, 5, 1])
            c_dot.markdown(
                f"<div style='width:18px;height:18px;border-radius:50%;"
                f"background:{row['color']};margin-top:4px'></div>",
                unsafe_allow_html=True,
            )
            c_date.write(str(row["obs_date"]))
            c_note.write(row["note"])
            if c_del.button("🗑️", key=f"del_obs_{row['id']}"):
                conn = get_connection()
                conn.execute("DELETE FROM chart_observations WHERE id=?", (row["id"],))
                conn.commit()
                conn.close()
                st.cache_data.clear()
                st.rerun()

    # ── News-Liste unter dem Chart ────────────────────────────────────────────
    if not news.empty:
        st.subheader(f"📰 News zu **{ticker}**")
        for _, row in news.iterrows():
            icon  = SENTIMENT_ICON.get(row["sentiment_label"], "⚪")
            label = f"{icon}  {row['date']}  |  {row['source']}  —  {row['title'][:90]}"
            with st.expander(label):
                c1, c2 = st.columns([4, 1])
                c1.write(row["summary"] or "—")
                c2.metric("Sentiment", row["sentiment_label"].capitalize(),
                           delta=f"{row['sentiment_score']:+.2f}")
                if row["url"]:
                    c1.markdown(f"[→ Originalartikel]({row['url']})")
    elif show_news:
        st.info("Keine News für diesen Ticker im Zeitraum. Bitte 'News laden' klicken.")


# ─── Tab 2: News-Feed ────────────────────────────────────────────────────────

def tab_newsfeed(start: str, end: str, continent: str):
    wl = _load_watchlist()
    if wl.empty:
        st.info("Watchlist leer.")
        return

    if continent != "Alle":
        wl = wl[wl["continent"] == continent]

    c1, c2 = st.columns([3, 1])
    selected = c1.multiselect(
        "Ticker filtern",
        wl["ticker"].tolist(),
        default=wl["ticker"].tolist()[:6],
    )
    sentiment_filter = c2.multiselect(
        "Sentiment",
        ["positive", "neutral", "negative"],
        default=["positive", "neutral", "negative"],
    )

    if not selected:
        st.info("Mindestens einen Ticker auswählen.")
        return

    frames = [_load_news_for_ticker(t, start, end) for t in selected]
    all_news = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["id"])
    all_news = all_news[all_news["sentiment_label"].isin(sentiment_filter)]
    all_news = all_news.sort_values("published_at", ascending=False)

    if all_news.empty:
        st.info("Keine passenden News gefunden.")
        return

    st.markdown(f"**{len(all_news)} Artikel** gefunden")
    for _, row in all_news.iterrows():
        icon  = SENTIMENT_ICON.get(row["sentiment_label"], "⚪")
        with st.expander(f"{icon}  **{row['date']}**  |  {row['source']}  —  {row['title'][:95]}"):
            c1, c2 = st.columns([4, 1])
            c1.write(row["summary"] or "—")
            c2.metric("Sentiment", row["sentiment_label"].capitalize(),
                       delta=f"{row['sentiment_score']:+.2f}")
            if row["url"]:
                c1.markdown(f"[→ Artikel]({row['url']})")


# ─── Tab 3: Marktüberblick ───────────────────────────────────────────────────

def tab_overview(continent: str):
    st.subheader("🌍 Marktüberblick")
    all_news = _load_all_news(limit=500)
    wl       = _load_watchlist()

    if all_news.empty:
        st.info("Noch keine Daten. Bitte 'News laden' in der Sidebar klicken.")
        return

    # KPIs
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Gesamt Artikel",  len(all_news))
    k2.metric("Positiv",  int((all_news["sentiment_label"] == "positive").sum()))
    k3.metric("Negativ",  int((all_news["sentiment_label"] == "negative").sum()))
    k4.metric("Neutral",  int((all_news["sentiment_label"] == "neutral").sum()))

    # Sentiment-Pie + Timeline
    c1, c2 = st.columns(2)
    with c1:
        sc = all_news["sentiment_label"].value_counts()
        fig_pie = go.Figure(go.Pie(
            labels=sc.index, values=sc.values,
            marker_colors=[SENTIMENT_COLOR.get(l, "#aaa") for l in sc.index],
            hole=0.42, textinfo="label+percent",
        ))
        fig_pie.update_layout(title="Sentiment-Verteilung", template="plotly_dark",
                               height=300, paper_bgcolor="rgba(0,0,0,0)",
                               margin=dict(l=10,r=10,t=40,b=10))
        st.plotly_chart(fig_pie, use_container_width=True)

    with c2:
        nbd = (
            all_news.groupby(["date", "sentiment_label"]).size()
            .unstack(fill_value=0).reset_index()
        )
        fig_bar = go.Figure()
        for label in ("positive", "neutral", "negative"):
            if label in nbd.columns:
                fig_bar.add_trace(go.Bar(
                    x=nbd["date"], y=nbd[label],
                    name=label.capitalize(),
                    marker_color=SENTIMENT_COLOR[label],
                ))
        fig_bar.update_layout(barmode="stack", title="News-Volumen pro Tag",
                               template="plotly_dark", height=300,
                               paper_bgcolor="rgba(0,0,0,0)",
                               margin=dict(l=10,r=10,t=40,b=10))
        st.plotly_chart(fig_bar, use_container_width=True)

    # Quellen-Statistik
    st.subheader("Quellen")
    src_stats = (
        all_news.groupby("source").agg(
            Artikel    =("id",              "count"),
            Positiv    =("sentiment_label", lambda x: (x=="positive").sum()),
            Negativ    =("sentiment_label", lambda x: (x=="negative").sum()),
            Ø_Score    =("sentiment_score", "mean"),
        ).reset_index()
        .rename(columns={"source": "Quelle"})
        .sort_values("Artikel", ascending=False)
    )
    src_stats["Ø_Score"] = src_stats["Ø_Score"].round(3)
    st.dataframe(src_stats, use_container_width=True, hide_index=True)

    # Top Ticker
    st.subheader("Top 20 meistgenannte Ticker")
    all_t = [
        t.strip()
        for row in all_news["tickers"].dropna()
        for t in str(row).split(",")
        if t.strip() and t.strip() != "None"
    ]
    if all_t:
        top = pd.DataFrame(Counter(all_t).most_common(20), columns=["Ticker", "Erwähnungen"])
        fig_h = go.Figure(go.Bar(
            x=top["Erwähnungen"], y=top["Ticker"],
            orientation="h", marker_color="#2979FF",
        ))
        fig_h.update_layout(
            template="plotly_dark", height=420,
            paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(autorange="reversed"),
            margin=dict(l=90, r=10, t=10, b=10),
        )
        st.plotly_chart(fig_h, use_container_width=True)

    # Watchlist-Übersicht nach Kontinent
    st.subheader("Watchlist nach Kontinent")
    if not wl.empty:
        for cont in wl["continent"].unique():
            subset = wl[wl["continent"] == cont]
            emoji  = CONTINENT_EMOJI.get(cont, "")
            with st.expander(f"{emoji} {cont}  ({len(subset)} Ticker)"):
                subset_disp = subset[["ticker", "name", "asset_type"]].reset_index(drop=True).copy()
                subset_disp["Chart"] = subset_disp["ticker"].apply(_chart_link)
                st.dataframe(
                    subset_disp, use_container_width=True, hide_index=True,
                    column_config={
                        "Chart": st.column_config.LinkColumn("Chart", display_text="📈 Öffnen"),
                    },
                )


# ─── Tab 4: Insider Trades ───────────────────────────────────────────────────

from src.fetchers.insider_fetcher import TRUMP_CIRCLE

TRADE_TYPE_ICON  = {"buy": "🟢", "sell": "🔴", "exchange": "🔄"}
SOURCE_LABEL     = {
    "senate": "Senat", "house": "Repräsentantenhaus", "ark": "ARK Invest",
    "djt_insider": "Trump Media (Familie)",
}


@st.cache_data(ttl=120)
def _load_insider_trades(days: int = 180) -> pd.DataFrame:
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = get_connection()
    df = pd.read_sql(
        """SELECT id, source, trader, ticker, company,
                  trade_date, trade_type,
                  amount_min, amount_max, shares_delta,
                  fund, raw_amount
           FROM insider_trades
           WHERE trade_date >= ?
           ORDER BY trade_date DESC""",
        conn, params=[cutoff],
    )
    conn.close()
    return df


@st.cache_data(ttl=120)
def _insider_active_tickers(days: int = 90) -> pd.DataFrame:
    """Ticker-Universum für den Tab 'Insider-Papier-Trades': ein Ticker pro
    Zeile, für den es in den letzten `days` Tagen mindestens einen
    Insider-Trade (Congress/Senate/House, ARK, Trump-Familie) gab, mit
    Firmenname, jüngstem Trade-Datum und Anzahl Trades im Fenster."""
    trades = _load_insider_trades(days=days)
    if trades.empty:
        return pd.DataFrame(columns=["ticker", "company", "last_trade_date", "trade_count"])
    g = (
        trades.groupby("ticker")
        .agg(company=("company", "first"),
             last_trade_date=("trade_date", "max"),
             trade_count=("trade_date", "count"))
        .reset_index()
        .sort_values("last_trade_date", ascending=False)
    )
    return g


def _ensure_insider_price_history(tickers: list[str]) -> dict:
    """Stellt sicher, dass für jeden übergebenen Ticker Kurs-Historie
    vorhanden ist, damit die Elliott-Wellen-Analyse darauf laufen kann.
    Ticker, die noch nicht in der Watchlist stehen (viele Insider-Trade-Ticker
    sind es nicht, siehe _apply_query_param_ticker), werden einmalig
    hinzugefügt (add-only) und ihre Kurse per _fetch_and_store_prices()
    nachgeladen — derselbe Mechanismus wie beim automatischen Watchlist-Add
    über einen Chart-Link. Bereits aktuelle Ticker werden übersprungen."""
    conn = get_connection()
    existing_wl = {r["ticker"] for r in conn.execute("SELECT ticker FROM watchlist").fetchall()}
    new_tickers = [t for t in tickers if t not in existing_wl]
    for t in new_tickers:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (ticker, name, asset_type, continent) VALUES (?,?,?,?)",
            (t, t, "stock", "Global"),
        )
    conn.commit()
    conn.close()
    if new_tickers:
        _load_watchlist.clear()

    refreshed = 0
    for t in tickers:
        if _prices_need_update(t):
            _fetch_and_store_prices(t)
            refreshed += 1
    if refreshed:
        _load_prices.clear()

    return {"added_to_watchlist": len(new_tickers), "price_refresh_attempted": refreshed}


@st.cache_data(ttl=120)
def _load_ark_latest() -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql(
        """SELECT ah.fund, ah.ticker, ah.company,
                  ah.shares, ah.market_value, ah.weight_pct,
                  ah.snapshot_date
           FROM ark_holdings ah
           INNER JOIN (
               SELECT fund, MAX(snapshot_date) AS max_date
               FROM ark_holdings GROUP BY fund
           ) latest ON ah.fund = latest.fund AND ah.snapshot_date = latest.max_date
           ORDER BY ah.fund, ah.weight_pct DESC""",
        conn,
    )
    conn.close()
    return df


def _fmt_amount(lo, hi) -> str:
    if lo is None and hi is None:
        return "—"
    if lo == hi or hi is None:
        return f"${lo:,.0f}"
    return f"${lo:,.0f} – ${hi:,.0f}"


def _run_insider_fetch():
    """Insider-Trades im Dashboard per Knopf laden (alle Quellen parallel)."""
    from src.database.insider_store import fetch_and_store_insiders
    from datetime import date as _date

    with st.sidebar.status("Insider-Trades laden …", expanded=True) as status:
        st.write("⏳ Congress, ARK und Trump-Familie werden parallel abgerufen …")
        r = fetch_and_store_insiders(_date.today().strftime("%Y-%m-%d"))
        if r["congress_error"] == "no_key":
            st.write("⚠️ Congress-Trades: kein FMP-Key → übersprungen")
        else:
            st.write(f"✅ {r['congress_new']} neue Congress-Trades ({r['congress_fetched']} geladen)")
        st.write(f"✅ {r['ark_new']} neue ARK-Trades ({r['ark_fetched']} geladen)")
        st.write(f"✅ {r['holdings_new']} neue Holdings-Zeilen")
        st.write(f"✅ {r['djt_new']} neue Trump-Familie-Trades ({r['djt_fetched']} geladen)")
        status.update(label="✅ Insider-Trades aktualisiert", state="complete")
    st.cache_data.clear()


def _render_embedded_chart(ticker: str, start: str, end: str) -> None:
    """Rendert Kurschart + News/Insider-Marker direkt inline (z.B. unter einer
    Tabelle im Insider-Trades-Tab), ohne dass zum Kurschart-Tab gewechselt
    werden muss. Funktioniert auch für Ticker außerhalb der Watchlist."""
    if _prices_need_update(ticker):
        with st.spinner(f"Lade Kurse für **{ticker}** …"):
            _fetch_and_store_prices(ticker)
            _load_prices.clear()

    prices = _load_prices(ticker, start, end)
    if prices.empty:
        st.warning(f"Keine Kursdaten für **{ticker}** verfügbar.")
        return

    news    = _load_news_for_ticker(ticker, start, end)
    obs     = _load_observations(ticker)
    insider = _load_insider_trades_for_ticker(ticker, start, end)

    last  = prices["close"].iloc[-1]
    prev  = prices["close"].iloc[-2] if len(prices) > 1 else last
    delta = (last - prev) / prev * 100
    k1, k2, k3 = st.columns(3)
    k1.metric("Letzter Kurs", f"{last:.2f}", f"{delta:+.2f} %")
    k2.metric("52W Hoch",     f"{prices['high'].max():.2f}")
    k3.metric("52W Tief",     f"{prices['low'].min():.2f}")

    fig = build_price_chart(prices, news, obs, ticker, "Candlestick", insider)
    render_chart_with_news_link(fig, height=420)


def _clickable_ticker_table(
    display: pd.DataFrame, ticker_col: str, key: str, column_config: dict | None = None,
) -> str | None:
    """Rendert eine Tabelle mit anklickbaren Zeilen; gibt den Ticker der
    ausgewählten Zeile zurück (oder None), damit darunter der Chart inline
    eingeblendet werden kann."""
    event = st.dataframe(
        display, use_container_width=True, hide_index=True,
        on_select="rerun", selection_mode="single-row",
        key=key, column_config=column_config or {},
    )
    rows = event.selection.rows if event is not None else []
    if rows:
        return str(display.iloc[rows[0]][ticker_col])
    return None


def tab_insider_trades(start: str, end: str):
    df = _load_insider_trades(days=365)

    if df.empty:
        st.info("Noch keine Insider-Trades gespeichert. Bitte '🏛️ Insider laden' in der Sidebar klicken.")
        return

    # Zeitraum-Filter anwenden
    df = df[(df["trade_date"] >= start) & (df["trade_date"] <= end)]

    # ── Filter-Zeile ─────────────────────────────────────────────────────────
    fc1, fc2, fc3, fc4 = st.columns([2, 2, 2, 2])

    source_opts = ["Alle"] + sorted(df["source"].unique().tolist())
    sel_source  = fc1.selectbox(
        "Quelle",
        source_opts,
        format_func=lambda s: SOURCE_LABEL.get(s, s.capitalize()) if s != "Alle" else "Alle",
        key="ins_src",
    )

    type_opts = ["Alle", "buy", "sell", "exchange"]
    sel_type  = fc2.selectbox(
        "Trade-Typ",
        type_opts,
        format_func=lambda t: {"buy": "🟢 Kauf", "sell": "🔴 Verkauf", "exchange": "🔄 Tausch", "Alle": "Alle"}.get(t, t),
        key="ins_type",
    )

    all_traders = sorted(df["trader"].unique().tolist())
    sel_traders = fc3.multiselect("Trader filtern", all_traders, key="ins_trader",
                                   placeholder="Alle Trader")

    ticker_input = fc4.text_input("Ticker", key="ins_ticker",
                                   placeholder="z.B. TSLA, NVDA").upper().strip()

    # Filter anwenden
    view = df.copy()
    if sel_source != "Alle":
        view = view[view["source"] == sel_source]
    if sel_type != "Alle":
        view = view[view["trade_type"] == sel_type]
    if sel_traders:
        view = view[view["trader"].isin(sel_traders)]
    if ticker_input:
        view = view[view["ticker"].str.contains(ticker_input, case=False, na=False)]

    # ── KPIs ─────────────────────────────────────────────────────────────────
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Trades gesamt",    len(view))
    k2.metric("Käufe",  int((view["trade_type"] == "buy").sum()))
    k3.metric("Verkäufe", int((view["trade_type"] == "sell").sum()))
    k4.metric("Trader",  view["trader"].nunique())

    # ── Top Performer (Buy-Trades seit Einstieg) ─────────────────────────────
    # Basiert auf Quelle/Trader/Ticker-Filter, aber unabhängig vom Trade-Typ-Filter
    # (eine "Performance" ergibt nur für Käufe Sinn, egal was oben ausgewählt ist).
    from src.analytics.backtest import rank_trade_performance

    buy_view = df[df["trade_type"] == "buy"].copy()
    if sel_source != "Alle":
        buy_view = buy_view[buy_view["source"] == sel_source]
    if sel_traders:
        buy_view = buy_view[buy_view["trader"].isin(sel_traders)]
    if ticker_input:
        buy_view = buy_view[buy_view["ticker"].str.contains(ticker_input, case=False, na=False)]

    st.subheader("🏆 Top Performer (Buy-Trades seit Einstieg)")
    if buy_view.empty:
        st.caption("Keine Buy-Trades im aktuellen Filter.")
    else:
        conn = get_connection()
        ranked = rank_trade_performance(conn, buy_view.to_dict("records"))
        conn.close()

        if not ranked:
            st.caption(
                "Keine der gefilterten Buy-Trades hat auswertbare Kursdaten "
                "(Ticker ggf. noch nicht in der Watchlist / Kurse geladen)."
            )
        else:
            top_n = st.slider(
                "Anzahl anzeigen", min_value=5,
                max_value=max(5, min(50, len(ranked))),
                value=min(15, len(ranked)), key="ins_top_n",
            )
            perf_df = pd.DataFrame(ranked[:top_n]).rename(columns={
                "trader": "Trader", "ticker": "Ticker", "company": "Unternehmen",
                "source": "Quelle", "trade_date": "Trade-Datum",
                "entry_date": "Einstieg", "entry_price": "Einstiegskurs",
                "latest_date": "Kurs vom", "latest_price": "Aktuell",
                "return_pct": "Performance",
            })
            perf_df["Quelle"]        = perf_df["Quelle"].map(lambda s: SOURCE_LABEL.get(s, s))
            perf_df["Einstiegskurs"] = perf_df["Einstiegskurs"].map("{:.2f}".format)
            perf_df["Aktuell"]       = perf_df["Aktuell"].map("{:.2f}".format)
            perf_df["Performance"]   = perf_df["Performance"].map("{:+.1f} %".format)
            st.caption("🖱️ Zeile anklicken, um den Chart darunter einzublenden.")
            clicked = _clickable_ticker_table(
                perf_df[["Ticker", "Unternehmen", "Trader", "Quelle", "Trade-Datum",
                          "Einstieg", "Einstiegskurs", "Kurs vom", "Aktuell",
                          "Performance"]],
                ticker_col="Ticker", key="ins_perf_tbl",
            )
            if clicked:
                st.markdown(f"#### 📈 {clicked}")
                _render_embedded_chart(clicked, start, end)

    # ── Trump-Kreis Highlight ─────────────────────────────────────────────────
    # Zwei Quellen fließen hier zusammen: (1) Congress-/Administration-Mitglieder
    # aus TRUMP_CIRCLE (per Namensabgleich), (2) alle djt_insider-Trades
    # (Trump-Familie bei Trump Media/DJT) unabhängig vom Namen.
    trump_df = view[view["trader"].isin(TRUMP_CIRCLE) | (view["source"] == "djt_insider")]
    if not trump_df.empty:
        with st.expander(f"🇺🇸 Trump-Kreis Trades ({len(trump_df)} im Zeitraum)", expanded=True):
            st.caption(
                "Umfasst Trump-nahe Congress-Mitglieder, aktuelle Kabinettsmitglieder/VP "
                "sowie Trump-Familienmitglieder als Insider bei Trump Media & Technology Group (DJT)."
            )
            trump_disp = trump_df.head(30)[
                ["trade_date", "trader", "ticker", "company", "trade_type", "raw_amount", "source"]
            ].copy()
            trump_disp["source"] = trump_disp["source"].map(lambda s: SOURCE_LABEL.get(s, s.capitalize()))
            trump_disp["trade_type"] = trump_disp["trade_type"].apply(
                lambda x: f"{TRADE_TYPE_ICON.get(x, '⚪')} {x.upper()}"
            )
            trump_disp = trump_disp.rename(columns={
                "trade_date": "Datum", "trader": "Trader", "ticker": "Ticker",
                "company": "Unternehmen", "trade_type": "Typ",
                "raw_amount": "Betrag", "source": "Quelle",
            })
            st.caption("🖱️ Zeile anklicken, um den Chart direkt darunter einzublenden (inkl. gelber ℹ-Marker am Trade-Tag).")
            clicked = _clickable_ticker_table(trump_disp, ticker_col="Ticker", key="ins_trump_tbl")
            if clicked:
                st.markdown(f"#### 📈 {clicked}")
                _render_embedded_chart(clicked, start, end)

    # ── Tabs: Congress / ARK ─────────────────────────────────────────────────
    ct1, ct2 = st.tabs(["🏛️  Congress (Senat + Repräsentantenhaus)", "🦄  ARK Invest (Cathie Wood)"])

    with ct1:
        from src.fetchers.insider_fetcher import get_fmp_key, save_fmp_key
        if not get_fmp_key():
            st.warning(
                "**Congress-Trades erfordern einen kostenlosen FMP-API-Key.**\n\n"
                "1. Gratis registrieren: [financialmodelingprep.com](https://financialmodelingprep.com)\n"
                "2. Key unten eingeben und speichern — danach '🏛️ Insider laden' klicken."
            )
            key_input = st.text_input("FMP API-Key eingeben", type="password", key="fmp_key_in")
            if st.button("Key speichern", key="fmp_key_save") and key_input.strip():
                save_fmp_key(key_input.strip())
                st.success("Key gespeichert! Bitte '🏛️ Insider laden' klicken.")
                st.rerun()

        congress_df = view[view["source"].isin(["senate", "house"])].copy()
        if congress_df.empty:
            st.info("Keine Congress-Trades vorhanden. Bitte '🏛️ Insider laden' klicken (FMP-Key erforderlich).")
        else:
            # Top-Trader Balkendiagramm
            top_traders = congress_df["trader"].value_counts().head(15)
            if not top_traders.empty:
                fig_t = go.Figure(go.Bar(
                    x=top_traders.values, y=top_traders.index,
                    orientation="h", marker_color="#2979FF",
                ))
                fig_t.update_layout(
                    title="Top 15 aktivste Trader",
                    template="plotly_dark", height=350,
                    paper_bgcolor="rgba(0,0,0,0)",
                    yaxis=dict(autorange="reversed"),
                    margin=dict(l=180, r=10, t=40, b=10),
                )
                st.plotly_chart(fig_t, use_container_width=True)

            # Tabelle
            st.markdown(f"**{len(congress_df)} Trades**")
            display = congress_df[["trade_date", "source", "trader", "ticker",
                                    "company", "trade_type", "raw_amount"]].copy()
            display["source"]     = display["source"].map(SOURCE_LABEL)
            display["trade_type"] = display["trade_type"].apply(
                lambda x: f"{TRADE_TYPE_ICON.get(x, '')} {x.upper()}"
            )
            display = display.rename(columns={
                "trade_date": "Datum", "source": "Kammer", "trader": "Trader",
                "ticker": "Ticker", "company": "Unternehmen",
                "trade_type": "Typ", "raw_amount": "Betrag",
            })
            st.caption("🖱️ Zeile anklicken, um den Chart darunter einzublenden.")
            clicked = _clickable_ticker_table(display, ticker_col="Ticker", key="ins_congress_tbl")
            if clicked:
                st.markdown(f"#### 📈 {clicked}")
                _render_embedded_chart(clicked, start, end)

    with ct2:
        ark_df = view[view["source"] == "ark"].copy()

        # Aktuelle Holdings anzeigen
        st.subheader("Aktuelle ARK-Positionen (neuester Snapshot)")
        holdings = _load_ark_latest()
        if not holdings.empty:
            fund_sel = st.selectbox(
                "Fonds",
                ["Alle"] + sorted(holdings["fund"].unique().tolist()),
                key="ark_fund_sel",
            )
            h_view = holdings if fund_sel == "Alle" else holdings[holdings["fund"] == fund_sel]
            h_display = h_view[["fund", "ticker", "company", "shares", "market_value", "weight_pct"]].copy()
            if "market_value" in h_display.columns:
                h_display["market_value"] = h_display["market_value"].apply(
                    lambda x: f"${x:,.0f}" if pd.notna(x) else "—"
                )
            if "weight_pct" in h_display.columns:
                h_display["weight_pct"] = h_display["weight_pct"].apply(
                    lambda x: f"{x:.2f}%" if pd.notna(x) else "—"
                )
            if "shares" in h_display.columns:
                h_display["shares"] = h_display["shares"].apply(
                    lambda x: f"{x:,.0f}" if pd.notna(x) else "—"
                )
            h_display = h_display.rename(columns={
                "fund": "Fonds", "ticker": "Ticker", "company": "Unternehmen",
                "shares": "Aktien", "market_value": "Marktwert ($)",
                "weight_pct": "Gewichtung",
            })
            st.caption("🖱️ Zeile anklicken, um den Chart darunter einzublenden.")
            clicked = _clickable_ticker_table(h_display, ticker_col="Ticker", key="ins_ark_holdings_tbl")
            if clicked:
                st.markdown(f"#### 📈 {clicked}")
                _render_embedded_chart(clicked, start, end)
        else:
            st.info("Noch keine ARK-Holdings gespeichert.")

        # ARK Trades
        st.subheader("Erkannte ARK-Käufe & Verkäufe")
        if ark_df.empty:
            st.info("Keine ARK-Trades im Zeitraum. Bitte 'Insider laden' klicken.")
        else:
            col_b, col_s = st.columns(2)
            ark_buys  = ark_df[ark_df["trade_type"] == "buy"].sort_values("shares_delta", ascending=False)
            ark_sells = ark_df[ark_df["trade_type"] == "sell"].sort_values("shares_delta")

            clicked = None
            with col_b:
                st.markdown("**🟢 Käufe**")
                if not ark_buys.empty:
                    b_disp = ark_buys[["trade_date", "fund", "ticker", "shares_delta"]].rename(
                        columns={"trade_date": "Datum", "fund": "Fonds",
                                 "ticker": "Ticker", "shares_delta": "Δ Aktien"}
                    )
                    b_disp["Δ Aktien"] = b_disp["Δ Aktien"].apply(lambda x: f"+{x:,.0f}" if pd.notna(x) else "—")
                    clicked = _clickable_ticker_table(b_disp, ticker_col="Ticker", key="ins_ark_buys_tbl") or clicked

            with col_s:
                st.markdown("**🔴 Verkäufe**")
                if not ark_sells.empty:
                    s_disp = ark_sells[["trade_date", "fund", "ticker", "shares_delta"]].rename(
                        columns={"trade_date": "Datum", "fund": "Fonds",
                                 "ticker": "Ticker", "shares_delta": "Δ Aktien"}
                    )
                    s_disp["Δ Aktien"] = s_disp["Δ Aktien"].apply(lambda x: f"{x:,.0f}" if pd.notna(x) else "—")
                    clicked = _clickable_ticker_table(s_disp, ticker_col="Ticker", key="ins_ark_sells_tbl") or clicked

            st.caption("🖱️ Zeile anklicken, um den Chart darunter einzublenden.")
            if clicked:
                st.markdown(f"#### 📈 {clicked}")
                _render_embedded_chart(clicked, start, end)


# ─── Tab 5: Vergleich ────────────────────────────────────────────────────────

# Fixed categorical order (dark-surface palette), assigned by selection order —
# never re-cycled when the selection changes, so a ticker keeps its color.
COMPARE_PALETTE = [
    "#3987e5",  # blue
    "#199e70",  # aqua
    "#c98500",  # yellow
    "#008300",  # green
    "#9085e9",  # violet
    "#e66767",  # red
    "#d55181",  # magenta
    "#d95926",  # orange
]
COMPARE_MAX_TICKERS = len(COMPARE_PALETTE)


def build_compare_chart(prices_by_ticker: dict[str, pd.DataFrame]) -> go.Figure:
    fig = go.Figure()

    for i, (ticker, df) in enumerate(prices_by_ticker.items()):
        if df.empty:
            continue
        base = df["close"].iloc[0]
        pct  = (df["close"] / base - 1) * 100
        fig.add_trace(go.Scatter(
            x=df["date"], y=pct,
            mode="lines", name=ticker,
            line=dict(color=COMPARE_PALETTE[i % len(COMPARE_PALETTE)], width=2),
            hovertemplate=f"<b>{ticker}</b>  %{{y:+.2f}}%<extra></extra>",
        ))

    fig.add_hline(y=0, line_width=1, line_dash="dot", line_color="rgba(255,255,255,0.25)")
    fig.update_layout(
        height=520,
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(10,12,20,1)",
        yaxis=dict(
            title="Performance seit Start",
            ticksuffix=" %",
            showgrid=True, gridcolor="rgba(255,255,255,0.06)",
        ),
        xaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)"),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="left", x=0,
            bgcolor="rgba(0,0,0,0.4)", font=dict(size=11),
        ),
        hovermode="x unified",
        margin=dict(l=10, r=20, t=30, b=20),
    )
    return fig


def tab_compare(start: str, end: str, continent: str):
    wl = _load_watchlist()
    if wl.empty:
        st.info("Watchlist leer — bitte Ticker hinzufügen oder 'Watchlist sync' klicken.")
        return

    wl_filtered = wl if continent == "Alle" else wl[wl["continent"] == continent]
    if wl_filtered.empty:
        st.info(f"Keine Ticker für Kontinent '{continent}' in der Watchlist.")
        return

    ticker_options = wl_filtered["ticker"].tolist()
    selected = st.multiselect(
        "Ticker vergleichen (relative Performance in % seit Zeitraumbeginn)",
        ticker_options,
        default=ticker_options[:3],
        format_func=lambda t: (
            f"{t}  —  {wl_filtered[wl_filtered['ticker']==t]['name'].values[0]}"
        ),
        key="compare_sel",
    )

    if not selected:
        st.info("Mindestens einen Ticker auswählen.")
        return
    if len(selected) > COMPARE_MAX_TICKERS:
        st.warning(
            f"Bitte maximal {COMPARE_MAX_TICKERS} Ticker gleichzeitig vergleichen "
            "(Übersichtlichkeit) — es werden nur die ersten "
            f"{COMPARE_MAX_TICKERS} angezeigt."
        )
        selected = selected[:COMPARE_MAX_TICKERS]

    with st.spinner("Lade Kursdaten …"):
        for t in selected:
            if _prices_need_update(t):
                _fetch_and_store_prices(t)
        _load_prices.clear()

    prices_by_ticker = {t: _load_prices(t, start, end) for t in selected}
    missing = [t for t, df in prices_by_ticker.items() if df.empty]
    if missing:
        st.caption(f"⚠️ Keine Kursdaten im Zeitraum für: {', '.join(missing)}")

    fig = build_compare_chart(prices_by_ticker)
    st.plotly_chart(fig, use_container_width=True)

    # Performance-Tabelle, absteigend sortiert
    rows = []
    for t, df in prices_by_ticker.items():
        if df.empty:
            continue
        first, last = df["close"].iloc[0], df["close"].iloc[-1]
        perf = (last / first - 1) * 100
        rows.append({
            "Ticker":      t,
            "Name":        wl_filtered[wl_filtered["ticker"] == t]["name"].values[0],
            "Start":       first,
            "Ende":        last,
            "Performance": perf,
        })
    if rows:
        perf_df = pd.DataFrame(rows).sort_values("Performance", ascending=False)
        stock_universe = tuple(sorted(wl_filtered[wl_filtered["asset_type"] == "stock"]["ticker"]))
        fund_scores = _load_fundamental_scores(stock_universe).set_index("ticker")
        perf_df["Fundamentalscore"] = perf_df["Ticker"].map(
            lambda t: _fundamental_badge(fund_scores.loc[t, "fundamental_score"])
                      if t in fund_scores.index else "➖ n/a"
        )
        perf_df["Start"]       = perf_df["Start"].map("{:.2f}".format)
        perf_df["Ende"]        = perf_df["Ende"].map("{:.2f}".format)
        perf_df["Performance"] = perf_df["Performance"].map("{:+.2f} %".format)
        perf_df["Chart"]       = perf_df["Ticker"].apply(_chart_link)
        st.dataframe(
            perf_df, use_container_width=True, hide_index=True,
            column_config={
                "Chart": st.column_config.LinkColumn("Chart", display_text="📈 Öffnen"),
            },
        )
        st.caption(
            "Fundamentalscore (Piotroski-F-Score/Gross-Profitability/Fama-French-Proxy) — nur aus "
            "dem Cache, für frische Werte oben unter 'Kurschart' den Refresh-Button nutzen oder "
            "'python main.py fundamentals' laufen lassen."
        )

    # ── Top Performer über die gesamte (kontinent-gefilterte) Watchlist ──────
    st.divider()
    st.subheader("🏆 Top Performer (gesamte Watchlist im Zeitraum)")
    st.caption(
        "Rankt alle Ticker der aktuell per Kontinent gefilterten Watchlist nach "
        "Kursperformance im gewählten Zeitraum — unabhängig von der Auswahl oben. "
        "Nutzt bereits geladene Kursdaten (ggf. erst 'Kurse laden' in der Sidebar "
        "ausführen); Ticker ohne ausreichende Historie werden übersprungen."
    )
    if st.button("🔍 Top-Performer ermitteln", key="cmp_top_btn"):
        rows_all = []
        skipped = 0
        with st.spinner(f"Werte {len(wl_filtered)} Ticker aus …"):
            for _, wl_row in wl_filtered.iterrows():
                t = wl_row["ticker"]
                p = _load_prices(t, start, end)
                if len(p) < 2:
                    skipped += 1
                    continue
                first, last = p["close"].iloc[0], p["close"].iloc[-1]
                rows_all.append({
                    "Ticker":      t,
                    "Name":        wl_row["name"],
                    "Start":       first,
                    "Ende":        last,
                    "Performance": (last / first - 1) * 100,
                })

        if not rows_all:
            st.info(f"Keine auswertbaren Kursdaten gefunden ({skipped} Ticker übersprungen).")
        else:
            top_all_df = pd.DataFrame(rows_all).sort_values("Performance", ascending=False)
            top_n = st.slider(
                "Anzahl anzeigen", min_value=5,
                max_value=max(5, min(50, len(top_all_df))),
                value=min(15, len(top_all_df)), key="cmp_top_n",
            )
            disp = top_all_df.head(top_n).copy()
            top_universe = tuple(sorted(wl_filtered[wl_filtered["asset_type"] == "stock"]["ticker"]))
            top_fund_scores = _load_fundamental_scores(top_universe).set_index("ticker")
            disp["Fundamentalscore"] = disp["Ticker"].map(
                lambda t: _fundamental_badge(top_fund_scores.loc[t, "fundamental_score"])
                          if t in top_fund_scores.index else "➖ n/a"
            )
            disp["Start"]       = disp["Start"].map("{:.2f}".format)
            disp["Ende"]        = disp["Ende"].map("{:.2f}".format)
            disp["Performance"] = disp["Performance"].map("{:+.2f} %".format)
            disp["Chart"]       = disp["Ticker"].apply(_chart_link)
            st.dataframe(
                disp, use_container_width=True, hide_index=True,
                column_config={
                    "Chart": st.column_config.LinkColumn("Chart", display_text="📈 Öffnen"),
                },
            )
            st.caption(
                f"{len(top_all_df)} Ticker ausgewertet, {skipped} übersprungen "
                "(keine/zu wenig Kursdaten im Zeitraum)."
            )


def tab_backtest():
    """'Was wäre passiert, hätte ich jedes Mal mitgekauft?' — Backtest der
    Buy-Trades eines Congress-Mitglieds oder ARK-Funds gegen den aktuellsten
    verfügbaren Kurs, mit Benchmark-Vergleich."""
    from src.analytics.backtest import backtest_trader, list_backtestable_traders

    conn = get_connection()
    traders = list_backtestable_traders(conn)
    conn.close()

    if not traders:
        st.info(
            "Noch keine Backtests möglich — dazu müssen für mindestens einen "
            "Trader Buy-Trades UND Kursdaten für die gehandelten Ticker "
            "vorliegen (Tab 'Insider Trades' → Trades laden, dann Ticker zur "
            "Watchlist hinzufügen)."
        )
        return

    labels = [f"{t['trader']}  ({t['source']}, {t['c']} Buy-Trades)" for t in traders]
    selected_idx = st.selectbox("Trader / Fund", options=range(len(labels)), format_func=lambda i: labels[i], key="backtest_trader_sel")
    trader, source = traders[selected_idx]["trader"], traders[selected_idx]["source"]

    benchmark = (st.text_input("Benchmark-Ticker", value="SPY", key="backtest_benchmark").strip().upper() or "SPY")

    conn = get_connection()
    result = backtest_trader(conn, trader, source=source, benchmark=benchmark)
    conn.close()

    if result["n_trades"] == 0:
        st.warning(
            "Für diesen Trader liegen aktuell keine auswertbaren Trades vor "
            "(keine Kursdaten oder noch keine Historie nach Einstieg)."
        )
        if result["skipped"]:
            st.caption(f"Übersprungen: {', '.join(s['ticker'] for s in result['skipped'])}")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Ø Rendite", f"{result['avg_return_pct']:+.1f} %")
    c2.metric("Trefferquote", f"{result['win_rate_pct']:.0f} %")
    c3.metric("Anzahl Trades", result["n_trades"])
    bench_avg = result["avg_benchmark_return_pct"]
    bench_delta = f"{result['avg_return_pct'] - bench_avg:+.1f} pp ggü. Trader" if bench_avg is not None else None
    c4.metric(
        f"Ø {benchmark}-Rendite (gleicher Zeitraum)",
        f"{bench_avg:+.1f} %" if bench_avg is not None else "—",
        delta=bench_delta,
    )

    bench_col = f"{benchmark}-Rendite"
    rows = [
        {
            "Ticker":        t["ticker"],
            "Firma":         t["company"],
            "Trade-Datum":   t["trade_date"],
            "Einstieg":      t["entry_date"],
            "Einstiegskurs": t["entry_price"],
            "Aktuell":       t["latest_price"],
            "Rendite":       t["return_pct"],
            bench_col:       t["benchmark_return_pct"],
        }
        for t in result["trades"]
    ]
    df = pd.DataFrame(rows).sort_values("Rendite", ascending=False)
    df["Einstiegskurs"] = df["Einstiegskurs"].map("{:.2f}".format)
    df["Aktuell"]       = df["Aktuell"].map("{:.2f}".format)
    df["Rendite"]       = df["Rendite"].map("{:+.1f} %".format)
    df[bench_col]       = df[bench_col].map(lambda v: f"{v:+.1f} %" if pd.notna(v) else "—")
    st.dataframe(df, use_container_width=True, hide_index=True)

    if result["skipped"]:
        with st.expander(f"⚠️ {len(result['skipped'])} Trade(s) ohne auswertbare Kursdaten übersprungen"):
            for s in result["skipped"]:
                st.caption(f"{s['ticker']} ({s['trade_date']}): {s['reason']}")


def tab_wave_scanner(continent: str):
    from src.analytics.elliott_wave import analyze_current_wave, STATE_LABELS, STRUCTURE_LABELS
    from src.analytics.wave_tracker import record_snapshot, is_canonical_scan

    wl = _load_watchlist()
    if wl.empty:
        st.info("Watchlist leer — bitte Ticker hinzufügen oder 'Watchlist sync' klicken.")
        return

    wl_filtered = wl if continent == "Alle" else wl[wl["continent"] == continent]
    if wl_filtered.empty:
        st.info(f"Keine Ticker für Kontinent '{continent}' in der Watchlist.")
        return

    c1, c2, c3 = st.columns([1, 1, 1])
    lookback_days = c1.slider("Lookback (Tage)", min_value=90, max_value=1000, value=365, step=15, key="ews_lb")
    sensitivity   = c2.slider("Zigzag-Empfindlichkeit (%)", min_value=1.0, max_value=20.0, value=5.0, step=0.5, key="ews_th")
    min_conf      = c3.slider("Mindest-Konfidenz", min_value=0.0, max_value=1.0, value=0.5, step=0.05, key="ews_mc")

    if not is_canonical_scan(sensitivity, lookback_days):
        st.caption("ℹ️ Abweichend von den Standard-Parametern (5 %, 365 Tage) — erkannte "
                   "Erwartungen werden angezeigt, aber nicht im Tab 'Vorhersagen' protokolliert.")
    st.caption(
        "Scannt alle Watchlist-Ticker (aktueller Kontinent-Filter) auf noch nicht "
        "abgeschlossene Elliott-Wellen-Bewegungen und schätzt die Zielzone der "
        "gerade laufenden Welle. Nutzt bereits geladene Kursdaten — Ticker ohne "
        "ausreichende Historie werden übersprungen (ggf. erst 'Kurse laden' in der "
        "Sidebar ausführen)."
    )

    fund_filter_on, min_fund_score, fund_scores = _fundamental_prefilter_controls(
        wl_filtered, key_prefix="ews",
    )

    end_d   = str(datetime.today().date())
    start_d = str(datetime.today().date() - timedelta(days=lookback_days))

    if not st.button("🔍 Scan starten", type="primary", key="ews_scan_btn"):
        return

    scan_universe, n_excluded = _apply_fundamental_prefilter(
        wl_filtered, fund_filter_on, min_fund_score, fund_scores,
    )
    if n_excluded:
        st.caption(f"📐 {n_excluded} Ticker durch Fundamental-Vorfilter (< {min_fund_score:.0f}/100) ausgeschlossen.")

    rows = []
    skipped = 0
    pred_conn = get_connection()
    with st.spinner(f"Scanne {len(scan_universe)} Ticker …"):
        for _, wl_row in scan_universe.iterrows():
            ticker = wl_row["ticker"]
            prices = _load_prices(ticker, start_d, end_d)
            if len(prices) < 20:
                skipped += 1
                continue
            state = analyze_current_wave(prices, threshold_pct=sensitivity)
            if state:
                # Für die Trefferquoten- und Verlaufs-Auswertung wird jede
                # erkannte Erwartung festgehalten, unabhängig vom Anzeige-Filter
                # min_conf.
                record_snapshot(pred_conn, ticker, state, sensitivity, lookback_days)
            if state and state["confidence"] >= min_conf:
                dist_pct = (
                    None if state["target_low"] is None
                    else ((state["target_low"] + state["target_high"]) / 2 / state["current_price"] - 1) * 100
                )
                rows.append({
                    "Ticker":        ticker,
                    "Name":          wl_row["name"],
                    "Muster":        STATE_LABELS.get(state["state"], state["state"]),
                    "Struktur":      STRUCTURE_LABELS.get(state.get("structure_type"), "—"),
                    "Richtung":      "🟢 Bullish" if state["direction"] == "bullish" else "🔴 Bearish",
                    "Konfidenz":     state["confidence"],
                    "Fundamentalscore": _fundamental_badge(_fund_score_for(fund_scores, ticker)),
                    "Aktueller Kurs": state["current_price"],
                    "Zielzone":      (
                        f"{state['target_low']:.2f} – {state['target_high']:.2f}"
                        if state["target_low"] is not None else "—"
                    ),
                    "Δ zur Zielmitte": dist_pct,
                    "Letzter Pivot": f"{state['last_pivot_price']:.2f} ({state['last_pivot_date']})",
                    "Folgewellen":   ", ".join(
                        f"{pw['label']}: {pw['target_low']:.2f}–{pw['target_high']:.2f}"
                        for pw in (state.get("projected_waves") or [])
                    ) or "—",
                    "Erwartung":     state["expectation_text"],
                })

    pred_conn.commit()
    pred_conn.close()

    if not rows:
        st.info(
            f"Keine aktuellen Muster über Mindest-Konfidenz {min_conf:.2f} gefunden "
            f"({skipped} Ticker ohne ausreichende Kurshistorie übersprungen)."
        )
        return

    df = pd.DataFrame(rows).sort_values("Konfidenz", ascending=False)
    st.success(f"{len(df)} Ticker mit aktuellem Muster gefunden ({skipped} übersprungen mangels Kurshistorie).")

    display = df.copy()
    display["Konfidenz"]       = display["Konfidenz"].map("{:.0%}".format)
    display["Aktueller Kurs"]  = display["Aktueller Kurs"].map("{:.2f}".format)
    display["Δ zur Zielmitte"] = display["Δ zur Zielmitte"].map(
        lambda v: "—" if pd.isna(v) else f"{v:+.1f} %"
    )
    display["Chart"] = display["Ticker"].apply(_chart_link)
    st.dataframe(
        display[["Ticker", "Name", "Muster", "Struktur", "Richtung", "Konfidenz",
                  "Fundamentalscore", "Aktueller Kurs", "Zielzone", "Δ zur Zielmitte",
                  "Folgewellen", "Letzter Pivot", "Chart"]],
        use_container_width=True, hide_index=True,
        column_config={
            "Chart": st.column_config.LinkColumn("Chart", display_text="📈 Öffnen"),
        },
    )

    with st.expander("📋 Erwartungstexte je Ticker"):
        for _, r in df.iterrows():
            st.markdown(f"**[{r['Ticker']}]({_chart_link(r['Ticker'])})** — {r['Erwartung']}")


# ─── Tab: Einstiegssignale (Elliott-Wellen + Insider-Abgleich) ───────────────

# Je erkanntem Wellen-Zustand: wie stark/riskant ist ein Einstieg an dieser
# Stelle einzuordnen. Die Richtung (Buy/Short) selbst liefert bereits
# state["direction"] aus analyze_current_wave() — bei allen vier Zuständen
# bezieht sich "direction" konsistent auf die als nächstes erwartete
# Kursbewegung, nicht auf die zuvor gelaufene.
SIGNAL_STRENGTH = {
    "wave3_forming": (
        "Starkes Signal", "Welle 3 gilt in der Elliott-Theorie meist als "
        "stärkste und längste Welle des Impulses."
    ),
    "new_impulse_possible": (
        "Starkes Signal", "Möglicher Beginn eines neuen Trends direkt nach "
        "abgeschlossener Korrektur."
    ),
    "wave5_forming": (
        "Später Einstieg", "Letzte Welle des Impulses — Kursziel oft "
        "begrenzt, erhöhtes Rückschlagrisiko nahe Trendende."
    ),
    "correction_forming": (
        "Gegentrend-Signal", "Impuls scheint beendet, Korrektur läuft an — "
        "eher kurzfristiger Gegentrend- als Trendfolge-Einstieg."
    ),
}


def _accepted_by_active_version(state: dict) -> bool:
    from src.analytics.model_versions import accepts
    from src.analytics.wave_tracker import barrier_baseline
    _, in_zone = barrier_baseline(state["direction"], state["current_price"], state["target_low"],
                                  state["target_high"], state["last_pivot_price"])
    return accepts({
        "state": state["state"], "direction": state["direction"], "confidence": state["confidence"],
        "target_low": state["target_low"], "in_zone_at_creation": int(bool(in_zone)),
    })


def _market_regime_hint() -> None:
    """Bärenmarkt-Hinweis (S&P 500 unter SMA200) — dort setzt v4 den doppelten Einsatz."""
    from src.analytics.model_versions import (
        market_closes, VERSION_BY_KEY, ACTIVE_VERSION, BEAR_STAKE_FACTOR,
    )
    conn = get_connection()
    closes = market_closes(conn)
    conn.close()
    if len(closes) < 200:
        return
    sma = closes.iloc[-200:].mean()
    dist = closes.iloc[-1] / sma - 1
    v4_active = VERSION_BY_KEY[ACTIVE_VERSION].stake is not None
    if dist < 0:
        st.warning(
            f"📉 **Bärenmarkt:** S&P 500 {dist*100:+.1f} % unter seiner 200-Tage-Linie. Long-Signale "
            "waren in solchen Phasen bisher überdurchschnittlich — "
            + (f"die aktive Version setzt den {BEAR_STAKE_FACTOR:.0f}-fachen Einsatz."
               if v4_active else "v4 (doppelter Einsatz) wird im Tab 'Modellvergleich' geprüft.")
        )
    else:
        st.caption(f"📈 S&P 500 {dist*100:+.1f} % über seiner 200-Tage-Linie — kein Bärenmarkt, "
                   "Standard-Einsatz.")


def tab_signals(continent: str):
    from src.analytics.elliott_wave import analyze_current_wave, STATE_LABELS
    from src.analytics.wave_tracker import record_snapshot, is_canonical_scan
    from src.analytics.model_versions import VERSION_BY_KEY, ACTIVE_VERSION

    st.caption(
        "Direkte Einstiegs-Meldungen: scannt die Watchlist auf vom "
        "Elliott-Wellen-Modul erkannte Einstiege, die die aktive Modellversion "
        "zulässt (Regeln im Tab 'Modellvergleich'), und gleicht "
        "jeden Treffer zusätzlich mit den zuletzt gemeldeten Insider-Trades "
        "(Congress + ARK) desselben Tickers ab — als unabhängige "
        "Zusatzinfo, ob Insider in dieselbe oder die entgegengesetzte "
        "Richtung handeln."
    )

    wl = _load_watchlist()
    if wl.empty:
        st.info("Watchlist leer — bitte Ticker hinzufügen oder 'Watchlist sync' klicken.")
        return

    wl_filtered = wl if continent == "Alle" else wl[wl["continent"] == continent]
    if wl_filtered.empty:
        st.info(f"Keine Ticker für Kontinent '{continent}' in der Watchlist.")
        return

    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
    lookback_days   = c1.slider("Lookback (Tage)", min_value=90, max_value=1000, value=365, step=15, key="sig_lb")
    sensitivity     = c2.slider("Zigzag-Empfindlichkeit (%)", min_value=1.0, max_value=20.0, value=5.0, step=0.5, key="sig_th")
    min_conf        = c3.slider("Mindest-Konfidenz", min_value=0.0, max_value=1.0, value=0.5, step=0.05, key="sig_mc")
    insider_days    = c4.slider("Insider-Fenster (Tage)", min_value=30, max_value=365, value=180, step=15, key="sig_id")

    if not is_canonical_scan(sensitivity, lookback_days):
        st.caption("ℹ️ Abweichend von den Standard-Parametern (5 %, 365 Tage) — erkannte "
                   "Erwartungen werden angezeigt, aber nicht im Tab 'Vorhersagen' protokolliert.")

    fund_filter_on, min_fund_score, fund_scores = _fundamental_prefilter_controls(
        wl_filtered, key_prefix="sig",
    )

    if not st.button("🔍 Nach Einstiegssignalen suchen", type="primary", key="sig_scan_btn"):
        return

    scan_universe, n_excluded = _apply_fundamental_prefilter(
        wl_filtered, fund_filter_on, min_fund_score, fund_scores,
    )
    if n_excluded:
        st.caption(f"📐 {n_excluded} Ticker durch Fundamental-Vorfilter (< {min_fund_score:.0f}/100) ausgeschlossen.")

    end_d   = str(datetime.today().date())
    start_d = str(datetime.today().date() - timedelta(days=lookback_days))

    signals = []
    skipped = n_rejected = 0
    pred_conn = get_connection()
    with st.spinner(f"Scanne {len(scan_universe)} Ticker …"):
        for _, wl_row in scan_universe.iterrows():
            ticker = wl_row["ticker"]
            prices = _load_prices(ticker, start_d, end_d)
            if len(prices) < 20:
                skipped += 1
                continue
            state = analyze_current_wave(prices, threshold_pct=sensitivity)
            if state:
                record_snapshot(pred_conn, ticker, state, sensitivity, lookback_days)
            if state and state["confidence"] >= min_conf and not _accepted_by_active_version(state):
                # Wird weiter protokolliert (Vorhersage-Statistik), aber nicht
                # als Einstieg gemeldet — Regeln in model_versions.py.
                n_rejected += 1
                continue
            if state and state["confidence"] >= min_conf:
                insider = _insider_bias(ticker, days=insider_days)
                signals.append({
                    "wl_row": wl_row, "state": state, "insider": insider,
                    "fund_score": _fund_score_for(fund_scores, ticker),
                })
    pred_conn.commit()
    pred_conn.close()

    if n_rejected:
        active = VERSION_BY_KEY[ACTIVE_VERSION]
        st.caption(
            f"🔕 {n_rejected} Signal(e) ausgeblendet, die die aktive Modellversion "
            f"**{active.label}** nicht als Einstieg wertet ({active.rule_text}). Sie fließen "
            "weiter in den Tab 'Vorhersagen' ein — Vergleich der Versionen im Tab 'Modellvergleich'."
        )
    _market_regime_hint()
    if not signals:
        st.info(
            f"Keine aktuellen Einstiegssignale über Mindest-Konfidenz {min_conf:.2f} "
            f"gefunden ({skipped} Ticker ohne ausreichende Kurshistorie übersprungen)."
        )
        return

    # Vielversprechendste zuerst: Fundamentalscore (fehlende Daten = neutral
    # 50, damit ETFs/Krypto nicht künstlich nach unten rutschen) vor Konfidenz.
    signals.sort(
        key=lambda s: (s["fund_score"] if s["fund_score"] is not None else 50.0,
                        s["state"]["confidence"]),
        reverse=True,
    )
    st.success(f"{len(signals)} Einstiegssignal(e) gefunden ({skipped} übersprungen mangels Kurshistorie).")

    for sig in signals:
        wl_row, state, insider = sig["wl_row"], sig["state"], sig["insider"]
        ticker  = wl_row["ticker"]
        bullish = state["direction"] == "bullish"
        action  = "🟢 BUY" if bullish else "🔴 SHORT"
        strength_label, strength_note = SIGNAL_STRENGTH.get(state["state"], ("Signal", ""))

        bias = insider["bias"]
        if bias is None:
            insider_line = "➖ Keine Insider-Trades im Betrachtungszeitraum."
        elif bias == "mixed":
            insider_line = (
                f"🔶 Insider uneinheitlich ({insider['buy_count']} Käufe / "
                f"{insider['sell_count']} Verkäufe) — kein klares Zusatzsignal."
            )
        elif (bias == "bullish") == bullish:
            insider_line = (
                f"✅ Insider-Handel bestätigt die Richtung ({insider['buy_count']} Käufe / "
                f"{insider['sell_count']} Verkäufe)."
            )
        else:
            insider_line = (
                f"⚠️ Insider-Handel widerspricht der Richtung ({insider['buy_count']} Käufe / "
                f"{insider['sell_count']} Verkäufe)."
            )

        with st.container(border=True):
            st.markdown(
                f"### {action} — [{ticker}]({_chart_link(ticker)}) · {wl_row['name']}  "
                f"`Konfidenz {state['confidence']:.0%}`"
            )
            st.caption(f"📐 {_fundamental_badge(sig['fund_score'])}")
            st.markdown(
                f"**{STATE_LABELS.get(state['state'], state['state'])}** "
                f"({strength_label}) — {state['expectation_text']}"
            )
            if strength_note:
                st.caption(strength_note)
            if state["target_low"] is not None:
                st.caption(
                    f"Aktueller Kurs {state['current_price']:.2f} · "
                    f"Zielzone {state['target_low']:.2f} – {state['target_high']:.2f}"
                )
            st.markdown(insider_line)
            if not insider["trades"].empty:
                with st.expander(f"Insider-Trades {ticker} (letzte {insider_days} Tage)"):
                    st.dataframe(
                        insider["trades"][["trade_date", "source", "trader", "trade_type",
                                            "amount_min", "amount_max", "fund"]],
                        use_container_width=True, hide_index=True,
                    )


# ─── Tab: Vorhersage-Tracking ────────────────────────────────────────────────

def tab_wave_predictions():
    from src.analytics.elliott_wave import STATE_LABELS
    from src.analytics.wave_tracker import (
        evaluate_open_predictions, load_predictions, accuracy_stats, honest_decided,
        latest_expectations, expectation_trend, weekly_accuracy,
        CANONICAL_THRESHOLD_PCT, CANONICAL_LOOKBACK_DAYS,
        target_overshoot, overshoot_coverage, overshoot_hold_stats,
    )

    st.caption(
        "Historie aller vom Elliott-Wellen-Modul erkannten Kurserwartungen — damit sich "
        "nachträglich prüfen lässt, wie zuverlässig die Schätzungen sind. Ein Eintrag "
        "entsteht beim täglichen Scan (`python main.py signals`) oder bei einem Scan im "
        "Dashboard mit Standard-Parametern (Empfindlichkeit "
        f"{CANONICAL_THRESHOLD_PCT:.0f}%, Lookback {CANONICAL_LOOKBACK_DAYS} Tage) — andere "
        "Einstellungen liefern andere Pivots und würden die Statistik vermischen. "
        "Offene Vorhersagen werden bei jedem `main.py signals`/`all`-Lauf automatisch abgeglichen. "
        "Muster ohne Zielzone (z.B. 'möglicher neuer Impuls') können nicht als "
        "'Ziel erreicht' aufgelöst werden — bei diesen zeigt der Status 'abgelaufen', "
        "dass die erwartete Richtung bis zum Prüfhorizont zumindest nicht widerlegt wurde.\n\n"
        "🪙 Enthält auch Kryptowährungen, sobald sie im Tab 'Krypto Top 50' gescannt "
        "oder dort im Coin-Detail geöffnet wurden — dieselbe Tabelle, kein separates "
        "Krypto-Tracking."
    )

    if st.button("🔄 Offene Vorhersagen mit aktuellen Kursen abgleichen", type="primary", key="wp_eval_btn"):
        conn = get_connection()
        with st.spinner("Gleiche offene Vorhersagen gegen eingetroffene Kurse ab …"):
            summary = evaluate_open_predictions(conn)
        conn.close()
        st.success(
            f"{summary['checked']} offene Vorhersage(n) geprüft — "
            f"{summary['hit']} Ziel erreicht, {summary['invalidated']} invalidiert, "
            f"{summary['expired']} abgelaufen."
        )

    conn = get_connection()
    df = load_predictions(conn)
    conn.close()

    if df.empty:
        st.info(
            "Noch keine Vorhersagen aufgezeichnet — im Tab 'Kurschart + News' die "
            "Elliott-Wellen-Anzeige aktivieren oder den Wellen-Scanner laufen lassen."
        )
        return

    stats = accuracy_stats(df)
    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    c1.metric("Vorhersagen gesamt", stats["total"])
    c2.metric("Noch offen",         stats["open"])
    c3.metric("🎯 Ziel erreicht",   stats["hit"])
    c4.metric("❌ Invalidiert",     stats["invalidated"])
    c5.metric(
        "Trefferquote",
        f"{stats['hit_rate']*100:.0f} %" if stats["hit_rate"] is not None else "—",
        help=f"Nur entschiedene Fälle (n={stats['decided']}), ohne die {stats['in_zone']} "
             "Vorhersagen, bei denen der Kurs schon bei Erstellung in der Zielzone stand.",
    )
    c6.metric(
        "Zufalls-Basis",
        f"{stats['baseline']*100:.0f} %" if stats["baseline"] is not None else "—",
        help="Trefferquote, die ein reiner Zufallskurs bei denselben Abständen zu Zielzone "
             "und stützendem Pivot erreichen würde: Abstand_Stop / (Abstand_Stop + Abstand_Ziel). "
             "Liegt das Ziel nah und der Pivot weit weg, ist eine hohe Trefferquote auch "
             "ohne Vorhersagekraft zu erwarten.",
    )
    c7.metric(
        "Edge",
        f"{stats['edge']*100:+.1f} pp" if stats["edge"] is not None else "—",
        help="Trefferquote minus Zufalls-Basis — nur ein klar positiver Edge zeigt echte "
             "Vorhersagekraft des Wellenmodells.",
    )

    with st.expander("📊 Trefferquote nach Wellen-Zustand & Konfidenz"):
        decided = honest_decided(df).copy()
        decided["hit"] = (decided["status"] == "hit").astype(float)
        if decided.empty:
            st.caption("Noch keine entschiedenen Vorhersagen für eine Aufschlüsselung.")
        else:
            decided["Konfidenz-Klasse"] = pd.cut(
                decided["confidence"], bins=[0, 0.3, 0.5, 0.7, 1.01],
                labels=["<30%", "30–50%", "50–70%", "≥70%"],
            )
            def _hit_vs_base(grp) -> pd.DataFrame:
                out = pd.DataFrame({
                    "Anzahl":           grp["hit"].count(),
                    "Trefferquote %":   (grp["hit"].mean() * 100).round(0),
                    "Zufalls-Basis %":  (grp["baseline_prob"].mean() * 100).round(0),
                })
                out["Edge (pp)"] = (out["Trefferquote %"] - out["Zufalls-Basis %"]).round(0)
                return out

            by_state = _hit_vs_base(decided.groupby("state"))
            by_state.index = by_state.index.map(lambda s: STATE_LABELS.get(s, s))
            by_conf = _hit_vs_base(decided.groupby("Konfidenz-Klasse", observed=True))

            cc1, cc2 = st.columns(2)
            with cc1:
                st.caption("Nach Wellen-Zustand")
                st.dataframe(by_state, use_container_width=True)
            with cc2:
                st.caption("Nach Konfidenz-Klasse")
                st.dataframe(by_conf, use_container_width=True)

    with st.expander("📅 Trefferquote nach Kalenderwoche der Ersterkennung"):
        wk = weekly_accuracy(get_connection())
        if wk.empty:
            st.caption("Noch keine Vorhersagen für eine Wochen-Auswertung.")
        else:
            wk_display = wk.copy()
            wk_display["Trefferquote"] = wk_display["hit_rate"].map(
                lambda v: "—" if pd.isna(v) else f"{v*100:.0f} %"
            )
            wk_display["Edge"] = wk_display["edge"].map(
                lambda v: "—" if pd.isna(v) else f"{v*100:+.0f} pp"
            )
            wk_display = wk_display.rename(columns={
                "week": "Kalenderwoche", "total": "Vorhersagen",
                "open": "Offen", "hit": "Ziel erreicht", "invalidated": "Invalidiert",
            })
            st.dataframe(
                wk_display[["Kalenderwoche", "Vorhersagen", "Offen", "Ziel erreicht",
                             "Invalidiert", "Trefferquote", "Edge"]],
                use_container_width=True, hide_index=True,
            )
            decided_weeks = wk.dropna(subset=["hit_rate"])
            if len(decided_weeks) >= 2:
                st.line_chart(decided_weeks.set_index("week")["hit_rate"] * 100)

    with st.expander("📈 Lohnt sich Halten über die Zielzone hinaus? (Exit-Timing-Analyse)"):
        st.caption(
            "Für alle als 'Ziel erreicht' aufgelösten Vorhersagen: wie weit lief der Kurs "
            "nach dem ersten Berühren der Zielzone noch weiter, bevor er umkehrte (Schlusskurs "
            "fällt zurück hinter die zuerst berührte Zonengrenze) oder der Prüfhorizont endete — "
            "und was hätte ein Sofort-Exit beim ersten Kontakt gegenüber Halten bis zur Umkehr "
            "netto gebracht. Damit lässt sich beurteilen, ob es sich lohnt, eine Position über "
            "die Zielzone hinaus laufen zu lassen, statt sofort zu verkaufen."
        )
        ov_df = target_overshoot(get_connection())
        if ov_df.empty:
            st.caption("Noch keine als 'Ziel erreicht' aufgelösten Vorhersagen für diese Auswertung.")
        else:
            hold = overshoot_hold_stats(ov_df)
            oc1, oc2, oc3, oc4 = st.columns(4)
            oc1.metric("Ausgewertete Treffer", hold["n"])
            oc2.metric(
                "Ø Sofort-Exit",
                f"{hold['avg_immediate_pnl_pct']:+.1f} %" if hold["avg_immediate_pnl_pct"] is not None else "—",
            )
            oc3.metric(
                "Ø Halten bis Umkehr/Horizont",
                f"{hold['avg_hold_pnl_pct']:+.1f} %" if hold["avg_hold_pnl_pct"] is not None else "—",
            )
            oc4.metric(
                "Halten war besser in",
                f"{hold['share_hold_better']*100:.0f} % der Fälle" if hold["share_hold_better"] is not None else "—",
            )
            if hold["avg_diff_pct"] is not None and hold["avg_diff_pct"] < 0:
                st.warning(
                    f"Historisch hätte Halten im Schnitt **{hold['avg_diff_pct']:+.1f} Prozentpunkte** "
                    f"schlechter abgeschnitten als der Sofort-Exit (Median {hold['median_diff_pct']:+.1f} pp) — "
                    f"in {hold['share_hold_worse']*100:.0f}% der Fälle war Halten schlechter. Der Kurs kehrt "
                    "nach Zonen-Kontakt meist schnell wieder um, statt weiterzulaufen."
                )
            elif hold["avg_diff_pct"] is not None:
                st.success(
                    f"Historisch hätte Halten im Schnitt **{hold['avg_diff_pct']:+.1f} Prozentpunkte** "
                    f"besser abgeschnitten als der Sofort-Exit (Median {hold['median_diff_pct']:+.1f} pp) — "
                    f"in {hold['share_hold_better']*100:.0f}% der Fälle war Halten besser."
                )

            st.caption(
                "Wie oft lief der Kurs nach dem Treffer noch mindestens X % der Zonenbreite "
                "über die Zielzone hinaus, bevor er umkehrte:"
            )
            cov = overshoot_coverage(ov_df)
            cov_display = cov.copy()
            cov_display["Zusätzliche Distanz"] = cov_display["threshold_pct"].map(lambda t: f"≥ {t:.0f} % der Zonenbreite")
            cov_display["Anteil erreicht"] = cov_display["share"].map(lambda v: f"{v*100:.0f} %")
            st.dataframe(
                cov_display[["Zusätzliche Distanz", "Anteil erreicht", "count"]]
                    .rename(columns={"count": "Anzahl"}),
                use_container_width=True, hide_index=True,
            )
            st.bar_chart(cov.set_index("threshold_pct")["share"] * 100)

    with st.expander("📌 Aktuellste Erwartung je Ticker"):
        latest = latest_expectations(get_connection())
        if latest.empty:
            st.caption("Noch keine Snapshots vorhanden.")
        else:
            ld = latest.copy()
            wl = _load_watchlist()
            universe = tuple(sorted(wl[wl["asset_type"] == "stock"]["ticker"]))
            fund_scores = _load_fundamental_scores(universe)
            ld["fund_score"] = ld["ticker"].map(lambda t: _fund_score_for(fund_scores, t))
            # Vielversprechendste zuerst: Fundamentalscore vor Konfidenz. Fehlende
            # Fundamentaldaten (ETF/Index/Krypto) rutschen ans Ende statt nach oben,
            # da hier (anders als beim Vorfilter) keine bewusste Ausnahme gilt,
            # sondern nur eine Sortierpriorität.
            ld = ld.sort_values(["fund_score", "confidence"], ascending=False, na_position="last")
            ld["Fundamentalscore"] = ld["fund_score"].map(_fundamental_badge)
            ld["Muster"]    = ld["state"].map(lambda s: STATE_LABELS.get(s, s))
            ld["Richtung"]  = ld["direction"].map(lambda d: "🟢 Bullish" if d == "bullish" else "🔴 Bearish")
            ld["Konfidenz"] = ld["confidence"].map("{:.0%}".format)
            ld["Kurs (Stand)"] = ld.apply(
                lambda r: f"{r['current_price']:.2f} ({r['snapshot_date']})", axis=1,
            )
            ld["Zielzone"] = ld.apply(
                lambda r: f"{r['target_low']:.2f} – {r['target_high']:.2f}" if pd.notna(r["target_low"]) else "—",
                axis=1,
            )
            ld = ld.rename(columns={"ticker": "Ticker"})
            ld["Chart"] = ld["Ticker"].apply(_chart_link)
            st.dataframe(
                ld[["Ticker", "Muster", "Richtung", "Konfidenz", "Fundamentalscore",
                     "Kurs (Stand)", "Zielzone", "Chart"]],
                use_container_width=True, hide_index=True,
                column_config={
                    "Chart": st.column_config.LinkColumn("Chart", display_text="📈 Öffnen"),
                },
            )

    with st.expander("🔄 Erwartungsverlauf je Ticker (erste vs. aktuellste Einschätzung)"):
        conn = get_connection()
        snap_tickers = pd.read_sql(
            "SELECT DISTINCT ticker FROM wave_snapshots ORDER BY ticker", conn
        )["ticker"].tolist()
        conn.close()
        if not snap_tickers:
            st.caption("Noch keine Snapshots vorhanden.")
        else:
            trend_ticker = st.selectbox("Ticker", snap_tickers, key="wp_trend_ticker")
            trend = expectation_trend(get_connection(), trend_ticker)
            if trend.empty:
                st.caption("Kein Verlauf für diesen Ticker.")
            else:
                first, last = trend.iloc[0], trend.iloc[-1]
                tc1, tc2 = st.columns(2)
                with tc1:
                    st.markdown(f"**Erste Einschätzung** ({first['snapshot_date']})")
                    st.caption(
                        f"{STATE_LABELS.get(first['state'], first['state'])} · "
                        f"{'🟢 Bullish' if first['direction']=='bullish' else '🔴 Bearish'} · "
                        f"Konfidenz {first['confidence']*100:.0f}% · "
                        f"Zielzone "
                        + (f"{first['target_low']:.2f}–{first['target_high']:.2f}"
                           if pd.notna(first["target_low"]) else "—")
                    )
                with tc2:
                    st.markdown(f"**Aktuellste Einschätzung** ({last['snapshot_date']})")
                    st.caption(
                        f"{STATE_LABELS.get(last['state'], last['state'])} · "
                        f"{'🟢 Bullish' if last['direction']=='bullish' else '🔴 Bearish'} · "
                        f"Konfidenz {last['confidence']*100:.0f}% · "
                        f"Zielzone "
                        + (f"{last['target_low']:.2f}–{last['target_high']:.2f}"
                           if pd.notna(last["target_low"]) else "—")
                        + (f" · Status: {last['status']}" if pd.notna(last["status"]) else "")
                    )
                trend_display = trend.copy()
                trend_display["Muster"]    = trend_display["state"].map(lambda s: STATE_LABELS.get(s, s))
                trend_display["Richtung"]  = trend_display["direction"].map(
                    lambda d: "🟢 Bullish" if d == "bullish" else "🔴 Bearish"
                )
                trend_display["Konfidenz"] = trend_display["confidence"].map("{:.0%}".format)
                trend_display["Zielzone"] = trend_display.apply(
                    lambda r: f"{r['target_low']:.2f} – {r['target_high']:.2f}" if pd.notna(r["target_low"]) else "—",
                    axis=1,
                )
                trend_display = trend_display.rename(columns={
                    "snapshot_date": "Datum", "current_price": "Kurs",
                })
                st.dataframe(
                    trend_display[["Datum", "Muster", "Richtung", "Konfidenz", "Kurs", "Zielzone", "status"]]
                        .rename(columns={"status": "Status (Lineage)"}),
                    use_container_width=True, hide_index=True,
                )

    STATUS_ICON = {"open": "⏳ offen", "hit": "✅ Ziel erreicht",
                   "invalidated": "❌ invalidiert", "expired": "⌛ abgelaufen"}
    display = df.copy()
    display["Status"]    = display["status"].map(STATUS_ICON)
    display["Muster"]    = display["state"].map(lambda s: STATE_LABELS.get(s, s))
    display["Richtung"]  = display["direction"].map(lambda d: "🟢 Bullish" if d == "bullish" else "🔴 Bearish")
    display["Konfidenz"] = display["confidence"].map("{:.0%}".format)
    display["Kurs bei Erkennung"] = display["price_at_prediction"].map("{:.2f}".format)
    display["Zielzone"] = display.apply(
        lambda r: f"{r['target_low']:.2f} – {r['target_high']:.2f}" if pd.notna(r["target_low"]) else "—",
        axis=1,
    )
    display["Ergebnis"] = display.apply(
        lambda r: f"{r['resolved_price']:.2f} ({r['resolved_date']})" if pd.notna(r["resolved_date"]) else "—",
        axis=1,
    )
    display = display.rename(columns={"ticker": "Ticker", "created_date": "Erkannt am"})

    status_filter = st.multiselect(
        "Status filtern", ["open", "hit", "invalidated", "expired"],
        default=["open", "hit", "invalidated", "expired"],
        format_func=lambda s: STATUS_ICON.get(s, s), key="wp_status_filter",
    )
    display = display[df["status"].isin(status_filter).values]
    display["Chart"] = display["Ticker"].apply(_chart_link)

    st.dataframe(
        display[["Ticker", "Erkannt am", "Muster", "Richtung", "Konfidenz",
                  "Kurs bei Erkennung", "Zielzone", "Status", "Ergebnis", "Chart"]],
        use_container_width=True, hide_index=True,
        column_config={
            "Chart": st.column_config.LinkColumn("Chart", display_text="📈 Öffnen"),
        },
    )


# ─── Tab: Modellvergleich (Versionen der Signal-Regeln) ─────────────────────

# Feste Reihenfolge = feste Farbe je Version (Referenzpalette, Dark-Mode-Slots 1-3).
VERSION_COLORS = ["#3987e5", "#d95926", "#199e70", "#c98500"]


def _fmt_pct(v, digits=1, signed=False):
    if v is None or pd.isna(v):
        return "—"
    return f"{v*100:+.{digits}f} %" if signed else f"{v*100:.{digits}f} %"


def _fmt_pp(v):
    return "—" if v is None or pd.isna(v) else f"{v*100:+.1f} pp"


def _comparison_table(cmp: pd.DataFrame, stake_eur: float = 100.0) -> pd.DataFrame:
    out = pd.DataFrame({
        "Version":            cmp["label"],
        "Zeitraum":           cmp["period"],
        "Signale":            cmp["n"],
        "Entschieden":        cmp["decided"],
        "Trefferquote":       cmp["hit_rate"].map(_fmt_pct),
        "Zufalls-Basis":      cmp["baseline"].map(_fmt_pct),
        "Edge":               cmp["edge"].map(_fmt_pp),
        "Ø Ergebnis/Signal":  cmp["avg_result"].map(lambda v: _fmt_pct(v, 2, True)),
        "20T-Überrendite":    cmp["excess20"].map(lambda v: _fmt_pct(v, 2, True)),
        "t-Wert (20T)":       cmp["excess20_t"].map(lambda v: "—" if v is None or pd.isna(v) else f"{v:.1f}"),
        "Einsatz":            (cmp["stake"] * stake_eur).map(lambda v: f"{v:,.0f} €".replace(",", ".")),
        "Ergebnis je Euro":   cmp["w_result"].map(lambda v: _fmt_pct(v, 2, True)),
        "Gewinn":             (cmp["pnl_units"] * stake_eur).map(lambda v: f"{v:+,.0f} €".replace(",", ".")),
    })
    if "w_result_net" in cmp.columns and cmp["w_result_net"].notna().any():
        out["Netto je Euro"] = cmp["w_result_net"].map(lambda v: _fmt_pct(v, 2, True)).to_numpy()
        out["Gewinn nach Kosten"] = cmp["pnl_net_eur"].map(_fmt_eur).to_numpy()
        out["Break-even-Einsatz"] = cmp["breakeven_stake"].map(
            lambda v: "nie" if v is None or pd.isna(v) else _fmt_eur(v, signed=False)).to_numpy()
    return out


def _fmt_eur(v, signed=True):
    if v is None or pd.isna(v):
        return "—"
    return (f"{v:+,.0f} €" if signed else f"{v:,.0f} €").replace(",", ".")


def _fee_controls(key: str):
    """Gebührenmodell und Standardeinsatz je Signal wählen (Netto-Kennzahlen)."""
    from src.analytics.model_versions import FEE_PRESETS, FeeModel
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    preset = c1.selectbox("Ordergebühren", list(FEE_PRESETS), index=1, key=f"{key}_fee",
                          format_func=lambda k: FEE_PRESETS[k].label)
    base = FEE_PRESETS[preset]
    stake = c2.number_input("Einsatz je Signal (€)", 50, 100_000, 1000, step=50, key=f"{key}_stake")
    per_order = c3.number_input("Gebühr je Order (€)", 0.0, 50.0, float(base.per_order_eur), step=0.5,
                                key=f"{key}_fee_eur_{preset}")
    slip = c4.number_input("Spread je Seite (%)", 0.0, 2.0, base.slippage_pct * 100, step=0.05,
                           key=f"{key}_slip_{preset}")
    fees = FeeModel(base.label, per_order_eur=per_order, pct=base.pct, min_eur=base.min_eur,
                    slippage_pct=slip / 100)
    st.caption(f"Kosten für Kauf + Verkauf bei {stake:,.0f} €: **{fees.cost_fraction(stake)*100:.2f} %** "
               "des Einsatzes. Steuern sind nicht enthalten.".replace(",", "."))
    return fees, float(stake)


def _version_delta_tiles(cmp: pd.DataFrame, period: str, base_key: str, new_key: str) -> None:
    from src.analytics.model_versions import VERSION_BY_KEY
    base = cmp[(cmp["version"] == base_key) & (cmp["period"] == period)].iloc[0]
    new  = cmp[(cmp["version"] == new_key) & (cmp["period"] == period)].iloc[0]

    def delta(col):
        if base[col] is None or new[col] is None or pd.isna(base[col]) or pd.isna(new[col]):
            return None
        return new[col] - base[col]

    st.markdown(f"**{VERSION_BY_KEY[new_key].label}** gegenüber **{VERSION_BY_KEY[base_key].label}** — {period}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Signale", f"{int(new['n']):,}".replace(",", "."),
              delta=f"{int(new['n'] - base['n']):+,}".replace(",", "."), delta_color="off")
    d = delta("edge")
    c2.metric("Edge", _fmt_pp(new["edge"]), delta=None if d is None else f"{d*100:+.1f} pp")
    d = delta("avg_result")
    c3.metric("Ø Ergebnis/Signal", _fmt_pct(new["avg_result"], 2, True),
              delta=None if d is None else f"{d*100:+.2f} pp")
    d = delta("excess20")
    c4.metric("20T-Überrendite", _fmt_pct(new["excess20"], 2, True),
              delta=None if d is None else f"{d*100:+.2f} pp")


def _bear_weighting_section(replay: pd.DataFrame, cmp: pd.DataFrame, period: str,
                            phases: list, universe: str) -> None:
    """v3 (Standard-Einsatz) gegen v4 (doppelter Einsatz im Bärenmarkt) plus
    die vorab festgelegte Entscheidungsregel (model_versions.v4_decision)."""
    from src.analytics.model_versions import (
        VERSION_BY_KEY, ACTIVE_VERSION, v4_decision, phase_table, yearly_return_per_euro,
    )

    st.markdown("#### ⚖️ Erhöhter Einsatz im Bärenmarkt: v4 gegen v3")
    st.caption(
        "Beide Versionen handeln exakt dieselben Signale — v4 setzt nur 200 € statt 100 €, wenn "
        "der S&P 500 beim Signal unter seiner 200-Tage-Linie liegt. Entscheidend ist deshalb das "
        "**Ergebnis je eingesetztem Euro**: steigt es, waren die Bärenmarkt-Signale besser als der Rest."
    )
    v3 = cmp[(cmp["version"] == "v3") & (cmp["period"] == period)].iloc[0]
    v4 = cmp[(cmp["version"] == "v4") & (cmp["period"] == period)].iloc[0]
    c1, c2, c3 = st.columns(3)
    c1.metric("Einsatz v4", f"{v4['stake']*100:,.0f} €".replace(",", "."),
              delta=f"{(v4['stake'] - v3['stake'])*100:+,.0f} € ggü. v3".replace(",", "."), delta_color="off")
    d = None if v3["w_result"] is None or v4["w_result"] is None else v4["w_result"] - v3["w_result"]
    c2.metric("Ergebnis je Euro v4", _fmt_pct(v4["w_result"], 2, True),
              delta=None if d is None else f"{d*100:+.2f} pp ggü. v3")
    c3.metric("Gewinn v4", f"{v4['pnl_units']*100:+,.0f} €".replace(",", "."),
              delta=f"{(v4['pnl_units'] - v3['pnl_units'])*100:+,.0f} € ggü. v3".replace(",", "."))

    yr = yearly_return_per_euro(replay)
    if not yr.empty:
        with st.expander("Ergebnis je Euro je Jahr"):
            st.dataframe(yr.map(lambda v: _fmt_pct(v, 2, True)), use_container_width=True)

    pt = phase_table(replay, phases)
    dec = v4_decision(replay, phases)
    with st.expander(f"Bärenphasen des S&P 500 ({len(phases)}) — Long-Signale im Bärenmarkt"):
        if pt.empty:
            st.info("Keine Bärenphasen im Datenzeitraum.")
        else:
            st.dataframe(pd.DataFrame({
                "Von": pt["start"], "Bis": pt["end"], "Signale": pt["n"], "Entschieden": pt["decided"],
                "Edge": pt["edge"].map(_fmt_pp),
                "Ø Ergebnis/Signal": pt["avg_result"].map(lambda v: _fmt_pct(v, 2, True)),
                "Gewertet": pt["counted"].map(lambda b: "ja" if b else "nein (zu wenige Signale)"),
            }), use_container_width=True, hide_index=True)

    if universe != "test":
        st.caption("Die Entscheidung über v4 fällt nur auf dem **Test-Universum** — auf der Watchlist "
                   "wurde der Bärenmarkt-Effekt gefunden, dort ist er kein unabhängiger Beleg.")
        return
    mark = lambda ok: "✅" if ok else "❌"
    verdict = ("**bestanden** — v4 kann aktiviert werden" if dec["passed"]
               else "**nicht bestanden** — v4 bleibt im Vergleich, aber inaktiv")
    st.markdown(
        f"**Vorab festgelegte Entscheidungsregel (Test-Universum, gesamter Zeitraum):** {verdict}  \n"
        f"{mark(dec['edge_ok'])} Edge im Bärenmarkt positiv in {dec['n_positive']} von "
        f"{dec['n_counted']} gewerteten Bärenphasen (Mehrheit nötig)  \n"
        f"{mark(dec['return_ok'])} Ergebnis je Euro v4 {_fmt_pct(dec['w_result_v4'], 2, True)} gegen "
        f"v3 {_fmt_pct(dec['w_result_v3'], 2, True)} (v4 muss höher sein)  \n"
        f"Aktive Version: **{VERSION_BY_KEY[ACTIVE_VERSION].label}**"
    )


def _exit_grid_section(fees, stake_eur: float) -> None:
    """TP/SL-Raster aus `python main.py exits` (Tabelle exit_grid), netto
    neu bewertet mit der oben gewählten Gebühr."""
    from src.analytics.exit_rules import CURRENT_RULE
    st.markdown("#### 🎯 Ausstiegsregeln: Take-Profit / Stop-Loss im Vergleich")
    conn = get_connection()
    try:
        grid = pd.read_sql("SELECT * FROM exit_grid", conn)
    except Exception:
        grid = pd.DataFrame()
    conn.close()
    if grid.empty:
        st.info("Noch kein Raster vorhanden — `python main.py exits` ausführen (dauert einige Minuten).")
        return
    st.caption(
        f"Stand {grid['run_date'].iloc[0]} · Signale der aktiven Version, Einstieg zum Eröffnungskurs am "
        "Tag nach dem Signal, TP/SL über Tageshoch/-tief (berühren beide denselben Tag, zählt der SL), "
        "sonst Verkauf nach 120 Tagen. **Vorsprung** = Brutto-Ergebnis minus gleichgewichteter Kauf "
        "aller Aktien des Universums über dieselbe Haltedauer — nur er zeigt, ob die Regel mehr bringt "
        "als der allgemeine Kursanstieg. **p.a.** gilt nur, wenn das Kapital ständig voll investiert "
        "wäre. Die heutige Regel ist fett markiert."
    )
    c1, c2, c3 = st.columns(3)
    uni = c1.radio("Aktien", ["test", "watchlist"], horizontal=True, key="eg_uni",
                   format_func=lambda u: {"test": "Test-Universum", "watchlist": "Watchlist"}[u])
    period = c2.radio("Zeitraum", ["Training", "Test", "Gesamt"], index=1, horizontal=True, key="eg_period")
    state = c3.selectbox("Wellen-Zustand", ["alle", "wave3_forming", "wave5_forming", "correction_forming"],
                         key="eg_state", format_func=lambda s: {
                             "alle": "alle", "wave3_forming": "nur Welle 3", "wave5_forming": "nur Welle 5",
                             "correction_forming": "nur Korrektur"}[s])
    g = grid[(grid["universe"] == uni) & (grid["period"] == period) & (grid["state"] == state)].copy()
    cost = fees.cost_fraction(stake_eur)
    g["net_sel"] = g["gross"] - cost
    g["pa_sel"] = g["net_sel"] * 365 / g["avg_days"]
    g = g.sort_values("pa_sel", ascending=False)
    table = pd.DataFrame({
        "Regel":             g["label"] + (g["rule"] == CURRENT_RULE).map({True: "  ← heute", False: ""}),
        "Trades":            g["trades"],
        "Gewinn-Trades":     g["win_rate"].map(_fmt_pct),
        "Ø Haltedauer":      g["avg_days"].map(lambda v: f"{v:.0f} T"),
        "Brutto je Trade":   g["gross"].map(lambda v: _fmt_pct(v, 2, True)),
        "Netto je Trade":    g["net_sel"].map(lambda v: _fmt_pct(v, 2, True)),
        "Vorsprung":         g["excess"].map(lambda v: _fmt_pct(v, 2, True)),
        "p.a. netto":        g["pa_sel"].map(lambda v: _fmt_pct(v, 1, True)),
        "Break-even-Einsatz": g["gross"].map(lambda v: (lambda b: "nie" if b is None else _fmt_eur(b, False))(
            fees.breakeven_stake(v))),
    })
    st.dataframe(table.style.apply(
        lambda r: ["font-weight: bold" if "← heute" in r["Regel"] else "" for _ in r], axis=1),
        use_container_width=True, hide_index=True, height=420)


def tab_model_comparison():
    from src.analytics.model_versions import (
        VERSIONS, VERSION_BY_KEY, ACTIVE_VERSION, TRAIN_END_YEAR,
        compare_versions, replay_periods, yearly_edge, live_signal_frame,
        bear_phases, market_closes,
    )
    from src.analytics.model_replay import load_replay
    from src.analytics.wave_tracker import paper_trades, paper_trade_stats

    st.caption(
        "Die Wellenerkennung ist für alle Versionen gleich — eine **Modellversion** legt nur fest, "
        "welche erkannten Signale als Einstieg gelten. Deshalb werden alle Versionen auf exakt "
        "denselben Signalen gemessen: in der Rückrechnung über ~10 Jahre und auf den live "
        "protokollierten Vorhersagen. Bestehende Versionen werden nie verändert, neue nur angehängt."
    )

    st.dataframe(pd.DataFrame([{
        "Version": v.label + (" ✅ aktiv" if v.key == ACTIVE_VERSION else ""),
        "Seit": v.introduced,
        "Regel": v.rule_text,
        "Einsatz": v.stake_text,
        "Begründung": v.rationale,
    } for v in VERSIONS]), use_container_width=True, hide_index=True)

    with st.expander("ℹ️ Was bedeuten die Kennzahlen?"):
        st.markdown(
            "- **Trefferquote**: Anteil der Signale, bei denen der Kurs die Zielzone erreicht, bevor er "
            "den stützenden Wellen-Pivot unterschreitet (nur entschiedene Fälle, ohne Signale, die schon "
            "in der Zielzone starteten).\n"
            "- **Zufalls-Basis**: Trefferquote, die ein reiner Zufallskurs bei denselben Abständen zu Ziel "
            "und Pivot erreichen würde.\n"
            "- **Edge** = Trefferquote − Zufalls-Basis: der Teil der Trefferquote, der echte "
            "Vorhersagekraft ist.\n"
            "- **Ø Ergebnis/Signal**: Treffer zählen den Abstand bis zur Zielzone als Gewinn, invalidierte "
            "Signale den Abstand bis zum Pivot als Verlust (ohne Gebühren).\n"
            "- **Netto je Euro / Gewinn nach Kosten**: dasselbe nach Ordergebühren und Spread für Kauf und "
            "Verkauf (Auswahl oberhalb der Tabelle). **Break-even-Einsatz**: kleinster Einsatz je "
            "Trade, ab dem die Kosten gedeckt sind.\n"
            "- **20T-Überrendite**: Rendite der folgenden 20 Handelstage in Signalrichtung minus "
            "Durchschnitt aller Titel im selben Zeitraum. **t-Wert** ab etwa 2 = kaum noch Zufall.\n"
            "- **Einsatz / Ergebnis je Euro / Gewinn**: wie Ø Ergebnis, aber mit dem Einsatz der "
            "Version gewichtet (gewählter Einsatz je Signal, bei v4 doppelt im Bärenmarkt). Nur so lassen sich "
            "Versionen mit unterschiedlichem Einsatz fair vergleichen."
        )

    # ── Rückrechnung ────────────────────────────────────────────────────
    st.subheader("📚 Rückrechnung über die gesamte Kurshistorie")
    universe = st.radio(
        "Aktien", ["watchlist", "test"], horizontal=True, key="mc_universe",
        format_func=lambda u: {"watchlist": "Watchlist",
                               "test": "Test-Universum (unberührt, nicht auf der Watchlist)"}[u],
    )
    replay_cmd = "python main.py replay" + (" test" if universe == "test" else "")
    conn = get_connection()
    replay = load_replay(conn, universe)
    phases = bear_phases(market_closes(conn))
    conn.close()
    if universe == "test":
        st.caption(
            "~450 S&P-500- und europäische Standardwerte, die nicht auf der Watchlist stehen und bei "
            "der Suche nach den Regeln nie angesehen wurden (Liste: src/analytics/test_universe.py). "
            "Kurse einmalig laden mit `python main.py testdata`."
        )
    if replay.empty:
        st.info(f"Noch keine Rückrechnung vorhanden — `{replay_cmd}` ausführen (dauert einige Minuten).")
    else:
        st.caption(
            f"Stand {replay['run_date'].iloc[0]} · {len(replay):,} erkannte Signale "
            f"({replay['created_date'].min()} – {replay['created_date'].max()}) · Aktualisieren mit "
            f"`{replay_cmd}`. Die Regeln von v3 wurden nur auf Signalen bis {TRAIN_END_YEAR} "
            f"ausgewählt — der **Test-Zeitraum** ab {TRAIN_END_YEAR + 1} ist für sie ungesehene Daten "
            "und damit der ehrlichste Vergleich.".replace(",", ".")
        )
        fees, stake_eur = _fee_controls("mc")
        cmp = compare_versions(replay, replay_periods(replay), fees, stake_eur)
        periods = list(dict.fromkeys(cmp["period"]))
        period = st.radio("Zeitraum", periods, index=1, horizontal=True, key="mc_period")
        _version_delta_tiles(cmp, period, "v1", ACTIVE_VERSION)
        st.dataframe(_comparison_table(cmp[cmp["period"] == period], stake_eur),
                     use_container_width=True, hide_index=True)
        with st.expander("Alle Zeiträume"):
            st.dataframe(_comparison_table(cmp, stake_eur), use_container_width=True, hide_index=True)

        _bear_weighting_section(replay, cmp, period, phases, universe)
        _exit_grid_section(fees, stake_eur)

        ye = yearly_edge(replay)
        if not ye.empty:
            st.markdown("**Edge je Jahr** — ist der Unterschied stabil oder stammt er aus einzelnen Jahren?")
            fig = go.Figure()
            for color, col in zip(VERSION_COLORS, ye.columns):
                fig.add_trace(go.Scatter(
                    x=ye.index, y=ye[col] * 100, name=col, mode="lines+markers",
                    line=dict(color=color, width=2), marker=dict(size=8),
                    hovertemplate="%{x}: %{y:+.1f} pp<extra>" + col + "</extra>",
                ))
            fig.add_hline(y=0, line=dict(color="rgba(230,233,240,0.35)", width=1))
            fig.add_vline(x=str(TRAIN_END_YEAR) , line=dict(color="rgba(230,233,240,0.25)", width=1, dash="dot"))
            fig.add_annotation(x=str(TRAIN_END_YEAR), y=1, yref="paper", text="← Training | Test →",
                               showarrow=False, font=dict(size=11, color="#9AA3B2"), yanchor="bottom")
            fig.update_layout(
                height=340, margin=dict(l=10, r=10, t=30, b=10), hovermode="x unified",
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#E6E9F0"), legend=dict(orientation="h", y=-0.15),
                yaxis=dict(title="Edge (Prozentpunkte)", gridcolor="rgba(255,255,255,0.06)", zeroline=False),
                xaxis=dict(type="category", gridcolor="rgba(0,0,0,0)"),
            )
            st.plotly_chart(fig, use_container_width=True)
            with st.expander("Tabelle: Edge je Jahr"):
                st.dataframe(ye.map(_fmt_pp), use_container_width=True)

    # ── Live ────────────────────────────────────────────────────────────
    st.subheader("📡 Live-Vorhersagen: vorher vs. nachher")
    conn = get_connection()
    live = live_signal_frame(conn)
    conn.close()
    if live.empty:
        st.info("Noch keine protokollierten Vorhersagen.")
    else:
        switch = VERSION_BY_KEY[ACTIVE_VERSION].introduced
        live_periods = {
            f"Vor Umstellung (bis {switch})": live["created_date"] < switch,
            f"Nach Umstellung (ab {switch})": live["created_date"] >= switch,
            "Gesamt": pd.Series(True, index=live.index),
        }
        st.caption(
            "Alle Versionen auf denselben, seit Juli 2026 täglich protokollierten Vorhersagen — "
            "auch rückwirkend, denn jede Vorhersage speichert ihre Merkmale zum Erkennungszeitpunkt. "
            f"'Nach Umstellung' enthält nur Vorhersagen ab {switch} und wächst mit jedem Lauf. "
            "Live-Zahlen beruhen auf wenigen Monaten und schwanken stark; die 20T-Überrendite "
            "erscheint erst 20 Handelstage nach dem Signal."
        )
        lcmp = compare_versions(live, live_periods)
        lperiod = st.radio("Zeitraum", list(live_periods), index=2, horizontal=True, key="mc_live_period")
        _version_delta_tiles(lcmp, lperiod, "v1", ACTIVE_VERSION)
        st.dataframe(_comparison_table(lcmp[lcmp["period"] == lperiod]), use_container_width=True, hide_index=True)

    # ── Papier-Trades ───────────────────────────────────────────────────
    st.subheader("💶 Papier-Trades je Version")
    st.caption("Handelsergebnis mit Stop-Loss/Trailing-Stop wie im Tab 'Papier-Trades' "
               "(je Signal 100 €, bei v4 200 € im Bärenmarkt, ohne Gebühren), gleicher Zeitraum "
               "für alle Versionen. Bei ungleichem Einsatz zählt **P&L je Euro**.")
    conn = get_connection()
    pt_rows = []
    for v in VERSIONS:
        stats = paper_trade_stats(paper_trades(conn, stake_eur=100.0, version=v.key))
        pt_rows.append({
            "Version":            v.label,
            "Orders":             stats["total"],
            "Geschlossen":        stats["closed"],
            "Offen":              stats["open"],
            "Trefferquote":       _fmt_pct(stats["win_rate"], 0),
            "Einsatz (geschl.)":  f"{stats['total_stake_eur']:,.0f} €".replace(",", "."),
            "P&L realisiert":     f"{stats['total_pnl_eur']:+.2f} €" if stats["closed"] else "—",
            "P&L je Euro":        _fmt_pct(stats["pnl_per_eur"], 2, True),
            "Ø P&L je Trade":     f"{stats['avg_pnl_eur']:+.2f} €" if stats["closed"] else "—",
        })
    conn.close()
    st.dataframe(pd.DataFrame(pt_rows), use_container_width=True, hide_index=True)


# ─── Tab: Papier-Trades (Order/Sell-Statistik) ──────────────────────────────

def _render_paper_trades_results(df: pd.DataFrame, key_prefix: str, empty_msg: str) -> None:
    """Gemeinsame Kennzahlen-/Tabellen-Darstellung einer paper_trades()-Historie
    — genutzt von tab_paper_trades() und tab_paper_trades_insider(), damit
    beide exakt dieselbe Kauf-/Verkaufslogik-Auswertung zeigen, nur für
    unterschiedliche Ticker-Mengen. key_prefix hält die Streamlit-Widget-Keys
    der beiden Tabs auseinander."""
    from src.analytics.wave_tracker import paper_trade_stats, EXIT_REASON_LABELS
    from src.analytics.elliott_wave import STATE_LABELS

    if df.empty:
        st.info(empty_msg)
        return

    stats = paper_trade_stats(df)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Orders gesamt", stats["total"])
    c2.metric("Noch offen", stats["open"])
    c3.metric("Geschlossen", stats["closed"])
    c4.metric(
        "Trefferquote",
        f"{stats['win_rate']*100:.0f} %" if stats["win_rate"] is not None else "—",
    )
    c5.metric(
        "P&L gesamt (realisiert)",
        f"{stats['total_pnl_eur']:+.2f} €" if stats["closed"] else "—",
    )
    if stats["closed"]:
        c6, c7 = st.columns(2)
        c6.metric("Ø P&L je Trade", f"{stats['avg_pnl_eur']:+.2f} €")
        c7.metric("Ø P&L in %", f"{stats['avg_pnl_pct']*100:+.1f} %")

    display = df.copy()
    display["Richtung"] = display["direction"].map(lambda d: "🟢 Long" if d == "Long" else "🔴 Short")
    display["Muster"]   = display["state"].map(lambda s: STATE_LABELS.get(s, s))
    display["Order"] = display.apply(
        lambda r: f"{r['order_date']} · {r['entry_price']:.2f} · {r['stake_eur']:.0f} € ({r['shares']:.4f} Stk.)",
        axis=1,
    )
    display["Verkaufszone"] = display.apply(
        lambda r: f"{r['sell_zone_low']:.2f} – {r['sell_zone_high']:.2f}", axis=1,
    )
    display["Status"] = display["exit_reason"].map(
        lambda s: EXIT_REASON_LABELS.get(s, s) if pd.notna(s) else "⏳ offen (unrealisiert)"
    )
    display["Sell"] = display.apply(
        lambda r: f"{r['exit_date']} · {r['exit_price']:.2f}"
                  if r["realized"] and pd.notna(r["exit_price"]) else "—",
        axis=1,
    )
    display["P&L"] = display.apply(
        lambda r: "—" if pd.isna(r["pnl_eur"])
                  else f"{r['pnl_eur']:+.2f} € ({r['pnl_pct']*100:+.1f} %)"
                       + ("" if r["realized"] else " · unrealisiert"),
        axis=1,
    )
    display["Konfidenz"] = display["confidence"].map("{:.0%}".format)
    display = display.rename(columns={"ticker": "Ticker"})
    display["Chart"] = display["Ticker"].apply(_chart_link)

    has_fund_col = "fundamental_score" in df.columns
    if has_fund_col:
        display["Fundamentalscore"] = df["fundamental_score"].map(_fundamental_badge)

    filter_options = ["offen"] + list(EXIT_REASON_LABELS.keys())
    status_filter = st.multiselect(
        "Status filtern", filter_options,
        default=filter_options,
        format_func=lambda s: "⏳ offen (unrealisiert)" if s == "offen" else EXIT_REASON_LABELS.get(s, s),
        key=f"{key_prefix}_status_filter",
    )
    reason_key = df["exit_reason"].fillna("offen")
    display = display[reason_key.isin(status_filter).values]

    cols = ["Ticker", "Richtung", "Muster", "Order", "Verkaufszone", "Status", "Sell", "P&L", "Konfidenz"]
    if has_fund_col:
        cols.append("Fundamentalscore")
    cols.append("Chart")
    st.dataframe(
        display[cols],
        use_container_width=True, hide_index=True,
        column_config={
            "Chart": st.column_config.LinkColumn("Chart", display_text="📈 Öffnen"),
        },
    )


def tab_paper_trades():
    from src.analytics.wave_tracker import (
        paper_trades, paper_trade_stats, PAPER_TRADES_START_DATE,
        WAVE3_ENTRY_MAX_WAIT_DAYS, target_overshoot, overshoot_hold_stats,
        STOP_RULES, DEFAULT_STOP_RULE,
    )
    from src.analytics.model_versions import VERSIONS, VERSION_BY_KEY, ACTIVE_VERSION

    keys = [v.key for v in VERSIONS]
    version = st.selectbox(
        "Modellversion", keys, index=keys.index(ACTIVE_VERSION),
        format_func=lambda k: VERSION_BY_KEY[k].label + (" (aktiv)" if k == ACTIVE_VERSION else ""),
        key="pt_version",
        help="Dieselben protokollierten Vorhersagen, gefiltert nach den Regeln der gewählten "
             "Version — so lassen sich die Handelsergebnisse der Versionen direkt vergleichen "
             "(Übersicht im Tab 'Modellvergleich').",
    )
    st.caption(f"**Regel {VERSION_BY_KEY[version].label}:** {VERSION_BY_KEY[version].rule_text}")

    rules = list(STOP_RULES)
    stop_rule = st.radio(
        "Stop-Regel", rules, index=rules.index(DEFAULT_STOP_RULE), horizontal=True,
        format_func=lambda k: STOP_RULES[k] + (" (Standard)" if k == DEFAULT_STOP_RULE else ""),
        key="pt_stop_rule",
        help="Gleiche Signale und Einstiege, nur die Positionsführung unterscheidet sich. "
             "Die Beschreibung unten gilt für Regel A.",
    )

    st.markdown("**Stop-Regeln im Vergleich** (gewählte Version, je Signal 100 €)")
    conn = get_connection()
    cmp_rows = []
    for k, label in STOP_RULES.items():
        d = paper_trades(conn, stake_eur=100.0, version=version, stop_rule=k)
        if d.empty:
            continue
        st_ = paper_trade_stats(d)
        filled = d[d["filled"] == True]
        closed = filled[filled["realized"]]
        stops = closed[closed["exit_reason"].astype(str).str.startswith("stop")]
        unreal = filled.loc[~filled["realized"], "pnl_eur"].sum()
        cmp_rows.append({
            "Stop-Regel":       label,
            "Geschlossen":      st_["closed"],
            "Offen":            st_["open"],
            "🎯 Zielzone":       int((closed["exit_reason"] == "target").sum()),
            "Stops im Minus":   int((stops["pnl_eur"] <= 0).sum()),
            "Stops im Plus":    int((stops["pnl_eur"] > 0).sum()),
            "Trefferquote":     _fmt_pct(st_["win_rate"], 0),
            "P&L realisiert":   f"{st_['total_pnl_eur']:+.2f} €",
            "P&L unrealisiert": f"{unreal:+.2f} €",
            "P&L gesamt":       f"{st_['total_pnl_eur'] + unreal:+.2f} €",
        })
    conn.close()
    if cmp_rows:
        st.dataframe(pd.DataFrame(cmp_rows), use_container_width=True, hide_index=True)

    st.caption(
        "Rein statistische Auswertung — kein echtes Trading: Sobald das "
        "Elliott-Wellen-Modul ein Signal meldet, das die gewählte Modellversion als "
        "Einstieg wertet (Regel oben), wird hier automatisch eine "
        "virtuelle **Order zu 100 €** gebucht — mit automatischer "
        "Positionsführung: Stop-Loss auf dem **stützenden Wellen-Pivot** "
        "(Welle-2- bzw. Welle-4-Tief, bei Korrekturen das Impulsende), mit kleinem Puffer gegen Kursdochte — bricht der "
        "Kurs dort durch, ist die Wellenzählung ohnehin hinfällig —, sobald "
        "die Position **+2% im Gewinn** ist wird der Stop einmalig auf "
        "**+1% Gewinn** nachgezogen (nie weiter als der strukturelle Stop) "
        "und bleibt dort fix. Alternativer Exit: die von den Elliott-Wellen "
        "berechnete Verkaufszone wird erreicht ('Sell'). So lässt sich "
        "messen, wie zuverlässig die Signale inklusive Risikomanagement in "
        "der Praxis wären.\n\n"
        "Welle 5 und Korrekturen steigen sofort zum aktuellen Kurs ein. "
        "Bei **'Welle 3 in Bildung'** wird nicht sofort zum aktuellen Kurs eingestiegen, sondern eine "
        "**Limit-Order auf ein 35%-Retracement der 0-1-Bewegung** platziert "
        "(Einstieg in die 2-3-Bewegung zu einem günstigeren Kurs, näher am "
        f"Stop). Kommt der Kurs innerhalb von {WAVE3_ENTRY_MAX_WAIT_DAYS} "
        "Handelstagen nicht dorthin zurück oder fällt der Kurs vorher unter "
        "den strukturellen Stop, gilt die Order als **🚫 nicht gefüllt** — "
        "kein Trade, kein Verlust, zählt aber separat mit.\n\n"
        f"Berücksichtigt werden nur Signale ab dem **{PAPER_TRADES_START_DATE}** "
        "(fester Stichtag, keine rückwirkende Bewertung älterer Kursverläufe "
        "aus der Zeit vor diesem Feature).\n\n"
        "🪙 Enthält auch Kryptowährungen, sobald sie im Tab 'Krypto Top 50' gescannt "
        "oder dort im Coin-Detail geöffnet wurden — Fundamentalscore bleibt für Coins "
        "immer 'n/a' (keine auswertbaren Bilanzdaten)."
    )

    with st.expander("❓ Warum wird sofort bei Zielzonen-Kontakt verkauft (statt weiter zu halten)?"):
        st.caption(
            "Der Sofort-Exit ist bewusst so gewählt — eine Auswertung aller bisher als "
            "'Ziel erreicht' aufgelösten Vorhersagen (Tab 'Vorhersagen' → Exit-Timing-Analyse) "
            "zeigt, wie sich Halten über den ersten Zonen-Kontakt hinaus historisch geschlagen hätte:"
        )
        ov_df = target_overshoot(get_connection())
        hold = overshoot_hold_stats(ov_df)
        if hold["n"] == 0:
            st.caption("Noch keine ausgewerteten Treffer.")
        else:
            oc1, oc2, oc3 = st.columns(3)
            oc1.metric("Ausgewertete Treffer", hold["n"])
            oc2.metric("Ø Sofort-Exit", f"{hold['avg_immediate_pnl_pct']:+.1f} %")
            oc3.metric("Ø Halten bis Umkehr/Horizont", f"{hold['avg_hold_pnl_pct']:+.1f} %")
            st.caption(
                f"Halten war nur in {hold['share_hold_better']*100:.0f}% der Fälle besser als der "
                f"Sofort-Exit (Ø {hold['avg_diff_pct']:+.1f} Prozentpunkte Unterschied) — Details und "
                "Aufschlüsselung nach zusätzlicher Distanz über die Zielzone hinaus im Tab 'Vorhersagen'."
            )

    conn = get_connection()
    df = paper_trades(conn, stake_eur=100.0, version=version, stop_rule=stop_rule)
    conn.close()

    if not df.empty:
        wl = _load_watchlist()
        universe = tuple(sorted(wl[wl["asset_type"] == "stock"]["ticker"]))
        fund_scores = _load_fundamental_scores(universe)
        df["fundamental_score"] = df["ticker"].map(lambda t: _fund_score_for(fund_scores, t))
        df = df.sort_values("fundamental_score", ascending=False, na_position="last")

    _render_paper_trades_results(
        df, key_prefix="pt",
        empty_msg=(
            "Noch keine auswertbaren Signale — dazu braucht es erkannte "
            "Erwartungen mit berechneter Zielzone. 'Möglicher neuer Impuls' "
            "zählt nicht mit, da dort keine Verkaufszone berechnet wird."
        ),
    )


# ─── Tab: Insider-Papier-Trades (Elliott-Wellen nur auf Insider-Ticker) ─────

def tab_paper_trades_insider():
    from src.analytics.wave_tracker import (
        paper_trades, record_snapshot, INSIDER_LOOKBACK_DAYS, PAPER_TRADES_MIN_CONFIDENCE,
    )
    from src.analytics.elliott_wave import analyze_current_wave

    st.caption(
        "Dieselbe Papier-Trade-Logik wie im Tab 'Papier-Trades' (nur "
        "'Welle 3 in Bildung' mit Confidence über "
        f"{PAPER_TRADES_MIN_CONFIDENCE*100:.0f}%, 100 €-Order, struktureller "
        "Stop-Loss, Trailing auf +1% ab +2% Gewinn, Exit bei "
        "Zielzonen-Kontakt) — aber mit einem eigenen, getrennt ausgewerteten "
        "Ticker-Universum: nur Aktien, die aktuell durch Insider "
        "(Congress/Senate/House, ARK, Trump-Familie) gehandelt werden bzw. "
        "in diesem Fenster noch als aktiv gehandelt gelten. Ergebnisse werden "
        "bewusst nicht mit dem regulären Papier-Trades-Tab vermischt, auch "
        "wenn sich einzelne Ticker überschneiden können.\n\n"
        "🪙 **Krypto-Erweiterung:** Coins selbst haben keine SEC-Insider-Meldungen — "
        f"sobald aber eine kryptonahe Aktie ({', '.join(CRYPTO_PROXY_TICKERS)}) aktuell "
        "durch Insider gehandelt wird, gilt das als Proxy-Signal und die komplette "
        "Krypto-Top-50 wird zusätzlich in dieses Ticker-Universum aufgenommen."
    )

    c1, c2, c3 = st.columns(3)
    insider_days  = c1.slider("Insider-Fenster (Tage)", min_value=30, max_value=365,
                               value=INSIDER_LOOKBACK_DAYS, step=15, key="pti_id")
    lookback_days = c2.slider("Kurs-Lookback (Tage)", min_value=90, max_value=1000,
                               value=365, step=15, key="pti_lb")
    sensitivity   = c3.slider("Zigzag-Empfindlichkeit (%)", min_value=1.0, max_value=20.0,
                               value=5.0, step=0.5, key="pti_th")

    universe = _insider_active_tickers(days=insider_days)
    if universe.empty:
        st.info(f"Keine Insider-Trades in den letzten {insider_days} Tagen gefunden.")
        return

    # ── Krypto-Proxy-Erweiterung ──────────────────────────────────────────
    # Sobald eine kryptonahe Aktie (CRYPTO_PROXY_TICKERS) im obigen Insider-
    # Fenster gehandelt wurde, wird die komplette Krypto-Top-50 zusätzlich in
    # das Ticker-Universum aufgenommen — mit eigener Kurs-Historie über
    # crypto_fetcher (CoinGecko), da yfinance für viele Altcoins keine
    # zuverlässigen Ticker liefert (siehe crypto_fetcher.py Moduldoc).
    crypto_trigger = set(universe["ticker"]) & set(CRYPTO_PROXY_TICKERS)
    crypto_tickers: set[str] = set()
    top50 = pd.DataFrame()
    if crypto_trigger:
        top50 = _load_crypto_top50()
        if not top50.empty:
            _sync_crypto_watchlist(top50)
            crypto_tickers = set(top50["ticker"])
            trigger_date = universe[universe["ticker"].isin(crypto_trigger)]["last_trade_date"].max()
            crypto_rows = pd.DataFrame({
                "ticker":          top50["ticker"],
                "company":         top50["name"],
                "last_trade_date": trigger_date,
                "trade_count":     0,
            })
            universe = pd.concat([universe, crypto_rows], ignore_index=True)
            st.info(
                f"🪙 Insider-Aktivität bei kryptonaher Aktie ({', '.join(sorted(crypto_trigger))}) "
                f"erkannt — Krypto-Top-50 als Proxy-Signal ({len(top50)} Coins) mit aufgenommen."
            )

    st.caption(f"{len(universe)} Ticker aktuell mit Insider-Aktivität (letzte {insider_days} Tage, Krypto-Proxy inklusive).")
    with st.expander("Ticker-Universum anzeigen"):
        st.dataframe(universe, use_container_width=True, hide_index=True)

    # Insider-Ticker (Congress/ARK/Trump) sind praktisch immer Einzelaktien —
    # main.py behandelt sie beim automatischen Watchlist-Eintrag genauso
    # (asset_type='stock' fest verdrahtet), hier übernommen, damit der
    # Fundamental-Vorfilter dieselbe Ticker-Menge wie tab_wave_scanner/
    # tab_signals bekommt. Krypto-Proxy-Ticker werden korrekt als 'crypto'
    # markiert, damit der Fundamental-Vorfilter sie (wie überall sonst im
    # Dashboard) unangetastet lässt statt sie fälschlich als Aktie zu zählen.
    universe_wl = pd.DataFrame({
        "ticker": universe["ticker"],
        "asset_type": universe["ticker"].map(lambda t: "crypto" if t in crypto_tickers else "stock"),
    })
    fund_filter_on, min_fund_score, fund_scores = _fundamental_prefilter_controls(
        universe_wl, key_prefix="pti",
    )

    if st.button("🔍 Insider-Ticker scannen (Elliott-Wellen)", type="primary", key="pti_scan_btn"):
        scan_universe, n_excluded = _apply_fundamental_prefilter(
            universe_wl, fund_filter_on, min_fund_score, fund_scores,
        )
        tickers = scan_universe["ticker"].tolist()
        crypto_scan_tickers = [t for t in tickers if t in crypto_tickers]
        stock_scan_tickers  = [t for t in tickers if t not in crypto_tickers]
        if n_excluded:
            st.caption(f"📐 {n_excluded} Ticker durch Fundamental-Vorfilter (< {min_fund_score:.0f}/100) ausgeschlossen.")

        if stock_scan_tickers:
            with st.spinner(f"Prüfe Kurs-Historie für {len(stock_scan_tickers)} Aktien …"):
                sync_result = _ensure_insider_price_history(stock_scan_tickers)
            if sync_result["added_to_watchlist"]:
                st.toast(
                    f"➕ {sync_result['added_to_watchlist']} Ticker neu zur Watchlist hinzugefügt.",
                    icon="➕",
                )
        if crypto_scan_tickers:
            with st.status(f"Lade Krypto-Kurs-Historie für {len(crypto_scan_tickers)} Coins …", expanded=True) as status:
                n = _ensure_crypto_price_history(top50[top50["ticker"].isin(crypto_scan_tickers)], status=status)
                status.update(label=f"✅ {n} Coin(s) aktualisiert" if n else "✅ Bereits aktuell", state="complete")

        end_d   = str(datetime.today().date())
        start_d = str(datetime.today().date() - timedelta(days=lookback_days))
        found, skipped = 0, 0
        pred_conn = get_connection()
        with st.spinner(f"Scanne {len(tickers)} Insider-Ticker …"):
            for ticker in tickers:
                prices = _load_prices(ticker, start_d, end_d)
                if len(prices) < 20:
                    skipped += 1
                    continue
                state = analyze_current_wave(prices, threshold_pct=sensitivity)
                if state:
                    record_snapshot(pred_conn, ticker, state, sensitivity, lookback_days)
                    found += 1
        pred_conn.commit()
        pred_conn.close()
        st.success(
            f"Scan abgeschlossen: {found} Ticker mit aktuellem Muster protokolliert, "
            f"{skipped} übersprungen mangels Kurshistorie."
        )

    conn = get_connection()
    df = paper_trades(conn, stake_eur=100.0)
    conn.close()

    insider_tickers = set(universe["ticker"])
    if not df.empty:
        df = df[df["ticker"].isin(insider_tickers)]
        df["fundamental_score"] = df["ticker"].map(lambda t: _fund_score_for(fund_scores, t))
        if not df.empty:
            df = df.sort_values("fundamental_score", ascending=False, na_position="last")

    _render_paper_trades_results(
        df, key_prefix="pti",
        empty_msg=(
            "Noch keine auswertbaren Signale für die aktuellen Insider-Ticker — "
            "oben zuerst 'Insider-Ticker scannen' ausführen, um Kurs-Historie zu "
            "laden und die Elliott-Wellen-Analyse laufen zu lassen."
        ),
    )


# ─── Tab: Krypto Top 50 ──────────────────────────────────────────────────────

def tab_crypto_top50():
    from src.analytics.elliott_wave import (
        detect_elliott_waves, analyze_current_wave, STATE_LABELS, STRUCTURE_LABELS,
    )
    from src.analytics.wave_tracker import record_snapshot

    st.caption(
        "Marktüberblick der 50 größten Kryptowährungen nach Marktkapitalisierung "
        "(live über CoinGecko) kombiniert mit Elliott-Wellen-Analyse, News-Sentiment "
        "(CoinDesk/CoinTelegraph + tickergetaggte allgemeine Finanznews), Derivate-"
        "Positionierung (Binance Funding-Rate/Open Interest) und einem Insider-Signal-"
        "Proxy über kryptonahe Aktien — echte SEC-Insider-Meldungen gibt es für Coins "
        "selbst nicht."
    )

    fng = _load_fear_greed()
    if fng:
        fg1, fg2 = st.columns([1, 3])
        latest = fng[0]
        prev_val = fng[1]["value"] if len(fng) > 1 else None
        delta = f"{latest['value'] - prev_val:+d} ggü. Vortag" if prev_val is not None else None
        fg1.metric("😨/🤑 Fear & Greed Index", f"{latest['value']}/100", delta)
        fg2.caption(
            f"**{latest['classification']}** ({latest['date']}) — krypto-WEITES "
            "Stimmungsbarometer über alle Coins hinweg (alternative.me), nicht "
            "coin-spezifisch. Extreme Fear gilt manchen als antizyklisches Kaufsignal, "
            "Extreme Greed als Warnsignal für Überhitzung — beides keine Garantie."
        )

    top50 = _load_crypto_top50()
    if top50.empty:
        st.warning("CoinGecko aktuell nicht erreichbar oder Rate-Limit erreicht — bitte später erneut versuchen.")
        if st.button("🔄 Erneut versuchen", key="crypto_retry"):
            _load_crypto_top50.clear()
            st.rerun()
        return

    _sync_crypto_watchlist(top50)

    c1, c2, c3 = st.columns([1.3, 1.6, 2])
    if c1.button("🔄 Top 50 neu laden", key="crypto_refresh"):
        _load_crypto_top50.clear()
        top50 = _load_crypto_top50()
        added = _sync_crypto_watchlist(top50)
        st.toast(f"➕ {added} neue Coin(s) zur Watchlist hinzugefügt" if added else "Watchlist bereits aktuell", icon="➕")
    if c2.button("📊 Kurs-Historie aktualisieren", key="crypto_fetch_hist",
                 help="Lädt fehlende/veraltete Tageshistorie für alle 50 Coins nach "
                      "(CoinGecko, sequenziell mit Pause zwischen den Requests)."):
        with st.status("Lade Kurs-Historie …", expanded=True) as status:
            n = _ensure_crypto_price_history(top50, status=status)
            status.update(label=f"✅ {n} Coin(s) aktualisiert" if n else "✅ Bereits aktuell", state="complete")
        _load_prices.clear()
    c3.caption(f"📡 Live-Daten (5-Min.-Cache) · {len(top50)} Coins in der Liste")

    sens_col, sens_caption_col = st.columns([1, 2])
    sensitivity = sens_col.slider(
        "🌊 Zigzag-Empfindlichkeit (%)", min_value=1.0, max_value=25.0, value=6.0, step=0.5,
        key="crypto_ew_th",
        help="Steuert sowohl die Spalte 'Tendenz' unten als auch den Wellen-Scan und die "
             "Coin-Detail-Analyse weiter unten. Krypto ist volatiler als die meisten Aktien "
             "— Default hier bewusst etwas höher als die 5% in den Aktien-Tabs, um Rauschen "
             "zu filtern.",
    )
    sens_caption_col.caption(
        "Die Spalten **'Tendenz'** und **'RSI(14)'** zeigen je Coin eine technische "
        "Einschätzung: Tendenz = zu wie viel Prozent die aktuell erkannte Elliott-Wellen-"
        "Struktur einer bullischen bzw. bärischen Phase entspricht (Konfidenz der "
        "Wellen-Erkennung, keine Erfolgsprognose); RSI = unabhängiger, reiner Preis-"
        "Momentum-Indikator (>70 überkauft, <30 überverkauft). Beide nutzen nur bereits "
        "lokal vorhandene Kurse — ohne 'Kurs-Historie aktualisieren' zeigen viele Coins "
        "'➖ keine Daten'."
    )
    st.caption(
        "**Abstand ATH** = Abstand vom Allzeithoch (🚀 <5% entfernt · 🟡 5-30% · 🔴 >30%). "
        "**Vol/MCap** = 24h-Handelsvolumen im Verhältnis zur Marktkapitalisierung, ein grobes "
        "Liquiditäts-/Interesse-Maß (🟢 ≥5% · ⚪ 1-5% · 🔴 <1%, dünner Handel). **BTC-Korr.** = "
        "Korrelation der täglichen Renditen zu Bitcoin über 90 Tage (🔗 stark gekoppelt · 🔸 "
        "moderat · ⚪ kaum Zusammenhang · 🔀 läuft BTC entgegen) — alle drei direkt aus "
        "bereits vorhandenen Daten, kein zusätzlicher Abruf nötig. **Funding** = aktuelle "
        "Funding-Rate der Binance-Perpetual (🔴 Markt überwiegend long positioniert · 🟢 "
        "überwiegend short · ⚪ ausgeglichen) — Positionierungssignal aus dem Derivatemarkt, "
        "fehlt bei Coins ohne gelisteten Perpetual (z.B. Stablecoins)."
    )

    # ── Übersichtstabelle ────────────────────────────────────────────────────
    metrics = {t: _crypto_technical_metrics(t, sensitivity) for t in top50["ticker"]}
    funding_rates = _load_funding_rates()

    disp = top50.copy()
    disp["Preis"]        = disp["price"].map(_fmt_crypto_price)
    disp["24h %"]        = disp["change_24h_pct"].map(_pct_badge)
    disp["7d %"]         = disp["change_7d_pct"].map(_pct_badge)
    disp["Tendenz"]      = disp["ticker"].map(lambda t: _tendency_badge(metrics.get(t, {"has_data": False, "wave": None, "rsi": None})))
    disp["RSI(14)"]      = disp["ticker"].map(lambda t: _rsi_badge(metrics.get(t, {"has_data": False, "wave": None, "rsi": None})))
    disp["Abstand ATH"]  = disp["ath_change_pct"].map(_ath_distance_badge)
    disp["Vol/MCap"]     = disp.apply(lambda r: _vol_mcap_badge(r["volume_24h"], r["market_cap"]), axis=1)
    disp["BTC-Korr."]    = disp["ticker"].apply(
        lambda t: _correlation_badge(_crypto_btc_correlation(t), is_btc=(t == "BTC-USD"))
    )
    disp["Funding"]      = disp["symbol"].map(lambda s: _funding_rate_badge(funding_rates.get(s)))
    disp["Marktkap."]    = disp["market_cap"].map(_fmt_large_usd)
    disp["Volumen 24h"]  = disp["volume_24h"].map(_fmt_large_usd)
    disp["Chart"]        = disp["ticker"].apply(_chart_link)
    disp = disp.rename(columns={"rank": "#", "name": "Name", "symbol": "Symbol"})
    st.dataframe(
        disp[["#", "Name", "Symbol", "Preis", "24h %", "7d %", "Tendenz", "RSI(14)",
              "Abstand ATH", "Vol/MCap", "BTC-Korr.", "Funding", "Marktkap.", "Volumen 24h", "Chart"]],
        use_container_width=True, hide_index=True, height=460,
        column_config={"Chart": st.column_config.LinkColumn("Chart", display_text="📈 Öffnen")},
    )

    st.divider()

    # ── Elliott-Wellen-Scan über alle 50 ─────────────────────────────────────
    st.subheader("🌊 Elliott-Wellen-Scan (Top 50)")
    st.caption(
        "Scannt alle 50 Coins auf noch nicht abgeschlossene Elliott-Wellen-Bewegungen — "
        "dieselbe regelbasierte Struktur-Erkennung wie im Tab 'Wellen-Scanner' (und dieselbe "
        "wie die Spalte 'Tendenz' oben, hier aber gefiltert und protokolliert). Erkannte "
        "Erwartungen fließen ebenfalls in den Tab 'Vorhersagen' ein (gemeinsame "
        "Trefferquoten-Auswertung)."
    )
    min_conf = st.slider("Mindest-Konfidenz", min_value=0.0, max_value=1.0, value=0.4, step=0.05, key="crypto_ew_mc")

    if st.button("🔍 Scan starten", type="primary", key="crypto_scan_btn"):
        end_d   = str(datetime.today().date())
        start_d = str(datetime.today().date() - timedelta(days=365))
        rows, skipped = [], 0
        pred_conn = get_connection()
        with st.spinner(f"Scanne {len(top50)} Coins …"):
            for _, r in top50.iterrows():
                prices = _load_prices(r["ticker"], start_d, end_d)
                if len(prices) < 20:
                    skipped += 1
                    continue
                state = analyze_current_wave(prices, threshold_pct=sensitivity)
                if state:
                    record_snapshot(pred_conn, r["ticker"], state, sensitivity, 365)
                if state and state["confidence"] >= min_conf:
                    dist_pct = (
                        None if state["target_low"] is None
                        else ((state["target_low"] + state["target_high"]) / 2 / state["current_price"] - 1) * 100
                    )
                    rows.append({
                        "Ticker":          r["ticker"],
                        "Name":            r["name"],
                        "Muster":          STATE_LABELS.get(state["state"], state["state"]),
                        "Struktur":        STRUCTURE_LABELS.get(state.get("structure_type"), "—"),
                        "Richtung":        "🟢 Bullish" if state["direction"] == "bullish" else "🔴 Bearish",
                        "Konfidenz":       state["confidence"],
                        "Aktueller Kurs":  state["current_price"],
                        "Zielzone": (
                            f"{state['target_low']:.4g} – {state['target_high']:.4g}"
                            if state["target_low"] is not None else "—"
                        ),
                        "Δ zur Zielmitte": dist_pct,
                        "Chart":           _chart_link(r["ticker"]),
                    })
        pred_conn.commit()
        pred_conn.close()
        st.session_state["crypto_scan_result"] = (rows, skipped, min_conf)

    if "crypto_scan_result" in st.session_state:
        rows, skipped, used_min_conf = st.session_state["crypto_scan_result"]
        if not rows:
            st.info(
                f"Keine aktuellen Muster über Mindest-Konfidenz {used_min_conf:.2f} gefunden "
                f"({skipped} Coins ohne ausreichende Kurshistorie übersprungen — ggf. zuerst "
                "'Kurs-Historie aktualisieren' klicken)."
            )
        else:
            df = pd.DataFrame(rows).sort_values("Konfidenz", ascending=False)
            st.success(f"{len(df)} Coin(s) mit aktuellem Muster gefunden ({skipped} übersprungen).")
            display = df.copy()
            display["Konfidenz"]       = display["Konfidenz"].map("{:.0%}".format)
            display["Aktueller Kurs"]  = display["Aktueller Kurs"].map(lambda v: f"{v:.4g}")
            display["Δ zur Zielmitte"] = display["Δ zur Zielmitte"].map(
                lambda v: "—" if pd.isna(v) else f"{v:+.1f} %"
            )
            st.dataframe(
                display, use_container_width=True, hide_index=True,
                column_config={"Chart": st.column_config.LinkColumn("Chart", display_text="📈 Öffnen")},
            )

    st.divider()

    # ── Coin-Detail: Chart + Elliott + News + Insider-Proxy ─────────────────
    st.subheader("🔍 Coin-Detail")
    coin_ticker = st.selectbox(
        "Coin",
        top50["ticker"].tolist(),
        format_func=lambda t: f"{t}  —  {top50[top50['ticker'] == t]['name'].values[0]}",
        key="crypto_detail_sel",
    )
    coin_row = top50[top50["ticker"] == coin_ticker].iloc[0]

    if _prices_need_update(coin_ticker):
        with st.spinner(f"Lade Kurshistorie für {coin_ticker} …"):
            from src.fetchers.crypto_fetcher import fetch_history
            from src.database.db import store_prices
            df_h = fetch_history(coin_row["id"], coin_ticker)
            if not df_h.empty:
                conn = get_connection()
                store_prices(conn, df_h)
                conn.commit()
                conn.close()
                _load_prices.clear()

    end_d   = str(datetime.today().date())
    start_d = str(datetime.today().date() - timedelta(days=365))
    prices  = _load_prices(coin_ticker, start_d, end_d)
    news    = _load_news_for_ticker(coin_ticker, start_d, end_d)
    obs     = _load_observations(coin_ticker)

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric(
        "Preis", _fmt_crypto_price(coin_row["price"]),
        f"{coin_row['change_24h_pct']:+.2f} % (24h)" if pd.notna(coin_row["change_24h_pct"]) else None,
    )
    k2.metric("7 Tage", f"{coin_row['change_7d_pct']:+.2f} %" if pd.notna(coin_row["change_7d_pct"]) else "—")
    k3.metric("Marktkap.-Rang", f"#{int(coin_row['rank'])}" if pd.notna(coin_row["rank"]) else "—")
    k4.metric("Marktkapitalisierung", _fmt_large_usd(coin_row["market_cap"]))
    k5.metric("News im Zeitraum", f"{len(news)}")

    from src.analytics.indicators import rsi as _rsi
    rsi_series = _rsi(prices.sort_values("date")["close"], period=14) if not prices.empty else pd.Series(dtype=float)
    rsi_value  = float(rsi_series.iloc[-1]) if not rsi_series.empty and pd.notna(rsi_series.iloc[-1]) else None

    k6, k7, k8 = st.columns(3)
    k6.metric(
        "RSI (14)", f"{rsi_value:.0f}" if rsi_value is not None else "—",
        ("Überkauft (>70)" if rsi_value >= 70 else "Überverkauft (<30)" if rsi_value <= 30 else "Neutral")
        if rsi_value is not None else None,
    )
    k7.metric("Abstand ATH", _ath_distance_badge(coin_row["ath_change_pct"]))
    k8.metric("Vol/MCap (24h)", _vol_mcap_badge(coin_row["volume_24h"], coin_row["market_cap"]))

    from src.fetchers.crypto_fetcher import fetch_open_interest
    oi = fetch_open_interest(coin_row["symbol"])
    corr = _crypto_btc_correlation(coin_ticker)
    funding_rates = _load_funding_rates()

    k9, k10, k11 = st.columns(3)
    k9.metric("BTC-Korrelation (90T)", _correlation_badge(corr, is_btc=(coin_ticker == "BTC-USD")))
    k10.metric("Funding-Rate (Binance Perp.)", _funding_rate_badge(funding_rates.get(coin_row["symbol"])))
    k11.metric(
        "Open Interest (Binance Perp.)",
        f"{oi['open_interest']:,.0f} {coin_row['symbol']}" if oi else "—",
        help="Offene Futures-Kontrakte in Coin-Einheiten — steigendes Open Interest bei "
             "steigendem Kurs deutet auf frisches (nicht nur eindeckendes) Kaufinteresse hin.",
    )

    elliott_patterns = detect_elliott_waves(prices, threshold_pct=sensitivity) if not prices.empty else None
    current_wave     = analyze_current_wave(prices, threshold_pct=sensitivity) if not prices.empty else None
    if current_wave:
        cw       = current_wave
        richtung = "🟢 Bullish" if cw["direction"] == "bullish" else "🔴 Bearish"
        zielzone = (
            f"{cw['target_low']:.4g} – {cw['target_high']:.4g}"
            if cw["target_low"] is not None else "—"
        )
        struktur = STRUCTURE_LABELS.get(cw.get("structure_type"), "")
        st.markdown(
            f"##### 🌊 Aktueller Wellen-Zustand: {STATE_LABELS.get(cw['state'], cw['state'])} "
            f"· {struktur} · {richtung} · Konfidenz {cw['confidence'] * 100:.0f}%"
        )
        st.caption(
            f"🎯 {cw['expectation_text'].split(' Danach:')[0].split(' Danach (')[0]}  \n"
            f"Nächste erwartete Zielzone: **{zielzone}** · "
            f"Letzter bestätigter Pivot: {cw['last_pivot_price']:.4g} ({cw['last_pivot_date']})"
        )
        conn = get_connection()
        record_snapshot(conn, coin_ticker, current_wave, sensitivity, 365)
        conn.commit()
        conn.close()
    elif not prices.empty:
        st.info("🌊 Keine gültige Elliott-Wellen-Struktur im aktuellen Zeitraum gefunden — "
                 "Empfindlichkeit oben anpassen.")

    fig = build_price_chart(prices, news, obs, coin_ticker, "Candlestick",
                             None, elliott_patterns, current_wave)
    render_chart_with_news_link(fig)
    if not news.empty:
        st.caption("🖱️ Auf ein 📰-Dreieck klicken öffnet den zugehörigen Artikel in einem neuen Tab.")

    # ── News ─────────────────────────────────────────────────────────────────
    st.markdown(f"**📰 News zu {coin_ticker}**")
    if news.empty:
        st.caption(
            "Keine News im Zeitraum getaggt — 'News laden' in der Sidebar ausführen "
            "(CoinDesk/CoinTelegraph sind als Quellen hinterlegt)."
        )
    else:
        for _, row in news.iterrows():
            icon = SENTIMENT_ICON.get(row["sentiment_label"], "⚪")
            with st.expander(f"{icon}  {row['date']}  |  {row['source']}  —  {row['title'][:90]}"):
                nc1, nc2 = st.columns([4, 1])
                nc1.write(row["summary"] or "—")
                nc2.metric("Sentiment", row["sentiment_label"].capitalize(), delta=f"{row['sentiment_score']:+.2f}")
                if row["url"]:
                    nc1.markdown(f"[→ Originalartikel]({row['url']})")

    # ── Insider-Signal-Proxy ─────────────────────────────────────────────────
    st.markdown("**🏛️ Insider-Signal (Proxy über kryptonahe Aktien)**")
    st.caption(
        "Für Kryptowährungen selbst gibt es keine SEC-Insider-Meldungen (Form 4) — als "
        "indirektes Zusatzsignal dient hier das aggregierte Congress-/ARK-Handelsverhalten "
        f"in {', '.join(CRYPTO_PROXY_TICKERS)} (Börse, Miner, Broker mit hoher Krypto-Korrelation)."
    )
    proxy = _crypto_proxy_insider_bias(days=180)
    if proxy["bias"] is None:
        st.info("➖ Keine Insider-Trades der Proxy-Aktien im Betrachtungszeitraum.")
    else:
        bias_label = {"bullish": "🟢 Bullish", "bearish": "🔴 Bearish", "mixed": "🔶 Uneinheitlich"}[proxy["bias"]]
        st.markdown(f"{bias_label} — {proxy['buy_count']} Käufe / {proxy['sell_count']} Verkäufe (letzte 180 Tage)")
        with st.expander("Proxy-Trades anzeigen"):
            st.dataframe(
                proxy["trades"][["trade_date", "ticker", "source", "trader", "trade_type",
                                   "amount_min", "amount_max", "fund"]],
                use_container_width=True, hide_index=True,
            )


# ─── Tab: Daytrading (Krypto, Intraday-Elliott-Wellen) ───────────────────────
# Bewusst separat von tab_crypto_top50()/build_price_chart(): beide sind auf
# Tages-Kerzen (Spalte 'date' als reines Datum, Prognose-Ausblick in Tagen
# projiziert) zugeschnitten. Für Intraday-Kerzen braucht es (a) eine andere
# Datenquelle (Binance-Klines statt CoinGecko — siehe crypto_fetcher.
# fetch_klines()-Doc, CoinGecko liefert im Free-Tier keine Minuten-/15-Min-
# Granularität) und (b) einen in Minuten statt Tagen projizierenden
# Chart-Ausblick — beides hier eigenständig gehalten, statt die bestehende,
# gut abgehangene Tages-Logik mit Zeitraum-Fallunterscheidungen zu verkomplizieren.

DAYTRADING_INTERVAL       = "15m"   # Empfehlung nach Rücksprache: 1-Min-Kerzen sind für
DAYTRADING_CANDLE_MINUTES = 15      # Elliott-Wellen zu verrauscht (siehe Tab-Hinweistext).
DAYTRADING_CANDLE_LIMIT   = 400     # ~4 Tage Historie bei 15-Min-Kerzen

DAYTRADING_FIXED_TICKERS = [
    "XRP-USD", "BTC-USD", "ETH-USD", "SOL-USD", "AVAX-USD", "HBAR-USD",
]

# Coins ohne "echtes" Wachstumspotenzial im ATH-Abstand-Sinn (Stablecoins,
# goldbesicherte Token) — für die automatische "unterbewertet"-Auswahl
# unten ausgeschlossen, da ein tiefer ATH-Abstand bei diesen nichts über
# Unterbewertung aussagt (Stablecoins sollen per Design ~1 USD halten).
DAYTRADING_UNDERVALUED_EXCLUDE_SYMBOLS = {
    "USDT", "USDC", "DAI", "PYUSD", "RLUSD", "USDY", "USYC", "BUIDL",
    "FDUSD", "TUSD", "USDE", "USDS", "XAUT", "USD1",
}


@st.cache_data(ttl=3600)
def _load_daytrading_undervalued(n: int = 3) -> list[str]:
    """Automatische Auswahl 'aktuell unterbewerteter' Coins als Ergänzung zu
    DAYTRADING_FIXED_TICKERS — Proxy-Definition: größter prozentualer Abstand
    vom Allzeithoch (CoinGecko 'ath_change_pct') unter den Top 50, ohne
    Stablecoins/Gold-Token und ohne die bereits fest gelisteten Coins. Grobe
    Heuristik (kein Fundamentalwert wie bei Aktien möglich, siehe
    fundamentals.py-Doc: Krypto hat keine auswertbaren Bilanzdaten) — 'tief
    unter ATH' ist NICHT dasselbe wie 'unterbewertet', nur ein Näherungswert
    (siehe Hinweistext im Tab). Nur Coins mit Binance-USDT-Spotpaar kommen in
    Frage, da fetch_klines() sonst keine Intraday-Historie liefert. 1h-Cache,
    da sich die Auswahl nicht minütlich ändern muss."""
    from src.fetchers.crypto_fetcher import fetch_klines

    top50 = _load_crypto_top50()
    if top50.empty:
        return []

    cand = top50[
        (~top50["ticker"].isin(DAYTRADING_FIXED_TICKERS))
        & (~top50["symbol"].isin(DAYTRADING_UNDERVALUED_EXCLUDE_SYMBOLS))
    ].sort_values("ath_change_pct", ascending=True)

    picked: list[str] = []
    for _, row in cand.iterrows():
        if len(picked) >= n:
            break
        if not fetch_klines(row["ticker"], interval=DAYTRADING_INTERVAL, limit=5).empty:
            picked.append(row["ticker"])
    return picked


@st.cache_data(ttl=120)
def _load_klines(ticker: str, interval: str, limit: int) -> pd.DataFrame:
    """Wie _load_prices(), aber Intraday über Binance statt Tages-Kerzen aus
    der lokalen DB — Klines werden bewusst NICHT in stock_prices persistiert
    (andere Granularität, würde die Tages-Charts/-Scans verfälschen und die
    DB mit hochfrequenten Daten aufblähen), sondern nur kurz (2-Min.-Cache,
    wie die Tages-Kurse) im Streamlit-Prozess gehalten."""
    from src.fetchers.crypto_fetcher import fetch_klines
    df = fetch_klines(ticker, interval=interval, limit=limit)
    if df.empty:
        return df
    df = df.copy()
    df["date"] = df["date"].dt.strftime("%Y-%m-%d %H:%M")
    return df


def _daytrading_metrics(ticker: str, sensitivity: float) -> dict:
    """Wie _crypto_technical_metrics(), aber auf Intraday-Klines statt
    Tages-Kursen — eigene Funktion statt Parametrisierung, da Datenquelle
    (_load_klines vs. _load_prices) und Mindest-Historie-Schwelle abweichen."""
    from src.analytics.elliott_wave import analyze_current_wave
    from src.analytics.indicators import rsi as _rsi

    prices = _load_klines(ticker, DAYTRADING_INTERVAL, DAYTRADING_CANDLE_LIMIT)
    if len(prices) < 20:
        return {"has_data": False, "wave": None, "rsi": None, "prices": prices}

    wave = analyze_current_wave(prices, threshold_pct=sensitivity)
    rsi_series = _rsi(prices.sort_values("date")["close"], period=14)
    rsi_value = float(rsi_series.iloc[-1]) if not rsi_series.empty and pd.notna(rsi_series.iloc[-1]) else None
    return {"has_data": True, "wave": wave, "rsi": rsi_value, "prices": prices}


def build_intraday_wave_chart(
    prices: pd.DataFrame, ticker: str, current_wave: dict | None, candle_minutes: int,
) -> go.Figure:
    """Candlestick + Elliott-Wellen-Overlay für Intraday-Kerzen — reduzierte
    Variante von build_price_chart() (kein News-/Insider-/Beobachtungs-
    Overlay, das ist alles Tages-granular und für Minuten-Kerzen nicht
    sinnvoll zuordenbar) mit in Kerzen-Minuten statt Tagen projiziertem
    Ausblick (siehe Moduldoc oben für die Begründung der Trennung von
    build_price_chart)."""
    from src.analytics.elliott_wave import STATE_LABELS

    fig = go.Figure()
    if prices.empty:
        fig.add_annotation(
            text="Kein Binance-Handelspaar für diesen Coin gefunden",
            showarrow=False, font=dict(size=16, color="#888"),
            xref="paper", yref="paper", x=0.5, y=0.5,
        )
        fig.update_layout(template="plotly_dark", height=420, paper_bgcolor="rgba(0,0,0,0)")
        return fig

    fig.add_trace(go.Candlestick(
        x=prices["date"], open=prices["open"], high=prices["high"],
        low=prices["low"], close=prices["close"], name="Kurs",
        increasing_line_color="#00C853", decreasing_line_color="#D50000",
        increasing_fillcolor="#00C853", decreasing_fillcolor="#D50000",
        whiskerwidth=0.3,
    ))
    vol_colors = ["#00C853" if c >= o else "#D50000" for c, o in zip(prices["close"], prices["open"])]
    fig.add_trace(go.Bar(
        x=prices["date"], y=prices["volume"], name="Volumen",
        marker_color=vol_colors, opacity=0.22, yaxis="y2",
    ))

    if current_wave:
        cw = current_wave
        cw_color = WAVE_DIRECTION_COLOR.get(cw["direction"], "#6FD9A0")
        cw_label = STATE_LABELS.get(cw["state"], cw["state"])

        cw_points = cw.get("points") or []
        if cw_points:
            fig.add_trace(go.Scatter(
                x=[pt["date"] for pt in cw_points], y=[pt["price"] for pt in cw_points],
                mode="lines+markers+text", text=[pt["label"] for pt in cw_points],
                textposition="top center", textfont=dict(size=13, color=cw_color),
                line=dict(color=cw_color, width=1.5), marker=dict(size=6, color=cw_color),
                name=f"🌊 Bestätigt: {cw_label}",
                hovertemplate=f"<b>Welle %{{text}}</b><br>%{{x}} · %{{y:.4f}}<extra></extra>",
                showlegend=True, legendgroup="current_wave",
            ))

        fig.add_trace(go.Scatter(
            x=[cw["last_pivot_date"], cw["current_date"]],
            y=[cw["last_pivot_price"], cw["current_price"]],
            mode="lines+markers", line=dict(color=cw_color, width=2, dash="dot"),
            marker=dict(size=7, color=cw_color, symbol="circle"),
            name=f"🎯 {cw_label} ({cw['confidence']*100:.0f}%)",
            hovertemplate=f"<b>{cw_label}</b><br>{cw['expectation_text']}<extra></extra>",
            showlegend=True, legendgroup="current_wave",
        ))

        fig.add_shape(
            type="line", xref="x", yref="paper",
            x0=cw["current_date"], x1=cw["current_date"], y0=0, y1=1,
            line=dict(color="rgba(255,255,255,0.25)", width=1, dash="dot"),
        )
        fig.add_annotation(
            x=cw["current_date"], y=1, xref="x", yref="paper",
            text="Jetzt", showarrow=False, yanchor="bottom",
            font=dict(size=10, color="rgba(255,255,255,0.5)"),
        )

        if cw["target_low"] is not None:
            n_candles = len(prices)
            extend_minutes = max(candle_minutes * 5, int(n_candles * 0.15) * candle_minutes)
            future_date = (
                datetime.strptime(cw["current_date"], "%Y-%m-%d %H:%M")
                + timedelta(minutes=extend_minutes)
            ).strftime("%Y-%m-%d %H:%M")
            target_mid = (cw["target_low"] + cw["target_high"]) / 2
            zone_fill = _hex_to_rgba(cw_color, 0.12)

            fig.add_trace(go.Scatter(
                x=[cw["current_date"], future_date], y=[cw["current_price"], target_mid],
                mode="lines", line=dict(color=_hex_to_rgba(cw_color, 0.55), width=1.5, dash="dashdot"),
                name="🔮 Ausblick (Elliott-Projektion)",
                hovertemplate=f"<b>Prognose</b><br>Zielmitte ≈ {target_mid:.4f}<extra></extra>",
                showlegend=True, legendgroup="current_wave",
            ))
            fig.add_trace(go.Scatter(
                x=[cw["current_date"], future_date, future_date, cw["current_date"]],
                y=[cw["target_low"], cw["target_low"], cw["target_high"], cw["target_high"]],
                mode="lines", line=dict(width=0), fill="toself", fillcolor=zone_fill,
                name=f"🎯 Zielzone {cw['target_low']:.4f}–{cw['target_high']:.4f}",
                hovertemplate=(
                    f"<b>Projizierte Zielzone</b><br>{cw['target_low']:.4f} – "
                    f"{cw['target_high']:.4f}<extra></extra>"
                ),
                showlegend=True, legendgroup="current_wave",
            ))

    fig.update_layout(
        title=dict(text=f"<b>{ticker}</b> · {DAYTRADING_INTERVAL}-Kerzen", font=dict(size=18, color="#eee")),
        template="plotly_dark", height=480, paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=10, r=10, t=50, b=10),
        xaxis=dict(rangeslider=dict(visible=False)),
        yaxis=dict(title="Preis (USD)", side="right"),
        yaxis2=dict(overlaying="y", side="left", showgrid=False, range=[0, prices["volume"].max() * 4]),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        hovermode="x unified",
    )
    return fig


def tab_daytrading():
    st.caption(
        "Intraday-Elliott-Wellen-Analyse auf **15-Minuten-Kerzen** (Binance) für eine feste "
        "Coin-Auswahl plus automatisch ermittelte 'unterbewertete' Coins."
    )
    st.warning(
        "⚠️ **Einordnung, bevor du das für Trading-Entscheidungen nutzt:** Elliott-Wellen sind "
        "fraktal und funktionieren technisch auf jedem Zeitrahmen — aber auf sehr kurzen "
        "Zeitrahmen (v.a. 1-Minute) dominiert meist Mikrostruktur-Rauschen statt echter "
        "Crowd-Psychology-Muster, was zu deutlich mehr Fehlsignalen/Whipsaws führt als auf "
        "Tages-Charts. Deshalb hier bewusst 15-Min- statt 1-Min-Kerzen als Kompromiss — "
        "zuverlässiger als 1-Min, aber immer noch deutlich verrauschter als die Tages-Analyse "
        "im Tab 'Krypto Top 50'. Die Konfidenz-Werte sind wie überall im Dashboard ein Maß für "
        "Fibonacci-Konformität der erkannten Struktur, **keine Erfolgsprognose**.",
        icon="⚠️",
    )

    undervalued = _load_daytrading_undervalued(3)
    tickers = DAYTRADING_FIXED_TICKERS + undervalued
    if undervalued:
        st.caption(
            f"🔍 Automatisch als 'unterbewertet' ergänzt (größter Abstand vom Allzeithoch unter "
            f"den Top 50, ohne Stablecoins): **{', '.join(t.replace('-USD','') for t in undervalued)}** "
            "— reiner ATH-Abstand-Proxy, keine fundamentale Unterbewertung (für Krypto anders als "
            "bei Aktien nicht berechenbar, siehe Tab 'Krypto Top 50')."
        )

    sensitivity = st.slider(
        "🌊 Zigzag-Empfindlichkeit (%)", min_value=0.3, max_value=5.0, value=1.0, step=0.1,
        key="daytrading_ew_th",
        help="Deutlich niedriger als die 5-6% in den Tages-Tabs — 15-Min-Kerzen schwanken in "
             "viel kleineren Prozentschritten. Höher = weniger, dafür robustere Signale.",
    )

    # ── Übersicht ─────────────────────────────────────────────────────────────
    metrics = {t: _daytrading_metrics(t, sensitivity) for t in tickers}
    rows = []
    for t in tickers:
        m = metrics[t]
        last_price = m["prices"]["close"].iloc[-1] if m["has_data"] and not m["prices"].empty else None
        rows.append({
            "Coin": t.replace("-USD", ""),
            "Preis": _fmt_crypto_price(last_price),
            "Tendenz": _tendency_badge(m),
            "RSI(14)": _rsi_badge(m),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    n_bull = sum(1 for t in tickers if metrics[t]["has_data"] and metrics[t]["wave"] and metrics[t]["wave"]["direction"] == "bullish")
    n_bear = sum(1 for t in tickers if metrics[t]["has_data"] and metrics[t]["wave"] and metrics[t]["wave"]["direction"] == "bearish")
    b1, b2 = st.columns(2)
    b1.metric("🟢 Bullisch (15-Min.)", n_bull)
    b2.metric("🔴 Bärisch (15-Min.)", n_bear)

    st.divider()

    # ── Detail-Chart ─────────────────────────────────────────────────────────
    sel = st.selectbox("Coin-Detail-Chart", tickers, format_func=lambda t: t.replace("-USD", ""), key="daytrading_sel")
    m = metrics[sel]
    if not m["has_data"]:
        st.info("Kein Binance-Handelspaar bzw. zu wenig Kerzenhistorie für diesen Coin.")
    else:
        fig = build_intraday_wave_chart(m["prices"], sel, m["wave"], DAYTRADING_CANDLE_MINUTES)
        st.plotly_chart(fig, use_container_width=True)
        if m["wave"]:
            st.caption(f"📋 {m['wave']['expectation_text']}")
        else:
            st.caption("⚪ Aktuell keine erkennbare Wellenstruktur bei dieser Empfindlichkeit.")


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    init_db()
    _inject_custom_css()
    _apply_query_param_ticker()
    added = st.session_state.pop("_chart_link_auto_added", None)
    if added:
        st.toast(f"➕ {added} war noch nicht in der Watchlist — automatisch hinzugefügt.", icon="➕")

    wl_count = len(_load_watchlist())
    st.markdown(
        f"""
        <div class="fi-header">
            <div>
                <h1>📈 Finance Intelligence</h1>
                <p>Kurse · News-Sentiment · Insider-Trades · Elliott-Wellen-Analyse</p>
            </div>
            <div class="fi-meta">
                <div><b>{wl_count}</b> Ticker in Watchlist</div>
                <div>{datetime.today().strftime('%A, %d.%m.%Y')}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    start, end, continent = render_sidebar()

    t1, t2, t3, t4, t5, t6, t7, t8, t9, t_cmp, t10, t11, t12, t13 = st.tabs([
        "📈  Kurschart + News",
        "📰  News-Feed",
        "🌍  Marktüberblick",
        "🏛️  Insider Trades",
        "⚖️  Vergleich",
        "🔁  Backtest",
        "🌊  Wellen-Scanner",
        "🔔  Signale",
        "🎯  Vorhersagen",
        "🧪  Modellvergleich",
        "💶  Papier-Trades",
        "🕵️  Insider Papier-Trades",
        "🪙  Krypto Top 50",
        "⚡  Daytrading",
    ])
    with t1:
        tab_chart(start, end, continent)
    with t2:
        tab_newsfeed(start, end, continent)
    with t3:
        tab_overview(continent)
    with t4:
        tab_insider_trades(start, end)
    with t5:
        tab_compare(start, end, continent)
    with t6:
        tab_backtest()
    with t7:
        tab_wave_scanner(continent)
    with t8:
        tab_signals(continent)
    with t9:
        tab_wave_predictions()
    with t_cmp:
        tab_model_comparison()
    with t10:
        tab_paper_trades()
    with t11:
        tab_paper_trades_insider()
    with t12:
        tab_crypto_top50()
    with t13:
        tab_daytrading()


if __name__ == "__main__":
    main()
