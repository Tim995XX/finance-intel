"""Tab „Vergleich“: mehrere Ticker normiert in einem Chart."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.data import (
    fundamental_badge,
    load_fundamental_scores,
    load_prices,
    load_watchlist,
)
from dashboard.fetching import fetch_and_store_prices, prices_need_update
from dashboard.navigation import chart_link

# ─── Tab 5: Vergleich ────────────────────────────────────────────────────────

# Fixed categorical order (dark-surface palette), assigned by selection order —
# never re-cycled when the selection changes, so a ticker keeps its color.
COMPARE_PALETTE = [
    "#3987e5",  # blue
    "#199e70",  # aqua
    "#c98500",  # yellow
    "#008300",  # green
    "#9085e9",  # violet
    "#e66767",  # red
    "#d55181",  # magenta
    "#d95926",  # orange
]
COMPARE_MAX_TICKERS = len(COMPARE_PALETTE)


def build_compare_chart(prices_by_ticker: dict[str, pd.DataFrame]) -> go.Figure:
    fig = go.Figure()

    for i, (ticker, df) in enumerate(prices_by_ticker.items()):
        if df.empty:
            continue
        base = df["close"].iloc[0]
        pct  = (df["close"] / base - 1) * 100
        fig.add_trace(go.Scatter(
            x=df["date"], y=pct,
            mode="lines", name=ticker,
            line=dict(color=COMPARE_PALETTE[i % len(COMPARE_PALETTE)], width=2),
            hovertemplate=f"<b>{ticker}</b>  %{{y:+.2f}}%<extra></extra>",
        ))

    fig.add_hline(y=0, line_width=1, line_dash="dot", line_color="rgba(255,255,255,0.25)")
    fig.update_layout(
        height=520,
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(10,12,20,1)",
        yaxis=dict(
            title="Performance seit Start",
            ticksuffix=" %",
            showgrid=True, gridcolor="rgba(255,255,255,0.06)",
        ),
        xaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)"),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="left", x=0,
            bgcolor="rgba(0,0,0,0.4)", font=dict(size=11),
        ),
        hovermode="x unified",
        margin=dict(l=10, r=20, t=30, b=20),
    )
    return fig


def tab_compare(start: str, end: str, continent: str):
    wl = load_watchlist()
    if wl.empty:
        st.info("Watchlist leer — bitte Ticker hinzufügen oder 'Watchlist sync' klicken.")
        return

    wl_filtered = wl if continent == "Alle" else wl[wl["continent"] == continent]
    if wl_filtered.empty:
        st.info(f"Keine Ticker für Kontinent '{continent}' in der Watchlist.")
        return

    ticker_options = wl_filtered["ticker"].tolist()
    selected = st.multiselect(
        "Ticker vergleichen (relative Performance in % seit Zeitraumbeginn)",
        ticker_options,
        default=ticker_options[:3],
        format_func=lambda t: (
            f"{t}  —  {wl_filtered[wl_filtered['ticker']==t]['name'].values[0]}"
        ),
        key="compare_sel",
    )

    if not selected:
        st.info("Mindestens einen Ticker auswählen.")
        return
    if len(selected) > COMPARE_MAX_TICKERS:
        st.warning(
            f"Bitte maximal {COMPARE_MAX_TICKERS} Ticker gleichzeitig vergleichen "
            "(Übersichtlichkeit) — es werden nur die ersten "
            f"{COMPARE_MAX_TICKERS} angezeigt."
        )
        selected = selected[:COMPARE_MAX_TICKERS]

    with st.spinner("Lade Kursdaten …"):
        for t in selected:
            if prices_need_update(t):
                fetch_and_store_prices(t)
        load_prices.clear()

    prices_by_ticker = {t: load_prices(t, start, end) for t in selected}
    missing = [t for t, df in prices_by_ticker.items() if df.empty]
    if missing:
        st.caption(f"⚠️ Keine Kursdaten im Zeitraum für: {', '.join(missing)}")

    fig = build_compare_chart(prices_by_ticker)
    st.plotly_chart(fig, use_container_width=True)

    # Performance-Tabelle, absteigend sortiert
    rows = []
    for t, df in prices_by_ticker.items():
        if df.empty:
            continue
        first, last = df["close"].iloc[0], df["close"].iloc[-1]
        perf = (last / first - 1) * 100
        rows.append({
            "Ticker":      t,
            "Name":        wl_filtered[wl_filtered["ticker"] == t]["name"].values[0],
            "Start":       first,
            "Ende":        last,
            "Performance": perf,
        })
    if rows:
        perf_df = pd.DataFrame(rows).sort_values("Performance", ascending=False)
        stock_universe = tuple(sorted(wl_filtered[wl_filtered["asset_type"] == "stock"]["ticker"]))
        fund_scores = load_fundamental_scores(stock_universe).set_index("ticker")
        perf_df["Fundamentalscore"] = perf_df["Ticker"].map(
            lambda t: fundamental_badge(fund_scores.loc[t, "fundamental_score"])
                      if t in fund_scores.index else "➖ n/a"
        )
        perf_df["Start"]       = perf_df["Start"].map("{:.2f}".format)
        perf_df["Ende"]        = perf_df["Ende"].map("{:.2f}".format)
        perf_df["Performance"] = perf_df["Performance"].map("{:+.2f} %".format)
        perf_df["Chart"]       = perf_df["Ticker"].apply(chart_link)
        st.dataframe(
            perf_df, use_container_width=True, hide_index=True,
            column_config={
                "Chart": st.column_config.LinkColumn("Chart", display_text="📈 Öffnen"),
            },
        )
        st.caption(
            "Fundamentalscore (Piotroski-F-Score/Gross-Profitability/Fama-French-Proxy) — nur aus "
            "dem Cache, für frische Werte oben unter 'Kurschart' den Refresh-Button nutzen oder "
            "'python main.py fundamentals' laufen lassen."
        )

    # ── Top Performer über die gesamte (kontinent-gefilterte) Watchlist ──────
    st.divider()
    st.subheader("🏆 Top Performer (gesamte Watchlist im Zeitraum)")
    st.caption(
        "Rankt alle Ticker der aktuell per Kontinent gefilterten Watchlist nach "
        "Kursperformance im gewählten Zeitraum — unabhängig von der Auswahl oben. "
        "Nutzt bereits geladene Kursdaten (ggf. erst 'Kurse laden' in der Sidebar "
        "ausführen); Ticker ohne ausreichende Historie werden übersprungen."
    )
    if st.button("🔍 Top-Performer ermitteln", key="cmp_top_btn"):
        rows_all = []
        skipped = 0
        with st.spinner(f"Werte {len(wl_filtered)} Ticker aus …"):
            for _, wl_row in wl_filtered.iterrows():
                t = wl_row["ticker"]
                p = load_prices(t, start, end)
                if len(p) < 2:
                    skipped += 1
                    continue
                first, last = p["close"].iloc[0], p["close"].iloc[-1]
                rows_all.append({
                    "Ticker":      t,
                    "Name":        wl_row["name"],
                    "Start":       first,
                    "Ende":        last,
                    "Performance": (last / first - 1) * 100,
                })

        if not rows_all:
            st.info(f"Keine auswertbaren Kursdaten gefunden ({skipped} Ticker übersprungen).")
        else:
            top_all_df = pd.DataFrame(rows_all).sort_values("Performance", ascending=False)
            top_n = st.slider(
                "Anzahl anzeigen", min_value=5,
                max_value=max(5, min(50, len(top_all_df))),
                value=min(15, len(top_all_df)), key="cmp_top_n",
            )
            disp = top_all_df.head(top_n).copy()
            top_universe = tuple(sorted(wl_filtered[wl_filtered["asset_type"] == "stock"]["ticker"]))
            top_fund_scores = load_fundamental_scores(top_universe).set_index("ticker")
            disp["Fundamentalscore"] = disp["Ticker"].map(
                lambda t: fundamental_badge(top_fund_scores.loc[t, "fundamental_score"])
                          if t in top_fund_scores.index else "➖ n/a"
            )
            disp["Start"]       = disp["Start"].map("{:.2f}".format)
            disp["Ende"]        = disp["Ende"].map("{:.2f}".format)
            disp["Performance"] = disp["Performance"].map("{:+.2f} %".format)
            disp["Chart"]       = disp["Ticker"].apply(chart_link)
            st.dataframe(
                disp, use_container_width=True, hide_index=True,
                column_config={
                    "Chart": st.column_config.LinkColumn("Chart", display_text="📈 Öffnen"),
                },
            )
            st.caption(
                f"{len(top_all_df)} Ticker ausgewertet, {skipped} übersprungen "
                "(keine/zu wenig Kursdaten im Zeitraum)."
            )
