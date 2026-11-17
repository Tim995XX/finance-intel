"""Tab „Krypto Top 50“."""
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from dashboard.charts import build_price_chart, render_chart_with_news_link
from dashboard.crypto_utils import (
    ath_distance_badge,
    correlation_badge,
    crypto_btc_correlation,
    crypto_proxy_insider_bias,
    crypto_technical_metrics,
    ensure_crypto_price_history,
    fmt_crypto_price,
    fmt_large_usd,
    funding_rate_badge,
    load_crypto_top50,
    load_fear_greed,
    load_funding_rates,
    pct_badge,
    rsi_badge,
    sync_crypto_watchlist,
    tendency_badge,
    vol_mcap_badge,
)
from dashboard.data import load_news_for_ticker, load_observations, load_prices
from dashboard.fetching import prices_need_update
from dashboard.navigation import chart_link
from dashboard.theme import SENTIMENT_ICON
from src.database.db import get_connection
from src.fetchers.crypto_fetcher import CRYPTO_PROXY_TICKERS

# ─── Tab: Krypto Top 50 ──────────────────────────────────────────────────────

def tab_crypto_top50():
    from src.analytics.elliott_wave import (
        STATE_LABELS,
        STRUCTURE_LABELS,
        analyze_current_wave,
        detect_elliott_waves,
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

    fng = load_fear_greed()
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

    top50 = load_crypto_top50()
    if top50.empty:
        st.warning("CoinGecko aktuell nicht erreichbar oder Rate-Limit erreicht — bitte später erneut versuchen.")
        if st.button("🔄 Erneut versuchen", key="crypto_retry"):
            load_crypto_top50.clear()
            st.rerun()
        return

    sync_crypto_watchlist(top50)

    c1, c2, c3 = st.columns([1.3, 1.6, 2])
    if c1.button("🔄 Top 50 neu laden", key="crypto_refresh"):
        load_crypto_top50.clear()
        top50 = load_crypto_top50()
        added = sync_crypto_watchlist(top50)
        st.toast(f"➕ {added} neue Coin(s) zur Watchlist hinzugefügt" if added else "Watchlist bereits aktuell", icon="➕")
    if c2.button("📊 Kurs-Historie aktualisieren", key="crypto_fetch_hist",
                 help="Lädt fehlende/veraltete Tageshistorie für alle 50 Coins nach "
                      "(CoinGecko, sequenziell mit Pause zwischen den Requests)."):
        with st.status("Lade Kurs-Historie …", expanded=True) as status:
            n = ensure_crypto_price_history(top50, status=status)
            status.update(label=f"✅ {n} Coin(s) aktualisiert" if n else "✅ Bereits aktuell", state="complete")
        load_prices.clear()
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
    metrics = {t: crypto_technical_metrics(t, sensitivity) for t in top50["ticker"]}
    funding_rates = load_funding_rates()

    disp = top50.copy()
    disp["Preis"]        = disp["price"].map(fmt_crypto_price)
    disp["24h %"]        = disp["change_24h_pct"].map(pct_badge)
    disp["7d %"]         = disp["change_7d_pct"].map(pct_badge)
    disp["Tendenz"]      = disp["ticker"].map(lambda t: tendency_badge(metrics.get(t, {"has_data": False, "wave": None, "rsi": None})))
    disp["RSI(14)"]      = disp["ticker"].map(lambda t: rsi_badge(metrics.get(t, {"has_data": False, "wave": None, "rsi": None})))
    disp["Abstand ATH"]  = disp["ath_change_pct"].map(ath_distance_badge)
    disp["Vol/MCap"]     = disp.apply(lambda r: vol_mcap_badge(r["volume_24h"], r["market_cap"]), axis=1)
    disp["BTC-Korr."]    = disp["ticker"].apply(
        lambda t: correlation_badge(crypto_btc_correlation(t), is_btc=(t == "BTC-USD"))
    )
    disp["Funding"]      = disp["symbol"].map(lambda s: funding_rate_badge(funding_rates.get(s)))
    disp["Marktkap."]    = disp["market_cap"].map(fmt_large_usd)
    disp["Volumen 24h"]  = disp["volume_24h"].map(fmt_large_usd)
    disp["Chart"]        = disp["ticker"].apply(chart_link)
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
                prices = load_prices(r["ticker"], start_d, end_d)
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
                        "Chart":           chart_link(r["ticker"]),
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

    if prices_need_update(coin_ticker):
        with st.spinner(f"Lade Kurshistorie für {coin_ticker} …"):
            from src.database.db import store_prices
            from src.fetchers.crypto_fetcher import fetch_history
            df_h = fetch_history(coin_row["id"], coin_ticker)
            if not df_h.empty:
                conn = get_connection()
                store_prices(conn, df_h)
                conn.commit()
                conn.close()
                load_prices.clear()

    end_d   = str(datetime.today().date())
    start_d = str(datetime.today().date() - timedelta(days=365))
    prices  = load_prices(coin_ticker, start_d, end_d)
    news    = load_news_for_ticker(coin_ticker, start_d, end_d)
    obs     = load_observations(coin_ticker)

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric(
        "Preis", fmt_crypto_price(coin_row["price"]),
        f"{coin_row['change_24h_pct']:+.2f} % (24h)" if pd.notna(coin_row["change_24h_pct"]) else None,
    )
    k2.metric("7 Tage", f"{coin_row['change_7d_pct']:+.2f} %" if pd.notna(coin_row["change_7d_pct"]) else "—")
    k3.metric("Marktkap.-Rang", f"#{int(coin_row['rank'])}" if pd.notna(coin_row["rank"]) else "—")
    k4.metric("Marktkapitalisierung", fmt_large_usd(coin_row["market_cap"]))
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
    k7.metric("Abstand ATH", ath_distance_badge(coin_row["ath_change_pct"]))
    k8.metric("Vol/MCap (24h)", vol_mcap_badge(coin_row["volume_24h"], coin_row["market_cap"]))

    from src.fetchers.crypto_fetcher import fetch_open_interest
    oi = fetch_open_interest(coin_row["symbol"])
    corr = crypto_btc_correlation(coin_ticker)
    funding_rates = load_funding_rates()

    k9, k10, k11 = st.columns(3)
    k9.metric("BTC-Korrelation (90T)", correlation_badge(corr, is_btc=(coin_ticker == "BTC-USD")))
    k10.metric("Funding-Rate (Binance Perp.)", funding_rate_badge(funding_rates.get(coin_row["symbol"])))
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
    proxy = crypto_proxy_insider_bias(days=180)
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
