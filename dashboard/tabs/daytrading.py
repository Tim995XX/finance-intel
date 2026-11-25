"""Tab „Daytrading“: Intraday-Elliott-Wellen auf Krypto (15-Min-Kerzen)."""
from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.crypto_utils import (
    fmt_crypto_price,
    load_crypto_top50,
    rsi_badge,
    tendency_badge,
)
from dashboard.theme import WAVE_DIRECTION_COLOR, hex_to_rgba

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

    top50 = load_crypto_top50()
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
    """Wie load_prices(), aber Intraday über Binance statt Tages-Kerzen aus
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
    """Wie crypto_technical_metrics(), aber auf Intraday-Klines statt
    Tages-Kursen — eigene Funktion statt Parametrisierung, da Datenquelle
    (_load_klines vs. load_prices) und Mindest-Historie-Schwelle abweichen."""
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
                hovertemplate="<b>Welle %{text}</b><br>%{x} · %{y:.4f}<extra></extra>",
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
            zone_fill = hex_to_rgba(cw_color, 0.12)

            fig.add_trace(go.Scatter(
                x=[cw["current_date"], future_date], y=[cw["current_price"], target_mid],
                mode="lines", line=dict(color=hex_to_rgba(cw_color, 0.55), width=1.5, dash="dashdot"),
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
            "Preis": fmt_crypto_price(last_price),
            "Tendenz": tendency_badge(m),
            "RSI(14)": rsi_badge(m),
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
