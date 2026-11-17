"""Tab „Modellvergleich“: Versionen der Signal-Regeln gegeneinander."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.database.db import get_connection

# ─── Tab: Modellvergleich (Versionen der Signal-Regeln) ─────────────────────

# Feste Reihenfolge = feste Farbe je Version (Referenzpalette, Dark-Mode-Slots 1-3).
VERSION_COLORS = ["#3987e5", "#d95926", "#199e70", "#c98500"]


def fmt_pct(v, digits=1, signed=False):
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
        "Trefferquote":       cmp["hit_rate"].map(fmt_pct),
        "Zufalls-Basis":      cmp["baseline"].map(fmt_pct),
        "Edge":               cmp["edge"].map(_fmt_pp),
        "Ø Ergebnis/Signal":  cmp["avg_result"].map(lambda v: fmt_pct(v, 2, True)),
        "20T-Überrendite":    cmp["excess20"].map(lambda v: fmt_pct(v, 2, True)),
        "t-Wert (20T)":       cmp["excess20_t"].map(lambda v: "—" if v is None or pd.isna(v) else f"{v:.1f}"),
        "Einsatz":            (cmp["stake"] * stake_eur).map(lambda v: f"{v:,.0f} €".replace(",", ".")),
        "Ergebnis je Euro":   cmp["w_result"].map(lambda v: fmt_pct(v, 2, True)),
        "Gewinn":             (cmp["pnl_units"] * stake_eur).map(lambda v: f"{v:+,.0f} €".replace(",", ".")),
    })
    if "w_result_net" in cmp.columns and cmp["w_result_net"].notna().any():
        out["Netto je Euro"] = cmp["w_result_net"].map(lambda v: fmt_pct(v, 2, True)).to_numpy()
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
    c3.metric("Ø Ergebnis/Signal", fmt_pct(new["avg_result"], 2, True),
              delta=None if d is None else f"{d*100:+.2f} pp")
    d = delta("excess20")
    c4.metric("20T-Überrendite", fmt_pct(new["excess20"], 2, True),
              delta=None if d is None else f"{d*100:+.2f} pp")


def _bear_weighting_section(replay: pd.DataFrame, cmp: pd.DataFrame, period: str,
                            phases: list, universe: str) -> None:
    """v3 (Standard-Einsatz) gegen v4 (doppelter Einsatz im Bärenmarkt) plus
    die vorab festgelegte Entscheidungsregel (model_versions.v4_decision)."""
    from src.analytics.model_versions import (
        ACTIVE_VERSION,
        VERSION_BY_KEY,
        phase_table,
        v4_decision,
        yearly_return_per_euro,
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
    c2.metric("Ergebnis je Euro v4", fmt_pct(v4["w_result"], 2, True),
              delta=None if d is None else f"{d*100:+.2f} pp ggü. v3")
    c3.metric("Gewinn v4", f"{v4['pnl_units']*100:+,.0f} €".replace(",", "."),
              delta=f"{(v4['pnl_units'] - v3['pnl_units'])*100:+,.0f} € ggü. v3".replace(",", "."))

    yr = yearly_return_per_euro(replay)
    if not yr.empty:
        with st.expander("Ergebnis je Euro je Jahr"):
            st.dataframe(yr.map(lambda v: fmt_pct(v, 2, True)), use_container_width=True)

    pt = phase_table(replay, phases)
    dec = v4_decision(replay, phases)
    with st.expander(f"Bärenphasen des S&P 500 ({len(phases)}) — Long-Signale im Bärenmarkt"):
        if pt.empty:
            st.info("Keine Bärenphasen im Datenzeitraum.")
        else:
            st.dataframe(pd.DataFrame({
                "Von": pt["start"], "Bis": pt["end"], "Signale": pt["n"], "Entschieden": pt["decided"],
                "Edge": pt["edge"].map(_fmt_pp),
                "Ø Ergebnis/Signal": pt["avg_result"].map(lambda v: fmt_pct(v, 2, True)),
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
        f"{mark(dec['return_ok'])} Ergebnis je Euro v4 {fmt_pct(dec['w_result_v4'], 2, True)} gegen "
        f"v3 {fmt_pct(dec['w_result_v3'], 2, True)} (v4 muss höher sein)  \n"
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
        "Gewinn-Trades":     g["win_rate"].map(fmt_pct),
        "Ø Haltedauer":      g["avg_days"].map(lambda v: f"{v:.0f} T"),
        "Brutto je Trade":   g["gross"].map(lambda v: fmt_pct(v, 2, True)),
        "Netto je Trade":    g["net_sel"].map(lambda v: fmt_pct(v, 2, True)),
        "Vorsprung":         g["excess"].map(lambda v: fmt_pct(v, 2, True)),
        "p.a. netto":        g["pa_sel"].map(lambda v: fmt_pct(v, 1, True)),
        "Break-even-Einsatz": g["gross"].map(lambda v: (lambda b: "nie" if b is None else _fmt_eur(b, False))(
            fees.breakeven_stake(v))),
    })
    st.dataframe(table.style.apply(
        lambda r: ["font-weight: bold" if "← heute" in r["Regel"] else "" for _ in r], axis=1),
        use_container_width=True, hide_index=True, height=420)


def tab_model_comparison():
    from src.analytics.model_replay import load_replay
    from src.analytics.model_versions import (
        ACTIVE_VERSION,
        TRAIN_END_YEAR,
        VERSION_BY_KEY,
        VERSIONS,
        bear_phases,
        compare_versions,
        live_signal_frame,
        market_closes,
        replay_periods,
        yearly_edge,
    )
    from src.analytics.wave_tracker import paper_trade_stats, paper_trades

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
            "Trefferquote":       fmt_pct(stats["win_rate"], 0),
            "Einsatz (geschl.)":  f"{stats['total_stake_eur']:,.0f} €".replace(",", "."),
            "P&L realisiert":     f"{stats['total_pnl_eur']:+.2f} €" if stats["closed"] else "—",
            "P&L je Euro":        fmt_pct(stats["pnl_per_eur"], 2, True),
            "Ø P&L je Trade":     f"{stats['avg_pnl_eur']:+.2f} €" if stats["closed"] else "—",
        })
    conn.close()
    st.dataframe(pd.DataFrame(pt_rows), use_container_width=True, hide_index=True)
