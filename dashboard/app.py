"""
Finance Intelligence Dashboard
Start: streamlit run dashboard/app.py   (aus dem Projektordner)

Dieses Modul baut nur die Seite zusammen; die Tabs liegen in dashboard/tabs/,
Datenzugriff, Charts und Hilfsfunktionen in den übrigen Modulen von dashboard/.
"""
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

# ─── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Finance Intelligence",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

from src.database.db import init_db  # noqa: E402 (nach sys.path)
from dashboard.data import load_watchlist  # noqa: E402 (nach sys.path)
from dashboard.navigation import apply_query_param_ticker  # noqa: E402 (nach sys.path)
from dashboard.sidebar import render_sidebar  # noqa: E402 (nach sys.path)
from dashboard.tabs.backtest import tab_backtest  # noqa: E402 (nach sys.path)
from dashboard.tabs.daytrading import tab_daytrading  # noqa: E402 (nach sys.path)
from dashboard.tabs.insider import tab_insider_trades  # noqa: E402 (nach sys.path)
from dashboard.tabs.insider_papier_trades import tab_paper_trades_insider  # noqa: E402 (nach sys.path)
from dashboard.tabs.krypto import tab_crypto_top50  # noqa: E402 (nach sys.path)
from dashboard.tabs.kurschart import tab_chart  # noqa: E402 (nach sys.path)
from dashboard.tabs.marktueberblick import tab_overview  # noqa: E402 (nach sys.path)
from dashboard.tabs.modellvergleich import tab_model_comparison  # noqa: E402 (nach sys.path)
from dashboard.tabs.news_feed import tab_newsfeed  # noqa: E402 (nach sys.path)
from dashboard.tabs.papier_trades import tab_paper_trades  # noqa: E402 (nach sys.path)
from dashboard.tabs.signale import tab_signals  # noqa: E402 (nach sys.path)
from dashboard.tabs.vergleich import tab_compare  # noqa: E402 (nach sys.path)
from dashboard.tabs.vorhersagen import tab_wave_predictions  # noqa: E402 (nach sys.path)
from dashboard.tabs.wellen_scanner import tab_wave_scanner  # noqa: E402 (nach sys.path)
from dashboard.theme import inject_custom_css  # noqa: E402 (nach sys.path)


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    init_db()
    inject_custom_css()
    apply_query_param_ticker()
    added = st.session_state.pop("_chart_link_auto_added", None)
    if added:
        st.toast(f"➕ {added} war noch nicht in der Watchlist — automatisch hinzugefügt.", icon="➕")

    wl_count = len(load_watchlist())
    st.markdown(
        f"""
        <div class="fi-header">
            <div>
                <h1>📈 Finance Intelligence</h1>
                <p>Kurse · News-Sentiment · Insider-Trades · Elliott-Wellen-Analyse</p>
            </div>
            <div class="fi-meta">
                <div><b>{wl_count}</b> Ticker in Watchlist</div>
                <div>{datetime.today().strftime('%A, %d.%m.%Y')}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    start, end, continent = render_sidebar()

    t1, t2, t3, t4, t5, t6, t7, t8, t9, t_cmp, t10, t11, t12, t13 = st.tabs([
        "📈  Kurschart + News",
        "📰  News-Feed",
        "🌍  Marktüberblick",
        "🏛️  Insider Trades",
        "⚖️  Vergleich",
        "🔁  Backtest",
        "🌊  Wellen-Scanner",
        "🔔  Signale",
        "🎯  Vorhersagen",
        "🧪  Modellvergleich",
        "💶  Papier-Trades",
        "🕵️  Insider Papier-Trades",
        "🪙  Krypto Top 50",
        "⚡  Daytrading",
    ])
    with t1:
        tab_chart(start, end, continent)
    with t2:
        tab_newsfeed(start, end, continent)
    with t3:
        tab_overview(continent)
    with t4:
        tab_insider_trades(start, end)
    with t5:
        tab_compare(start, end, continent)
    with t6:
        tab_backtest()
    with t7:
        tab_wave_scanner(continent)
    with t8:
        tab_signals(continent)
    with t9:
        tab_wave_predictions()
    with t_cmp:
        tab_model_comparison()
    with t10:
        tab_paper_trades()
    with t11:
        tab_paper_trades_insider()
    with t12:
        tab_crypto_top50()
    with t13:
        tab_daytrading()


if __name__ == "__main__":
    main()
