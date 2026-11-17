"""Tab „Marktüberblick“."""
from collections import Counter

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.data import load_all_news, load_watchlist
from dashboard.navigation import chart_link
from dashboard.theme import CONTINENT_EMOJI, SENTIMENT_COLOR

# ─── Tab 3: Marktüberblick ───────────────────────────────────────────────────

def tab_overview(continent: str):
    st.subheader("🌍 Marktüberblick")
    all_news = load_all_news(limit=500)
    wl       = load_watchlist()

    if all_news.empty:
        st.info("Noch keine Daten. Bitte 'News laden' in der Sidebar klicken.")
        return

    # KPIs
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Gesamt Artikel",  len(all_news))
    k2.metric("Positiv",  int((all_news["sentiment_label"] == "positive").sum()))
    k3.metric("Negativ",  int((all_news["sentiment_label"] == "negative").sum()))
    k4.metric("Neutral",  int((all_news["sentiment_label"] == "neutral").sum()))

    # Sentiment-Pie + Timeline
    c1, c2 = st.columns(2)
    with c1:
        sc = all_news["sentiment_label"].value_counts()
        fig_pie = go.Figure(go.Pie(
            labels=sc.index, values=sc.values,
            marker_colors=[SENTIMENT_COLOR.get(l, "#aaa") for l in sc.index],
            hole=0.42, textinfo="label+percent",
        ))
        fig_pie.update_layout(title="Sentiment-Verteilung", template="plotly_dark",
                               height=300, paper_bgcolor="rgba(0,0,0,0)",
                               margin=dict(l=10,r=10,t=40,b=10))
        st.plotly_chart(fig_pie, use_container_width=True)

    with c2:
        nbd = (
            all_news.groupby(["date", "sentiment_label"]).size()
            .unstack(fill_value=0).reset_index()
        )
        fig_bar = go.Figure()
        for label in ("positive", "neutral", "negative"):
            if label in nbd.columns:
                fig_bar.add_trace(go.Bar(
                    x=nbd["date"], y=nbd[label],
                    name=label.capitalize(),
                    marker_color=SENTIMENT_COLOR[label],
                ))
        fig_bar.update_layout(barmode="stack", title="News-Volumen pro Tag",
                               template="plotly_dark", height=300,
                               paper_bgcolor="rgba(0,0,0,0)",
                               margin=dict(l=10,r=10,t=40,b=10))
        st.plotly_chart(fig_bar, use_container_width=True)

    # Quellen-Statistik
    st.subheader("Quellen")
    src_stats = (
        all_news.groupby("source").agg(
            Artikel    =("id",              "count"),
            Positiv    =("sentiment_label", lambda x: (x=="positive").sum()),
            Negativ    =("sentiment_label", lambda x: (x=="negative").sum()),
            Ø_Score    =("sentiment_score", "mean"),
        ).reset_index()
        .rename(columns={"source": "Quelle"})
        .sort_values("Artikel", ascending=False)
    )
    src_stats["Ø_Score"] = src_stats["Ø_Score"].round(3)
    st.dataframe(src_stats, use_container_width=True, hide_index=True)

    # Top Ticker
    st.subheader("Top 20 meistgenannte Ticker")
    all_t = [
        t.strip()
        for row in all_news["tickers"].dropna()
        for t in str(row).split(",")
        if t.strip() and t.strip() != "None"
    ]
    if all_t:
        top = pd.DataFrame(Counter(all_t).most_common(20), columns=["Ticker", "Erwähnungen"])
        fig_h = go.Figure(go.Bar(
            x=top["Erwähnungen"], y=top["Ticker"],
            orientation="h", marker_color="#2979FF",
        ))
        fig_h.update_layout(
            template="plotly_dark", height=420,
            paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(autorange="reversed"),
            margin=dict(l=90, r=10, t=10, b=10),
        )
        st.plotly_chart(fig_h, use_container_width=True)

    # Watchlist-Übersicht nach Kontinent
    st.subheader("Watchlist nach Kontinent")
    if not wl.empty:
        for cont in wl["continent"].unique():
            subset = wl[wl["continent"] == cont]
            emoji  = CONTINENT_EMOJI.get(cont, "")
            with st.expander(f"{emoji} {cont}  ({len(subset)} Ticker)"):
                subset_disp = subset[["ticker", "name", "asset_type"]].reset_index(drop=True).copy()
                subset_disp["Chart"] = subset_disp["ticker"].apply(chart_link)
                st.dataframe(
                    subset_disp, use_container_width=True, hide_index=True,
                    column_config={
                        "Chart": st.column_config.LinkColumn("Chart", display_text="📈 Öffnen"),
                    },
                )
