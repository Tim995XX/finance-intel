"""Chart-Links per URL-Parameter (Ticker in neuem Browser-Tab öffnen)."""
from urllib.parse import quote

import streamlit as st

from dashboard.data import load_watchlist
from dashboard.theme import CONTINENT_EMOJI
from src.database.db import get_connection

# ─── Chart-Link (neuer Tab, per Ticker) ──────────────────────────────────────

def chart_link(ticker: str) -> str:
    """Relativer Link, der die App neu lädt und im Kurschart-Tab (erster Tab,
    daher standardmäßig aktiv) den gewünschten Ticker vorauswählt."""
    return f"?ticker={quote(str(ticker))}"


def apply_query_param_ticker() -> None:
    """Übernimmt einen per URL übergebenen Ticker (?ticker=XYZ) einmalig als
    Vorauswahl für den Kurschart-Tab. Viele Insider-Trade-Ticker (Congress/ARK)
    sind nicht Teil der kuratierten Watchlist — ohne Auto-Add würde der Link
    dort stillschweigend ins Leere laufen, weil die Ticker-Auswahl im Chart-Tab
    nur Watchlist-Ticker anbietet. Grobe Formatprüfung, da der Wert aus der
    URL kommt (keine ungeprüfte Übernahme beliebiger Strings in die DB)."""
    q_ticker = st.query_params.get("ticker")
    if not q_ticker or "ticker_sel" in st.session_state:
        return
    q_ticker = str(q_ticker).strip().upper()
    if not (1 <= len(q_ticker) <= 12 and all(c.isalnum() or c in ".-^=" for c in q_ticker)):
        return

    wl = load_watchlist()
    if q_ticker not in set(wl["ticker"].tolist()):
        conn = get_connection()
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (ticker,name,asset_type,continent) VALUES (?,?,?,?)",
            (q_ticker, q_ticker, "stock", "Global"),
        )
        conn.commit()
        conn.close()
        load_watchlist.clear()
        st.session_state["_chart_link_auto_added"] = q_ticker

    # Kontinent-Filter auf "Alle" zurücksetzen, damit der Ticker unabhängig vom
    # zuletzt gewählten Kontinent sichtbar ist (sonst würde die Ticker-Auswahl
    # im Chart-Tab den Wert ablehnen, falls er zu einem gefilterten Kontinent
    # nicht passt).
    st.session_state["continent_sel"] = f"{CONTINENT_EMOJI.get('Alle', '')} Alle"
    st.session_state["ticker_sel"] = q_ticker
