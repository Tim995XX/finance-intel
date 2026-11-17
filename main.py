"""
Finance Intelligence — CLI runner
Usage:
  python main.py             # fetch news + prices (default)
  python main.py news        # news only
  python main.py prices      # prices only
  python main.py insiders    # congress trades + ARK holdings
  python main.py fundamentals # Piotroski-F-Score/Gross-Profitability/Fama-French-Proxy
                              # für alle Watchlist-Aktien aktualisieren (Cache, 7 Tage gültig)
  python main.py signals     # Elliott-Wellen-Scan der Watchlist + der Insider-aktiven
                              # Ticker (Papier-Trade-Orders, 2 getrennte Auswertungen),
                              # vorgefiltert nach Fundamentalscore (siehe MIN_FUNDAMENTAL_SCORE)
                              # + Abgleich offener Vorhersagen mit aktuellen Kursen
  python main.py all         # news + prices + insiders + fundamentals + signals
  python main.py replay      # Walk-forward-Rückrechnung des Wellenmodells über die
                              # gesamte Kurshistorie (einige Minuten) — Grundlage
                              # für den Dashboard-Tab 'Modellvergleich'
  python main.py replay test # dasselbe auf dem unberührten Test-Universum
  python main.py exits      # TP/SL-Raster + "nur Welle 3" mit Ordergebühren (exit_rules.py)
  python main.py margin     # mehr Spanne je Trade: Mindest-Kursziel + Trailing Stop (margin_study.py)
  python main.py testdata    # einmalig 10 Jahre Kurse des Test-Universums laden
                              # (src/analytics/test_universe.py, nicht in der Watchlist)
  python main.py clean       # einmalig unplausible gespeicherte Kurse löschen (falscher
                              # Coin unter gleichem Kürzel, Einzelausreißer) + betroffene
                              # Vorhersagen löschen/neu auswerten (price_plausibility.py)
  python main.py init        # init DB only
"""
import sys
import logging
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

