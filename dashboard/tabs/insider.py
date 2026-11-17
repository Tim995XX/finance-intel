"""Tab „Insider Trades“ mit eingebettetem Chart je Ticker."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.charts import build_price_chart, render_chart_with_news_link
from dashboard.data import (
    load_insider_trades_for_ticker,
    load_news_for_ticker,
    load_observations,
    load_prices,
)
from dashboard.fetching import fetch_and_store_prices, prices_need_update
from dashboard.insider_data import (
    SOURCE_LABEL,
    TRADE_TYPE_ICON,
    load_ark_latest,
    load_insider_trades,
)
from src.database.db import get_connection
from src.fetchers.insider_fetcher import TRUMP_CIRCLE


def _render_embedded_chart(ticker: str, start: str, end: str) -> None:
    """Rendert Kurschart + News/Insider-Marker direkt inline (z.B. unter einer
    Tabelle im Insider-Trades-Tab), ohne dass zum Kurschart-Tab gewechselt
    werden muss. Funktioniert auch für Ticker außerhalb der Watchlist."""
    if prices_need_update(ticker):
        with st.spinner(f"Lade Kurse für **{ticker}** …"):
            fetch_and_store_prices(ticker)
            load_prices.clear()

    prices = load_prices(ticker, start, end)
    if prices.empty:
        st.warning(f"Keine Kursdaten für **{ticker}** verfügbar.")
        return

    news    = load_news_for_ticker(ticker, start, end)
    obs     = load_observations(ticker)
    insider = load_insider_trades_for_ticker(ticker, start, end)

    last  = prices["close"].iloc[-1]
    prev  = prices["close"].iloc[-2] if len(prices) > 1 else last
    delta = (last - prev) / prev * 100
    k1, k2, k3 = st.columns(3)
    k1.metric("Letzter Kurs", f"{last:.2f}", f"{delta:+.2f} %")
    k2.metric("52W Hoch",     f"{prices['high'].max():.2f}")
    k3.metric("52W Tief",     f"{prices['low'].min():.2f}")

    fig = build_price_chart(prices, news, obs, ticker, "Candlestick", insider)
    render_chart_with_news_link(fig, height=420)


def _clickable_ticker_table(
    display: pd.DataFrame, ticker_col: str, key: str, column_config: dict | None = None,
) -> str | None:
    """Rendert eine Tabelle mit anklickbaren Zeilen; gibt den Ticker der
    ausgewählten Zeile zurück (oder None), damit darunter der Chart inline
    eingeblendet werden kann."""
    event = st.dataframe(
        display, use_container_width=True, hide_index=True,
        on_select="rerun", selection_mode="single-row",
        key=key, column_config=column_config or {},
    )
    rows = event.selection.rows if event is not None else []
    if rows:
        return str(display.iloc[rows[0]][ticker_col])
    return None


def tab_insider_trades(start: str, end: str):
    df = load_insider_trades(days=365)

    if df.empty:
        st.info("Noch keine Insider-Trades gespeichert. Bitte '🏛️ Insider laden' in der Sidebar klicken.")
        return

    # Zeitraum-Filter anwenden
    df = df[(df["trade_date"] >= start) & (df["trade_date"] <= end)]

    # ── Filter-Zeile ─────────────────────────────────────────────────────────
    fc1, fc2, fc3, fc4 = st.columns([2, 2, 2, 2])

    source_opts = ["Alle"] + sorted(df["source"].unique().tolist())
    sel_source  = fc1.selectbox(
        "Quelle",
        source_opts,
        format_func=lambda s: SOURCE_LABEL.get(s, s.capitalize()) if s != "Alle" else "Alle",
        key="ins_src",
    )

    type_opts = ["Alle", "buy", "sell", "exchange"]
    sel_type  = fc2.selectbox(
        "Trade-Typ",
        type_opts,
        format_func=lambda t: {"buy": "🟢 Kauf", "sell": "🔴 Verkauf", "exchange": "🔄 Tausch", "Alle": "Alle"}.get(t, t),
        key="ins_type",
    )

    all_traders = sorted(df["trader"].unique().tolist())
    sel_traders = fc3.multiselect("Trader filtern", all_traders, key="ins_trader",
                                   placeholder="Alle Trader")

    ticker_input = fc4.text_input("Ticker", key="ins_ticker",
                                   placeholder="z.B. TSLA, NVDA").upper().strip()

    # Filter anwenden
    view = df.copy()
    if sel_source != "Alle":
        view = view[view["source"] == sel_source]
    if sel_type != "Alle":
        view = view[view["trade_type"] == sel_type]
    if sel_traders:
        view = view[view["trader"].isin(sel_traders)]
    if ticker_input:
        view = view[view["ticker"].str.contains(ticker_input, case=False, na=False)]

    # ── KPIs ─────────────────────────────────────────────────────────────────
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Trades gesamt",    len(view))
    k2.metric("Käufe",  int((view["trade_type"] == "buy").sum()))
    k3.metric("Verkäufe", int((view["trade_type"] == "sell").sum()))
    k4.metric("Trader",  view["trader"].nunique())

    # ── Top Performer (Buy-Trades seit Einstieg) ─────────────────────────────
    # Basiert auf Quelle/Trader/Ticker-Filter, aber unabhängig vom Trade-Typ-Filter
    # (eine "Performance" ergibt nur für Käufe Sinn, egal was oben ausgewählt ist).
    from src.analytics.backtest import rank_trade_performance

    buy_view = df[df["trade_type"] == "buy"].copy()
    if sel_source != "Alle":
        buy_view = buy_view[buy_view["source"] == sel_source]
    if sel_traders:
        buy_view = buy_view[buy_view["trader"].isin(sel_traders)]
    if ticker_input:
        buy_view = buy_view[buy_view["ticker"].str.contains(ticker_input, case=False, na=False)]

    st.subheader("🏆 Top Performer (Buy-Trades seit Einstieg)")
    if buy_view.empty:
        st.caption("Keine Buy-Trades im aktuellen Filter.")
    else:
        conn = get_connection()
        ranked = rank_trade_performance(conn, buy_view.to_dict("records"))
        conn.close()

        if not ranked:
            st.caption(
                "Keine der gefilterten Buy-Trades hat auswertbare Kursdaten "
                "(Ticker ggf. noch nicht in der Watchlist / Kurse geladen)."
            )
        else:
            top_n = st.slider(
                "Anzahl anzeigen", min_value=5,
                max_value=max(5, min(50, len(ranked))),
                value=min(15, len(ranked)), key="ins_top_n",
            )
            perf_df = pd.DataFrame(ranked[:top_n]).rename(columns={
                "trader": "Trader", "ticker": "Ticker", "company": "Unternehmen",
                "source": "Quelle", "trade_date": "Trade-Datum",
                "entry_date": "Einstieg", "entry_price": "Einstiegskurs",
                "latest_date": "Kurs vom", "latest_price": "Aktuell",
                "return_pct": "Performance",
            })
            perf_df["Quelle"]        = perf_df["Quelle"].map(lambda s: SOURCE_LABEL.get(s, s))
            perf_df["Einstiegskurs"] = perf_df["Einstiegskurs"].map("{:.2f}".format)
            perf_df["Aktuell"]       = perf_df["Aktuell"].map("{:.2f}".format)
            perf_df["Performance"]   = perf_df["Performance"].map("{:+.1f} %".format)
            st.caption("🖱️ Zeile anklicken, um den Chart darunter einzublenden.")
            clicked = _clickable_ticker_table(
                perf_df[["Ticker", "Unternehmen", "Trader", "Quelle", "Trade-Datum",
                          "Einstieg", "Einstiegskurs", "Kurs vom", "Aktuell",
                          "Performance"]],
                ticker_col="Ticker", key="ins_perf_tbl",
            )
            if clicked:
                st.markdown(f"#### 📈 {clicked}")
                _render_embedded_chart(clicked, start, end)

    # ── Trump-Kreis Highlight ─────────────────────────────────────────────────
    # Zwei Quellen fließen hier zusammen: (1) Congress-/Administration-Mitglieder
    # aus TRUMP_CIRCLE (per Namensabgleich), (2) alle djt_insider-Trades
    # (Trump-Familie bei Trump Media/DJT) unabhängig vom Namen.
    trump_df = view[view["trader"].isin(TRUMP_CIRCLE) | (view["source"] == "djt_insider")]
    if not trump_df.empty:
        with st.expander(f"🇺🇸 Trump-Kreis Trades ({len(trump_df)} im Zeitraum)", expanded=True):
            st.caption(
                "Umfasst Trump-nahe Congress-Mitglieder, aktuelle Kabinettsmitglieder/VP "
                "sowie Trump-Familienmitglieder als Insider bei Trump Media & Technology Group (DJT)."
            )
            trump_disp = trump_df.head(30)[
                ["trade_date", "trader", "ticker", "company", "trade_type", "raw_amount", "source"]
            ].copy()
            trump_disp["source"] = trump_disp["source"].map(lambda s: SOURCE_LABEL.get(s, s.capitalize()))
            trump_disp["trade_type"] = trump_disp["trade_type"].apply(
                lambda x: f"{TRADE_TYPE_ICON.get(x, '⚪')} {x.upper()}"
            )
            trump_disp = trump_disp.rename(columns={
                "trade_date": "Datum", "trader": "Trader", "ticker": "Ticker",
                "company": "Unternehmen", "trade_type": "Typ",
                "raw_amount": "Betrag", "source": "Quelle",
            })
            st.caption("🖱️ Zeile anklicken, um den Chart direkt darunter einzublenden (inkl. gelber ℹ-Marker am Trade-Tag).")
            clicked = _clickable_ticker_table(trump_disp, ticker_col="Ticker", key="ins_trump_tbl")
            if clicked:
                st.markdown(f"#### 📈 {clicked}")
                _render_embedded_chart(clicked, start, end)

    # ── Tabs: Congress / ARK ─────────────────────────────────────────────────
    ct1, ct2 = st.tabs(["🏛️  Congress (Senat + Repräsentantenhaus)", "🦄  ARK Invest (Cathie Wood)"])

    with ct1:
        from src.fetchers.insider_fetcher import get_fmp_key, save_fmp_key
        if not get_fmp_key():
            st.warning(
                "**Congress-Trades erfordern einen kostenlosen FMP-API-Key.**\n\n"
                "1. Gratis registrieren: [financialmodelingprep.com](https://financialmodelingprep.com)\n"
                "2. Key unten eingeben und speichern — danach '🏛️ Insider laden' klicken."
            )
            key_input = st.text_input("FMP API-Key eingeben", type="password", key="fmp_key_in")
            if st.button("Key speichern", key="fmp_key_save") and key_input.strip():
                save_fmp_key(key_input.strip())
                st.success("Key gespeichert! Bitte '🏛️ Insider laden' klicken.")
                st.rerun()

        congress_df = view[view["source"].isin(["senate", "house"])].copy()
        if congress_df.empty:
            st.info("Keine Congress-Trades vorhanden. Bitte '🏛️ Insider laden' klicken (FMP-Key erforderlich).")
        else:
            # Top-Trader Balkendiagramm
            top_traders = congress_df["trader"].value_counts().head(15)
            if not top_traders.empty:
                fig_t = go.Figure(go.Bar(
                    x=top_traders.values, y=top_traders.index,
                    orientation="h", marker_color="#2979FF",
                ))
                fig_t.update_layout(
                    title="Top 15 aktivste Trader",
                    template="plotly_dark", height=350,
                    paper_bgcolor="rgba(0,0,0,0)",
                    yaxis=dict(autorange="reversed"),
                    margin=dict(l=180, r=10, t=40, b=10),
                )
                st.plotly_chart(fig_t, use_container_width=True)

            # Tabelle
            st.markdown(f"**{len(congress_df)} Trades**")
            display = congress_df[["trade_date", "source", "trader", "ticker",
                                    "company", "trade_type", "raw_amount"]].copy()
            display["source"]     = display["source"].map(SOURCE_LABEL)
            display["trade_type"] = display["trade_type"].apply(
                lambda x: f"{TRADE_TYPE_ICON.get(x, '')} {x.upper()}"
            )
            display = display.rename(columns={
                "trade_date": "Datum", "source": "Kammer", "trader": "Trader",
                "ticker": "Ticker", "company": "Unternehmen",
                "trade_type": "Typ", "raw_amount": "Betrag",
            })
            st.caption("🖱️ Zeile anklicken, um den Chart darunter einzublenden.")
            clicked = _clickable_ticker_table(display, ticker_col="Ticker", key="ins_congress_tbl")
            if clicked:
                st.markdown(f"#### 📈 {clicked}")
                _render_embedded_chart(clicked, start, end)

    with ct2:
        ark_df = view[view["source"] == "ark"].copy()

        # Aktuelle Holdings anzeigen
        st.subheader("Aktuelle ARK-Positionen (neuester Snapshot)")
        holdings = load_ark_latest()
        if not holdings.empty:
            fund_sel = st.selectbox(
                "Fonds",
                ["Alle"] + sorted(holdings["fund"].unique().tolist()),
                key="ark_fund_sel",
            )
            h_view = holdings if fund_sel == "Alle" else holdings[holdings["fund"] == fund_sel]
            h_display = h_view[["fund", "ticker", "company", "shares", "market_value", "weight_pct"]].copy()
            if "market_value" in h_display.columns:
                h_display["market_value"] = h_display["market_value"].apply(
                    lambda x: f"${x:,.0f}" if pd.notna(x) else "—"
                )
            if "weight_pct" in h_display.columns:
                h_display["weight_pct"] = h_display["weight_pct"].apply(
                    lambda x: f"{x:.2f}%" if pd.notna(x) else "—"
                )
            if "shares" in h_display.columns:
                h_display["shares"] = h_display["shares"].apply(
                    lambda x: f"{x:,.0f}" if pd.notna(x) else "—"
                )
            h_display = h_display.rename(columns={
                "fund": "Fonds", "ticker": "Ticker", "company": "Unternehmen",
                "shares": "Aktien", "market_value": "Marktwert ($)",
                "weight_pct": "Gewichtung",
            })
            st.caption("🖱️ Zeile anklicken, um den Chart darunter einzublenden.")
            clicked = _clickable_ticker_table(h_display, ticker_col="Ticker", key="ins_ark_holdings_tbl")
            if clicked:
                st.markdown(f"#### 📈 {clicked}")
                _render_embedded_chart(clicked, start, end)
        else:
            st.info("Noch keine ARK-Holdings gespeichert.")

        # ARK Trades
        st.subheader("Erkannte ARK-Käufe & Verkäufe")
        if ark_df.empty:
            st.info("Keine ARK-Trades im Zeitraum. Bitte 'Insider laden' klicken.")
        else:
            col_b, col_s = st.columns(2)
            ark_buys  = ark_df[ark_df["trade_type"] == "buy"].sort_values("shares_delta", ascending=False)
            ark_sells = ark_df[ark_df["trade_type"] == "sell"].sort_values("shares_delta")

            clicked = None
            with col_b:
                st.markdown("**🟢 Käufe**")
                if not ark_buys.empty:
                    b_disp = ark_buys[["trade_date", "fund", "ticker", "shares_delta"]].rename(
                        columns={"trade_date": "Datum", "fund": "Fonds",
                                 "ticker": "Ticker", "shares_delta": "Δ Aktien"}
                    )
                    b_disp["Δ Aktien"] = b_disp["Δ Aktien"].apply(lambda x: f"+{x:,.0f}" if pd.notna(x) else "—")
                    clicked = _clickable_ticker_table(b_disp, ticker_col="Ticker", key="ins_ark_buys_tbl") or clicked

            with col_s:
                st.markdown("**🔴 Verkäufe**")
                if not ark_sells.empty:
                    s_disp = ark_sells[["trade_date", "fund", "ticker", "shares_delta"]].rename(
                        columns={"trade_date": "Datum", "fund": "Fonds",
                                 "ticker": "Ticker", "shares_delta": "Δ Aktien"}
                    )
                    s_disp["Δ Aktien"] = s_disp["Δ Aktien"].apply(lambda x: f"{x:,.0f}" if pd.notna(x) else "—")
                    clicked = _clickable_ticker_table(s_disp, ticker_col="Ticker", key="ins_ark_sells_tbl") or clicked

            st.caption("🖱️ Zeile anklicken, um den Chart darunter einzublenden.")
            if clicked:
                st.markdown(f"#### 📈 {clicked}")
                _render_embedded_chart(clicked, start, end)
