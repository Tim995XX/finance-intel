"""Tab „Signale“: Einstiegssignale aus Elliott-Wellen mit Insider-Abgleich."""
from datetime import datetime, timedelta

import streamlit as st

from dashboard.data import (
    apply_fundamental_prefilter,
    fund_score_for,
    fundamental_badge,
    fundamental_prefilter_controls,
    insider_bias,
    load_prices,
    load_watchlist,
)
from dashboard.navigation import chart_link
from src.database.db import get_connection

# ─── Tab: Einstiegssignale (Elliott-Wellen + Insider-Abgleich) ───────────────

# Je erkanntem Wellen-Zustand: wie stark/riskant ist ein Einstieg an dieser
# Stelle einzuordnen. Die Richtung (Buy/Short) selbst liefert bereits
# state["direction"] aus analyze_current_wave() — bei allen vier Zuständen
# bezieht sich "direction" konsistent auf die als nächstes erwartete
# Kursbewegung, nicht auf die zuvor gelaufene.
SIGNAL_STRENGTH = {
    "wave3_forming": (
        "Starkes Signal", "Welle 3 gilt in der Elliott-Theorie meist als "
        "stärkste und längste Welle des Impulses."
    ),
    "new_impulse_possible": (
        "Starkes Signal", "Möglicher Beginn eines neuen Trends direkt nach "
        "abgeschlossener Korrektur."
    ),
    "wave5_forming": (
        "Später Einstieg", "Letzte Welle des Impulses — Kursziel oft "
        "begrenzt, erhöhtes Rückschlagrisiko nahe Trendende."
    ),
    "correction_forming": (
        "Gegentrend-Signal", "Impuls scheint beendet, Korrektur läuft an — "
        "eher kurzfristiger Gegentrend- als Trendfolge-Einstieg."
    ),
}


def _accepted_by_active_version(state: dict) -> bool:
    from src.analytics.model_versions import accepts
    from src.analytics.wave_tracker import barrier_baseline
    _, in_zone = barrier_baseline(state["direction"], state["current_price"], state["target_low"],
                                  state["target_high"], state["last_pivot_price"])
    return accepts({
        "state": state["state"], "direction": state["direction"], "confidence": state["confidence"],
        "target_low": state["target_low"], "in_zone_at_creation": int(bool(in_zone)),
    })


def _market_regime_hint() -> None:
    """Bärenmarkt-Hinweis (S&P 500 unter SMA200) — dort setzt v4 den doppelten Einsatz."""
    from src.analytics.model_versions import (
        ACTIVE_VERSION,
        BEAR_STAKE_FACTOR,
        VERSION_BY_KEY,
        market_closes,
    )
    conn = get_connection()
    closes = market_closes(conn)
    conn.close()
    if len(closes) < 200:
        return
    sma = closes.iloc[-200:].mean()
    dist = closes.iloc[-1] / sma - 1
    v4_active = VERSION_BY_KEY[ACTIVE_VERSION].stake is not None
    if dist < 0:
        st.warning(
            f"📉 **Bärenmarkt:** S&P 500 {dist*100:+.1f} % unter seiner 200-Tage-Linie. Long-Signale "
            "waren in solchen Phasen bisher überdurchschnittlich — "
            + (f"die aktive Version setzt den {BEAR_STAKE_FACTOR:.0f}-fachen Einsatz."
               if v4_active else "v4 (doppelter Einsatz) wird im Tab 'Modellvergleich' geprüft.")
        )
    else:
        st.caption(f"📈 S&P 500 {dist*100:+.1f} % über seiner 200-Tage-Linie — kein Bärenmarkt, "
                   "Standard-Einsatz.")


