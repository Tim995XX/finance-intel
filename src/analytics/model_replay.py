"""
Walk-forward-Rückrechnung des Elliott-Wellen-Modells über die gespeicherte
Kurshistorie: für jeden Handelstag wird analyze_current_wave() nur mit den
Kursen bis zu diesem Tag aufgerufen (wie im täglichen Scan:
CANONICAL_THRESHOLD_PCT, CANONICAL_LOOKBACK_DAYS), dedupliziert wie
record_prediction() und aufgelöst wie evaluate_open_predictions().

Ergebnis landet in der Tabelle replay_signals — Grundlage für den Vergleich
der Modellversionen (model_versions.py) über viele Jahre statt nur über die
wenigen Monate Live-Historie. Aufruf: python main.py replay (dauert einige
Minuten, nutzt alle CPU-Kerne). Mit python main.py replay test wird statt der
Watchlist das unberührte Test-Universum (test_universe.py, Tabelle
test_prices) durchgerechnet; die Ergebnisse stehen getrennt nach der Spalte
universe in replay_signals.
"""
from __future__ import annotations

import sqlite3
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

import numpy as np
import pandas as pd

from src.analytics.elliott_wave import analyze_current_wave
from src.analytics.signal_features import compute_features, MARKET_TICKER
from src.analytics.wave_tracker import (
    barrier_baseline, CANONICAL_THRESHOLD_PCT, CANONICAL_LOOKBACK_DAYS, MAX_HORIZON_DAYS,
)

FWD_DAYS = 20          # Horizont der Richtungs-Rendite (Handelstage)
MIN_ROWS = 120         # erst ab so vielen Kurstagen wird gescannt


def _replay_ticker(args) -> list[dict]:
    ticker, g, market = args
    g = g.reset_index(drop=True)
    dates = pd.to_datetime(g["date"])
    closes = g["close"].to_numpy()
    m_dates = market.index.to_numpy() if market is not None else None
    seen, rows = set(), []

    for i in range(MIN_ROWS, len(g)):
        lo = dates.searchsorted(dates[i] - pd.Timedelta(days=CANONICAL_LOOKBACK_DAYS))
        w = analyze_current_wave(g.iloc[lo:i + 1], threshold_pct=CANONICAL_THRESHOLD_PCT)
        if not w or w["target_low"] is None:
            continue
        key = (w["state"], w["last_pivot_date"])
        if key in seen:
            continue
        seen.add(key)

        bull = w["direction"] == "bullish"
        status, resolved_date = "open", None
        for j in range(i + 1, len(g)):
            p = g.iloc[j]
            if p["low"] <= w["target_high"] and p["high"] >= w["target_low"]:
                status, resolved_date = "hit", p["date"]; break
            if (bull and p["close"] < w["last_pivot_price"]) or (not bull and p["close"] > w["last_pivot_price"]):
                status, resolved_date = "invalidated", p["date"]; break
            if (dates[j] - dates[i]).days > MAX_HORIZON_DAYS:
                status, resolved_date = "expired", p["date"]; break

        price = w["current_price"]
        s = 1 if bull else -1
        near = w["target_low"] if bull else w["target_high"]
        base, in_zone = barrier_baseline(w["direction"], price, w["target_low"], w["target_high"],
                                         w["last_pivot_price"])
        mk = None
        if m_dates is not None:
            k = np.searchsorted(m_dates, g["date"][i], side="right")
            mk = market.iloc[max(0, k - 260):k]
        feats = compute_features(pd.Series(closes[max(0, i - 259):i + 1]), mk)
        rows.append({
            "ticker": ticker, "created_date": g["date"][i], "state": w["state"],
            "direction": w["direction"], "confidence": w["confidence"],
            "price": price, "target_low": w["target_low"], "target_high": w["target_high"],
            "last_pivot_price": w["last_pivot_price"],
            "baseline_prob": base, "in_zone_at_creation": int(bool(in_zone)),
            "d_target": (near - price) * s / price,
            "d_stop": (price - w["last_pivot_price"]) * s / price,
            "status": status, "resolved_date": resolved_date,
            "fwd20": closes[i + FWD_DAYS] / price - 1 if i + FWD_DAYS < len(g) else None,
            **feats,
        })
    return rows


UNIVERSES = ("watchlist", "test")


def run_replay(conn: sqlite3.Connection, workers: int | None = None,
               universe: str = "watchlist") -> pd.DataFrame:
    """Rechnet alle Aktien/ETFs eines Kurs-Universums neu durch und ersetzt
    dessen Zeilen in replay_signals. universe='watchlist': stock_prices ohne
    Krypto und Indizes; 'test': test_prices. Der Marktfilter (^GSPC) kommt in
    beiden Fällen aus stock_prices. Committet."""
    if universe not in UNIVERSES:
        raise ValueError(f"unbekanntes Universum: {universe}")
    if universe == "test":
        px = pd.read_sql("SELECT ticker, date, high, low, close FROM test_prices ORDER BY ticker, date", conn)
    else:
        crypto = {r[0] for r in conn.execute("SELECT ticker FROM watchlist WHERE asset_type='crypto'")}
        px = pd.read_sql("SELECT ticker, date, high, low, close FROM stock_prices ORDER BY ticker, date", conn)
        px = px[~px["ticker"].isin(crypto) & ~px["ticker"].str.startswith("^")]
    market_rows = conn.execute(
        "SELECT date, close FROM stock_prices WHERE ticker=? ORDER BY date", (MARKET_TICKER,)
    ).fetchall()
    market = pd.Series([r[1] for r in market_rows], index=[r[0] for r in market_rows], dtype=float) \
        if market_rows else None

    jobs = [(t, g, market) for t, g in px.groupby("ticker") if len(g) > MIN_ROWS + 30]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        rows = [r for chunk in ex.map(_replay_ticker, jobs, chunksize=4) for r in chunk]
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # Überrendite ggü. dem Durchschnitt aller Titel am selben Tag (gleicher
    # Horizont) — trennt Signalqualität von der allgemeinen Marktbewegung.
    closes = px.pivot(index="date", columns="ticker", values="close").sort_index()
    univ_fwd = (closes.shift(-FWD_DAYS) / closes - 1).mean(axis=1)
    df["univ_fwd20"] = df["created_date"].map(univ_fwd)
    df["run_date"] = str(datetime.now().date())
    df["universe"] = universe

    conn.execute("DELETE FROM replay_signals WHERE COALESCE(universe, 'watchlist')=?", (universe,))
    df.to_sql("replay_signals", conn, if_exists="append", index=False)
    conn.commit()
    return df


def load_replay(conn: sqlite3.Connection, universe: str = "watchlist") -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM replay_signals WHERE COALESCE(universe, 'watchlist')=?",
                       conn, params=[universe])
