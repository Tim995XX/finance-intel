"""
Backtest des eigenen Insider-Signals: "Was wäre passiert, hätte ich jedes Mal
mitgekauft, wenn [Trader X] gekauft hat?"

Nutzt ausschließlich vorhandene Daten (insider_trades + stock_prices), keine
neue Datenquelle nötig. Betrachtet nur 'buy'-Trades (buy-and-hold bis zum
aktuellsten verfügbaren Kurs) — Sell-Trades als Exit-Signal, Positionsgrößen-
Gewichtung und Transaktionskosten sind bewusst nicht Teil dieser Iteration.
"""
import sqlite3


def _price_on_or_after(conn: sqlite3.Connection, ticker: str, date_str: str):
    """Erster verfügbarer Handelstag an oder nach date_str (Einstiegskurs)."""
    row = conn.execute(
        "SELECT date, close FROM stock_prices WHERE ticker=? AND date>=? ORDER BY date ASC LIMIT 1",
        (ticker, date_str),
    ).fetchone()
    return (row["date"], row["close"]) if row else (None, None)


def _price_on_or_before(conn: sqlite3.Connection, ticker: str, date_str: str):
    """Letzter verfügbarer Handelstag an oder vor date_str (für Benchmark-Alignment)."""
    row = conn.execute(
        "SELECT date, close FROM stock_prices WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1",
        (ticker, date_str),
    ).fetchone()
    return (row["date"], row["close"]) if row else (None, None)


def _latest_price(conn: sqlite3.Connection, ticker: str):
    row = conn.execute(
        "SELECT date, close FROM stock_prices WHERE ticker=? ORDER BY date DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    return (row["date"], row["close"]) if row else (None, None)


def list_backtestable_traders(conn: sqlite3.Connection, min_trades: int = 1) -> list[dict]:
    """Trader/Fund-Kombinationen mit mindestens min_trades Buy-Trades, für die
    auch Kursdaten vorliegen (sonst wäre nichts backtestbar)."""
    rows = conn.execute(
        """
        SELECT trader, source, COUNT(*) c
        FROM insider_trades t
        WHERE trade_type='buy'
          AND EXISTS (SELECT 1 FROM stock_prices p WHERE p.ticker = t.ticker)
        GROUP BY trader, source
        HAVING c >= ?
        ORDER BY c DESC
        """,
        (min_trades,),
    ).fetchall()
    return [dict(r) for r in rows]


def rank_trade_performance(conn: sqlite3.Connection, trades: list[dict]) -> list[dict]:
    """Berechnet für eine Liste von Buy-Trades (dicts mit mind. 'ticker' und
    'trade_date') die Performance seit Einstieg bis zum aktuellsten verfügbaren
    Kurs. Trades ohne auswertbare Kursdaten werden übersprungen. Rückgabe
    absteigend nach Performance (return_pct) sortiert — für ein "Top Performer"
    Ranking über beliebige Trade-Teilmengen (z.B. nach Filter im Dashboard)."""
    results = []
    for t in trades:
        ticker, trade_date = t["ticker"], t["trade_date"]

        entry_date, entry_price = _price_on_or_after(conn, ticker, trade_date)
        if entry_price is None:
            continue

        latest_date, latest_price = _latest_price(conn, ticker)
        if latest_price is None or latest_date == entry_date:
            continue

        results.append({
            **t,
            "entry_date":   entry_date,
            "entry_price":  entry_price,
            "latest_date":  latest_date,
            "latest_price": latest_price,
            "return_pct":   (latest_price / entry_price - 1) * 100,
        })

    return sorted(results, key=lambda r: r["return_pct"], reverse=True)


def backtest_trader(
    conn: sqlite3.Connection,
    trader: str,
    source: str | None = None,
    benchmark: str = "SPY",
) -> dict:
    """Backtestet alle Buy-Trades eines Traders gegen den aktuellsten
    verfügbaren Kurs, inkl. Benchmark-Vergleich über denselben Zeitraum.

    Rückgabe-dict: trades (Liste je Trade), skipped (Trades ohne
    auswertbare Kursdaten, mit Grund), n_trades, avg_return_pct,
    win_rate_pct, best_trade, worst_trade, avg_benchmark_return_pct.
    """
    query = "SELECT ticker, trade_date, company FROM insider_trades WHERE trader=? AND trade_type='buy'"
    params: list = [trader]
    if source:
        query += " AND source=?"
        params.append(source)
    query += " ORDER BY trade_date"
    trades = conn.execute(query, params).fetchall()

    results = []
    skipped = []
    for t in trades:
        ticker, trade_date, company = t["ticker"], t["trade_date"], t["company"]

        entry_date, entry_price = _price_on_or_after(conn, ticker, trade_date)
        if entry_price is None:
            skipped.append({"ticker": ticker, "trade_date": trade_date, "reason": "keine Kursdaten"})
            continue

        latest_date, latest_price = _latest_price(conn, ticker)
        if latest_price is None or latest_date == entry_date:
            skipped.append({"ticker": ticker, "trade_date": trade_date, "reason": "noch keine Kurshistorie nach Einstieg"})
            continue

        _, bench_entry_price = _price_on_or_after(conn, benchmark, entry_date)
        _, bench_latest_price = _price_on_or_before(conn, benchmark, latest_date)
        bench_return_pct = (
            (bench_latest_price / bench_entry_price - 1) * 100
            if bench_entry_price and bench_latest_price
            else None
        )

        results.append({
            "ticker":               ticker,
            "company":              company,
            "trade_date":           trade_date,
            "entry_date":           entry_date,
            "entry_price":          entry_price,
            "latest_date":          latest_date,
            "latest_price":         latest_price,
            "return_pct":           (latest_price / entry_price - 1) * 100,
            "benchmark_return_pct": bench_return_pct,
        })

    if not results:
        return {
            "trader": trader, "source": source, "benchmark": benchmark,
            "trades": [], "skipped": skipped,
            "n_trades": 0, "avg_return_pct": None, "win_rate_pct": None,
            "best_trade": None, "worst_trade": None, "avg_benchmark_return_pct": None,
        }

    returns = [r["return_pct"] for r in results]
    bench_returns = [r["benchmark_return_pct"] for r in results if r["benchmark_return_pct"] is not None]

    return {
        "trader":                    trader,
        "source":                    source,
        "benchmark":                 benchmark,
        "trades":                    results,
        "skipped":                   skipped,
        "n_trades":                  len(results),
        "avg_return_pct":            sum(returns) / len(returns),
        "win_rate_pct":              sum(1 for r in returns if r > 0) / len(returns) * 100,
        "best_trade":                max(results, key=lambda r: r["return_pct"]),
        "worst_trade":               min(results, key=lambda r: r["return_pct"]),
        "avg_benchmark_return_pct":  (sum(bench_returns) / len(bench_returns)) if bench_returns else None,
    }
