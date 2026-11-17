"""
Plausibilitätsprüfung für Kurse vor dem Speichern (siehe db.store_prices).

Anlass (2026-11-02): Die Krypto-Historie kommt einmalig von CoinGecko, die
täglichen Updates aber von Yahoo unter '<SYMBOL>-USD'. Für M-USD und MNT-USD
liefert Yahoo dort einen ganz anderen, fast wertlosen Coin (0,0003 $ statt
~1 $, Volumen ~0) — erst an einzelnen Tagen (24.09.), ab 29.09. dauerhaft.
Das Wellenmodell las das als −99,9 %-Welle, die Auswertung zeigte im Schnitt
+1630 % je Short. Ähnliche Einzeltags-Ausreißer: SBK.JO, USDG-USD.

Regel: Ein Schlusskurs, der um mehr als MAX_DAILY_FACTOR vom letzten gültigen
Schlusskurs abweicht, wird verworfen — außer das neue Niveau hält
CONFIRM_ROWS Tage an (jeder Tag mindestens Faktor 2 vom alten Niveau entfernt,
in derselben Richtung) und wird mit nennenswertem Volumen gehandelt. Das ist
dann ein echter Crash/Kurssprung (z.B. Biotech nach Studiendaten); er wird
höchstens CONFIRM_ROWS-1 Tage später übernommen, weil der nächste Abruf die
Tage erneut enthält. Ein falscher Coin mit Volumen ~0 wird dauerhaft
abgewiesen. Splits laufen über price_history.py (bereinigte Gesamthistorie).

Nicht-positive Kurse (CL=F am 20.04.2020: −37,63 $) sind echt und werden
nicht geprüft.
"""
from __future__ import annotations

import logging
import sqlite3
from statistics import median

import pandas as pd

logger = logging.getLogger(__name__)

MAX_DAILY_FACTOR = 4.0
CONFIRM_ROWS     = 3
# Mindestabstand jedes Bestätigungstags vom alten Niveau — ein Ausreißer, der
# am Folgetag (fast) zurückspringt, bestätigt nichts.
CONFIRM_FACTOR   = 2.0
# Median-Volumen des neuen Niveaus mindestens 5 % des bisherigen Medians.
MIN_VOLUME_SHARE = 0.05
REF_VOLUME_ROWS  = 20


def _level_confirmed(closes, volumes, i: int, ref: float, vol_hist: list[float]) -> bool:
    window = closes[i:i + CONFIRM_ROWS]
    if len(window) < CONFIRM_ROWS or not all(w > 0 for w in window):
        return False
    up = closes[i] > ref
    if not all((w > ref * CONFIRM_FACTOR) if up else (w < ref / CONFIRM_FACTOR) for w in window):
        return False
    ref_vol = median(vol_hist) if vol_hist else 0.0
    if ref_vol <= 0:   # Indizes/Devisen ohne Volumen: Dauer allein entscheidet
        return True
    return median(volumes[i:i + CONFIRM_ROWS]) >= MIN_VOLUME_SHARE * ref_vol


def implausible_mask(closes, volumes, ref_close: float | None = None,
                     ref_volumes=(), stored=None) -> list[bool]:
    """Je Zeile True = verwerfen. closes/volumes chronologisch; ref_close ist
    der letzte bereits gespeicherte Schlusskurs davor (None: erste gültige
    Zeile wird Referenz), ref_volumes dessen Volumen-Vorgeschichte.
    stored: je Zeile der bereits gespeicherte (close, volume) desselben Tages
    oder None. Ein gespeicherter Tag ist die Referenz für die Folgetage, nicht
    der frisch abgerufene Wert — sonst schiebt ein falscher Coin die Referenz
    über die überlappenden Tage schrittweise auf sein eigenes Niveau."""
    closes  = [float(c) for c in closes]
    volumes = [float(v) if pd.notna(v) else 0.0 for v in volumes]
    stored  = list(stored) if stored is not None else [None] * len(closes)
    ref = ref_close if ref_close is not None and ref_close > 0 else None
    vol_hist = list(ref_volumes)[-REF_VOLUME_ROWS:]
    bad = [False] * len(closes)

    for i, c in enumerate(closes):
        if c > 0:              # nicht-positiv/NaN: ungeprüft durchlassen
            ok = (ref is None or 1 / MAX_DAILY_FACTOR <= c / ref <= MAX_DAILY_FACTOR
                  or _level_confirmed(closes, volumes, i, ref, vol_hist))
            bad[i] = not ok
        s = stored[i]
        if s is not None and s[0] is not None and s[0] > 0:
            ref = float(s[0])
            vol_hist = (vol_hist + [float(s[1] or 0)])[-REF_VOLUME_ROWS:]
        elif c > 0 and not bad[i]:
            ref = c
            vol_hist = (vol_hist + [volumes[i]])[-REF_VOLUME_ROWS:]
    return bad


