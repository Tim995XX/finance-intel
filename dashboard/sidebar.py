"""Seitenleiste: Zeitraum, Kontinent und Buttons zum Nachladen."""
from datetime import datetime, timedelta

import streamlit as st

from dashboard.fetching import run_news_fetch, run_price_fetch
from dashboard.insider_data import run_insider_fetch
from dashboard.theme import CONTINENT_EMOJI
from src.database.db import get_connection
from src.watchlist_updater import ALL_CONTINENTS

# ─── Sidebar ─────────────────────────────────────────────────────────────────

def render_sidebar() -> tuple[str, str, str]:
    st.sidebar.markdown(
        """
        <div class="fi-sidebar-brand">
            <h2>📈 Finance Intel</h2>
            <p>Markets · News · Insider Trades</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Zeitraum
    st.sidebar.subheader("Zeitraum")
    today     = datetime.today().date()
    default_s = today - timedelta(days=180)
    c1, c2    = st.sidebar.columns(2)
    start = c1.date_input("Von", value=default_s, key="dt_s")
    end   = c2.date_input("Bis", value=today,      key="dt_e")

    # Kontinent-Filter
    st.sidebar.subheader("Kontinent")
    continent_options = [f"{CONTINENT_EMOJI.get(c, '')} {c}" for c in ALL_CONTINENTS]
    selected_label = st.sidebar.selectbox(
        "Kontinent wählen", continent_options,
        index=0, key="continent_sel",
    )
    selected_continent = selected_label.split(" ", 1)[1].strip()

    # Watchlist verwalten
    st.sidebar.subheader("Watchlist")
    with st.sidebar.expander("+ Ticker hinzufügen"):
        new_t = st.text_input("Ticker (z.B. AAPL, WES.AX)", key="add_t").upper().strip()
        new_n = st.text_input("Name",                         key="add_n")
        new_c = st.selectbox("Kontinent", ALL_CONTINENTS[1:], key="add_c")
        new_a = st.selectbox("Typ", ["stock","etf","index","crypto","commodity"], key="add_a")
        if st.button("Hinzufügen", key="btn_add"):
            if new_t:
                conn = get_connection()
                conn.execute(
                    "INSERT OR IGNORE INTO watchlist (ticker,name,asset_type,continent) VALUES (?,?,?,?)",
                    (new_t, new_n or new_t, new_a, new_c),
                )
                conn.commit()
                conn.close()
                st.cache_data.clear()
                st.rerun()

    # Daten abrufen
    st.sidebar.divider()
    st.sidebar.subheader("Daten abrufen")
    if st.sidebar.button("📰  News laden", use_container_width=True):
        run_news_fetch()
    if st.sidebar.button("📊  Kurse laden", use_container_width=True):
        run_price_fetch()
    if st.sidebar.button("🔄  Watchlist sync", use_container_width=True,
                          help="Neue Kontinent-Aktien hinzufügen (löscht nichts)"):
        from src.watchlist_updater import update_watchlist
        added = update_watchlist()
        st.cache_data.clear()
        st.sidebar.success(f"{added} neue Ticker hinzugefügt" if added else "Alles aktuell")
    if st.sidebar.button("🏛️  Insider laden", use_container_width=True,
                          help="Congress-Trades + ARK Holdings aktualisieren"):
        run_insider_fetch()

    return str(start), str(end), selected_continent
