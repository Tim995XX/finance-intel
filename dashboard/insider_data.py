"""Insider-Trades laden, aufbereiten und nachladen (Congress, ARK, DJT)."""
import pandas as pd
import streamlit as st

from dashboard.data import load_prices, load_watchlist
from dashboard.fetching import fetch_and_store_prices, prices_need_update
from src.database.db import get_connection

# ─── Insider-Daten (genutzt von Kurschart, Sidebar und Insider-Tabs) ─────────


TRADE_TYPE_ICON  = {"buy": "🟢", "sell": "🔴", "exchange": "🔄"}
SOURCE_LABEL     = {
    "senate": "Senat", "house": "Repräsentantenhaus", "ark": "ARK Invest",
    "djt_insider": "Trump Media (Familie)",
}


@st.cache_data(ttl=120)
def load_insider_trades(days: int = 180) -> pd.DataFrame:
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = get_connection()
    df = pd.read_sql(
        """SELECT id, source, trader, ticker, company,
                  trade_date, trade_type,
                  amount_min, amount_max, shares_delta,
                  fund, raw_amount
           FROM insider_trades
           WHERE trade_date >= ?
           ORDER BY trade_date DESC""",
        conn, params=[cutoff],
    )
    conn.close()
    return df


@st.cache_data(ttl=120)
def insider_active_tickers(days: int = 90) -> pd.DataFrame:
    """Ticker-Universum für den Tab 'Insider-Papier-Trades': ein Ticker pro
    Zeile, für den es in den letzten `days` Tagen mindestens einen
    Insider-Trade (Congress/Senate/House, ARK, Trump-Familie) gab, mit
    Firmenname, jüngstem Trade-Datum und Anzahl Trades im Fenster."""
    trades = load_insider_trades(days=days)
    if trades.empty:
        return pd.DataFrame(columns=["ticker", "company", "last_trade_date", "trade_count"])
    g = (
        trades.groupby("ticker")
        .agg(company=("company", "first"),
             last_trade_date=("trade_date", "max"),
             trade_count=("trade_date", "count"))
        .reset_index()
        .sort_values("last_trade_date", ascending=False)
    )
    return g


def ensure_insider_price_history(tickers: list[str]) -> dict:
    """Stellt sicher, dass für jeden übergebenen Ticker Kurs-Historie
    vorhanden ist, damit die Elliott-Wellen-Analyse darauf laufen kann.
    Ticker, die noch nicht in der Watchlist stehen (viele Insider-Trade-Ticker
    sind es nicht, siehe apply_query_param_ticker), werden einmalig
    hinzugefügt (add-only) und ihre Kurse per fetch_and_store_prices()
    nachgeladen — derselbe Mechanismus wie beim automatischen Watchlist-Add
    über einen Chart-Link. Bereits aktuelle Ticker werden übersprungen."""
    conn = get_connection()
    existing_wl = {r["ticker"] for r in conn.execute("SELECT ticker FROM watchlist").fetchall()}
    new_tickers = [t for t in tickers if t not in existing_wl]
    for t in new_tickers:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (ticker, name, asset_type, continent) VALUES (?,?,?,?)",
            (t, t, "stock", "Global"),
        )
    conn.commit()
    conn.close()
    if new_tickers:
        load_watchlist.clear()

    refreshed = 0
    for t in tickers:
        if prices_need_update(t):
            fetch_and_store_prices(t)
            refreshed += 1
    if refreshed:
        load_prices.clear()

    return {"added_to_watchlist": len(new_tickers), "price_refresh_attempted": refreshed}


@st.cache_data(ttl=120)
def load_ark_latest() -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql(
        """SELECT ah.fund, ah.ticker, ah.company,
                  ah.shares, ah.market_value, ah.weight_pct,
                  ah.snapshot_date
           FROM ark_holdings ah
           INNER JOIN (
               SELECT fund, MAX(snapshot_date) AS max_date
               FROM ark_holdings GROUP BY fund
           ) latest ON ah.fund = latest.fund AND ah.snapshot_date = latest.max_date
           ORDER BY ah.fund, ah.weight_pct DESC""",
        conn,
    )
    conn.close()
    return df


def fmt_amount(lo, hi) -> str:
    if lo is None and hi is None:
        return "—"
    if lo == hi or hi is None:
        return f"${lo:,.0f}"
    return f"${lo:,.0f} – ${hi:,.0f}"


def run_insider_fetch():
    """Insider-Trades im Dashboard per Knopf laden (alle Quellen parallel)."""
    from datetime import date as _date

    from src.database.insider_store import fetch_and_store_insiders

    with st.sidebar.status("Insider-Trades laden …", expanded=True) as status:
        st.write("⏳ Congress, ARK und Trump-Familie werden parallel abgerufen …")
        r = fetch_and_store_insiders(_date.today().strftime("%Y-%m-%d"))
        if r["congress_error"] == "no_key":
            st.write("⚠️ Congress-Trades: kein FMP-Key → übersprungen")
        else:
            st.write(f"✅ {r['congress_new']} neue Congress-Trades ({r['congress_fetched']} geladen)")
        st.write(f"✅ {r['ark_new']} neue ARK-Trades ({r['ark_fetched']} geladen)")
        st.write(f"✅ {r['holdings_new']} neue Holdings-Zeilen")
        st.write(f"✅ {r['djt_new']} neue Trump-Familie-Trades ({r['djt_fetched']} geladen)")
        status.update(label="✅ Insider-Trades aktualisiert", state="complete")
    st.cache_data.clear()
