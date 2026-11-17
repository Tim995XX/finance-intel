import sqlite3

import pandas as pd
import pytest

from src.database import db
from src.database.db import init_db, get_connection, store_prices
from src.database.price_history import (
    needs_full_reload, replace_history, tickers_without_full_history,
)
from src.analytics.wave_tracker import (
    barrier_baseline, record_snapshot, accuracy_stats, load_predictions,
)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    init_db()
    c = get_connection()
    yield c
    c.close()


def _prices(ticker, closes, start="2026-01-01"):
    dates = pd.bdate_range(start, periods=len(closes)).strftime("%Y-%m-%d")
    return pd.DataFrame({
        "ticker": ticker, "date": dates,
        "open": closes, "high": closes, "low": closes, "close": closes, "volume": 1000,
    })


def _wave(date, price, pivot=90.0, t_lo=110.0, t_hi=130.0, direction="bullish"):
    return {
        "current_date": date, "state": "wave3_forming", "direction": direction,
        "confidence": 0.8, "current_price": price, "target_low": t_lo, "target_high": t_hi,
        "last_pivot_date": "2026-01-02", "last_pivot_price": pivot,
        "expectation_text": "x", "entry_limit_price": None,
    }


# ─── store_prices: upsert ────────────────────────────────────────────────

def test_store_prices_overwrites_existing_rows_but_counts_only_new(conn):
    """Intraday-Stand des heutigen Tages bzw. rückwirkend bereinigte Kurse
    müssen beim nächsten Abruf überschrieben werden (früher INSERT OR IGNORE)."""
    store_prices(conn, _prices("AAA", [10.0, 11.0]))
    updated = _prices("AAA", [10.0, 11.5, 12.0])
    assert store_prices(conn, updated, overwrite=True) == 1
    closes = [r[0] for r in conn.execute("SELECT close FROM stock_prices ORDER BY date")]
    assert closes == [10.0, 11.5, 12.0]


def test_store_prices_without_overwrite_keeps_existing_rows(conn):
    store_prices(conn, _prices("AAA", [10.0, 11.0]))
    assert store_prices(conn, _prices("AAA", [10.0, 11.5, 12.0])) == 1
    closes = [r[0] for r in conn.execute("SELECT close FROM stock_prices ORDER BY date")]
    assert closes == [10.0, 11.0, 12.0]


# ─── Split-Erkennung ──────────────────────────────────────────────────────

def test_needs_full_reload_detects_split_adjusted_history(conn):
    store_prices(conn, _prices("AAA", [100.0] * 10))
    fresh_after_split = _prices("AAA", [50.0] * 10)
    assert needs_full_reload(conn, "AAA", fresh_after_split)


def test_high_dividend_is_rescaled_not_treated_as_split(conn):
    """Dividendenabschlag (~4%) ist ein realer Kursabschlag, kein Phantom-
    Sprung: Vorhersage bleibt erhalten und wird auf die neue Basis umgerechnet."""
    store_prices(conn, _prices("AAA", [100.0] * 12))
    dates = _prices("AAA", [0] * 12)["date"].tolist()
    record_snapshot(conn, "AAA", _wave(dates[9], 100.0), 5.0, 365)
    conn.commit()
    res = replace_history(conn, "AAA", _prices("AAA", [96.0] * 6 + [100.0] * 6))
    assert res["split_dates"] == []
    assert res["deleted"] == 0
    assert load_predictions(conn).iloc[0]["price_at_prediction"] == pytest.approx(100.0)


def test_needs_full_reload_ignores_small_dividend_drift_and_todays_bar(conn):
    store_prices(conn, _prices("AAA", [100.0] * 10))
    fresh = _prices("AAA", [99.5] * 7 + [80.0] * 3)   # letzte Tage = Intraday, ignoriert
    assert not needs_full_reload(conn, "AAA", fresh)