def tab_signals(continent: str):
    from src.analytics.elliott_wave import STATE_LABELS, analyze_current_wave
    from src.analytics.model_versions import ACTIVE_VERSION, VERSION_BY_KEY
    from src.analytics.wave_tracker import is_canonical_scan, record_snapshot

    st.caption(
        "Direkte Einstiegs-Meldungen: scannt die Watchlist auf vom "
        "Elliott-Wellen-Modul erkannte Einstiege, die die aktive Modellversion "
        "zulässt (Regeln im Tab 'Modellvergleich'), und gleicht "
        "jeden Treffer zusätzlich mit den zuletzt gemeldeten Insider-Trades "
        "(Congress + ARK) desselben Tickers ab — als unabhängige "
        "Zusatzinfo, ob Insider in dieselbe oder die entgegengesetzte "
        "Richtung handeln."
    )

    wl = load_watchlist()
    if wl.empty:
        st.info("Watchlist leer — bitte Ticker hinzufügen oder 'Watchlist sync' klicken.")
        return

    wl_filtered = wl if continent == "Alle" else wl[wl["continent"] == continent]
    if wl_filtered.empty:
        st.info(f"Keine Ticker für Kontinent '{continent}' in der Watchlist.")
        return

    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
    lookback_days   = c1.slider("Lookback (Tage)", min_value=90, max_value=1000, value=365, step=15, key="sig_lb")
    sensitivity     = c2.slider("Zigzag-Empfindlichkeit (%)", min_value=1.0, max_value=20.0, value=5.0, step=0.5, key="sig_th")
    min_conf        = c3.slider("Mindest-Konfidenz", min_value=0.0, max_value=1.0, value=0.5, step=0.05, key="sig_mc")
    insider_days    = c4.slider("Insider-Fenster (Tage)", min_value=30, max_value=365, value=180, step=15, key="sig_id")

    if not is_canonical_scan(sensitivity, lookback_days):
        st.caption("ℹ️ Abweichend von den Standard-Parametern (5 %, 365 Tage) — erkannte "
                   "Erwartungen werden angezeigt, aber nicht im Tab 'Vorhersagen' protokolliert.")

    fund_filter_on, min_fund_score, fund_scores = fundamental_prefilter_controls(
        wl_filtered, key_prefix="sig",
    )

    if not st.button("🔍 Nach Einstiegssignalen suchen", type="primary", key="sig_scan_btn"):
        return

    scan_universe, n_excluded = apply_fundamental_prefilter(
        wl_filtered, fund_filter_on, min_fund_score, fund_scores,
    )
    if n_excluded:
        st.caption(f"📐 {n_excluded} Ticker durch Fundamental-Vorfilter (< {min_fund_score:.0f}/100) ausgeschlossen.")

    end_d   = str(datetime.today().date())
    start_d = str(datetime.today().date() - timedelta(days=lookback_days))

    signals = []
    skipped = n_rejected = 0
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
                record_snapshot(pred_conn, ticker, state, sensitivity, lookback_days)
            if state and state["confidence"] >= min_conf and not _accepted_by_active_version(state):
                # Wird weiter protokolliert (Vorhersage-Statistik), aber nicht
                # als Einstieg gemeldet — Regeln in model_versions.py.
                n_rejected += 1
                continue
            if state and state["confidence"] >= min_conf:
                insider = insider_bias(ticker, days=insider_days)
                signals.append({
                    "wl_row": wl_row, "state": state, "insider": insider,
                    "fund_score": fund_score_for(fund_scores, ticker),
                })
    pred_conn.commit()
    pred_conn.close()

    if n_rejected:
        active = VERSION_BY_KEY[ACTIVE_VERSION]
        st.caption(
            f"🔕 {n_rejected} Signal(e) ausgeblendet, die die aktive Modellversion "
            f"**{active.label}** nicht als Einstieg wertet ({active.rule_text}). Sie fließen "
            "weiter in den Tab 'Vorhersagen' ein — Vergleich der Versionen im Tab 'Modellvergleich'."
        )
    _market_regime_hint()
    if not signals:
        st.info(
            f"Keine aktuellen Einstiegssignale über Mindest-Konfidenz {min_conf:.2f} "
            f"gefunden ({skipped} Ticker ohne ausreichende Kurshistorie übersprungen)."
        )
        return

    # Vielversprechendste zuerst: Fundamentalscore (fehlende Daten = neutral
    # 50, damit ETFs/Krypto nicht künstlich nach unten rutschen) vor Konfidenz.
    signals.sort(
        key=lambda s: (s["fund_score"] if s["fund_score"] is not None else 50.0,
                        s["state"]["confidence"]),
        reverse=True,
    )
    st.success(f"{len(signals)} Einstiegssignal(e) gefunden ({skipped} übersprungen mangels Kurshistorie).")

    for sig in signals:
        wl_row, state, insider = sig["wl_row"], sig["state"], sig["insider"]
        ticker  = wl_row["ticker"]
        bullish = state["direction"] == "bullish"
        action  = "🟢 BUY" if bullish else "🔴 SHORT"
        strength_label, strength_note = SIGNAL_STRENGTH.get(state["state"], ("Signal", ""))

        bias = insider["bias"]
        if bias is None:
            insider_line = "➖ Keine Insider-Trades im Betrachtungszeitraum."
        elif bias == "mixed":
            insider_line = (
                f"🔶 Insider uneinheitlich ({insider['buy_count']} Käufe / "
                f"{insider['sell_count']} Verkäufe) — kein klares Zusatzsignal."
            )
        elif (bias == "bullish") == bullish:
            insider_line = (
                f"✅ Insider-Handel bestätigt die Richtung ({insider['buy_count']} Käufe / "
                f"{insider['sell_count']} Verkäufe)."
            )
        else:
            insider_line = (
                f"⚠️ Insider-Handel widerspricht der Richtung ({insider['buy_count']} Käufe / "
                f"{insider['sell_count']} Verkäufe)."
            )

        with st.container(border=True):
            st.markdown(
                f"### {action} — [{ticker}]({chart_link(ticker)}) · {wl_row['name']}  "
                f"`Konfidenz {state['confidence']:.0%}`"
            )
            st.caption(f"📐 {fundamental_badge(sig['fund_score'])}")
            st.markdown(
                f"**{STATE_LABELS.get(state['state'], state['state'])}** "
                f"({strength_label}) — {state['expectation_text']}"
            )
            if strength_note:
                st.caption(strength_note)
            if state["target_low"] is not None:
                st.caption(
                    f"Aktueller Kurs {state['current_price']:.2f} · "
                    f"Zielzone {state['target_low']:.2f} – {state['target_high']:.2f}"
                )
            st.markdown(insider_line)
            if not insider["trades"].empty:
                with st.expander(f"Insider-Trades {ticker} (letzte {insider_days} Tage)"):
                    st.dataframe(
                        insider["trades"][["trade_date", "source", "trader", "trade_type",
                                            "amount_min", "amount_max", "fund"]],
                        use_container_width=True, hide_index=True,
                    )
