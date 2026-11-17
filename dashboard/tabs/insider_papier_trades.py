"""Tab „Insider Papier-Trades“: Wellen-Signale nur auf Insider-Tickern."""
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from dashboard.crypto_utils import (
    ensure_crypto_price_history,
    load_crypto_top50,
    sync_crypto_watchlist,
)
from dashboard.data import (
    apply_fundamental_prefilter,
    fund_score_for,
    fundamental_prefilter_controls,
    load_prices,
)
from dashboard.insider_data import (
    ensure_insider_price_history,
    insider_active_tickers,
)
from dashboard.tabs.papier_trades import render_paper_trades_results
from src.database.db import get_connection
from src.fetchers.crypto_fetcher import CRYPTO_PROXY_TICKERS

# ─── Tab: Insider-Papier-Trades (Elliott-Wellen nur auf Insider-Ticker) ─────

def tab_paper_trades_insider():
    from src.analytics.elliott_wave import analyze_current_wave
    from src.analytics.wave_tracker import (
        INSIDER_LOOKBACK_DAYS,
        PAPER_TRADES_MIN_CONFIDENCE,
        paper_trades,
        record_snapshot,
    )

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

    universe = insider_active_tickers(days=insider_days)
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
        top50 = load_crypto_top50()
        if not top50.empty:
            sync_crypto_watchlist(top50)
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
    fund_filter_on, min_fund_score, fund_scores = fundamental_prefilter_controls(
        universe_wl, key_prefix="pti",
    )

    if st.button("🔍 Insider-Ticker scannen (Elliott-Wellen)", type="primary", key="pti_scan_btn"):
        scan_universe, n_excluded = apply_fundamental_prefilter(
            universe_wl, fund_filter_on, min_fund_score, fund_scores,
        )
        tickers = scan_universe["ticker"].tolist()
        crypto_scan_tickers = [t for t in tickers if t in crypto_tickers]
        stock_scan_tickers  = [t for t in tickers if t not in crypto_tickers]
        if n_excluded:
            st.caption(f"📐 {n_excluded} Ticker durch Fundamental-Vorfilter (< {min_fund_score:.0f}/100) ausgeschlossen.")

        if stock_scan_tickers:
            with st.spinner(f"Prüfe Kurs-Historie für {len(stock_scan_tickers)} Aktien …"):
                sync_result = ensure_insider_price_history(stock_scan_tickers)
            if sync_result["added_to_watchlist"]:
                st.toast(
                    f"➕ {sync_result['added_to_watchlist']} Ticker neu zur Watchlist hinzugefügt.",
                    icon="➕",
                )
        if crypto_scan_tickers:
            with st.status(f"Lade Krypto-Kurs-Historie für {len(crypto_scan_tickers)} Coins …", expanded=True) as status:
                n = ensure_crypto_price_history(top50[top50["ticker"].isin(crypto_scan_tickers)], status=status)
                status.update(label=f"✅ {n} Coin(s) aktualisiert" if n else "✅ Bereits aktuell", state="complete")

        end_d   = str(datetime.today().date())
        start_d = str(datetime.today().date() - timedelta(days=lookback_days))
        found, skipped = 0, 0
        pred_conn = get_connection()
        with st.spinner(f"Scanne {len(tickers)} Insider-Ticker …"):
            for ticker in tickers:
                prices = load_prices(ticker, start_d, end_d)
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
        df["fundamental_score"] = df["ticker"].map(lambda t: fund_score_for(fund_scores, t))
        if not df.empty:
            df = df.sort_values("fundamental_score", ascending=False, na_position="last")

    render_paper_trades_results(
        df, key_prefix="pti",
        empty_msg=(
            "Noch keine auswertbaren Signale für die aktuellen Insider-Ticker — "
            "oben zuerst 'Insider-Ticker scannen' ausführen, um Kurs-Historie zu "
            "laden und die Elliott-Wellen-Analyse laufen zu lassen."
        ),
    )
