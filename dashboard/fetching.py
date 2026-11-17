"""Nachladen von News und Kursen aus dem Dashboard heraus (mit Statusanzeige)."""
import streamlit as st

from src.database.db import get_connection

# ─── Data fetch helpers (run inside sidebar status widget) ───────────────────

def run_news_fetch():
    import time as _t

    from src.fetchers.news_fetcher import fetch_feed
    from src.processors.text_processor import ensure_nltk_data, process_item

    ensure_nltk_data()
    conn    = get_connection()
    sources = conn.execute("SELECT * FROM sources WHERE active=1").fetchall()
    conn.close()

    with st.sidebar.status("News laden …", expanded=True) as status:
        total = 0
        for src in sources:
            st.write(f"⏳ {src['name']}")
            items = fetch_feed(src["feed_url"])
            new_c = 0
            conn  = get_connection()
            for item in items:
                if not item["url"]:
                    continue
                if conn.execute("SELECT id FROM news_items WHERE url=?", (item["url"],)).fetchone():
                    continue
                proc = process_item(item["title"], item["raw_content"], src["language"])
                cur  = conn.execute(
                    """INSERT OR IGNORE INTO news_items
                           (source_id,title,url,published_at,raw_content,
                            summary,sentiment_label,sentiment_score)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (src["id"], item["title"], item["url"], item["published_at"],
                     item["raw_content"], proc["summary"],
                     proc["sentiment_label"], proc["sentiment_score"]),
                )
                if cur.rowcount > 0:
                    nid = cur.lastrowid
                    for ticker, cname in proc["tickers"]:
                        conn.execute(
                            "INSERT INTO news_tickers (news_id,ticker,company_name) VALUES (?,?,?)",
                            (nid, ticker, cname),
                        )
                    new_c += 1
            conn.commit()
            conn.close()
            total += new_c
            _t.sleep(0.3)
        status.update(label=f"✅ {total} neue Artikel geladen", state="complete")
    st.cache_data.clear()


def fetch_and_store_prices(ticker: str) -> int:
    """Fetch + store prices for one ticker. Returns number of new rows."""
    from src.database.db import store_prices
    from src.fetchers.stock_fetcher import fetch_prices
    df = fetch_prices(ticker, days=400)
    if df.empty:
        return 0
    conn = get_connection()
    inserted = store_prices(conn, df)
    conn.commit()
    conn.close()
    return inserted


def prices_need_update(ticker: str) -> bool:
    """True if ticker has no prices or latest date is before today."""
    from datetime import date
    conn   = get_connection()
    latest = conn.execute(
        "SELECT MAX(date) FROM stock_prices WHERE ticker=?", (ticker,)
    ).fetchone()[0]
    conn.close()
    if latest is None:
        return True
    return latest < date.today().strftime("%Y-%m-%d")


def run_price_fetch():
    """Refresh prices only for tickers that already have data (not all 131)."""
    from src.database.db import store_prices
    from src.database.price_history import needs_full_reload
    from src.fetchers.stock_fetcher import UPDATE_DAYS, fetch_prices_batch

    conn = get_connection()
    # Only refresh tickers already in price DB — not the full 131-ticker universe
    tickers_with_data = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT ticker FROM stock_prices ORDER BY ticker"
        ).fetchall()
    ]
    crypto = {r[0] for r in conn.execute("SELECT ticker FROM watchlist WHERE asset_type='crypto'").fetchall()}
    conn.close()

    BATCH_SIZE = 40
    with st.sidebar.status(f"Kurse für {len(tickers_with_data)} Ticker aktualisieren …",
                           expanded=True) as status:
        for i in range(0, len(tickers_with_data), BATCH_SIZE):
            chunk = tickers_with_data[i:i + BATCH_SIZE]
            st.write(f"⏳ Batch {i // BATCH_SIZE + 1}: {', '.join(chunk)}")
            results = fetch_prices_batch(chunk, days=UPDATE_DAYS)
            conn = get_connection()
            for t in chunk:
                df = results.get(t)
                if df is None or df.empty:
                    continue
                # Geänderte Kursbasis (Split) nicht überschreiben — sonst wäre
                # sie für den Abgleich in main.fetch_all_prices() unsichtbar,
                # der dann die komplette Historie ersetzt.
                if t not in crypto and needs_full_reload(conn, t, df):
                    st.write(f"⚠️ {t}: Kursbasis geändert (Split?) — wird beim nächsten "
                             "`python main.py prices` komplett neu geladen")
                    continue
                store_prices(conn, df, overwrite=t not in crypto)
            conn.commit()
            conn.close()
        status.update(label="✅ Kurse aktualisiert", state="complete")
    st.cache_data.clear()
