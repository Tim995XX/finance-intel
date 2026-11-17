"""Tab „Backtest“."""
import pandas as pd
import streamlit as st

from src.database.db import get_connection


def tab_backtest():
    """'Was wäre passiert, hätte ich jedes Mal mitgekauft?' — Backtest der
    Buy-Trades eines Congress-Mitglieds oder ARK-Funds gegen den aktuellsten
    verfügbaren Kurs, mit Benchmark-Vergleich."""
    from src.analytics.backtest import backtest_trader, list_backtestable_traders

    conn = get_connection()
    traders = list_backtestable_traders(conn)
    conn.close()

    if not traders:
        st.info(
            "Noch keine Backtests möglich — dazu müssen für mindestens einen "
            "Trader Buy-Trades UND Kursdaten für die gehandelten Ticker "
            "vorliegen (Tab 'Insider Trades' → Trades laden, dann Ticker zur "
            "Watchlist hinzufügen)."
        )
        return

    labels = [f"{t['trader']}  ({t['source']}, {t['c']} Buy-Trades)" for t in traders]
    selected_idx = st.selectbox("Trader / Fund", options=range(len(labels)), format_func=lambda i: labels[i], key="backtest_trader_sel")
    trader, source = traders[selected_idx]["trader"], traders[selected_idx]["source"]

    benchmark = (st.text_input("Benchmark-Ticker", value="SPY", key="backtest_benchmark").strip().upper() or "SPY")

    conn = get_connection()
    result = backtest_trader(conn, trader, source=source, benchmark=benchmark)
    conn.close()

    if result["n_trades"] == 0:
        st.warning(
            "Für diesen Trader liegen aktuell keine auswertbaren Trades vor "
            "(keine Kursdaten oder noch keine Historie nach Einstieg)."
        )
        if result["skipped"]:
            st.caption(f"Übersprungen: {', '.join(s['ticker'] for s in result['skipped'])}")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Ø Rendite", f"{result['avg_return_pct']:+.1f} %")
    c2.metric("Trefferquote", f"{result['win_rate_pct']:.0f} %")
    c3.metric("Anzahl Trades", result["n_trades"])
    bench_avg = result["avg_benchmark_return_pct"]
    bench_delta = f"{result['avg_return_pct'] - bench_avg:+.1f} pp ggü. Trader" if bench_avg is not None else None
    c4.metric(
        f"Ø {benchmark}-Rendite (gleicher Zeitraum)",
        f"{bench_avg:+.1f} %" if bench_avg is not None else "—",
        delta=bench_delta,
    )

    bench_col = f"{benchmark}-Rendite"
    rows = [
        {
            "Ticker":        t["ticker"],
            "Firma":         t["company"],
            "Trade-Datum":   t["trade_date"],
            "Einstieg":      t["entry_date"],
            "Einstiegskurs": t["entry_price"],
            "Aktuell":       t["latest_price"],
            "Rendite":       t["return_pct"],
            bench_col:       t["benchmark_return_pct"],
        }
        for t in result["trades"]
    ]
    df = pd.DataFrame(rows).sort_values("Rendite", ascending=False)
    df["Einstiegskurs"] = df["Einstiegskurs"].map("{:.2f}".format)
    df["Aktuell"]       = df["Aktuell"].map("{:.2f}".format)
    df["Rendite"]       = df["Rendite"].map("{:+.1f} %".format)
    df[bench_col]       = df[bench_col].map(lambda v: f"{v:+.1f} %" if pd.notna(v) else "—")
    st.dataframe(df, use_container_width=True, hide_index=True)

    if result["skipped"]:
        with st.expander(f"⚠️ {len(result['skipped'])} Trade(s) ohne auswertbare Kursdaten übersprungen"):
            for s in result["skipped"]:
                st.caption(f"{s['ticker']} ({s['trade_date']}): {s['reason']}")
