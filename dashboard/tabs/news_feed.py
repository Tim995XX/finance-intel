"""Tab „News-Feed“."""
import pandas as pd
import streamlit as st

from dashboard.data import load_news_for_ticker, load_watchlist
from dashboard.theme import SENTIMENT_ICON

# ─── Tab 2: News-Feed ────────────────────────────────────────────────────────

def tab_newsfeed(start: str, end: str, continent: str):
    wl = load_watchlist()
    if wl.empty:
        st.info("Watchlist leer.")
        return

    if continent != "Alle":
        wl = wl[wl["continent"] == continent]

    c1, c2 = st.columns([3, 1])
    selected = c1.multiselect(
        "Ticker filtern",
        wl["ticker"].tolist(),
        default=wl["ticker"].tolist()[:6],
    )
    sentiment_filter = c2.multiselect(
        "Sentiment",
        ["positive", "neutral", "negative"],
        default=["positive", "neutral", "negative"],
    )

    if not selected:
        st.info("Mindestens einen Ticker auswählen.")
        return

    frames = [load_news_for_ticker(t, start, end) for t in selected]
    all_news = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["id"])
    all_news = all_news[all_news["sentiment_label"].isin(sentiment_filter)]
    all_news = all_news.sort_values("published_at", ascending=False)

    if all_news.empty:
        st.info("Keine passenden News gefunden.")
        return

    st.markdown(f"**{len(all_news)} Artikel** gefunden")
    for _, row in all_news.iterrows():
        icon  = SENTIMENT_ICON.get(row["sentiment_label"], "⚪")
        with st.expander(f"{icon}  **{row['date']}**  |  {row['source']}  —  {row['title'][:95]}"):
            c1, c2 = st.columns([4, 1])
            c1.write(row["summary"] or "—")
            c2.metric("Sentiment", row["sentiment_label"].capitalize(),
                       delta=f"{row['sentiment_score']:+.2f}")
            if row["url"]:
                c1.markdown(f"[→ Artikel]({row['url']})")
