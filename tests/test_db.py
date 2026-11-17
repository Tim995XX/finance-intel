import sqlite3

import pandas as pd
import pytest

from src.database import db
from src.database.db import init_db, store_prices


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Points db.DB_PATH at a throwaway file so init_db() never touches the
    real database, then yields a connection to that file for assertions."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_finance_intel.db")
    yield db.DB_PATH


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("""
        CREATE TABLE stock_prices (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker  TEXT    NOT NULL,
            date    DATE    NOT NULL,
            open    REAL,
            high    REAL,
            low     REAL,
            close   REAL,
            volume  INTEGER,
            UNIQUE(ticker, date)
        )
    """)
    yield c
    c.close()


def _df(rows):
    return pd.DataFrame(rows, columns=["ticker", "date", "open", "high", "low", "close", "volume"])


def test_store_prices_empty_df_returns_zero(conn):
    assert store_prices(conn, pd.DataFrame()) == 0


def test_store_prices_inserts_all_new_rows(conn):
    df = _df([("AAPL", f"2024-01-{d:02d}", 1, 2, 0.5, 1.5, 1000) for d in range(1, 11)])
    assert store_prices(conn, df) == 10
    assert conn.execute("SELECT COUNT(*) FROM stock_prices").fetchone()[0] == 10


def test_store_prices_skips_existing_rows_and_counts_only_new(conn):
    """Regression test for the old row-by-row implementation: verifies the
    executemany + total_changes-delta rewrite still reports only newly
    inserted rows, not rows skipped by INSERT OR IGNORE on conflict."""
    df1 = _df([("AAPL", f"2024-01-{d:02d}", 1, 2, 0.5, 1.5, 1000) for d in range(1, 11)])
    store_prices(conn, df1)

    # Same 10 rows again plus 2 genuinely new ones
    df2 = _df(
        [("AAPL", f"2024-01-{d:02d}", 1, 2, 0.5, 1.5, 1000) for d in range(1, 11)]
        + [("AAPL", "2024-01-11", 1, 2, 0.5, 1.5, 1000), ("AAPL", "2024-01-12", 1, 2, 0.5, 1.5, 1000)]
    )
    inserted = store_prices(conn, df2)
    assert inserted == 2
    assert conn.execute("SELECT COUNT(*) FROM stock_prices").fetchone()[0] == 12


def test_store_prices_missing_volume_column_defaults_to_zero(conn):
    """Some tickers (e.g. certain FX pairs) come back from yfinance without a
    volume column at all; store_prices must not raise AttributeError."""
    df = pd.DataFrame(
        [("EURUSD=X", "2024-01-01", 1.1, 1.2, 1.0, 1.15)],
        columns=["ticker", "date", "open", "high", "low", "close"],
    )
    assert store_prices(conn, df) == 1
    row = conn.execute("SELECT volume FROM stock_prices").fetchone()
    assert row[0] == 0


def test_store_prices_nan_volume_defaults_to_zero(conn):
    df = _df([("AAPL", "2024-01-01", 1, 2, 0.5, 1.5, float("nan"))])
    assert store_prices(conn, df) == 1
    row = conn.execute("SELECT volume FROM stock_prices").fetchone()
    assert row[0] == 0


# ─── init_db() ───────────────────────────────────────────────────────────

_EXPECTED_TABLES = {
    "sources", "news_items", "news_tickers", "stock_prices",
    "watchlist", "chart_observations", "insider_trades", "ark_holdings",
}


def test_init_db_creates_all_expected_tables(isolated_db):
    init_db()
    conn = sqlite3.connect(str(isolated_db))
    tables = {
        r[0] for r in
        conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    assert _EXPECTED_TABLES <= tables


def test_init_db_is_idempotent(isolated_db):
    """Running init_db() twice on the same file must not raise (CREATE TABLE
    IF NOT EXISTS / INSERT OR IGNORE), and must not duplicate default sources."""
    init_db()
    init_db()
    conn = sqlite3.connect(str(isolated_db))
    count = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    conn.close()
    assert count == 12  # len(default_sources) in init_db()


def test_init_db_inserts_default_rss_sources(isolated_db):
    init_db()
    conn = sqlite3.connect(str(isolated_db))
    names = {r[0] for r in conn.execute("SELECT name FROM sources")}
    conn.close()
    assert "Yahoo Finance" in names
    assert "Handelsblatt" in names


def test_init_db_migration_adds_continent_column_to_legacy_watchlist(isolated_db):
    """Simulates a pre-migration DB (watchlist without 'continent') and
    verifies init_db()'s ALTER TABLE migration adds it without data loss."""
    conn = sqlite3.connect(str(isolated_db))
    conn.execute("""
        CREATE TABLE watchlist (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker     TEXT    UNIQUE NOT NULL,
            name       TEXT,
            asset_type TEXT    DEFAULT 'stock',
            added_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("INSERT INTO watchlist (ticker, name) VALUES ('AAPL', 'Apple')")
    conn.commit()
    conn.close()

    init_db()

    conn = sqlite3.connect(str(isolated_db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(watchlist)")}
    row = conn.execute("SELECT ticker, continent FROM watchlist WHERE ticker='AAPL'").fetchone()
    conn.close()
    assert "continent" in cols
    assert row == ("AAPL", "Global")
