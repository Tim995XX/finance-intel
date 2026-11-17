"""
Konsistenz der gespeicherten Kurshistorie gegenüber Splits/Dividenden.

yfinance liefert split- und dividendenbereinigte Kurse (auto_adjust=True).
Nach einem Split ändert Yahoo rückwirkend die GESAMTE Historie — gespeichert
ist aber noch die alte Basis. Ohne Korrektur entsteht in stock_prices am
Split-Tag ein künstlicher Kurssprung (z.B. -50% bei 2:1), der im
Elliott-Wellen-Modul als echter Pivot zählt.

Ablauf (siehe main.fetch_all_prices):
  1. needs_full_reload(): Vergleich frisch abgerufener mit gespeicherten
     Schlusskursen auf überlappenden Tagen. Weicht ein Tag um mehr als
     RELOAD_TOLERANCE ab, ist die gespeicherte Basis veraltet.
  2. replace_history(): komplette lange Historie ersetzen und die bereits
     protokollierten Wellen-Vorhersagen dieses Tickers auf die neue
     Kursbasis umrechnen. Vorhersagen, deren Lookback-Fenster einen
     unbereinigten Split enthielt, beruhen auf einem Phantom-Kurssprung und
     werden gelöscht.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pandas as pd

from src.database.db import store_prices

# Ab dieser Abweichung (frisch vs. gespeichert) wird die Historie ersetzt.
# Dividendenbereinigung über den 400-Tage-Abrufzeitraum bleibt fast immer
# darunter; ein Split (≥ 2:1, auch Spin-offs ~5%) klar darüber.
RELOAD_TOLERANCE = 0.02

# Dauerhafter Sprung im Umrechnungsfaktor (neu/alt), ab dem von einem Split
# statt einer normalen Dividendenbereinigung ausgegangen wird. "Dauerhaft"
# heißt: Median der SPLIT_WINDOW Tage davor vs. danach — ein einzelner
# fehlerhafter Tag (z.B. ein früher eingefrorener Intraday-Stand) springt
# hin und gleich wieder zurück und ist damit kein Split.
# 15%: liegt unter dem kleinsten üblichen Split (5:4 = 25%), aber über hohen
# Dividenden (ALV/DBK ~4%) und Spin-offs (~5-6%) — dort ist der Kursabschlag
# am Ex-Tag real und kein Phantom-Sprung, die Vorhersagen werden nur
# umgerechnet.
SPLIT_STEP_TOLERANCE = 0.15
SPLIT_WINDOW = 5

# Muss zum Lookback der Wellen-Scans passen (main.scan_wave_signals).
WAVE_LOOKBACK_DAYS = 365

# Die letzten Handelstage bleiben beim Vergleich außen vor: der heutige
# Balken ist ein Intraday-Stand und ändert sich bis Handelsschluss.
_IGNORE_RECENT_ROWS = 3

_PRED_PRICE_COLS = ("price_at_prediction", "target_low", "target_high",
                    "last_pivot_price", "entry_limit_price")
_SNAP_PRICE_COLS = ("current_price", "target_low", "target_high", "last_pivot_price")


def _stored_closes(conn: sqlite3.Connection, ticker: str) -> pd.Series:
    rows = conn.execute(
        "SELECT date, close FROM stock_prices WHERE ticker=? ORDER BY date", (ticker,)
    ).fetchall()
    return pd.Series({r[0]: r[1] for r in rows}, dtype=float)


def _ratio(conn: sqlite3.Connection, ticker: str, fresh: pd.DataFrame) -> pd.Series:
    """Umrechnungsfaktor neu/alt je gespeichertem Tag (nur überlappende Tage)."""
    old = _stored_closes(conn, ticker)
    new = fresh.set_index("date")["close"].astype(float)
    common = old.index.intersection(new.index)
    old, new = old[common], new[common]
    valid = old > 0
    return (new[valid] / old[valid]).sort_index()


def needs_full_reload(conn: sqlite3.Connection, ticker: str, fresh: pd.DataFrame) -> bool:
    """True, wenn gespeicherte und frisch abgerufene Kurse auf gemeinsamen
    (abgeschlossenen) Handelstagen um mehr als RELOAD_TOLERANCE abweichen."""
    if fresh.empty:
        return False
    ratio = _ratio(conn, ticker, fresh.iloc[:-_IGNORE_RECENT_ROWS])
    return bool(((ratio - 1).abs() > RELOAD_TOLERANCE).any())


def tickers_without_full_history(conn: sqlite3.Connection, tickers: list[str]) -> list[str]:
    have = {r[0] for r in conn.execute("SELECT ticker FROM price_history_meta").fetchall()}
    return [t for t in tickers if t not in have]


def _smoothed(ratio: pd.Series) -> pd.Series:
    """Faktor ohne einzelne Ausreißer-Tage (zentrierter Median)."""
    return ratio.rolling(SPLIT_WINDOW, center=True, min_periods=1).median()


def _factor_at(ratio: pd.Series, date: str | None) -> float:
    """Faktor am Tag `date` bzw. am letzten Handelstag davor; 1.0, wenn für
    diesen Zeitraum keine alten Kurse gespeichert waren."""
    if date is None or ratio.empty:
        return 1.0
    prior = ratio[ratio.index <= date]
    return float(prior.iloc[-1]) if not prior.empty else 1.0


def _split_dates(ratio: pd.Series) -> list[str]:
    """Tage, ab denen sich der Umrechnungsfaktor dauerhaft ändert (erster
    Tag der neuen Kursbasis). Benötigt mindestens 2 Tage danach — ein Split
    erst am allerletzten gespeicherten Tag bleibt unerkannt; dessen Kurse
    werden trotzdem ersetzt, nur die Vorhersagen dieses einen Tages nicht
    gelöscht."""
    vals, idx = ratio.to_numpy(), list(ratio.index)
    out, i = [], 1
    while i < len(vals) - 1:
        step   = vals[i] / vals[i - 1] - 1
        before = pd.Series(vals[max(0, i - SPLIT_WINDOW):i]).median()
        after  = pd.Series(vals[i:i + SPLIT_WINDOW]).median()
        if (abs(step) > SPLIT_STEP_TOLERANCE and len(vals) - i >= 2
                and abs(after / before - 1) > SPLIT_STEP_TOLERANCE):
            out.append(idx[i])
            i += SPLIT_WINDOW   # nicht denselben Sprung mehrfach melden
            continue
        i += 1
    return out


def replace_history(conn: sqlite3.Connection, ticker: str, full: pd.DataFrame) -> dict:
    """Ersetzt die gespeicherte Kurshistorie von `ticker` durch `full` und
    bringt wave_predictions/wave_snapshots auf dieselbe Kursbasis:
      - Vorhersagen, in deren Lookback-Fenster (WAVE_LOOKBACK_DAYS bis zum
        Erstellungstag) ein unbereinigter Split lag, werden gelöscht — das
        Modell hat dort einen Phantom-Kurssprung als Welle gezählt.
      - alle übrigen Preisfelder werden mit dem Faktor neu/alt ihres
        Stichtags umgerechnet.
      - bei erkanntem Split werden die verbleibenden Vorhersagen wieder auf
        'open' gesetzt, damit evaluate_open_predictions() sie mit den
        korrigierten Kursen neu auflöst.
    Committet nicht (Aufrufer committet). Gibt {split_dates, deleted,
    rescaled} zurück."""
    raw = _ratio(conn, ticker, full)
    splits = _split_dates(raw)
    ratio = _smoothed(raw)

    deleted = rescaled = 0
    preds = conn.execute(
        "SELECT id, created_date, resolved_date FROM wave_predictions WHERE ticker=?", (ticker,)
    ).fetchall()
    for pid, created, resolved in preds:
        window_start = str((datetime.strptime(created, "%Y-%m-%d")
                            - timedelta(days=WAVE_LOOKBACK_DAYS)).date())
        if any(window_start < s <= created for s in splits):
            conn.execute("DELETE FROM wave_snapshots WHERE prediction_id=?", (pid,))
            conn.execute("DELETE FROM wave_predictions WHERE id=?", (pid,))
            deleted += 1
            continue
        f = _factor_at(ratio, created)
        if abs(f - 1) > 1e-6:
            conn.execute(
                f"UPDATE wave_predictions SET "
                + ", ".join(f"{c} = {c} * ?" for c in _PRED_PRICE_COLS)
                + " WHERE id=?",
                (*([f] * len(_PRED_PRICE_COLS)), pid),
            )
            rescaled += 1
        if resolved is not None:
            conn.execute(
                "UPDATE wave_predictions SET resolved_price = resolved_price * ? WHERE id=?",
                (_factor_at(ratio, resolved), pid),
            )

    snaps = conn.execute(
        "SELECT id, snapshot_date FROM wave_snapshots WHERE ticker=?", (ticker,)
    ).fetchall()
    for sid, snap_date in snaps:
        f = _factor_at(ratio, snap_date)
        if abs(f - 1) > 1e-6:
            conn.execute(
                f"UPDATE wave_snapshots SET "
                + ", ".join(f"{c} = {c} * ?" for c in _SNAP_PRICE_COLS)
                + " WHERE id=?",
                (*([f] * len(_SNAP_PRICE_COLS)), sid),
            )

    if splits:
        conn.execute(
            "UPDATE wave_predictions SET status='open', resolved_date=NULL, resolved_price=NULL "
            "WHERE ticker=?", (ticker,),
        )

    # Nur ab Beginn der neuen Historie löschen: liefert Yahoo (selten) eine
    # kürzere Historie als gespeichert, bleiben ältere Zeilen erhalten statt
    # verloren zu gehen.
    conn.execute("DELETE FROM stock_prices WHERE ticker=? AND date >= ?",
                 (ticker, full["date"].min()))
    store_prices(conn, full)
    conn.execute(
        "INSERT INTO price_history_meta (ticker, full_history_date) VALUES (?, date('now')) "
        "ON CONFLICT(ticker) DO UPDATE SET full_history_date=excluded.full_history_date",
        (ticker,),
    )
    return {"split_dates": splits, "deleted": deleted, "rescaled": rescaled}


def clean_implausible_prices(conn: sqlite3.Connection) -> dict:
    """Einmalige Bereinigung bereits gespeicherter Kurse nach
    price_plausibility.implausible_segments_mask() (falscher Coin unter
    gleichem Kürzel, Einzelausreißer). Für jeden betroffenen Ticker:
      - unplausible Kurszeilen löschen
      - Vorhersagen (samt Snapshots), in deren Lookback-Fenster ein
        gelöschter Tag lag, löschen — das Modell hat dort einen Phantom-
        Sprung als Welle gezählt (wie bei Splits, siehe replace_history)
      - übrige Vorhersagen, die an/nach dem ersten gelöschten Tag aufgelöst
        wurden, wieder auf 'open' setzen, damit evaluate_open_predictions()
        sie mit den bereinigten Kursen neu auflöst.
    Committet nicht. Gibt {tickers: {ticker: n_rows}, deleted, reopened}."""
    from src.database.price_plausibility import implausible_segments_mask

    crypto = {r[0] for r in conn.execute("SELECT ticker FROM watchlist WHERE asset_type='crypto'")}
    tickers = [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM stock_prices")]
    out = {"tickers": {}, "deleted": 0, "reopened": 0}
    for ticker in tickers:
        rows = conn.execute(
            "SELECT date, close FROM stock_prices WHERE ticker=? ORDER BY date", (ticker,)
        ).fetchall()
        bad = implausible_segments_mask(
            [r[1] if r[1] is not None else float("nan") for r in rows],
            crypto=ticker in crypto or ticker.endswith("-USD"),
        )
        bad_dates = [r[0] for r, b in zip(rows, bad) if b]
        if not bad_dates:
            continue
        conn.executemany("DELETE FROM stock_prices WHERE ticker=? AND date=?",
                         [(ticker, d) for d in bad_dates])
        out["tickers"][ticker] = len(bad_dates)

        def contaminated(day: str) -> bool:
            start = str((datetime.strptime(day, "%Y-%m-%d")
                         - timedelta(days=WAVE_LOOKBACK_DAYS)).date())
            # inklusive: der Scan liest BETWEEN heute-365 AND heute
            return any(start <= d <= day for d in bad_dates)

        for pid, created in conn.execute(
            "SELECT id, created_date FROM wave_predictions WHERE ticker=?", (ticker,)
        ).fetchall():
            if contaminated(created):
                conn.execute("DELETE FROM wave_snapshots WHERE prediction_id=?", (pid,))
                conn.execute("DELETE FROM wave_predictions WHERE id=?", (pid,))
                out["deleted"] += 1
        for sid, snap_date in conn.execute(
            "SELECT id, snapshot_date FROM wave_snapshots WHERE ticker=?", (ticker,)
        ).fetchall():
            if contaminated(snap_date):
                conn.execute("DELETE FROM wave_snapshots WHERE id=?", (sid,))
        out["reopened"] += conn.execute(
            "UPDATE wave_predictions SET status='open', resolved_date=NULL, resolved_price=NULL "
            "WHERE ticker=? AND status!='open' AND resolved_date >= ?",
            (ticker, bad_dates[0]),
        ).rowcount
    return out
