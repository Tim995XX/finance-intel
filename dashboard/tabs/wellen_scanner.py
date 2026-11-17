"""Tab „Wellen-Scanner“."""
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from dashboard.data import (
    apply_fundamental_prefilter,
    fund_score_for,
    fundamental_badge,
    fundamental_prefilter_controls,
    load_prices,
    load_watchlist,
)
from dashboard.navigation import chart_link
from src.database.db import get_connection


def tab_wave_scanner(continent: str):
    from src.analytics.elliott_wave import STATE_LABELS, STRUCTURE_LABELS, analyze_current_wave
    from src.analytics.wave_tracker import is_canonical_scan, record_snapshot

    wl = load_watchlist()
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

    fund_filter_on, min_fund_score, fund_scores = fundamental_prefilter_controls(
        wl_filtered, key_prefix="ews",
    )

    end_d   = str(datetime.today().date())
    start_d = str(datetime.today().date() - timedelta(days=lookback_days))

    if not st.button("🔍 Scan starten", type="primary", key="ews_scan_btn"):
        return

    scan_universe, n_excluded = apply_fundamental_prefilter(
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
            prices = load_prices(ticker, start_d, end_d)
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
                    "Fundamentalscore": fundamental_badge(fund_score_for(fund_scores, ticker)),
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
    display["Chart"] = display["Ticker"].apply(chart_link)
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
            st.markdown(f"**[{r['Ticker']}]({chart_link(r['Ticker'])})** — {r['Erwartung']}")
