"""
Kontext-Merkmale eines Wellensignals zum Zeitpunkt seiner Erkennung —
ausschließlich aus Kursen bis einschließlich zu diesem Tag (kein Blick in
die Zukunft). Dieselbe Funktion wird für Live-Vorhersagen (wave_tracker),
das nachträgliche Ergänzen alter Vorhersagen und die Walk-forward-
Rückrechnung (model_replay) genutzt, damit alle Modellversionen auf exakt
denselben Merkmalen verglichen werden.
"""
from __future__ import annotations

import json
import sqlite3

import pandas as pd

MARKET_TICKER = "^GSPC"


def compute_features(closes: pd.Series, market_closes: pd.Series | None) -> dict:
    """closes / market_closes: Schlusskurse aufsteigend nach Datum, letzter
    Wert = Signaltag. Fehlende Historie -> Merkmal None."""
    c = closes.dropna().to_numpy()
    f: dict = {"above_sma200": None, "dist_sma200": None, "mom126": None, "mkt_above_sma200": None}
    if len(c) >= 200:
        sma = c[-200:].mean()
        f["above_sma200"] = bool(c[-1] > sma)
        f["dist_sma200"] = float(c[-1] / sma - 1)
    if len(c) >= 126:
        f["mom126"] = float(c[-21] / c[-126] - 1)
    if market_closes is not None:
        m = market_closes.dropna().to_numpy()
        if len(m) >= 200:
            f["mkt_above_sma200"] = bool(m[-1] > m[-200:].mean())
    return f


def _closes_until(conn: sqlite3.Connection, ticker: str, date: str, n: int = 260) -> pd.Series:
    rows = conn.execute(
        "SELECT date, close FROM stock_prices WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT ?",
        (ticker, date, n),
    ).fetchall()
    return pd.Series([r[1] for r in reversed(rows)], index=[r[0] for r in reversed(rows)], dtype=float)


def features_from_db(conn: sqlite3.Connection, ticker: str, date: str) -> dict:
    return compute_features(_closes_until(conn, ticker, date), _closes_until(conn, MARKET_TICKER, date))


def to_json(features: dict) -> str:
    return json.dumps(features)


def from_json(s: str | None) -> dict:
    return json.loads(s) if s else {}
