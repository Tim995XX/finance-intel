import sqlite3

import pytest

from src.analytics.backtest import backtest_trader, list_backtestable_traders


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("""
        CREATE TABLE insider_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT, trader TEXT, ticker TEXT, company TEXT,
            trade_date DATE, trade_type TEXT,
            amount_min REAL, amount_max REAL, shares_delta REAL,
            fund TEXT, raw_amount TEXT
        )
    """)
    c.execute("""
        CREATE TABLE stock_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT, date DATE, open REAL, high REAL, low REAL,
            close REAL, volume INTEGER, UNIQUE(ticker, date)
        )
    """)
    yield c
    c.close()


def _insert_trade(conn, trader, ticker, trade_date, trade_type="buy", source="test", company=""):
    conn.execute(
        "INSERT INTO insider_trades (source, trader, ticker, company, trade_date, trade_type) VALUES (?,?,?,?,?,?)",
        (source, trader, ticker, company, trade_date, trade_type),
    )


def _insert_price(conn, ticker, date, close):
    conn.execute(
        "INSERT INTO stock_prices (ticker, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
        (ticker, date, close, close, close, close, 1000),
    )


def test_single_trade_return_is_computed_exactly(conn):
    """Entry at 100, latest at 150 -> +50% return, no ambiguity."""
    _insert_trade(conn, "Trader X", "AAA", "2024-01-01")
    _insert_price(conn, "AAA", "2024-01-01", 100)
    _insert_price(conn, "AAA", "2024-06-01", 150)

    result = backtest_trader(conn, "Trader X", benchmark="BENCH")
    assert result["n_trades"] == 1
    assert result["trades"][0]["return_pct"] == pytest.approx(50.0)
    assert result["avg_return_pct"] == pytest.approx(50.0)
    assert result["win_rate_pct"] == pytest.approx(100.0)


def test_entry_price_uses_next_available_trading_day(conn):
    """Trade date falls on a weekend/holiday with no price row -> entry price
    must come from the next available trading day, not fail silently."""
    _insert_trade(conn, "Trader X", "AAA", "2024-01-01")  # no price on this exact date
    _insert_price(conn, "AAA", "2024-01-03", 100)  # next available trading day
    _insert_price(conn, "AAA", "2024-06-01", 120)

    result = backtest_trader(conn, "Trader X")
    assert result["n_trades"] == 1
    assert result["trades"][0]["entry_date"] == "2024-01-03"
    assert result["trades"][0]["return_pct"] == pytest.approx(20.0)


def test_trade_without_any_price_data_is_skipped_not_silently_dropped(conn):
    _insert_trade(conn, "Trader X", "NOPRICE", "2024-01-01")

    result = backtest_trader(conn, "Trader X")
    assert result["n_trades"] == 0
    assert len(result["skipped"]) == 1
    assert result["skipped"][0]["reason"] == "keine Kursdaten"


def test_trade_with_only_one_price_point_is_skipped(conn):
    """Bought yesterday, only the entry price exists yet -> no return
    computable; must not report a bogus 0% return."""
    _insert_trade(conn, "Trader X", "AAA", "2024-06-01")
    _insert_price(conn, "AAA", "2024-06-01", 100)

    result = backtest_trader(conn, "Trader X")
    assert result["n_trades"] == 0
    assert result["skipped"][0]["reason"] == "noch keine Kurshistorie nach Einstieg"


def test_aggregate_stats_across_multiple_trades(conn):
    """Two trades: +100% and -50% -> avg 25%, win rate 50%."""
    _insert_trade(conn, "Trader X", "WIN", "2024-01-01")
    _insert_price(conn, "WIN", "2024-01-01", 100)
    _insert_price(conn, "WIN", "2024-06-01", 200)

    _insert_trade(conn, "Trader X", "LOSE", "2024-01-01")
    _insert_price(conn, "LOSE", "2024-01-01", 100)
    _insert_price(conn, "LOSE", "2024-06-01", 50)

    result = backtest_trader(conn, "Trader X")
    assert result["n_trades"] == 2
    assert result["avg_return_pct"] == pytest.approx(25.0)
    assert result["win_rate_pct"] == pytest.approx(50.0)
    assert result["best_trade"]["ticker"] == "WIN"
    assert result["worst_trade"]["ticker"] == "LOSE"


def test_benchmark_return_computed_over_same_window(conn):
    _insert_trade(conn, "Trader X", "AAA", "2024-01-01")
    _insert_price(conn, "AAA", "2024-01-01", 100)
    _insert_price(conn, "AAA", "2024-06-01", 150)
    _insert_price(conn, "SPY", "2024-01-01", 400)
    _insert_price(conn, "SPY", "2024-06-01", 440)

    result = backtest_trader(conn, "Trader X", benchmark="SPY")
    assert result["trades"][0]["benchmark_return_pct"] == pytest.approx(10.0)
    assert result["avg_benchmark_return_pct"] == pytest.approx(10.0)


def test_missing_benchmark_data_still_returns_trade_result(conn):
    """Benchmark ticker has no price rows at all -> trade return must still
    be reported, benchmark fields just come back as None."""
    _insert_trade(conn, "Trader X", "AAA", "2024-01-01")
    _insert_price(conn, "AAA", "2024-01-01", 100)
    _insert_price(conn, "AAA", "2024-06-01", 150)

    result = backtest_trader(conn, "Trader X", benchmark="NO_SUCH_TICKER")
    assert result["n_trades"] == 1
    assert result["trades"][0]["benchmark_return_pct"] is None
    assert result["avg_benchmark_return_pct"] is None


def test_source_filter_separates_traders_with_same_name_different_source(conn):
    _insert_trade(conn, "Same Name", "AAA", "2024-01-01", source="senate")
    _insert_trade(conn, "Same Name", "BBB", "2024-01-01", source="house")
    _insert_price(conn, "AAA", "2024-01-01", 100)
    _insert_price(conn, "AAA", "2024-06-01", 110)
    _insert_price(conn, "BBB", "2024-01-01", 100)
    _insert_price(conn, "BBB", "2024-06-01", 200)

    senate_only = backtest_trader(conn, "Same Name", source="senate")
    assert senate_only["n_trades"] == 1
    assert senate_only["trades"][0]["ticker"] == "AAA"


def test_sell_trades_are_excluded_from_backtest(conn):
    _insert_trade(conn, "Trader X", "AAA", "2024-01-01", trade_type="sell")
    _insert_price(conn, "AAA", "2024-01-01", 100)
    _insert_price(conn, "AAA", "2024-06-01", 150)

    result = backtest_trader(conn, "Trader X")
    assert result["n_trades"] == 0
    assert result["trades"] == []


def test_list_backtestable_traders_requires_price_data(conn):
    _insert_trade(conn, "Has Prices", "AAA", "2024-01-01")
    _insert_price(conn, "AAA", "2024-01-01", 100)
    _insert_trade(conn, "No Prices", "ZZZ", "2024-01-01")

    traders = list_backtestable_traders(conn)
    names = [t["trader"] for t in traders]
    assert "Has Prices" in names
    assert "No Prices" not in names
