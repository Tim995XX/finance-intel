"""
Speichern von Insider-Trades und ARK-Holdings (gemeinsam für main.py und
Dashboard).

Eindeutigkeit eines Trades: Der ursprüngliche Schlüssel
(source, trader, ticker, trade_date, trade_type) hat echte, getrennte Trades
verschluckt — z.B. Senator kauft für das eigene Depot UND für den Ehepartner,
oder mehrere Positionen derselben Aktie in einer Meldung. Bei einem Abruf
(2026-10-04) waren das 37 von 160 Congress-Trades. Jetzt gehören zusätzlich
owner, raw_amount, filing_url und seq zum Schlüssel; seq nummeriert Zeilen,
die auch darin vollständig übereinstimmen (die Quelle listet sie mehrfach,
z.B. zwei gleich große Käufe am selben Tag in derselben Meldung).
"""
import sqlite3
from collections import Counter

INSIDER_TRADES_DDL = """
    CREATE TABLE IF NOT EXISTS insider_trades (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        source          TEXT    NOT NULL,
        trader          TEXT    NOT NULL,
        ticker          TEXT    NOT NULL,
        company         TEXT,
        trade_date      DATE    NOT NULL,
        trade_type      TEXT,
        amount_min      REAL,
        amount_max      REAL,
        shares_delta    REAL,
        fund            TEXT,
        raw_amount      TEXT    NOT NULL DEFAULT '',
        fetched_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        owner           TEXT    NOT NULL DEFAULT '',
        disclosure_date DATE,
        filing_url      TEXT    NOT NULL DEFAULT '',
        seq             INTEGER NOT NULL DEFAULT 0,
        UNIQUE(source, trader, ticker, trade_date, trade_type,
               owner, raw_amount, filing_url, seq)
    );
    CREATE INDEX IF NOT EXISTS idx_insider_ticker ON insider_trades(ticker);
    CREATE INDEX IF NOT EXISTS idx_insider_trader ON insider_trades(trader);
    CREATE INDEX IF NOT EXISTS idx_insider_date   ON insider_trades(trade_date);

    -- SEC-Meldungen, die vollständig verarbeitet wurden (auch solche ohne
    -- Trump-Bezug), damit sie nicht bei jedem Lauf erneut geladen werden.
    CREATE TABLE IF NOT EXISTS sec_filings_seen (
        filing_url   TEXT PRIMARY KEY,
        processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
"""

_OLD_COLUMNS = ("id, source, trader, ticker, company, trade_date, trade_type, "
                "amount_min, amount_max, shares_delta, fund, raw_amount, fetched_at")


