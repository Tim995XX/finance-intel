"""Hilfsfunktionen für Krypto: Top-50-Daten, technische Kennzahlen, Badges."""
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from dashboard.data import (
    load_insider_trades_for_ticker,
    load_prices,
    load_watchlist,
)
from dashboard.fetching import prices_need_update
from src.database.db import get_connection
from src.fetchers.crypto_fetcher import CRYPTO_PROXY_TICKERS

# ─── Krypto Top 50 — Helper ──────────────────────────────────────────────────


@st.cache_data(ttl=300)
def load_crypto_top50() -> pd.DataFrame:
    """Top 50 Kryptowährungen nach Marktkapitalisierung, live von CoinGecko.
    5-Minuten-Cache statt 120s wie bei Kursdaten — Ranking/Preise ändern sich
    für diesen Überblick nicht schnell genug, um kürzer zu cachen, und schont
    das Free-Tier-Rate-Limit."""
    from src.fetchers.crypto_fetcher import fetch_top50_markets
    return fetch_top50_markets()


def sync_crypto_watchlist(top50: pd.DataFrame) -> int:
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
        load_watchlist.clear()
    return added


def ensure_crypto_price_history(top50: pd.DataFrame, status=None) -> int:
    """Lädt für jeden Top-50-Coin ohne aktuelle Kurs-Historie (siehe
    prices_need_update) die Tageshistorie über crypto_fetcher nach — bewusst
    CoinGecko statt yfinance (siehe Modul-Doc in crypto_fetcher.py:
    uneinheitliche/gesuffixte Yahoo-Ticker für viele Altcoins). Die
    Rate-Limit-Pause zwischen Requests steckt in
    crypto_fetcher.fetch_top50_history_batch(), nicht hier verdoppelt.
    `status` (st.status-Objekt) optional für Fortschrittsausgabe. Gibt die
    Anzahl aktualisierter Coins zurück."""
    from src.database.db import store_prices
    from src.fetchers.crypto_fetcher import fetch_top50_history_batch

    stale = top50[top50["ticker"].apply(prices_need_update)]
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
        load_prices.clear()
    return updated


def crypto_proxy_insider_bias(days: int = 180) -> dict:
    """Wie insider_bias(), aber über mehrere Ticker (CRYPTO_PROXY_TICKERS)
    aggregiert — die Krypto-Proxy-Variante des Insider-Abgleichs, da Coins
    selbst keine SEC-Insider-Meldungen haben."""
    end   = str(datetime.today().date())
    start = str(datetime.today().date() - timedelta(days=days))
    frames = [load_insider_trades_for_ticker(t, start, end) for t in CRYPTO_PROXY_TICKERS]
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


def fmt_crypto_price(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    v = float(v)
    return f"${v:,.2f}" if v >= 1 else f"${v:.6f}"


def fmt_large_usd(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    v = float(v)
    if v >= 1e9:
        return f"${v / 1e9:,.2f} Mrd."
    if v >= 1e6:
        return f"${v / 1e6:,.1f} Mio."
    return f"${v:,.0f}"


def pct_badge(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    icon = "🟢" if v >= 0 else "🔴"
    return f"{icon} {v:+.2f} %"


def crypto_technical_metrics(ticker: str, sensitivity: float) -> dict:
    """Kombinierte technische Kennzahlen für die Krypto-Top-50-Übersicht —
    lädt die Kurshistorie einmal (load_prices, gecacht) und leitet daraus
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
    prices = load_prices(ticker, start, end)
    if len(prices) < 20:
        return {"has_data": False, "wave": None, "rsi": None}

    wave = analyze_current_wave(prices, threshold_pct=sensitivity)
    rsi_series = _rsi(prices.sort_values("date")["close"], period=14)
    rsi_value = float(rsi_series.iloc[-1]) if not rsi_series.empty and pd.notna(rsi_series.iloc[-1]) else None
    return {"has_data": True, "wave": wave, "rsi": rsi_value}


def tendency_badge(metrics: dict) -> str:
    """Formatiert die Elliott-Wellen-Tendenz aus crypto_technical_metrics()
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


def rsi_badge(metrics: dict) -> str:
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


def ath_distance_badge(ath_change_pct) -> str:
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


def vol_mcap_badge(volume_24h, market_cap) -> str:
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
def load_fear_greed() -> list[dict]:
    """Crypto Fear & Greed Index — 30-Min.-Cache, da der Index nur einmal
    täglich aktualisiert wird (siehe crypto_fetcher.fetch_fear_greed)."""
    from src.fetchers.crypto_fetcher import fetch_fear_greed
    return fetch_fear_greed(limit=2)


@st.cache_data(ttl=300)
def load_funding_rates() -> dict:
    """Funding-Rates aller Binance-Futures-Perpetuals, EIN Bulk-Request für
    alle Top-50-Coins zusammen (siehe crypto_fetcher.fetch_funding_rates) —
    5-Min.-Cache wie die Top-50-Marktdaten."""
    from src.fetchers.crypto_fetcher import fetch_funding_rates
    return fetch_funding_rates()


def crypto_btc_correlation(ticker: str, days: int = 90) -> float | None:
    """Pearson-Korrelation der täglichen Kursrenditen zu Bitcoin über die
    letzten `days` Tage — rein lokal aus bereits vorhandenen Kursdaten
    berechnet (load_prices, gecacht), kein zusätzlicher Live-Abruf. None
    für BTC-USD selbst (Referenz) oder wenn nicht genug überlappende
    Handelstage vorliegen."""
    if ticker == "BTC-USD":
        return None
    end   = str(datetime.today().date())
    start = str(datetime.today().date() - timedelta(days=days))
    p_coin = load_prices(ticker, start, end)
    p_btc  = load_prices("BTC-USD", start, end)
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


def correlation_badge(corr: float | None, is_btc: bool = False) -> str:
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


def funding_rate_badge(rate: float | None) -> str:
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
