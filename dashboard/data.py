"""Lesezugriffe auf die SQLite-Datenbank (mit Streamlit-Cache) und Fundamentaldaten-Filter."""
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from src.database.db import get_connection

# ─── DB helpers ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=120)
def load_watchlist() -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql(
        "SELECT * FROM watchlist ORDER BY continent, asset_type, ticker", conn
    )
    conn.close()
    return df


@st.cache_data(ttl=120)
def load_prices(ticker: str, start: str, end: str) -> pd.DataFrame:
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
def load_news_for_ticker(ticker: str, start: str, end: str) -> pd.DataFrame:
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
def load_insider_trades_for_ticker(ticker: str, start: str, end: str) -> pd.DataFrame:
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
def insider_bias(ticker: str, days: int = 180) -> dict:
    """Aggregiert Insider-Trades (Congress + ARK) der letzten `days` Tage zu
    einer groben Richtungseinschätzung — dient als unabhängiger Abgleich für
    Elliott-Wellen-Einstiegssignale (bestätigt die Richtung oder widerspricht ihr).
    bias: 'bullish' (mehr Käufe), 'bearish' (mehr Verkäufe), 'mixed' (Gleichstand,
    aber Trades vorhanden) oder None (keine Trades im Zeitraum)."""
    end   = str(datetime.today().date())
    start = str(datetime.today().date() - timedelta(days=days))
    df = load_insider_trades_for_ticker(ticker, start, end)
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
def load_fundamentals(ticker: str) -> dict:
    """Fundamentaldaten ändern sich langsam — 1h Cache statt 120s wie bei Kursen."""
    from src.fetchers.stock_fetcher import get_ticker_info
    return get_ticker_info(ticker)


@st.cache_data(ttl=3600)
def load_fundamental_scores(tickers: tuple) -> pd.DataFrame:
    """Piotroski-F-Score/Gross-Profitability/Fama-French-Proxy für die
    übergebenen Ticker, aus dem DB-Cache (fundamentals_raw) — löst KEINEN
    Live-Abruf aus (das wäre bei hunderten Tickern zu langsam), siehe dazu
    fundamentals_refresh_button(). Die Perzentile (Value/Profitabilität/
    Investment/Quality) werden relativ zu genau dieser Ticker-Menge
    berechnet — deshalb möglichst dieselbe (kontinent-)gefilterte Menge
    übergeben, die gerade angezeigt/gescannt wird, nicht in jedem Tab neu
    zusammengewürfelt."""
    from src.analytics.fundamentals import load_scores
    conn = get_connection()
    df = load_scores(conn, list(tickers))
    conn.close()
    return df


def fundamentals_refresh_button(tickers: list, key: str) -> None:
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
        load_fundamental_scores.clear()
        st.toast(
            f"✅ {result['refreshed']} aktualisiert · {result['skipped_fresh']} bereits aktuell "
            f"· {result['no_data']} ohne auswertbare Bilanzdaten",
            icon="✅",
        )
        st.rerun()


def fundamental_badge(score) -> str:
    if score is None or (isinstance(score, float) and pd.isna(score)):
        return "➖ n/a"
    if score >= 70:
        return f"🟢 Stark ({score:.0f}/100)"
    if score >= 40:
        return f"🟡 Mittel ({score:.0f}/100)"
    return f"🔴 Schwach ({score:.0f}/100)"


def fund_score_for(scores: pd.DataFrame, ticker: str):
    """fundamental_score für einen einzelnen Ticker aus einer
    load_fundamental_scores()-Tabelle, None wenn nicht vorhanden/keine
    Bilanzdaten (ETF/Index/Krypto)."""
    if scores.empty:
        return None
    row = scores[scores["ticker"] == ticker]
    if row.empty:
        return None
    v = row.iloc[0]["fundamental_score"]
    return None if (v is None or (isinstance(v, float) and pd.isna(v))) else v


def fundamental_prefilter_controls(wl_filtered: pd.DataFrame, key_prefix: str):
    """Gemeinsame UI (Checkbox + Mindestscore-Slider + Refresh-Button) für den
    Fundamental-Vorfilter, genutzt von Wellen-Scanner, Signale und
    Insider-Papier-Trades — dieselben drei Studien wie im Kurschart-Tab
    (Fama-French/Piotroski/Novy-Marx), hier aber um VOR dem eigentlichen
    Elliott-Wellen-Scan auszusieben statt nur anzuzeigen. Liest nur den
    DB-Cache (kein Live-Abruf), damit ein Scan über hunderte Ticker nicht an
    yfinance-Latenz hängt. Gibt (aktiv, Mindestscore, Scores-Tabelle) zurück."""
    stock_tickers = wl_filtered[wl_filtered["asset_type"] == "stock"]["ticker"].tolist()
    universe = tuple(sorted(stock_tickers))
    scores = load_fundamental_scores(universe)

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
        fundamentals_refresh_button(stock_tickers, key=f"{key_prefix}_fund_refresh")

    return active, float(min_score), scores


def apply_fundamental_prefilter(
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
def load_observations(ticker: str) -> pd.DataFrame:
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
def load_all_news(limit: int = 400) -> pd.DataFrame:
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
