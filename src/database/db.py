import sqlite3
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).parent.parent.parent
DB_PATH  = ROOT_DIR / "data" / "finance_intel.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def store_prices(conn: sqlite3.Connection, df: pd.DataFrame, overwrite: bool = False) -> int:
    """Bulk-insert an OHLCV DataFrame (columns: ticker,date,open,high,low,close,volume)
    into stock_prices via a single executemany() round-trip. Caller commits.

    overwrite=True ersetzt bereits gespeicherte Tage (für den regulären
    yfinance-Abruf von Aktien: split-/dividendenbereinigte Kurse und der
    Intraday-Stand des laufenden Tages blieben sonst auf dem Wert des ersten
    Abrufs eingefroren). overwrite=False überspringt sie (Krypto: dort
    mischen sich CoinGecko- und Yahoo-Kurse, die sich sonst gegenseitig
    überschreiben würden). Returns the number of newly inserted rows
    (row-count delta of the affected tickers; overwritten rows don't count).

    Unplausible Kurssprünge (falscher Coin unter gleichem Kürzel, einzelne
    Datenfehler) werden vorher verworfen, siehe price_plausibility.py."""
    from src.database.price_plausibility import drop_implausible

    if df.empty:
        return 0
    df = drop_implausible(conn, df)
    if df.empty:
        return 0

    has_volume = "volume" in df.columns
    rows = [
        (
            r.ticker, r.date,
            float(r.open), float(r.high), float(r.low), float(r.close),
            int(r.volume) if has_volume and pd.notna(r.volume) else 0,
        )
        for r in df.itertuples(index=False)
    ]

    tickers = sorted(set(df["ticker"]))
    count_sql = f"SELECT COUNT(*) FROM stock_prices WHERE ticker IN ({','.join('?' * len(tickers))})"
    before = conn.execute(count_sql, tickers).fetchone()[0]
    on_conflict = (
        """DO UPDATE SET open=excluded.open, high=excluded.high, low=excluded.low,
                         close=excluded.close, volume=excluded.volume"""
        if overwrite else "DO NOTHING"
    )
    conn.executemany(
        f"""INSERT INTO stock_prices (ticker, date, open, high, low, close, volume)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(ticker, date) {on_conflict}""",
        rows,
    )
    return conn.execute(count_sql, tickers).fetchone()[0] - before


