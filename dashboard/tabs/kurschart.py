"""Tab „Kurschart + News“."""
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from dashboard.charts import build_price_chart, render_chart_with_news_link
from dashboard.data import (
    fundamental_badge,
    fundamentals_refresh_button,
    load_fundamental_scores,
    load_fundamentals,
    load_insider_trades_for_ticker,
    load_news_for_ticker,
    load_observations,
    load_prices,
    load_watchlist,
)
from dashboard.fetching import fetch_and_store_prices, prices_need_update
from dashboard.theme import SENTIMENT_ICON
from src.database.db import get_connection

# ─── Tab 1: Kurschart ────────────────────────────────────────────────────────

def tab_chart(start: str, end: str, continent: str):
    wl = load_watchlist()
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
    if prices_need_update(ticker):
        with st.spinner(f"Lade Kurse für **{ticker}** …"):
            fetch_and_store_prices(ticker)
            load_prices.clear()   # nur diesen Cache-Eintrag invalidieren

    prices  = load_prices(ticker, start, end)
    news    = load_news_for_ticker(ticker, start, end) if show_news else pd.DataFrame()
    obs     = load_observations(ticker)
    insider = load_insider_trades_for_ticker(ticker, start, end) if show_insider else pd.DataFrame()

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
    fund = load_fundamentals(ticker)
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
                scores = load_fundamental_scores(universe)
                row = scores[scores["ticker"] == ticker]
                if row.empty or not bool(row.iloc[0]["data_ok"]):
                    st.caption(
                        "📐 Fundamentalscore (Piotroski/Gross-Profitability/Fama-French) noch nicht "
                        "im Cache oder keine auswertbare Bilanzhistorie für diesen Ticker."
                    )
                    fundamentals_refresh_button([ticker], key="chart_fund_refresh")
                else:
                    r = row.iloc[0]
                    if pd.notna(r["fundamental_score"]):
                        st.markdown(
                            f"📐 **Fundamentalscore:** {fundamental_badge(r['fundamental_score'])}  "
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
                    fundamentals_refresh_button([ticker], key="chart_fund_refresh")

    # Elliott-Wellen-Erkennung
    elliott_patterns = None
    current_wave = None
    if show_elliott and not prices.empty:
        from src.analytics.elliott_wave import (
            STATE_LABELS,
            STRUCTURE_LABELS,
            analyze_current_wave,
            detect_elliott_waves,
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