def test_replace_history_fixes_phantom_crash_and_rescales_predictions(conn):
    # Gespeichert: 2:1-Split ab Tag 6 nicht bereinigt -> Phantom-Crash 100 -> 50
    store_prices(conn, _prices("AAA", [100.0] * 5 + [50.0] * 5))
    dates = _prices("AAA", [0] * 10)["date"].tolist()
    # Vorhersage VOR dem Split: Fenster ohne Phantom-Crash -> umrechnen
    record_snapshot(conn, "AAA", _wave(dates[2], 100.0), 5.0, 365)
    # Vorhersage NACH dem Split: Lookback enthielt den Phantom-Crash -> löschen
    w = _wave(dates[8], 50.0, pivot=45.0, t_lo=55.0, t_hi=65.0)
    w["last_pivot_date"] = dates[6]
    record_snapshot(conn, "AAA", w, 5.0, 365)
    conn.commit()

    res = replace_history(conn, "AAA", _prices("AAA", [50.0] * 10))
    conn.commit()

    assert res["split_dates"] == [dates[5]]
    assert res["deleted"] == 1
    preds = load_predictions(conn)
    assert len(preds) == 1
    p = preds.iloc[0]
    assert p["price_at_prediction"] == pytest.approx(50.0)
    assert p["target_low"] == pytest.approx(55.0)
    assert p["last_pivot_price"] == pytest.approx(45.0)
    assert p["status"] == "open"
    assert conn.execute("SELECT COUNT(*) FROM wave_snapshots").fetchone()[0] == 1
    closes = {r[0] for r in conn.execute("SELECT close FROM stock_prices")}
    assert closes == {50.0}
    assert tickers_without_full_history(conn, ["AAA", "BBB"]) == ["BBB"]


def test_replace_history_keeps_rows_older_than_new_history(conn):
    store_prices(conn, _prices("AAA", [100.0] * 10))
    newer_only = _prices("AAA", [100.0] * 5, start="2026-01-08")
    replace_history(conn, "AAA", newer_only)
    assert conn.execute("SELECT COUNT(*) FROM stock_prices").fetchone()[0] == 10


# ─── Zufalls-Basis / ehrliche Trefferquote ───────────────────────────────

def test_barrier_baseline_bullish():
    # Stop 10 entfernt, Ziel 30 entfernt -> 10 / 40
    base, in_zone = barrier_baseline("bullish", 100.0, 130.0, 150.0, 90.0)
    assert base == pytest.approx(0.25)
    assert in_zone is False


def test_barrier_baseline_bearish_mirrors_bullish():
    base, in_zone = barrier_baseline("bearish", 100.0, 50.0, 70.0, 110.0)
    assert base == pytest.approx(10 / 40)
    assert in_zone is False


def test_barrier_baseline_flags_price_already_in_zone():
    assert barrier_baseline("bullish", 120.0, 110.0, 130.0, 90.0) == (1.0, True)
    assert barrier_baseline("bullish", 100.0, None, None, 90.0) == (None, None)


def test_record_snapshot_skips_non_canonical_scans(conn):
    assert not record_snapshot(conn, "AAA", _wave("2026-01-05", 100.0), 6.0, 365)
    assert not record_snapshot(conn, "AAA", _wave("2026-01-05", 100.0), 5.0, 180)
    assert record_snapshot(conn, "AAA", _wave("2026-01-05", 100.0), 5.0, 365)
    assert len(load_predictions(conn)) == 1


def test_accuracy_stats_excludes_in_zone_and_reports_edge():
    df = pd.DataFrame({
        "status":              ["hit", "hit", "invalidated", "hit", "open", "invalidated"],
        "target_low":          [1.0, 1.0, 1.0, 1.0, 1.0, None],   # letzte: ohne Zielzone
        "baseline_prob":       [0.5, 0.5, 0.5, 1.0, 0.5, None],
        "in_zone_at_creation": [0, 0, 0, 1, 0, None],
    })
    s = accuracy_stats(df)
    assert s["decided"] == 3
    assert s["in_zone"] == 1
    assert s["hit_rate"] == pytest.approx(2 / 3)
    assert s["baseline"] == pytest.approx(0.5)
    assert s["edge"] == pytest.approx(2 / 3 - 0.5)


def test_single_bad_day_is_not_a_split(conn):
    """Ein einzelner fehlerhaft gespeicherter Tag (eingefrorener Intraday-
    Stand) springt hin und zurück — Kurse werden ersetzt, aber Vorhersagen
    bleiben erhalten und werden nicht umgerechnet."""
    closes = [100.0] * 12
    closes[6] = 90.0
    store_prices(conn, _prices("AAA", closes))
    dates = _prices("AAA", [0] * 12)["date"].tolist()
    record_snapshot(conn, "AAA", _wave(dates[9], 100.0), 5.0, 365)
    w = _wave(dates[6], 90.0)
    w["last_pivot_date"] = dates[4]
    record_snapshot(conn, "AAA", w, 5.0, 365)
    conn.commit()

    res = replace_history(conn, "AAA", _prices("AAA", [100.0] * 12))

    assert res["split_dates"] == []
    assert res["deleted"] == 0
    assert set(load_predictions(conn)["price_at_prediction"]) == {100.0, 90.0}