def init_db():
    conn = get_connection()
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS sources (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            feed_url    TEXT    NOT NULL UNIQUE,
            region      TEXT    DEFAULT 'Global',
            language    TEXT    DEFAULT 'en',
            active      INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS news_items (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id       INTEGER REFERENCES sources(id),
            title           TEXT    NOT NULL,
            url             TEXT    UNIQUE,
            published_at    DATETIME,
            raw_content     TEXT,
            summary         TEXT,
            sentiment_label TEXT    CHECK(sentiment_label IN ('positive','negative','neutral')),
            sentiment_score REAL,
            fetched_at      DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS news_tickers (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            news_id      INTEGER NOT NULL REFERENCES news_items(id) ON DELETE CASCADE,
            ticker       TEXT    NOT NULL,
            company_name TEXT
        );

        CREATE TABLE IF NOT EXISTS stock_prices (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker  TEXT    NOT NULL,
            date    DATE    NOT NULL,
            open    REAL,
            high    REAL,
            low     REAL,
            close   REAL,
            volume  INTEGER,
            UNIQUE(ticker, date)
        );

        CREATE TABLE IF NOT EXISTS watchlist (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker     TEXT    UNIQUE NOT NULL,
            name       TEXT,
            asset_type TEXT    DEFAULT 'stock',
            continent  TEXT    DEFAULT 'Global',
            added_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS chart_observations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker     TEXT    NOT NULL,
            obs_date   DATE    NOT NULL,
            note       TEXT    NOT NULL,
            color      TEXT    DEFAULT '#FFD600',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ark_holdings (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date DATE    NOT NULL,
            fund          TEXT    NOT NULL,
            ticker        TEXT    NOT NULL,
            company       TEXT,
            shares        REAL,
            market_value  REAL,
            weight_pct    REAL,
            UNIQUE(snapshot_date, fund, ticker)
        );

        CREATE TABLE IF NOT EXISTS wave_predictions (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker               TEXT    NOT NULL,
            created_date         DATE    NOT NULL,
            state                TEXT    NOT NULL,
            direction            TEXT    NOT NULL,
            confidence           REAL    NOT NULL,
            price_at_prediction  REAL    NOT NULL,
            target_low           REAL,
            target_high          REAL,
            last_pivot_date      DATE,
            last_pivot_price     REAL,
            threshold_pct        REAL,
            expectation_text     TEXT,
            status               TEXT    DEFAULT 'open'
                                     CHECK(status IN ('open','hit','invalidated','expired')),
            resolved_date        DATE,
            resolved_price       REAL,
            created_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(ticker, state, last_pivot_date)
        );

        CREATE TABLE IF NOT EXISTS wave_snapshots (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            prediction_id     INTEGER REFERENCES wave_predictions(id),
            ticker            TEXT    NOT NULL,
            snapshot_date     DATE    NOT NULL,
            state             TEXT    NOT NULL,
            direction         TEXT    NOT NULL,
            confidence        REAL    NOT NULL,
            current_price     REAL    NOT NULL,
            target_low        REAL,
            target_high       REAL,
            last_pivot_date   DATE,
            last_pivot_price  REAL,
            expectation_text  TEXT,
            created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(ticker, snapshot_date)
        );

        CREATE TABLE IF NOT EXISTS fundamentals_raw (
            ticker              TEXT    PRIMARY KEY,
            computed_date       DATE    NOT NULL,
            data_ok             INTEGER NOT NULL DEFAULT 0,
            data_note           TEXT,
            price_to_book       REAL,
            operating_margin    REAL,
            asset_growth        REAL,
            gross_profitability REAL,
            f_score             INTEGER,
            f_score_detail      TEXT,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- Wann die komplette (lange) Kurshistorie eines Tickers zuletzt neu
        -- geladen und ersetzt wurde (siehe src/database/price_history.py).
        CREATE TABLE IF NOT EXISTS price_history_meta (
            ticker            TEXT PRIMARY KEY,
            full_history_date DATE NOT NULL
        );

        -- Walk-forward-Rückrechnung des Wellenmodells (src/analytics/model_replay.py),
        -- Grundlage für den Modellversions-Vergleich. Wird pro Lauf komplett ersetzt.
        CREATE TABLE IF NOT EXISTS replay_signals (
            ticker              TEXT NOT NULL,
            created_date        DATE NOT NULL,
            state               TEXT NOT NULL,
            direction           TEXT NOT NULL,
            confidence          REAL,
            price               REAL,
            target_low          REAL,
            target_high         REAL,
            last_pivot_price    REAL,
            baseline_prob       REAL,
            in_zone_at_creation INTEGER,
            d_target            REAL,
            d_stop              REAL,
            status              TEXT,
            resolved_date       DATE,
            fwd20               REAL,
            above_sma200        INTEGER,
            dist_sma200         REAL,
            mom126              REAL,
            mkt_above_sma200    INTEGER,
            univ_fwd20          REAL,
            run_date            DATE
        );

        -- Kurse des unberührten Test-Universums (src/analytics/test_universe.py) —
        -- getrennt von stock_prices, damit diese Titel nirgends sonst auftauchen.
        CREATE TABLE IF NOT EXISTS test_prices (
            ticker  TEXT NOT NULL,
            date    DATE NOT NULL,
            open    REAL,
            high    REAL,
            low     REAL,
            close   REAL,
            PRIMARY KEY (ticker, date)
        );

        CREATE INDEX IF NOT EXISTS idx_news_published   ON news_items(published_at);
        CREATE INDEX IF NOT EXISTS idx_news_tickers_t   ON news_tickers(ticker);
        CREATE INDEX IF NOT EXISTS idx_prices_ticker_dt ON stock_prices(ticker, date);
        CREATE INDEX IF NOT EXISTS idx_obs_ticker       ON chart_observations(ticker);
        CREATE INDEX IF NOT EXISTS idx_ark_date_fund    ON ark_holdings(snapshot_date, fund);
        CREATE INDEX IF NOT EXISTS idx_wave_pred_status ON wave_predictions(status);
        CREATE INDEX IF NOT EXISTS idx_wave_pred_ticker ON wave_predictions(ticker);
        CREATE INDEX IF NOT EXISTS idx_wave_snap_ticker  ON wave_snapshots(ticker);
        CREATE INDEX IF NOT EXISTS idx_wave_snap_pred    ON wave_snapshots(prediction_id);
    """)

    # insider_trades: Anlage bzw. Umbau auf den erweiterten Eindeutigkeits-
    # Schlüssel (siehe src/database/insider_store.py)
    from src.database.insider_store import migrate_insider_trades
    migrate_insider_trades(c)

    # ── Migration: add new columns to existing tables (safe, idempotent) ──
    for sql in [
        "ALTER TABLE watchlist ADD COLUMN continent TEXT DEFAULT 'Global'",
        "ALTER TABLE wave_predictions ADD COLUMN entry_limit_price REAL",
        # Zufalls-Basis der Vorhersage (siehe wave_tracker.barrier_baseline)
        "ALTER TABLE wave_predictions ADD COLUMN baseline_prob REAL",
        "ALTER TABLE wave_predictions ADD COLUMN in_zone_at_creation INTEGER",
        # Kontext-Merkmale zum Erkennungszeitpunkt (src/analytics/signal_features.py)
        "ALTER TABLE wave_predictions ADD COLUMN features_json TEXT",
        # Rückrechnung je Kurs-Universum: 'watchlist' oder 'test' (test_universe.py)
        "ALTER TABLE replay_signals ADD COLUMN universe TEXT DEFAULT 'watchlist'",
    ]:
        try:
            c.execute(sql)
        except Exception:
            pass  # column already exists

    # ── Default RSS sources ──
    default_sources = [
        ("Yahoo Finance",    "https://finance.yahoo.com/news/rssindex",                     "Global", "en"),
        ("CNBC Markets",     "https://www.cnbc.com/id/100003114/device/rss/rss.html",        "USA",    "en"),
        ("CNBC Finance",     "https://www.cnbc.com/id/10000664/device/rss/rss.html",         "USA",    "en"),
        ("BBC Business",     "https://feeds.bbci.co.uk/news/business/rss.xml",               "Global", "en"),
        ("The Guardian Biz", "https://www.theguardian.com/business/rss",                     "Global", "en"),
        ("MarketWatch",      "https://feeds.content.dowjones.io/public/rss/mw_topstories",   "USA",    "en"),
        ("Investing.com",    "https://www.investing.com/rss/news.rss",                       "Global", "en"),
        ("Handelsblatt",     "https://www.handelsblatt.com/contentexport/feed/schlagzeilen", "Europe", "de"),
        ("Zeit Wirtschaft",  "https://newsfeed.zeit.de/wirtschaft/index",                    "Europe", "de"),
        ("finanzen.net",     "https://www.finanzen.net/rss/news",                            "Europe", "de"),
        ("CoinDesk",         "https://www.coindesk.com/arc/outboundfeeds/rss/",              "Krypto", "en"),
        ("CoinTelegraph",    "https://cointelegraph.com/rss",                                "Krypto", "en"),
    ]
    c.executemany(
        "INSERT OR IGNORE INTO sources (name, feed_url, region, language) VALUES (?,?,?,?)",
        default_sources,
    )

    conn.commit()
    conn.close()
    print(f"[DB] Ready: {DB_PATH}")