# Ensure project root is on path when called from sub-directories
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def fetch_all_news():
    from src.database.db import get_connection
    from src.fetchers.news_fetcher import fetch_feed
    from src.processors.text_processor import process_item, ensure_nltk_data

    ensure_nltk_data()
    conn    = get_connection()
    sources = conn.execute("SELECT * FROM sources WHERE active=1").fetchall()
    conn.close()

    total_new = 0
    for source in sources:
        print(f"  [{source['region']:6}] {source['name']:25}", end=" … ", flush=True)
        items = fetch_feed(source["feed_url"])
        new_count = 0

        conn = get_connection()
        for item in items:
            if not item["url"] or not item["title"]:
                continue
            exists = conn.execute(
                "SELECT id FROM news_items WHERE url=?", (item["url"],)
            ).fetchone()
            if exists:
                continue

            processed = process_item(
                item["title"], item["raw_content"], source["language"]
            )

            cursor = conn.execute(
                """INSERT OR IGNORE INTO news_items
                       (source_id, title, url, published_at, raw_content,
                        summary, sentiment_label, sentiment_score)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    source["id"], item["title"], item["url"], item["published_at"],
                    item["raw_content"], processed["summary"],
                    processed["sentiment_label"], processed["sentiment_score"],
                ),
            )
            if cursor.rowcount > 0:
                news_id = cursor.lastrowid
                for ticker, company_name in processed["tickers"]:
                    conn.execute(
                        "INSERT INTO news_tickers (news_id, ticker, company_name) VALUES (?,?,?)",
                        (news_id, ticker, company_name),
                    )
                new_count += 1

        conn.commit()
        conn.close()
        print(f"{new_count:3} neu")
        total_new += new_count
        time.sleep(0.4)   # polite delay

    print(f"\n  ✓ Gesamt neue Artikel: {total_new}")
    return total_new


def fetch_all_prices():
    """Aktualisiert die Kurse der Watchlist. Zusätzlich wird für jeden Ticker
    (außer Krypto, dort gibt es keine Splits) einmalig die lange Historie
    (HISTORY_DAYS) geladen — und erneut, sobald gespeicherte und frisch
    abgerufene Kurse auseinanderlaufen (Split/rückwirkende Bereinigung durch
    Yahoo, siehe src/database/price_history.py)."""
    from src.database.db import get_connection, store_prices
    from src.database.price_history import (
        needs_full_reload, replace_history, tickers_without_full_history,
    )
    from src.fetchers.stock_fetcher import fetch_prices_batch, UPDATE_DAYS, HISTORY_DAYS

    conn      = get_connection()
    rows      = conn.execute("SELECT ticker, asset_type FROM watchlist").fetchall()
    watchlist = [r["ticker"] for r in rows]
    crypto    = {r["ticker"] for r in rows if r["asset_type"] == "crypto"}
    conn.close()

    BATCH_SIZE = 40
    print(f"  Lade Kurse für {len(watchlist)} Ticker (Batch-Abruf, {BATCH_SIZE}er-Gruppen) …")
    total_inserted = 0
    reload_split, reload_new = [], []
    for i in range(0, len(watchlist), BATCH_SIZE):
        chunk   = watchlist[i:i + BATCH_SIZE]
        results = fetch_prices_batch(chunk, days=UPDATE_DAYS)

        conn = get_connection()
        for ticker in chunk:
            df = results.get(ticker, pd.DataFrame())
            if df.empty:
                print(f"  {ticker:12} … keine Daten")
                continue
            # Vor dem Speichern prüfen — store_prices überschreibt das
            # Abruf-Fenster, danach wäre die Abweichung nicht mehr sichtbar.
            if ticker not in crypto and needs_full_reload(conn, ticker, df):
                reload_split.append(ticker)
                print(f"  {ticker:12} … Kursbasis geändert (Split?) → komplette Historie wird ersetzt")
                continue
            inserted = store_prices(conn, df, overwrite=ticker not in crypto)
            total_inserted += inserted
            print(f"  {ticker:12} … {inserted:4} neue Zeilen")
        reload_new += tickers_without_full_history(
            conn, [t for t in chunk if t not in crypto and t not in reload_split],
        )
        conn.commit()
        conn.close()
        time.sleep(0.3)   # polite delay between batches

    print(f"  ✓ Kurse aktualisiert ({total_inserted} neue Zeilen gesamt)")

    to_reload = reload_split + reload_new
    if not to_reload:
        return
    print(f"  Lade lange Historie ({HISTORY_DAYS} Tage) für {len(to_reload)} Ticker "
          f"({len(reload_split)} mit geänderter Kursbasis, {len(reload_new)} erstmalig) …")
    n_deleted = n_rescaled = 0
    for i in range(0, len(to_reload), BATCH_SIZE):
        chunk   = to_reload[i:i + BATCH_SIZE]
        results = fetch_prices_batch(chunk, days=HISTORY_DAYS)
        conn = get_connection()
        for ticker in chunk:
            df = results.get(ticker, pd.DataFrame())
            if df.empty:
                continue
            res = replace_history(conn, ticker, df)
            n_deleted  += res["deleted"]
            n_rescaled += res["rescaled"]
            if res["split_dates"]:
                print(f"  {ticker:12} … Split/Kursbasis-Sprung am {', '.join(res['split_dates'])} korrigiert "
                      f"({res['deleted']} Vorhersagen gelöscht, {res['rescaled']} umgerechnet)")
        conn.commit()
        conn.close()
        time.sleep(0.3)
    print(f"  ✓ Lange Historie geladen ({n_deleted} Vorhersagen auf Basis eines Phantom-Kurssprungs "
          f"gelöscht, {n_rescaled} auf neue Kursbasis umgerechnet)")


def update_watchlist():
    """Add new continent tickers to watchlist (never deletes existing)."""
    from src.watchlist_updater import update_watchlist as _update
    added = _update()
    if added:
        print(f"  ✓ {added} neue Ticker zur Watchlist hinzugefügt")
    else:
        print("  ✓ Watchlist aktuell (keine neuen Ticker)")
    return added


def fetch_insiders():
    """Congress-Trades (Senat + Repräsentantenhaus), ARK-Trades und -Holdings
    sowie Trump-Familie (SEC/DJT) — alle Quellen parallel."""
    from src.database.insider_store import fetch_and_store_insiders

    print("  Lade Insider-Trades (Congress, ARK, Trump-Familie) parallel …")
    t0 = time.time()
    r  = fetch_and_store_insiders(datetime.now().strftime("%Y-%m-%d"))
    if r["congress_error"] == "no_key":
        print("  ⚠ Kein FMP-API-Key konfiguriert. Congress-Trades übersprungen.")
        print("    Key eintragen: echo '{\"fmp_api_key\": \"DEIN_KEY\"}' > .insider_config.json")
    else:
        print(f"  ✓ {r['congress_new']} neue Congress-Trades gespeichert ({r['congress_fetched']} abgerufen)")
    print(f"  ✓ ARK: {r['ark_new']} neue Trades gespeichert ({r['ark_fetched']} abgerufen)")
    print(f"  ✓ ARK Holdings: {r['holdings_new']} neue Zeilen gespeichert")
    print(f"  ✓ Trump-Familie: {r['djt_new']} neue Trades gespeichert "
          f"({r['djt_fetched']} abgerufen, {r['djt_filings_new']} neue SEC-Meldungen geprüft)")
    print(f"  ({time.time() - t0:.1f} s)")


# Mindest-Fundamentalscore (0-100, siehe src/analytics/fundamentals.py) für die
# Aufnahme in den headless Wellen-Scan. Ticker ohne Fundamentaldaten (ETF/Index/
# Krypto, siehe filter_promising()) sind davon ausgenommen, nicht betroffen.
# 0 = Filter faktisch aus, da fundamental_score nie negativ wird.
MIN_FUNDAMENTAL_SCORE = 30.0


def fetch_fundamentals():
    """Aktualisiert den Fundamentaldaten-Cache (fundamentals_raw) für alle
    Watchlist-Aktien (asset_type='stock') — Piotroski-F-Score,
    Gross-Profitability, Fama-French-Proxy. ETFs/Indizes/Krypto werden
    übersprungen (keine auswertbaren Jahresabschlüsse). 7 Tage Cache-Gültigkeit,
    macht bei mehrmals täglichem Lauf also i.d.R. keine neuen API-Calls."""
    from src.database.db import get_connection
    from src.analytics.fundamentals import refresh_fundamentals

    conn = get_connection()
    tickers = [
        r["ticker"] for r in
        conn.execute("SELECT ticker FROM watchlist WHERE asset_type='stock'").fetchall()
    ]
    result = refresh_fundamentals(conn, tickers)
    conn.close()
    print(f"  ✓ Fundamentaldaten: {result['refreshed']} aktualisiert, "
          f"{result['skipped_fresh']} bereits aktuell (<{7}d), "
          f"{result['no_data']} ohne auswertbare Bilanzdaten")


def _fundamental_prefilter(conn, tickers: list[str]) -> tuple[list[str], int]:
    """Lädt/berechnet die Fundamentalscores für tickers (ohne neu von
    yfinance abzurufen — dafür ist fetch_fundamentals()/'main.py
    fundamentals' zuständig, live-Abrufe für hunderte Ticker mitten im
    Wellen-Scan wären zu langsam) und gibt (übrig gebliebene Ticker,
    Anzahl ausgefiltert) zurück."""
    from src.analytics.fundamentals import load_scores, filter_promising
    scores = load_scores(conn, tickers)
    if scores.empty:
        return tickers, 0
    included, excluded = filter_promising(scores, MIN_FUNDAMENTAL_SCORE)
    return included, len(excluded)


def scan_wave_signals():
    """Scannt die gesamte Watchlist auf vom Elliott-Wellen-Modul erkannte
    Einstiegssignale und protokolliert jede erkannte Erwartung in
    wave_predictions/wave_snapshots (record_snapshot) — headless-Äquivalent
    zu den Dashboard-Tabs 'Wellen-Scanner'/'Signale'/'Kurschart'. Nötig,
    damit im Tab 'Papier-Trades' auch neue Order entstehen, wenn niemand das
    Dashboard geöffnet hat (z.B. bei geplanter Ausführung per Cron).

    Vor dem eigentlichen Scan wird die Watchlist per Fundamentaldaten-Score
    vorgefiltert (MIN_FUNDAMENTAL_SCORE) — Aktien mit schwachen Piotroski-/
    Gross-Profitability-/Fama-French-Werten werden gar nicht erst auf
    Elliott-Wellen-Muster geprüft. Ticker ohne Fundamentaldaten (ETF/Index/
    Krypto) bleiben unberührt."""
    from datetime import timedelta
    from src.database.db import get_connection
    from src.analytics.elliott_wave import analyze_current_wave
    from src.analytics.wave_tracker import record_snapshot

    SENSITIVITY   = 5.0   # Zigzag-Empfindlichkeit %, wie Dashboard-Default
    LOOKBACK_DAYS = 365

    from src.database.price_plausibility import penny_tickers

    conn = get_connection()
    watchlist = [r["ticker"] for r in conn.execute("SELECT ticker FROM watchlist").fetchall()]
    pennies = penny_tickers(conn)
    n_penny = sum(t in pennies for t in watchlist)
    watchlist = [t for t in watchlist if t not in pennies]
    watchlist, n_filtered_out = _fundamental_prefilter(conn, watchlist)
    conn.close()

    end_d   = str(datetime.now().date())
    start_d = str(datetime.now().date() - timedelta(days=LOOKBACK_DAYS))

    found, skipped = 0, 0
    pred_conn = get_connection()
    for ticker in watchlist:
        prices = pd.read_sql(
            "SELECT date, high, low, close FROM stock_prices WHERE ticker=? AND date BETWEEN ? AND ? ORDER BY date",
            pred_conn, params=[ticker, start_d, end_d],
        )
        if len(prices) < 20:
            skipped += 1
            continue
        state = analyze_current_wave(prices, threshold_pct=SENSITIVITY)
        if state:
            record_snapshot(pred_conn, ticker, state, SENSITIVITY, LOOKBACK_DAYS)
            found += 1
    pred_conn.commit()
    pred_conn.close()

    print(f"  ✓ Wellen-Scan: {found} Ticker mit aktuellem Muster protokolliert ({skipped} übersprungen "
          f"mangels Kurshistorie, {n_filtered_out} vorab wegen niedrigem Fundamentalscore ausgeschlossen, "
          f"{n_penny} unter Mindestkurs)")


def scan_insider_wave_signals():
    """Wie scan_wave_signals(), aber beschränkt auf Ticker mit aktueller
    Insider-Aktivität (Congress/Senate/House, ARK, Trump-Familie) statt der
    kompletten Watchlist — headless-Äquivalent zum Dashboard-Tab
    'Insider-Papier-Trades'. Damit entstehen dort auch dann neue Orders,
    wenn niemand das Dashboard geöffnet hat. Ticker ohne bisherige
    Kurs-Historie werden dafür einmalig zur Watchlist hinzugefügt und
    rückwirkend geladen (add-only, wie update_watchlist()).

    Krypto-Proxy-Erweiterung (headless-Äquivalent zur selben Logik im
    Dashboard-Tab): Coins haben selbst keine SEC-Insider-Meldungen — sobald
    aber eine kryptonahe Aktie (CRYPTO_PROXY_TICKERS) im Insider-Fenster
    gehandelt wurde, wird die komplette Krypto-Top-50 (CoinGecko) zusätzlich
    in dieses Ticker-Universum aufgenommen, mit eigener Kurs-Historie über
    crypto_fetcher statt yfinance (siehe dessen Moduldoc: uneinheitliche/
    gesuffixte Yahoo-Ticker für viele Altcoins)."""
    from datetime import timedelta
    from src.database.db import get_connection, store_prices
    from src.fetchers.stock_fetcher import fetch_prices_batch
    from src.fetchers.crypto_fetcher import (
        fetch_top50_markets, fetch_top50_history_batch, CRYPTO_PROXY_TICKERS,
    )
    from src.analytics.elliott_wave import analyze_current_wave
    from src.analytics.wave_tracker import record_snapshot, INSIDER_LOOKBACK_DAYS
    from src.database.price_plausibility import is_penny, penny_tickers

    SENSITIVITY   = 5.0   # wie scan_wave_signals()/Dashboard-Default
    LOOKBACK_DAYS = 365

    cutoff = str(datetime.now().date() - timedelta(days=INSIDER_LOOKBACK_DAYS))
    conn = get_connection()
    tickers = [
        row["ticker"] for row in conn.execute(
            "SELECT DISTINCT ticker FROM insider_trades WHERE trade_date >= ?", (cutoff,)
        ).fetchall()
    ]
    if not tickers:
        conn.close()
        print("  ✓ Insider-Wellen-Scan: keine Ticker mit aktueller Insider-Aktivität")
        return

    # Kurse neuer Ticker zuerst laden: Penny-Aktien (letzter Kurs unter
    # MIN_STOCK_PRICE) kommen weder auf die Watchlist noch in stock_prices.
    have_prices = {r["ticker"] for r in conn.execute("SELECT DISTINCT ticker FROM stock_prices").fetchall()}
    missing_prices = [t for t in tickers if t not in have_prices]
    rejected_penny: set[str] = set()
    if missing_prices:
        batch = fetch_prices_batch(missing_prices, days=400)
        for t, df in batch.items():
            if df.empty:
                continue
            if is_penny(df.sort_values("date")["close"].iloc[-1]):
                rejected_penny.add(t)
                continue
            store_prices(conn, df)
        conn.commit()
    tickers = [t for t in tickers if t not in rejected_penny]

    existing_wl = {r["ticker"] for r in conn.execute("SELECT ticker FROM watchlist").fetchall()}
    new_tickers = [t for t in tickers if t not in existing_wl]
    for t in new_tickers:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (ticker, name, asset_type, continent) VALUES (?,?,?,?)",
            (t, t, "stock", "Global"),
        )
    conn.commit()

    # ── Krypto-Proxy-Erweiterung ──────────────────────────────────────────
    crypto_trigger = set(tickers) & set(CRYPTO_PROXY_TICKERS)
    crypto_tickers: set[str] = set()
    if crypto_trigger:
        print(f"  🪙 Insider-Aktivität bei kryptonaher Aktie ({', '.join(sorted(crypto_trigger))}) "
              "— lade Krypto-Top-50 als Proxy-Signal …")
        top50 = fetch_top50_markets()
        if not top50.empty:
            crypto_tickers = set(top50["ticker"])
            existing_wl2 = {r["ticker"] for r in conn.execute("SELECT ticker FROM watchlist").fetchall()}
            for _, r in top50.iterrows():
                if r["ticker"] not in existing_wl2:
                    conn.execute(
                        "INSERT OR IGNORE INTO watchlist (ticker, name, asset_type, continent) VALUES (?,?,?,?)",
                        (r["ticker"], r["name"], "crypto", "Global"),
                    )
            conn.commit()

            have_prices2 = {r["ticker"] for r in conn.execute("SELECT DISTINCT ticker FROM stock_prices").fetchall()}
            missing_crypto = top50[~top50["ticker"].isin(have_prices2)]
            if not missing_crypto.empty:
                crypto_batch = fetch_top50_history_batch(missing_crypto)
                for t, df in crypto_batch.items():
                    if not df.empty:
                        store_prices(conn, df)
                conn.commit()
            tickers = tickers + [t for t in top50["ticker"] if t not in tickers]

    end_d   = str(datetime.now().date())
    start_d = str(datetime.now().date() - timedelta(days=LOOKBACK_DAYS))

    # Krypto-Ticker sind von Natur aus ohne Fundamentaldaten (siehe
    # filter_promising() in fundamentals.py) und passieren den Vorfilter
    # dadurch ohnehin unangetastet — kein separater Aufruf nötig.
    pennies = penny_tickers(conn)
    n_penny = len(rejected_penny) + sum(t in pennies for t in tickers)
    tickers = [t for t in tickers if t not in pennies]
    scan_tickers, n_filtered_out = _fundamental_prefilter(conn, tickers)

    found, skipped = 0, 0
    for ticker in scan_tickers:
        prices = pd.read_sql(
            "SELECT date, high, low, close FROM stock_prices WHERE ticker=? AND date BETWEEN ? AND ? ORDER BY date",
            conn, params=[ticker, start_d, end_d],
        )
        if len(prices) < 20:
            skipped += 1
            continue
        state = analyze_current_wave(prices, threshold_pct=SENSITIVITY)
        if state:
            record_snapshot(conn, ticker, state, SENSITIVITY, LOOKBACK_DAYS)
            found += 1
    conn.commit()
    conn.close()

    print(f"  ✓ Insider-Wellen-Scan: {len(new_tickers)} Ticker neu zur Watchlist hinzugefügt, "
          f"{len(crypto_tickers)} Krypto-Ticker via Proxy-Signal ergänzt, "
          f"{found} mit aktuellem Muster protokolliert ({skipped} übersprungen mangels Kurshistorie, "
          f"{n_filtered_out} vorab wegen niedrigem Fundamentalscore ausgeschlossen, "
          f"{n_penny} unter Mindestkurs)")


def clean_prices():
    from src.database.db import get_connection
    from src.database.price_history import clean_implausible_prices

    conn = get_connection()
    res = clean_implausible_prices(conn)
    conn.commit()
    conn.close()
    for ticker, n in res["tickers"].items():
        print(f"  {ticker:16} … {n:4} unplausible Kurse gelöscht")
    print(f"  ✓ {sum(res['tickers'].values())} Kurse in {len(res['tickers'])} Tickern gelöscht, "
          f"{res['deleted']} Vorhersagen auf Basis eines Phantom-Sprungs gelöscht, "
          f"{res['reopened']} zur Neuauswertung wieder geöffnet")
    evaluate_predictions()


def evaluate_predictions():
    """Headless-Äquivalent zum Button im Dashboard-Tab 'Vorhersagen' — damit
    offene Vorhersagen auch ohne geöffnetes Dashboard aufgelöst werden."""
    from src.database.db import get_connection
    from src.analytics.wave_tracker import evaluate_open_predictions

    conn = get_connection()
    summary = evaluate_open_predictions(conn)
    conn.close()
    print(f"  ✓ {summary['checked']} offene Vorhersage(n) geprüft — {summary['hit']} Ziel erreicht, "
          f"{summary['invalidated']} invalidiert, {summary['expired']} abgelaufen")


def load_test_universe():
    from src.database.db import get_connection
    from src.analytics.test_universe import load_test_prices, TEST_TICKERS

    conn = get_connection()
    print(f"  Lade 10 Jahre Kurse für {len(TEST_TICKERS)} Test-Ticker …")
    out = load_test_prices(conn)
    conn.close()
    print(f"  ✓ {out['loaded']} geladen, {out['skipped']} schon vorhanden, "
          f"{len(out['empty'])} ohne Daten {out['empty'] or ''}")


def run_model_replay(universe: str = "watchlist"):
    from src.database.db import get_connection
    from src.analytics.model_replay import run_replay
    from src.analytics.model_versions import compare_versions, replay_periods

    t0 = time.time()
    conn = get_connection()
    df = run_replay(conn, universe=universe)
    conn.close()
    print(f"  ✓ {len(df)} Signale rückgerechnet ({time.time() - t0:.0f} s)")
    if df.empty:
        return
    cmp = compare_versions(df, replay_periods(df))
    for _, r in cmp.iterrows():
        edge = "—" if r["edge"] is None else f"{r['edge']*100:+.1f} pp"
        res = "—" if r["avg_result"] is None else f"{r['avg_result']*100:+.2f} %"
        wres = "—" if r["w_result"] is None else f"{r['w_result']*100:+.2f} %"
        print(f"    {r['label']:22} {r['period']:18} n={r['n']:6}  Edge {edge:>9}  "
              f"Ø Ergebnis {res:>8}  je Euro {wres:>8}")
    if universe == "test":
        print_v4_decision(df)


def run_exit_study():
    from src.database.db import get_connection
    from src.analytics.exit_rules import run_exit_study as study, CURRENT_RULE
    from src.analytics.model_versions import DEFAULT_FEES

    stake = 1000.0
    t0 = time.time()
    conn = get_connection()
    res = study(conn, DEFAULT_FEES, stake)
    conn.close()
    print(f"  ✓ fertig ({time.time() - t0:.0f} s) · Kosten: {DEFAULT_FEES.label} · {stake:.0f} € je Trade")

    def line(name, m):
        be = "—" if m["breakeven_stake"] is None or pd.isna(m["breakeven_stake"]) else f"{m['breakeven_stake']:,.0f} €"
        print(f"    {name:34} Trades {int(m['trades']):6}  Gewinn-Trades {m['win_rate']*100:5.1f} %  "
              f"Ø {m['avg_days']:5.1f} T  brutto {m['gross']*100:+.2f} %  netto {m['net']*100:+.2f} %  "
              f"p.a. {m['net_per_year']*100:+6.1f} %  Break-even {be}")

    e = res["exit"]
    print(f"\n  TP/SL-Raster: beste Kombination auf Watchlist-Training = {e['best_label']}")
    line("Training · beste", e["train_best"]); line("Training · heute (Zone/Pivot)", e["train_current"])
    line("TEST-Universum ab 2023 · beste", e["test_best"]); line("TEST-Universum ab 2023 · heute", e["test_current"])
    for y, r in e["yearly"].iterrows():
        print(f"      {y}  beste {r[e['best']]*100:+.2f} %  heute {r[CURRENT_RULE]*100:+.2f} %")
    print(f"  Jahre besser: {e['years_better']}/{e['n_years']} ⇒ {'ÜBERNEHMEN' if e['passed'] else 'NICHT übernehmen'}")

    w = res["wave3"]
    print("\n  Nur Welle 3 (Test-Universum ab 2023, heutige Exit-Regel):")
    line("nur Welle 3", w["wave3"]); line("alle Zustände", w["all"])
    print(f"  Jahre besser: {w['years_better']}/{w['n_years']} ⇒ {'nur Welle 3 handeln' if w['passed'] else 'alle Zustände behalten'}")


def run_margin_study():
    from src.database.db import get_connection
    from src.analytics.margin_study import run_margin_study as study
    from src.analytics.model_versions import DEFAULT_FEES

    stake = 1000.0
    t0 = time.time()
    conn = get_connection()
    res = study(conn, DEFAULT_FEES, stake)
    conn.close()
    print(f"  ✓ fertig ({time.time() - t0:.0f} s) · Kosten: {DEFAULT_FEES.label} · {stake:.0f} € je Trade")
    print(f"  Auswahl auf Watchlist bis 2022: Mindest-Kursziel {res['best_threshold']*100:g} %, "
          f"Trailing Stop = {res['best_trail_label']}")

    def pct(v, d=2):
        return "—" if v is None or pd.isna(v) else f"{v*100:+.{d}f} %"

    for period in ("Training", "Test"):
        print(f"\n  {period} ({'Watchlist bis 2022' if period == 'Training' else 'Test-Universum ab 2023'}):")
        for _, m in res["table"][res["table"].period == period].iterrows():
            print(f"    {m['variant']:27} Trades {int(m['trades']):6}  Gew. {m['win_rate']*100:4.1f} %  "
                  f"Ø {m['avg_days']:5.1f} T  brutto {pct(m['gross'])}  netto {pct(m['net'])}  "
                  f"Vorsprung {pct(m['excess'])}  Vorspr. p.a. {pct(m['excess_per_year'], 1)}  "
                  f"netto p.a. {pct(m['net_per_year'], 1)}  schlechtester {pct(m['worst'], 0)}  "
                  f"5%-Q. {pct(m['q05'], 1)}")
    print("\n  Vorsprung p.a. je Jahr (Test):")
    print(res["yearly"].map(lambda v: pct(v, 1)).to_string())
    print()
    for name, d in res["decisions"].items():
        print(f"  {name:27} Vorsprung besser: {'ja' if d['excess_ok'] else 'nein'} · netto/Trade besser: "
              f"{'ja' if d['net_ok'] else 'nein'} · Jahre besser {d['years_better']}/{d['n_years']} "
              f"⇒ {'BESTANDEN' if d['passed'] else 'nicht bestanden'}")


def print_v4_decision(df):
    from src.database.db import get_connection
    from src.analytics.model_versions import v4_decision, bear_phases, market_closes

    conn = get_connection()
    dec = v4_decision(df, bear_phases(market_closes(conn)))
    conn.close()
    print("\n  Bärenphasen (Long-Signale v3 im Bärenmarkt):")
    for _, p in dec["phases"].iterrows():
        edge = "—" if p["edge"] is None or pd.isna(p["edge"]) else f"{p['edge']*100:+.1f} pp"
        res = "—" if p["avg_result"] is None or pd.isna(p["avg_result"]) else f"{p['avg_result']*100:+.2f} %"
        print(f"    {p['start']} – {p['end']}  entschieden {p['decided']:5}  Edge {edge:>9}  "
              f"Ø Ergebnis {res:>8}{'' if p['counted'] else '  (nicht gewertet)'}")
    print(f"  Regel 1 (Edge positiv in Mehrheit der Phasen): {dec['n_positive']}/{dec['n_counted']} "
          f"→ {'erfüllt' if dec['edge_ok'] else 'nicht erfüllt'}")
    print(f"  Regel 2 (Ergebnis je Euro v4 > v3): {dec['w_result_v4']*100:+.3f} % vs "
          f"{dec['w_result_v3']*100:+.3f} % → {'erfüllt' if dec['return_ok'] else 'nicht erfüllt'}")
    print(f"  Gewinn bei 100 €/Signal: v3 {dec['pnl_units_v3']*100:+,.0f} €, v4 {dec['pnl_units_v4']*100:+,.0f} €")
    print(f"  ⇒ v4 {'BESTANDEN' if dec['passed'] else 'NICHT bestanden'}")


def main():
    from src.database.db import init_db
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    print(f"\n=== Finance Intelligence  {datetime.now():%Y-%m-%d %H:%M} ===\n")
    init_db()

    if cmd == "init":
        print("DB initialisiert.")
        return

    if cmd == "replay":
        universe = "test" if len(sys.argv) > 2 and sys.argv[2] == "test" else "watchlist"
        print(f"→ Walk-forward-Rückrechnung des Wellenmodells (Universum: {universe}) …")
        run_model_replay(universe)
        return

    if cmd == "exits":
        print("→ TP/SL-Raster und Welle-3-Vergleich mit Gebühren …")
        run_exit_study()
        return

    if cmd == "margin":
        print("→ Mehr Spanne je Trade: Mindest-Kursziel und Trailing Stop …")
        run_margin_study()
        return

    if cmd == "clean":
        print("→ Unplausible Kurse bereinigen …")
        clean_prices()
        return

    if cmd == "testdata":
        print("→ Kurse des Test-Universums laden …")
        load_test_universe()
        return

    # Always sync watchlist universe first (add-only, never delete)
    print("→ Watchlist synchronisieren …")
    update_watchlist()

    if cmd in ("news", "all"):
        print("\n→ News abrufen …")
        fetch_all_news()

    if cmd in ("prices", "all"):
        print("\n→ Kurse abrufen …")
        fetch_all_prices()

    if cmd in ("insiders", "all"):
        print("\n→ Insider-Trades abrufen …")
        fetch_insiders()

    if cmd in ("fundamentals", "all"):
        print("\n→ Fundamentaldaten aktualisieren …")
        fetch_fundamentals()

    if cmd in ("signals", "all"):
        print("\n→ Elliott-Wellen-Scan (Papier-Trade-Signale) …")
        scan_wave_signals()
        print("\n→ Elliott-Wellen-Scan Insider-Ticker (Insider-Papier-Trade-Signale) …")
        scan_insider_wave_signals()
        print("\n→ Offene Vorhersagen mit aktuellen Kursen abgleichen …")
        evaluate_predictions()

    if cmd == "help":
        print(__doc__)


if __name__ == "__main__":
    main()