def implausible_segments_mask(closes, crypto: bool) -> list[bool]:
    """Für die nachträgliche Bereinigung einer kompletten gespeicherten Reihe
    (price_history.clean_implausible_prices). Die Reihe wird an jedem Sprung
    > MAX_DAILY_FACTOR in Abschnitte geteilt; maßgeblich ist der längste
    Abschnitt. Verworfen werden Abschnitte, deren Median-Kurs um mehr als
    MAX_DAILY_FACTOR von dessen Median abweicht — bei Krypto alle solchen
    (falscher Coin unter gleichem Kürzel), bei Aktien nur die mit
    < CONFIRM_ROWS Tagen (Einzelausreißer wie SBK.JO am 25.04.2025).

    Aktien bleiben sonst unangetastet: echte Kursexplosionen (BMNR 06/2025,
    DJT/DWAC 10/2021) bilden lange Abschnitte mit geringem Volumen davor und
    sind per Volumen nicht von einem falschen Coin zu trennen (0,15 % vs.
    0,1 % des Hauptabschnitts). Anders als implausible_mask() hängt das
    Ergebnis nicht davon ab, welcher Abschnitt zuerst kommt: bei M-USD/
    USDG-USD/GRAM-USD stammt der ANFANG der Historie (Yahoo-Rückabruf bis
    23.09.2025) vom falschen Coin, erst danach folgen die CoinGecko-Kurse."""
    closes = [float(c) for c in closes]
    starts, last = [0], None
    for i, c in enumerate(closes):
        if not c > 0:
            continue
        if last is not None and not 1 / MAX_DAILY_FACTOR <= c / last <= MAX_DAILY_FACTOR:
            starts.append(i)
        last = c
    if len(starts) == 1:
        return [False] * len(closes)

    bounds = list(zip(starts, starts[1:] + [len(closes)]))
    levels = [median([c for c in closes[a:b] if c > 0] or [0.0]) for a, b in bounds]
    main_a, main_b = max(bounds, key=lambda s: s[1] - s[0])
    main = levels[bounds.index((main_a, main_b))]
    bad = [False] * len(closes)
    for (a, b), lvl in zip(bounds, levels):
        off_level = main > 0 and lvl > 0 and not 1 / MAX_DAILY_FACTOR <= lvl / main <= MAX_DAILY_FACTOR
        if off_level and (crypto or b - a < CONFIRM_ROWS):
            bad[a:b] = [c > 0 for c in closes[a:b]]
    return bad


def drop_implausible(conn: sqlite3.Connection, df: pd.DataFrame) -> pd.DataFrame:
    """Entfernt unplausible Zeilen aus einem OHLCV-DataFrame (mehrere Ticker
    möglich), gemessen am zuletzt gespeicherten Kurs vor dem Abruf-Fenster.
    Verworfene Zeilen werden als Warnung geloggt."""
    keep = []
    for ticker, g in df.groupby("ticker", sort=False):
        g = g.sort_values("date")
        ref = conn.execute(
            "SELECT close FROM stock_prices WHERE ticker=? AND date < ? AND close > 0 "
            "ORDER BY date DESC LIMIT 1",
            (ticker, g["date"].iloc[0]),
        ).fetchone()
        ref_vols = [float(r[0] or 0) for r in conn.execute(
            "SELECT volume FROM stock_prices WHERE ticker=? AND date < ? "
            "ORDER BY date DESC LIMIT ?",
            (ticker, g["date"].iloc[0], REF_VOLUME_ROWS),
        ).fetchall()[::-1]]
        # Das Abruf-Fenster (UPDATE_DAYS) überlappt fast immer bereits
        # gespeicherte Tage — die gelten als Referenz (siehe implausible_mask).
        have = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT date, close, volume FROM stock_prices WHERE ticker=? AND date >= ?",
            (ticker, g["date"].iloc[0]),
        ).fetchall()}
        bad = implausible_mask(g["close"], g["volume"] if "volume" in g else [0] * len(g),
                               float(ref[0]) if ref else None, ref_vols,
                               stored=[have.get(d) for d in g["date"]])
        rejected = g[bad]
        if not rejected.empty:
            first = rejected.iloc[0]
            logger.warning(
                "%s: %d unplausible(r) Kurs(e) verworfen (z.B. %s: %.6g, Sprung > Faktor %.0f "
                "ggü. letztem gültigen Kurs)",
                ticker, len(rejected), first["date"], first["close"], MAX_DAILY_FACTOR,
            )
        keep.append(g[[not b for b in bad]])
    return pd.concat(keep) if keep else df.iloc[0:0]


# ─── Mindestkurs für Aktien ─────────────────────────────────────────────
# Anlass (2026-11-16): MICR kam über eine Insider-Meldung auf die Watchlist —
# eine Penny-Aktien-Leiche bei 0,0003 $, die wochenlang auf 0,05 $ und zurück
# springt (implausible_mask lässt das durch, weil das neue Niveau anhält).
# Das ergab +16.900 % in 20 Tagen und verzerrte den Universumsdurchschnitt
# in live_signal_frame (Okt. +19,5 % statt Median +2,75 %). Aktien unter
# MIN_STOCK_PRICE werden deshalb weder zur Watchlist hinzugefügt noch
# gescannt noch im Vergleichsuniversum mitgezählt. Krypto/Indizes sind
# ausgenommen (ADA, DOT usw. notieren regulär unter 1 $).

MIN_STOCK_PRICE = 1.0
_PRICE_CHECKED_TYPES = "COALESCE(w.asset_type, 'stock') NOT IN ('crypto', 'index')"


def is_penny(last_close) -> bool:
    """True, wenn ein (Aktien-)Schlusskurs unter dem Mindestkurs liegt.
    Fehlende Kurse zählen nicht als Penny — dafür gibt es eigene Prüfungen."""
    return last_close is not None and pd.notna(last_close) and 0 < float(last_close) < MIN_STOCK_PRICE


def penny_tickers(conn: sqlite3.Connection) -> set[str]:
    """Aktien (alles außer Krypto/Index), deren letzter gespeicherter
    Schlusskurs unter MIN_STOCK_PRICE liegt."""
    rows = conn.execute(
        f"""SELECT p.ticker, p.close FROM stock_prices p
            LEFT JOIN watchlist w ON w.ticker = p.ticker
            WHERE {_PRICE_CHECKED_TYPES}
              AND p.date = (SELECT MAX(date) FROM stock_prices WHERE ticker = p.ticker)"""
    ).fetchall()
    return {r[0] for r in rows if is_penny(r[1])}
