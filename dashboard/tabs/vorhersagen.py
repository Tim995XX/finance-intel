"""Tab „Vorhersagen“: Tracking protokollierter Wellen-Erwartungen."""
import pandas as pd
import streamlit as st

from dashboard.data import (
    fund_score_for,
    fundamental_badge,
    load_fundamental_scores,
    load_watchlist,
)
from dashboard.navigation import chart_link
from src.database.db import get_connection

# ─── Tab: Vorhersage-Tracking ────────────────────────────────────────────────

def tab_wave_predictions():
    from src.analytics.elliott_wave import STATE_LABELS
    from src.analytics.wave_tracker import (
        CANONICAL_LOOKBACK_DAYS,
        CANONICAL_THRESHOLD_PCT,
        accuracy_stats,
        evaluate_open_predictions,
        expectation_trend,
        honest_decided,
        latest_expectations,
        load_predictions,
        overshoot_coverage,
        overshoot_hold_stats,
        target_overshoot,
        weekly_accuracy,
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
            wl = load_watchlist()
            universe = tuple(sorted(wl[wl["asset_type"] == "stock"]["ticker"]))
            fund_scores = load_fundamental_scores(universe)
            ld["fund_score"] = ld["ticker"].map(lambda t: fund_score_for(fund_scores, t))
            # Vielversprechendste zuerst: Fundamentalscore vor Konfidenz. Fehlende
            # Fundamentaldaten (ETF/Index/Krypto) rutschen ans Ende statt nach oben,
            # da hier (anders als beim Vorfilter) keine bewusste Ausnahme gilt,
            # sondern nur eine Sortierpriorität.
            ld = ld.sort_values(["fund_score", "confidence"], ascending=False, na_position="last")
            ld["Fundamentalscore"] = ld["fund_score"].map(fundamental_badge)
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
            ld["Chart"] = ld["Ticker"].apply(chart_link)
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
    display["Chart"] = display["Ticker"].apply(chart_link)

    st.dataframe(
        display[["Ticker", "Erkannt am", "Muster", "Richtung", "Konfidenz",
                  "Kurs bei Erkennung", "Zielzone", "Status", "Ergebnis", "Chart"]],
        use_container_width=True, hide_index=True,
        column_config={
            "Chart": st.column_config.LinkColumn("Chart", display_text="📈 Öffnen"),
        },
    )