def migrate_insider_trades(conn: sqlite3.Connection) -> bool:
    """Baut eine insider_trades-Tabelle im alten Format auf das neue Format
    um (SQLite kann UNIQUE-Constraints nicht nachträglich ändern). Alle
    Zeilen und ihre ids bleiben erhalten. Bei ARK ist der Veröffentlichungstag
    gleich dem Handelstag; bei Congress/DJT ist er für Altdaten unbekannt
    (NULL) und wird ergänzt, sobald der Trade erneut abgerufen wird.
    Rückgabe: True, wenn umgebaut wurde. Caller commits."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(insider_trades)")}
    if not cols or "seq" in cols:
        conn.executescript(INSIDER_TRADES_DDL)
        return False

    conn.execute("ALTER TABLE insider_trades RENAME TO insider_trades_old")
    for idx in ("idx_insider_ticker", "idx_insider_trader", "idx_insider_date"):
        conn.execute(f"DROP INDEX IF EXISTS {idx}")
    conn.executescript(INSIDER_TRADES_DDL)
    conn.execute(f"""
        INSERT INTO insider_trades ({_OLD_COLUMNS}, disclosure_date)
        SELECT id, source, trader, ticker, company, trade_date, trade_type,
               amount_min, amount_max, shares_delta, fund,
               COALESCE(raw_amount, ''), fetched_at,
               CASE WHEN source = 'ark' THEN trade_date END
        FROM insider_trades_old
    """)
    old_n = conn.execute("SELECT COUNT(*) FROM insider_trades_old").fetchone()[0]
    new_n = conn.execute("SELECT COUNT(*) FROM insider_trades").fetchone()[0]
    if old_n != new_n:
        raise RuntimeError(f"Migration insider_trades: {old_n} alte vs {new_n} neue Zeilen")
    conn.execute("DROP TABLE insider_trades_old")
    return True


def _with_seq(trades: list[dict]) -> list[tuple[dict, int]]:
    """Nummeriert Trades, die im vollständigen Schlüssel übereinstimmen."""
    seen: Counter = Counter()
    out = []
    for t in trades:
        key = (t["source"], t["trader"], t["ticker"], t["trade_date"], t["trade_type"],
               t.get("owner") or "", t.get("raw_amount") or "", t.get("filing_url") or "")
        out.append((t, seen[key]))
        seen[key] += 1
    return out


def store_insider_trades(conn: sqlite3.Connection, trades: list[dict]) -> int:
    """Speichert Trades, gibt die Anzahl neuer Zeilen zurück. Caller commits.

    Ein Trade, der als Altzeile (vor der Migration gespeichert, ohne
    Veröffentlichungstag) schon vorhanden ist, wird nicht doppelt angelegt,
    sondern um owner/disclosure_date/filing_url ergänzt."""
    inserted = 0
    for t, seq in _with_seq(trades):
        if not t.get("trade_date") or len(str(t["trade_date"])) < 8:
            continue
        owner      = t.get("owner") or ""
        raw_amount = t.get("raw_amount") or ""
        filing_url = t.get("filing_url") or ""
        disclosure = t.get("disclosure_date")

        legacy = conn.execute(
            """SELECT id FROM insider_trades
               WHERE source=? AND trader=? AND ticker=? AND trade_date=? AND trade_type=?
                 AND raw_amount=? AND owner='' AND filing_url='' AND seq=0
                 AND disclosure_date IS NULL
               LIMIT 1""",
            (t["source"], t["trader"], t["ticker"], t["trade_date"], t["trade_type"], raw_amount),
        ).fetchone()
        if legacy and (owner or filing_url or disclosure):
            try:
                conn.execute(
                    """UPDATE insider_trades
                       SET owner=?, filing_url=?, disclosure_date=?, seq=?
                       WHERE id=?""",
                    (owner, filing_url, disclosure, seq, legacy[0]),
                )
                continue
            except sqlite3.IntegrityError:
                pass  # neuer Schlüssel existiert schon → normal einfügen/ignorieren

        cur = conn.execute(
            """INSERT OR IGNORE INTO insider_trades
                   (source, trader, ticker, company, trade_date, trade_type,
                    amount_min, amount_max, shares_delta, fund, raw_amount,
                    owner, disclosure_date, filing_url, seq)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                t["source"], t["trader"], t["ticker"], t["company"],
                t["trade_date"], t["trade_type"],
                t["amount_min"], t["amount_max"],
                t["shares_delta"], t["fund"], raw_amount,
                owner, disclosure, filing_url, seq,
            ),
        )
        inserted += cur.rowcount > 0
    return inserted


def store_ark_holdings(conn: sqlite3.Connection, holdings: list[dict], today: str) -> int:
    """Speichert einen ARK-Holdings-Snapshot. Caller commits."""
    inserted = 0
    for h in holdings:
        cur = conn.execute(
            """INSERT OR IGNORE INTO ark_holdings
                   (snapshot_date, fund, ticker, company, shares, market_value, weight_pct)
               VALUES (?,?,?,?,?,?,?)""",
            (h.get("snapshot_date") or today, h["fund"], h["ticker"],
             h["company"], h["shares"], h["market_value"], h["weight_pct"]),
        )
        inserted += cur.rowcount > 0
    return inserted


def seen_sec_filings(conn: sqlite3.Connection) -> frozenset[str]:
    return frozenset(r[0] for r in conn.execute("SELECT filing_url FROM sec_filings_seen"))


def mark_sec_filings_seen(conn: sqlite3.Connection, urls: list[str]) -> None:
    """Caller commits — und zwar erst nach dem Speichern der zugehörigen
    Trades, damit eine Meldung nie als erledigt gilt, deren Trades fehlen."""
    conn.executemany("INSERT OR IGNORE INTO sec_filings_seen (filing_url) VALUES (?)",
                     [(u,) for u in urls])


def fetch_and_store_insiders(today: str) -> dict:
    """Ruft alle Insider-Quellen parallel ab und speichert sie in einer
    Transaktion. Rückgabe: Zählerstände je Quelle für die Ausgabe."""
    from src.database.db import get_connection
    from src.fetchers.insider_fetcher import fetch_all_insider_sources

    conn = get_connection()
    try:
        data = fetch_all_insider_sources(djt_skip_filings=seen_sec_filings(conn))
        result = {
            "congress_error":    data["congress_error"],
            "congress_fetched":  len(data["congress"]),
            "congress_new":      store_insider_trades(conn, data["congress"]),
            "ark_fetched":       len(data["ark_trades"]),
            "ark_new":           store_insider_trades(conn, data["ark_trades"]),
            "holdings_new":      store_ark_holdings(conn, data["ark_holdings"], today),
            "djt_fetched":       len(data["djt_trades"]),
            "djt_new":           store_insider_trades(conn, data["djt_trades"]),
            "djt_filings_new":   len(data["djt_done_filings"]),
        }
        mark_sec_filings_seen(conn, data["djt_done_filings"])
        conn.commit()
        return result
    finally:
        conn.close()
