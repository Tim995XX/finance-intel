"""Tab „Papier-Trades“: simulierte Orders und deren Statistik."""
import pandas as pd
import streamlit as st

from dashboard.data import (
    fund_score_for,
    fundamental_badge,
    load_fundamental_scores,
    load_watchlist,
)
from dashboard.navigation import chart_link
from dashboard.tabs.modellvergleich import fmt_pct
from src.database.db import get_connection

# ─── Tab: Papier-Trades (Order/Sell-Statistik) ──────────────────────────────

def render_paper_trades_results(df: pd.DataFrame, key_prefix: str, empty_msg: str) -> None:
    """Gemeinsame Kennzahlen-/Tabellen-Darstellung einer paper_trades()-Historie
    — genutzt von tab_paper_trades() und tab_paper_trades_insider(), damit
    beide exakt dieselbe Kauf-/Verkaufslogik-Auswertung zeigen, nur für
    unterschiedliche Ticker-Mengen. key_prefix hält die Streamlit-Widget-Keys
    der beiden Tabs auseinander."""
    from src.analytics.elliott_wave import STATE_LABELS
    from src.analytics.wave_tracker import EXIT_REASON_LABELS, paper_trade_stats

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
    display["Chart"] = display["Ticker"].apply(chart_link)

    has_fund_col = "fundamental_score" in df.columns
    if has_fund_col:
        display["Fundamentalscore"] = df["fundamental_score"].map(fundamental_badge)

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
    from src.analytics.model_versions import ACTIVE_VERSION, VERSION_BY_KEY, VERSIONS
    from src.analytics.wave_tracker import (
        DEFAULT_STOP_RULE,
        PAPER_TRADES_START_DATE,
        STOP_RULES,
        WAVE3_ENTRY_MAX_WAIT_DAYS,
        overshoot_hold_stats,
        paper_trade_stats,
        paper_trades,
        target_overshoot,
    )

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
            "Trefferquote":     fmt_pct(st_["win_rate"], 0),
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
        wl = load_watchlist()
        universe = tuple(sorted(wl[wl["asset_type"] == "stock"]["ticker"]))
        fund_scores = load_fundamental_scores(universe)
        df["fundamental_score"] = df["ticker"].map(lambda t: fund_score_for(fund_scores, t))
        df = df.sort_values("fundamental_score", ascending=False, na_position="last")

    render_paper_trades_results(
        df, key_prefix="pt",
        empty_msg=(
            "Noch keine auswertbaren Signale — dazu braucht es erkannte "
            "Erwartungen mit berechneter Zielzone. 'Möglicher neuer Impuls' "
            "zählt nicht mit, da dort keine Verkaufszone berechnet wird."
        ),
    )
