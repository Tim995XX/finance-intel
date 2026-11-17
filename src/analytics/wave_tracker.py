"""
Trackt die vom Elliott-Wellen-Modul (analyze_current_wave) erzeugten
Kurserwartungen über die Zeit, damit sich nachträglich prüfen lässt, wie
zuverlässig die Schätzungen sind (Zielzone erreicht vs. Wellenzählung
invalidiert vs. ausgelaufen ohne Entscheidung).

Kein ML — reine Buchführung, in zwei Tabellen:
  - wave_predictions: eine "Lineage" pro erkannter Welle (dedupliziert über
    Ticker+Zustand+auslösenden Pivot), final aufgelöst zu hit/invalidated/
    expired. Beantwortet "wie oft lag das Modell insgesamt richtig".
  - wave_snapshots: append-only Verlaufslog — bei jedem Check (z.B.
    wöchentlicher Wellen-Scanner-Lauf) wird ein neuer Eintrag angehängt,
    nie überschrieben. Beantwortet "wie hat sich die Einschätzung für diesen
    Ticker über die Zeit entwickelt" (erste vs. aktuellste Erwartung).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

import pandas as pd

from src.analytics.model_versions import ACTIVE_VERSION, accept_mask, stake_weights
from src.analytics.signal_features import features_from_db, to_json

MAX_HORIZON_DAYS = 120  # unentschiedene Vorhersagen gelten danach als "expired"

# Nur Erwartungen aus einem Scan mit genau diesen Parametern fließen in die
# Statistik ein (main.scan_wave_signals, Dashboard-Scans mit Default-Reglern).
# Andere Lookback-Fenster/Empfindlichkeiten liefern andere Pivots für
# denselben Ticker und würden die Trefferquoten vermischen.
CANONICAL_THRESHOLD_PCT = 5.0
CANONICAL_LOOKBACK_DAYS = 365


def is_canonical_scan(threshold_pct: float, lookback_days: int) -> bool:
    return threshold_pct == CANONICAL_THRESHOLD_PCT and lookback_days == CANONICAL_LOOKBACK_DAYS


def barrier_baseline(
    direction: str, price: float, target_low: float | None, target_high: float | None,
    pivot_price: float,
) -> tuple[float | None, bool | None]:
    """Zufalls-Basis einer Vorhersage: Wahrscheinlichkeit, dass ein reiner
    Zufallskurs (ohne Drift) die nahe Zielzonen-Kante vor dem stützenden Pivot
    erreicht — P ≈ Abstand_Stop / (Abstand_Stop + Abstand_Ziel). Liegt das
    Ziel nah und der Pivot weit weg, ist eine hohe Trefferquote auch ohne
    jede Vorhersagekraft zu erwarten; erst Trefferquote MINUS dieser Basis
    zeigt, ob das Modell etwas kann.

    Gibt (baseline, in_zone) zurück; in_zone=True heißt: Kurs stand bei
    Erstellung schon in/jenseits der Zielzone (Treffer am nächsten Tag ist
    dann keine Vorhersageleistung). (None, None) ohne Zielzone."""
    if target_low is None or target_high is None or not price:
        return None, None
    s = 1 if direction == "bullish" else -1
    near = target_low if s == 1 else target_high
    d_target = (near - price) * s
    d_stop = max((price - pivot_price) * s, 0.0)
    if d_target <= 0:
        return 1.0, True
    if d_stop + d_target == 0:
        return None, False
    return d_stop / (d_stop + d_target), False


def backfill_prediction_context(conn: sqlite3.Connection) -> int:
    """Ergänzt baseline_prob/in_zone_at_creation und features_json für
    Vorhersagen, die vor Einführung dieser Spalten angelegt wurden. Die
    Merkmale werden nur aus Kursen bis zum Erstellungstag berechnet (wie sie
    damals bekannt waren). Committet nicht. Gibt die Anzahl ergänzter
    Einträge zurück."""
    rows = conn.execute(
        """SELECT id, direction, price_at_prediction, target_low, target_high, last_pivot_price
           FROM wave_predictions WHERE baseline_prob IS NULL AND target_low IS NOT NULL"""
    ).fetchall()
    for r in rows:
        base, in_zone = barrier_baseline(r[1], r[2], r[3], r[4], r[5])
        conn.execute(
            "UPDATE wave_predictions SET baseline_prob=?, in_zone_at_creation=? WHERE id=?",
            (base, None if in_zone is None else int(in_zone), r[0]),
        )
    missing = conn.execute(
        "SELECT id, ticker, created_date FROM wave_predictions WHERE features_json IS NULL"
    ).fetchall()
    for pid, ticker, created in missing:
        conn.execute(
            "UPDATE wave_predictions SET features_json=? WHERE id=?",
            (to_json(features_from_db(conn, ticker, created)), pid),
        )
    return len(rows) + len(missing)


def record_prediction(
    conn: sqlite3.Connection, ticker: str, wave: dict, threshold_pct: float
) -> bool:
    """Speichert eine von analyze_current_wave() erkannte Erwartung. Der
    UNIQUE-Constraint (ticker, state, last_pivot_date) sorgt dafür, dass
    wiederholtes Scannen desselben, noch laufenden Wellenbeins keine Duplikate
    erzeugt — es entsteht genau ein Eintrag pro tatsächlich neuer Erwartung.
    Committet nicht (Aufrufer committet). Gibt True zurück, wenn neu angelegt."""
    base, in_zone = barrier_baseline(
        wave["direction"], wave["current_price"], wave["target_low"], wave["target_high"],
        wave["last_pivot_price"],
    )
    cur = conn.execute(
        """INSERT OR IGNORE INTO wave_predictions
               (ticker, created_date, state, direction, confidence,
                price_at_prediction, target_low, target_high,
                last_pivot_date, last_pivot_price, threshold_pct, expectation_text,
                entry_limit_price, baseline_prob, in_zone_at_creation, features_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            ticker, wave["current_date"], wave["state"], wave["direction"], wave["confidence"],
            wave["current_price"], wave["target_low"], wave["target_high"],
            wave["last_pivot_date"], wave["last_pivot_price"], threshold_pct,
            wave["expectation_text"], wave.get("entry_limit_price"),
            base, None if in_zone is None else int(in_zone),
            to_json(features_from_db(conn, ticker, wave["current_date"])),
        ),
    )
    return cur.rowcount > 0


def record_snapshot(
    conn: sqlite3.Connection, ticker: str, wave: dict, threshold_pct: float,
    lookback_days: int,
) -> bool:
    """Hängt einen neuen Verlaufs-Eintrag an wave_snapshots an (ein Eintrag pro
    Ticker+Tag — mehrfaches Snapshotten am selben Tag überschreibt nur den
    heutigen Wert, ältere Tage bleiben unangetastet). Legt bei Bedarf zuerst
    die zugehörige wave_predictions-Lineage an (record_prediction) und verlinkt
    den Snapshot darüber. Committet nicht (Aufrufer committet).

    Scans mit abweichenden Parametern (siehe is_canonical_scan) werden nicht
    protokolliert — Rückgabe False."""
    if not is_canonical_scan(threshold_pct, lookback_days):
        return False
    record_prediction(conn, ticker, wave, threshold_pct)
    pred_row = conn.execute(
        "SELECT id FROM wave_predictions WHERE ticker=? AND state=? AND last_pivot_date=?",
        (ticker, wave["state"], wave["last_pivot_date"]),
    ).fetchone()
    pred_id = pred_row["id"] if pred_row else None

    conn.execute(
        """INSERT OR REPLACE INTO wave_snapshots
               (prediction_id, ticker, snapshot_date, state, direction, confidence,
                current_price, target_low, target_high, last_pivot_date,
                last_pivot_price, expectation_text)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            pred_id, ticker, wave["current_date"], wave["state"], wave["direction"],
            wave["confidence"], wave["current_price"], wave["target_low"], wave["target_high"],
            wave["last_pivot_date"], wave["last_pivot_price"], wave["expectation_text"],
        ),
    )
    return True


def latest_expectations(conn: sqlite3.Connection) -> pd.DataFrame:
    """Die jeweils aktuellste Erwartung pro Ticker (letzter Snapshot)."""
    return pd.read_sql(
        """SELECT s.* FROM wave_snapshots s
           INNER JOIN (
               SELECT ticker, MAX(snapshot_date) AS max_date
               FROM wave_snapshots GROUP BY ticker
           ) latest ON s.ticker = latest.ticker AND s.snapshot_date = latest.max_date
           ORDER BY s.ticker""",
        conn,
    )


def expectation_trend(conn: sqlite3.Connection, ticker: str) -> pd.DataFrame:
    """Vollständiger Verlauf aller Snapshots für einen Ticker, chronologisch
    sortiert — der erste Eintrag ist die ursprüngliche Einschätzung beim
    Erkennen des Musters, der letzte die aktuellste. Der Status-/Ergebnis-Wert
    aus der zugehörigen wave_predictions-Lineage wird mitgeliefert."""
    return pd.read_sql(
        """SELECT s.*, p.status, p.resolved_date, p.resolved_price
           FROM wave_snapshots s
           LEFT JOIN wave_predictions p ON p.id = s.prediction_id
           WHERE s.ticker = ?
           ORDER BY s.snapshot_date""",
        conn, params=[ticker],
    )


def weekly_accuracy(conn: sqlite3.Connection) -> pd.DataFrame:
    """Trefferquote gruppiert nach Kalenderwoche der Ersterkennung (created_date
    von wave_predictions) — zeigt, ob die Vorhersagequalität über die Zeit
    zu- oder abnimmt. Nur bereits entschiedene Fälle (hit/invalidated) fließen
    in die Quote ein (ohne Start in der Zielzone, siehe honest_decided);
    'offen' zählt separat mit, ohne die Quote zu verzerren. 'edge' =
    Trefferquote minus Zufalls-Basis derselben Fälle."""
    df = pd.read_sql(
        "SELECT created_date, status, target_low, baseline_prob, in_zone_at_creation "
        "FROM wave_predictions", conn,
    )
    if df.empty:
        return df
    df["week"] = pd.to_datetime(df["created_date"]).dt.strftime("%G-W%V")

    rows = []
    for week, grp in df.groupby("week"):
        decided = honest_decided(grp)
        hit_rate = (decided["status"] == "hit").mean() if len(decided) else None
        baseline = decided["baseline_prob"].mean() if len(decided) else None
        rows.append({
            "week":      week,
            "total":     len(grp),
            "open":      int((grp["status"] == "open").sum()),
            "hit":       int((grp["status"] == "hit").sum()),
            "invalidated": int((grp["status"] == "invalidated").sum()),
            "hit_rate":  hit_rate,
            "edge":      (hit_rate - baseline) if hit_rate is not None and pd.notna(baseline) else None,
        })
    return pd.DataFrame(rows).sort_values("week")


def evaluate_open_predictions(conn: sqlite3.Connection) -> dict:
    """Prüft alle offenen Vorhersagen Tag für Tag gegen die seither
    eingetroffenen Kursdaten und aktualisiert deren Status:
      - 'hit'         Tageskurs (High/Low) hat die projizierte Zielzone berührt
      - 'invalidated' Schlusskurs ist unter/über den stützenden Pivot
                       zurückgefallen — die Wellenzählung, auf der die
                       Erwartung beruhte, gilt damit als hinfällig
      - 'expired'     nach MAX_HORIZON_DAYS Tagen weder noch eingetreten
      - 'open'        weiterhin unentschieden, bleibt unverändert

    Muster ohne Zielzone (z.B. "möglicher neuer Impuls") können nicht als
    'hit' aufgelöst werden — dort zeigt 'expired' (statt 'invalidated'), dass
    die erwartete Richtung bis zum Horizont zumindest nicht widerlegt wurde.

    Gibt {checked, hit, invalidated, expired} zurück.
    """
    backfill_prediction_context(conn)
    open_rows = conn.execute("SELECT * FROM wave_predictions WHERE status='open'").fetchall()
    summary = {"checked": len(open_rows), "hit": 0, "invalidated": 0, "expired": 0}
    today = datetime.today().date()

    for row in open_rows:
        prices = pd.read_sql(
            """SELECT date, high, low, close FROM stock_prices
               WHERE ticker=? AND date > ? ORDER BY date""",
            conn, params=[row["ticker"], row["created_date"]],
        )
        if prices.empty:
            continue

        bullish  = row["direction"] == "bullish"
        resolved = None

        for _, p in prices.iterrows():
            hit = (
                row["target_low"] is not None
                and p["low"] <= row["target_high"] and p["high"] >= row["target_low"]
            )
            invalidated = (
                (bullish and p["close"] < row["last_pivot_price"])
                or (not bullish and p["close"] > row["last_pivot_price"])
            )
            if hit:
                resolved = ("hit", p["date"], float(p["close"]))
                break
            if invalidated:
                resolved = ("invalidated", p["date"], float(p["close"]))
                break

        if resolved is None:
            created = datetime.strptime(row["created_date"], "%Y-%m-%d").date()
            if (today - created).days > MAX_HORIZON_DAYS:
                last = prices.iloc[-1]
                resolved = ("expired", last["date"], float(last["close"]))

        if resolved:
            status, resolved_date, resolved_price = resolved
            conn.execute(
                "UPDATE wave_predictions SET status=?, resolved_date=?, resolved_price=? WHERE id=?",
                (status, resolved_date, resolved_price, row["id"]),
            )
            summary[status] += 1

    conn.commit()
    return summary


def load_predictions(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM wave_predictions ORDER BY created_date DESC", conn)


STOP_PIVOT_BUFFER_PCT  = 0.003  # 0,3% Puffer über/unter dem strukturellen Pivot
                                  # (Welle-2/4-Tief bzw. Impulsende), gegen einzelne Kursdochte
STOP_TRAIL_TRIGGER_PCT = 0.02   # ab +2% Kursgewinn wird der Stop nachgezogen
STOP_TRAIL_LOCK_PCT    = 0.01   # ... einmalig auf +1% Gewinn (bleibt danach fix)
STOP_FIXED_PCT         = 0.05   # Stop-Regel 'fixed5': fester Stop 5% unter/über dem Einstieg

# Wählbare Stop-Regeln für paper_trades() — Vergleich im Tab 'Papier-Trades'.
# Die Signale und Einstiege (inkl. Limit-Füllung, die weiterhin am Wellen-Pivot
# invalidiert) sind bei allen Regeln gleich, nur die Positionsführung ändert sich.
STOP_RULES = {
    "trail":  "A: Wellen-Pivot + Nachziehen auf +1% (ab +2%)",
    "pivot":  "B: Wellen-Pivot, kein Nachziehen",
    "fixed5": "C: Fester Stop -5%, kein Nachziehen",
}
DEFAULT_STOP_RULE = "trail"

# Wie lange auf die Füllung der Retracement-Limit-Order gewartet wird (siehe
# WAVE3_ENTRY_RETRACE_PCT in elliott_wave.py), bevor das Setup als "nicht
# gefüllt" verworfen wird. Bewusst kürzer als MAX_HORIZON_DAYS (120) — ein
# Pullback, der erst nach vier Handelswochen kommt, ist nicht mehr derselbe
# Trade wie der ursprünglich erkannte 0-1-2-Ausbruch.
WAVE3_ENTRY_MAX_WAIT_DAYS = 20

# Bewusst ein fester Kalenderwert, kein datetime.today() — die Papier-Trade-
# Statistik soll nur Signale ab dem Tag zählen, an dem das Feature eingeführt
# wurde (keine rückwirkende Bewertung historischer wave_predictions). Würde
# hier "heute" dynamisch berechnet, würde sich der Cutoff mit jedem Tag
# weiterschieben statt fix zu bleiben.
PAPER_TRADES_START_DATE = "2026-08-03"

# Confidence-Schwelle der Versionen v1/v2 (siehe model_versions.py). Die
# aktive Version v3 nutzt sie nicht mehr — sie hatte über 10 Jahre keinen
# messbaren Effekt.
PAPER_TRADES_MIN_CONFIDENCE = 0.70

# Welche Signale gehandelt werden, legt die Modellversion fest (model_versions.py).

# Default-Rückblickfenster für "aktuell durch Insider gehandelt" (Congress/
# Senate/House, ARK, Trump-Familie) — Ticker-Universum für den separaten Tab
# 'Insider-Papier-Trades' und dessen headless-Äquivalent scan_insider_wave_signals().
INSIDER_LOOKBACK_DAYS = 90

EXIT_REASON_LABELS = {
    "target":       "🎯 Zielzone erreicht",
    "stop_initial": "🛑 Stop-Loss (Welle-2/4-Tief)",
    "stop_trail":   "🔒 Trailing-Stop (+1%)",
    "stop_fixed":   "🛑 Stop-Loss (fest -5%)",
    "expired":      "⌛ Prüfhorizont abgelaufen",
    "not_filled":   "🚫 Nicht gefüllt (Kurs kam nicht zur Retracement-Zone zurück)",
}


def _structural_stop(last_pivot_price: float, long_side: bool) -> float:
    """Stop-Loss-Niveau: der stützende Pivot der erkannten Wellenstruktur
    (Welle-2-Tief bei 'Welle 3 in Bildung', Welle-4-Tief bei 'Welle 5 in
    Bildung', Impulsende bei 'Korrektur nach Impuls') mit kleinem Puffer
    gegen einzelne Dochte — statt eines pauschalen Prozentwerts. Fällt der
    Kurs unter diesen Pivot, ist die Wellenzählung, auf der die Erwartung
    beruht, ohnehin hinfällig (dieselbe Logik wie 'invalidated' in
    evaluate_open_predictions())."""
    return (last_pivot_price * (1 - STOP_PIVOT_BUFFER_PCT) if long_side
            else last_pivot_price * (1 + STOP_PIVOT_BUFFER_PCT))


def _simulate_stop_loss(
    conn: sqlite3.Connection, ticker: str, entry_date: str, entry_price: float,
    long_side: bool, target_low: float | None, target_high: float | None,
    stop_price: float, trail: bool = True, stop_reason: str = "stop_initial",
) -> tuple[str | None, float | None, str | None]:
    """Simuliert Tag für Tag die Positionsführung einer einzelnen Papier-Order:
    Stop-Loss auf dem übergebenen strukturellen Niveau (siehe
    _structural_stop); sobald der Kurs +2% Gewinn erreicht, wird der Stop
    einmalig auf +1% Gewinn nachgezogen (nie jedoch weiter weg als der
    strukturelle Stop) und bleibt dort dauerhaft fix — ein späterer
    Rücksetzer und erneuter Anstieg zieht ihn NICHT erneut nach (kein
    fortlaufendes Trailing über +1% hinaus). Alternativer Exit: die vom
    Elliott-Wellen-Modul berechnete Zielzone wird erreicht. Nach
    MAX_HORIZON_DAYS ohne Auslösung gilt die Order als abgelaufen (Exit zum
    letzten verfügbaren Schlusskurs).

    Pro Tag wird zuerst der zu Handelsbeginn gültige Stop geprüft (auf Basis
    von Low/High des Vortages nachgezogen) — ein Ratchet, der erst am selben
    Tag ausgelöst wurde, kann also frühestens am Folgetag zum Stop-Out führen.
    Das vermeidet Blick-in-die-Zukunft innerhalb eines einzelnen Handelstages,
    dessen Reihenfolge von Hoch/Tief wir aus OHLC-Daten nicht kennen.

    trail=False schaltet das Nachziehen ab (Stop bleibt auf stop_price);
    stop_reason ist der exit_reason, wenn dieser Stop auslöst.

    Gibt (exit_date, exit_price, exit_reason) zurück — alle drei None, wenn
    die Order mangels weiterer Kursdaten weiterhin offen ist."""
    prices = pd.read_sql(
        "SELECT date, high, low, close FROM stock_prices WHERE ticker=? AND date > ? ORDER BY date",
        conn, params=[ticker, entry_date],
    )
    if prices.empty:
        return None, None, None

    trailed = not trail
    entry_dt = datetime.strptime(entry_date, "%Y-%m-%d").date()

    for _, p in prices.iterrows():
        stop_hit = (p["low"] <= stop_price) if long_side else (p["high"] >= stop_price)
        if stop_hit:
            reason = "stop_trail" if trailed and trail else stop_reason
            return p["date"], float(stop_price), reason

        if target_low is not None:
            zone_hit = p["low"] <= target_high and p["high"] >= target_low
            if zone_hit:
                return p["date"], float(p["close"]), "target"

        if not trailed:
            trigger_price = (
                entry_price * (1 + STOP_TRAIL_TRIGGER_PCT) if long_side
                else entry_price * (1 - STOP_TRAIL_TRIGGER_PCT)
            )
            touched = (p["high"] >= trigger_price) if long_side else (p["low"] <= trigger_price)
            if touched:
                trail_stop = (
                    entry_price * (1 + STOP_TRAIL_LOCK_PCT) if long_side
                    else entry_price * (1 - STOP_TRAIL_LOCK_PCT)
                )
                # Nachziehen nur, wenn das enger ist als der strukturelle
                # Stop — der Trailing-Stop darf ihn nie nach außen verschieben.
                stop_price = max(stop_price, trail_stop) if long_side else min(stop_price, trail_stop)
                trailed = True

        if (pd.Timestamp(p["date"]).date() - entry_dt).days > MAX_HORIZON_DAYS:
            return p["date"], float(p["close"]), "expired"

    return None, None, None


def _simulate_limit_fill(
    conn: sqlite3.Connection, ticker: str, signal_date: str, limit_price: float,
    long_side: bool, stop_price: float,
) -> tuple[str | None, float | None, str | None]:
    """Simuliert Tag für Tag, ob die Retracement-Limit-Order (Einstieg in die
    2-3-Bewegung bei 'Welle 3 in Bildung', siehe WAVE3_ENTRY_RETRACE_PCT in
    elliott_wave.py) ausgeführt wird — bevor entweder der strukturelle Stop
    verletzt wird (die Wellenzählung wäre dann ohnehin hinfällig, eine
    verspätete Füllung läge direkt unter dem eigenen Stop) oder
    WAVE3_ENTRY_MAX_WAIT_DAYS ohne Füllung verstreichen.

    Anders als bei einer bereits offenen Position (_simulate_stop_loss nutzt
    dort bewusst Intraday-Low/High) wird die Invalidierung hier über den
    Schlusskurs geprüft: ein einzelner Docht unter den Pivot muss die
    Wellenzählung nicht kippen, ein Schlusskurs dort schon eher — die Order
    wartet also etwas geduldiger, bevor sie ganz verworfen wird.

    Gibt (fill_date, fill_price, status) zurück. status ist 'filled',
    'invalidated', 'expired' oder None (noch keine Entscheidung möglich,
    weil seit dem Signal schlicht noch keine/zu wenig Kursdaten vorliegen)."""
    prices = pd.read_sql(
        "SELECT date, high, low, close FROM stock_prices WHERE ticker=? AND date > ? ORDER BY date",
        conn, params=[ticker, signal_date],
    )
    if prices.empty:
        return None, None, None

    signal_dt = datetime.strptime(signal_date, "%Y-%m-%d").date()

    for _, p in prices.iterrows():
        filled = (p["low"] <= limit_price) if long_side else (p["high"] >= limit_price)
        if filled:
            return p["date"], float(limit_price), "filled"

        invalidated = (p["close"] < stop_price) if long_side else (p["close"] > stop_price)
        if invalidated:
            return None, None, "invalidated"

        if (pd.Timestamp(p["date"]).date() - signal_dt).days > WAVE3_ENTRY_MAX_WAIT_DAYS:
            return None, None, "expired"

    return None, None, None


def paper_trades(
    conn: sqlite3.Connection, stake_eur: float = 100.0, version: str = ACTIVE_VERSION,
    stop_rule: str = DEFAULT_STOP_RULE,
) -> pd.DataFrame:
    """Leitet aus wave_predictions eine Papier-Trade-Historie ab: pro erkanntem
    Long-/Short-Einstieg (bullish/bearish) mit berechneter Zielzone eine
    virtuelle Order zu stake_eur, mit automatischer Stop-Loss-/Trailing-Stop-
    Positionsführung (Stop am stützenden Wellen-Pivot, siehe _structural_stop
    und _simulate_stop_loss). Bei 'Welle 3 in Bildung' mit gültigem
    Retracement-Level (entry_limit_price, siehe WAVE3_ENTRY_RETRACE_PCT) wird
    statt eines Sofort-Einstiegs eine Limit-Order simuliert (_simulate_limit_fill)
    — kommt der Kurs nie zurück, entsteht kein Trade, sondern ein Eintrag mit
    exit_reason='not_filled' (kein P&L, zählt separat in paper_trade_stats()).
    Alle anderen Zustände steigen weiterhin sofort zum aktuellen Kurs ein.

    Diese Simulation läuft unabhängig vom status-Feld in wave_predictions
    (das bewertet weiterhin nur die Trefferquote des reinen Wellen-Modells
    im Tab "Vorhersagen"); hier geht es um das Handelsergebnis der Order
    inklusive Risikomanagement. Noch offene Trades (weder Stop noch Zielzone
    noch Horizont ausgelöst) werden zusätzlich unrealisiert mit dem letzten
    verfügbaren Schlusskurs bewertet, sofern vorhanden.

    Reine Statistik-Buchführung — kein echtes Orderrouting, keine Gebühren
    oder Slippage. Zustände ohne Zielzone (z.B. "möglicher neuer Impuls")
    werden ausgeschlossen, da dort keine Elliott-Wellen-Verkaufszone berechnet
    wird und somit kein Zielzonen-Exit-Kriterium existiert. Erfasst werden nur
    Signale ab PAPER_TRADES_START_DATE — ältere, bereits vor Einführung dieses
    Features erkannte wave_predictions fließen bewusst nicht in die Statistik
    ein. Welche Signale gehandelt werden, bestimmt `version` (Regeln in
    model_versions.py, Standard: ACTIVE_VERSION) — so lassen sich die
    Handelsergebnisse verschiedener Versionen auf denselben Vorhersagen
    vergleichen. `stop_rule` wählt die Positionsführung (siehe STOP_RULES)."""
    df = pd.read_sql(
        """SELECT * FROM wave_predictions
           WHERE target_low IS NOT NULL AND created_date >= ?
           ORDER BY created_date DESC""",
        conn, params=[PAPER_TRADES_START_DATE],
    )
    df = df[accept_mask(df, version)] if not df.empty else df
    if df.empty:
        return df
    factors = stake_weights(df, version)

    latest_close = pd.read_sql(
        """SELECT ticker, close FROM stock_prices sp
           WHERE date = (SELECT MAX(date) FROM stock_prices WHERE ticker = sp.ticker)""",
        conn,
    ).set_index("ticker")["close"].to_dict()

    rows = []
    for idx, r in df.iterrows():
        stake       = stake_eur * float(factors[idx])
        long_side   = r["direction"] == "bullish"
        stop_price  = _structural_stop(float(r["last_pivot_price"]), long_side)
        limit_price = r["entry_limit_price"]
        has_limit   = pd.notna(limit_price)

        base_row = {
            "ticker":         r["ticker"],
            "direction":      "Long" if long_side else "Short",
            "state":          r["state"],
            "order_date":     r["created_date"],
            "limit_price":    float(limit_price) if has_limit else None,
            "stake_eur":      stake,
            "sell_zone_low":  r["target_low"],
            "sell_zone_high": r["target_high"],
            "confidence":     r["confidence"],
        }

        if has_limit:
            fill_date, fill_price, fill_status = _simulate_limit_fill(
                conn, r["ticker"], r["created_date"], float(limit_price), long_side, stop_price,
            )
            if fill_status in ("invalidated", "expired"):
                rows.append({**base_row, "entry_price": None, "shares": None, "filled": False,
                             "exit_reason": "not_filled", "realized": True,
                             "exit_date": None, "exit_price": None, "pnl_pct": None, "pnl_eur": None})
                continue
            if fill_status is None:
                rows.append({**base_row, "entry_price": None, "shares": None, "filled": False,
                             "exit_reason": None, "realized": False,
                             "exit_date": None, "exit_price": None, "pnl_pct": None, "pnl_eur": None})
                continue
            entry_date, entry = fill_date, fill_price
        else:
            entry_date, entry = r["created_date"], float(r["price_at_prediction"])

        if stop_rule == "fixed5":
            pos_stop = entry * (1 - STOP_FIXED_PCT) if long_side else entry * (1 + STOP_FIXED_PCT)
            stop_reason = "stop_fixed"
        else:
            pos_stop, stop_reason = stop_price, "stop_initial"
        exit_date, exit_price, exit_reason = _simulate_stop_loss(
            conn, r["ticker"], entry_date, entry, long_side,
            r["target_low"], r["target_high"], pos_stop,
            trail=stop_rule == "trail", stop_reason=stop_reason,
        )
        realized = exit_reason is not None
        if not realized:
            exit_price = latest_close.get(r["ticker"])

        pnl_pct = pnl_eur = None
        if exit_price is not None and entry:
            move = (float(exit_price) - entry) / entry
            pnl_pct = move if long_side else -move
            pnl_eur = stake * pnl_pct

        rows.append({
            **base_row,
            "entry_price": entry,
            "shares":      stake / entry if entry else None,
            "filled":      True,
            "exit_reason": exit_reason,
            "realized":    realized,
            "exit_date":   exit_date,
            "exit_price":  exit_price,
            "pnl_pct":     pnl_pct,
            "pnl_eur":     pnl_eur,
        })
    return pd.DataFrame(rows)


def paper_trade_stats(df: pd.DataFrame) -> dict:
    """Kennzahlen über eine paper_trades()-Historie. 'win_rate' und die
    P&L-Mittelwerte beziehen sich nur auf bereits realisierte (geschlossene)
    Trades — offene Positionen zählen separat und unrealisiert nicht mit ein,
    damit ein einzelner noch laufender Ausreißer die Quote nicht verzerrt.
    'not_filled' (nur bei Retracement-Limit-Einstiegen möglich, siehe
    _simulate_limit_fill) zählt weder als offen noch als geschlossen — es kam
    schlicht nie zu einer Position, also auch zu keinem P&L."""
    if df.empty:
        return {"total": 0, "open": 0, "closed": 0, "not_filled": 0, "wins": 0, "losses": 0,
                "win_rate": None, "total_pnl_eur": 0.0, "avg_pnl_eur": None, "avg_pnl_pct": None,
                "total_stake_eur": 0.0, "pnl_per_eur": None}

    not_filled = df[df["exit_reason"] == "not_filled"]
    closed     = df[df["realized"] & df["pnl_eur"].notna()]
    return {
        "total":         len(df),
        "open":          int((~df["realized"]).sum()),
        "closed":        len(closed),
        "not_filled":    len(not_filled),
        "wins":          int((closed["pnl_eur"] > 0).sum()),
        "losses":        int((closed["pnl_eur"] <= 0).sum()),
        "win_rate":      (closed["pnl_eur"] > 0).mean() if len(closed) else None,
        "total_pnl_eur": closed["pnl_eur"].sum() if len(closed) else 0.0,
        "avg_pnl_eur":   closed["pnl_eur"].mean() if len(closed) else None,
        "avg_pnl_pct":   closed["pnl_pct"].mean() if len(closed) else None,
        # Gewinn je eingesetztem Euro — vergleichbar auch bei ungleichen Einsätzen
        "total_stake_eur": closed["stake_eur"].sum() if len(closed) else 0.0,
        "pnl_per_eur":   closed["pnl_eur"].sum() / closed["stake_eur"].sum() if len(closed) else None,
    }


def honest_decided(df: pd.DataFrame) -> pd.DataFrame:
    """Entschiedene Vorhersagen (hit/invalidated), die für die Trefferquote
    zählen: nur mit Zielzone (ohne kann es keinen 'hit' geben, z.B. 'möglicher
    neuer Impuls') und ohne die, bei denen der Kurs schon bei Erstellung in
    der Zielzone stand (sofortiger 'Treffer' ohne Vorhersageleistung)."""
    decided = df[df["status"].isin(["hit", "invalidated"])]
    if "target_low" in decided.columns:
        decided = decided[decided["target_low"].notna()]
    if "in_zone_at_creation" in decided.columns:
        decided = decided[decided["in_zone_at_creation"].fillna(0) != 1]
    return decided


def accuracy_stats(df: pd.DataFrame) -> dict:
    """Kennzahlen über alle aufgezeichneten Vorhersagen. 'hit_rate' bezieht
    sich nur auf klar entschiedene Fälle (hit/invalidated) ohne Start in der
    Zielzone (siehe honest_decided) — 'expired' zählt als nicht eindeutig
    entschieden. 'baseline' ist die mittlere Zufalls-Basis derselben Fälle
    (siehe barrier_baseline), 'edge' = hit_rate - baseline: nur ein
    positiver Edge zeigt echte Vorhersagekraft."""
    decided = honest_decided(df)
    hit_rate = (decided["status"] == "hit").mean() if len(decided) else None
    baseline = None
    if len(decided) and "baseline_prob" in decided.columns and decided["baseline_prob"].notna().any():
        baseline = float(decided["baseline_prob"].mean())
    return {
        "total":       len(df),
        "open":        int((df["status"] == "open").sum()),
        "hit":         int((df["status"] == "hit").sum()),
        "invalidated": int((df["status"] == "invalidated").sum()),
        "expired":     int((df["status"] == "expired").sum()),
        "in_zone":     int((df.get("in_zone_at_creation", pd.Series(dtype=float)) == 1).sum()),
        "decided":     len(decided),
        "hit_rate":    hit_rate,
        "baseline":    baseline,
        "edge":        (hit_rate - baseline) if hit_rate is not None and baseline is not None else None,
    }


def target_overshoot(conn: sqlite3.Connection) -> pd.DataFrame:
    """Für jede als 'hit' aufgelöste Vorhersage: wie weit läuft der Kurs nach
    dem ersten Berühren der Zielzone noch in die erwartete Richtung weiter,
    bevor er umkehrt — beantwortet die Frage, ob ein Exit sofort beim ersten
    Kontakt (aktuelle Regel in _simulate_stop_loss) oder ein längeres Halten
    historisch besser abgeschnitten hätte.

    Ab dem Tag NACH dem Treffertag wird Tag für Tag der Schlusskurs geprüft;
    fällt er wieder hinter die näher gelegene (zuerst berührte) Zonengrenze
    zurück, gilt das als Umkehrsignal ('reversed'). Der Treffertag selbst
    zählt nicht als Umkehr, da sein Schlusskurs beim Hit-Test schon innerhalb
    der Zone liegen darf, ohne dass das eine Trendwende bedeutet. Ohne Umkehr
    bis MAX_HORIZON_DAYS (ab Ersterkennung, konsistent mit den übrigen
    Auswertungen dieses Moduls) gilt der letzte verfügbare Schlusskurs als
    Halte-Exit.

    'overshoot_pct' misst die weiteste in der Haltephase erreichte Kursmarke
    jenseits der fernen Zonengrenze, normiert auf die Zonenbreite
    (target_high - target_low) — z.B. 30 bedeutet: der Kurs lief nach dem
    Treffer noch um 30% der Zonenbreite über die Zielzone hinaus, bevor er
    umkehrte. 'pnl_diff_pct' vergleicht das Ergebnis von Sofort-Exit
    (Schlusskurs des Treffertags) gegen Halten-bis-Umkehr/Horizont in
    Prozentpunkten der Kursbewegung — negativ heißt: Halten wäre schlechter
    gewesen als der Sofort-Exit."""
    hits = pd.read_sql(
        "SELECT * FROM wave_predictions WHERE status='hit'", conn,
    )
    if hits.empty:
        return hits

    rows = []
    for _, r in hits.iterrows():
        bullish = r["direction"] == "bullish"
        near_edge = r["target_low"] if bullish else r["target_high"]
        far_edge  = r["target_high"] if bullish else r["target_low"]
        zone_width = r["target_high"] - r["target_low"]
        if zone_width <= 0:
            continue

        prices = pd.read_sql(
            "SELECT date, high, low, close FROM stock_prices WHERE ticker=? AND date>=? ORDER BY date",
            conn, params=[r["ticker"], r["resolved_date"]],
        )
        if prices.empty:
            continue

        created = datetime.strptime(r["created_date"], "%Y-%m-%d").date()
        extreme = None
        reversal_date = reversal_close = None
        last_close = None

        for _, p in prices.iterrows():
            last_close = float(p["close"])
            if bullish:
                extreme = p["high"] if extreme is None else max(extreme, p["high"])
            else:
                extreme = p["low"] if extreme is None else min(extreme, p["low"])
            if p["date"] > r["resolved_date"]:
                turned_back = (p["close"] < near_edge) if bullish else (p["close"] > near_edge)
                if turned_back:
                    reversal_date, reversal_close = p["date"], float(p["close"])
                    break
            if (pd.Timestamp(p["date"]).date() - created).days > MAX_HORIZON_DAYS:
                break

        hold_exit = reversal_close if reversal_date else last_close
        overshoot_abs = (extreme - far_edge) if bullish else (far_edge - extreme)
        overshoot_pct = max(overshoot_abs, 0) / zone_width * 100

        entry = float(r["price_at_prediction"])
        immediate_pnl_pct = (
            (float(r["resolved_price"]) - entry) / entry if bullish
            else (entry - float(r["resolved_price"])) / entry
        ) * 100
        hold_pnl_pct = (
            (hold_exit - entry) / entry if bullish else (entry - hold_exit) / entry
        ) * 100

        rows.append({
            "ticker":            r["ticker"],
            "direction":         "Long" if bullish else "Short",
            "state":             r["state"],
            "created_date":      r["created_date"],
            "resolved_date":     r["resolved_date"],
            "target_low":        r["target_low"],
            "target_high":       r["target_high"],
            "overshoot_pct":     overshoot_pct,
            "reversed":          reversal_date is not None,
            "reversal_date":     reversal_date,
            "immediate_pnl_pct": immediate_pnl_pct,
            "hold_pnl_pct":      hold_pnl_pct,
            "pnl_diff_pct":      hold_pnl_pct - immediate_pnl_pct,
        })

    return pd.DataFrame(rows)


OVERSHOOT_THRESHOLDS_PCT = [0, 10, 20, 30, 40, 50, 75, 100, 150]


def overshoot_coverage(df: pd.DataFrame) -> pd.DataFrame:
    """Für eine Reihe von Schwellenwerten (in % der Zonenbreite): wie oft lief
    der Kurs nach dem Zielzonen-Treffer noch mindestens so weit weiter, bevor
    er umkehrte. Das ist die direkte Antwort auf 'bis zu welcher zusätzlichen
    Distanz jenseits der Zielzone ist die Chance auf noch mehr Bewegung noch
    hoch' — die Quote fällt erwartungsgemäß mit steigendem Schwellenwert."""
    if df.empty:
        return pd.DataFrame(columns=["threshold_pct", "share", "count"])
    return pd.DataFrame([
        {
            "threshold_pct": t,
            "share": (df["overshoot_pct"] >= t).mean(),
            "count": int((df["overshoot_pct"] >= t).sum()),
        }
        for t in OVERSHOOT_THRESHOLDS_PCT
    ])


def overshoot_hold_stats(df: pd.DataFrame) -> dict:
    """Vergleicht Sofort-Exit beim ersten Zielzonen-Kontakt gegen Halten bis
    zur Umkehr/zum Horizont. 'share_hold_better' ist der Anteil der Fälle, in
    denen Halten netto mehr Rendite gebracht hätte als der Sofort-Exit."""
    if df.empty:
        return {"n": 0, "avg_diff_pct": None, "median_diff_pct": None,
                "share_hold_better": None, "share_hold_worse": None,
                "avg_immediate_pnl_pct": None, "avg_hold_pnl_pct": None}
    return {
        "n":                     len(df),
        "avg_diff_pct":          df["pnl_diff_pct"].mean(),
        "median_diff_pct":       df["pnl_diff_pct"].median(),
        "share_hold_better":     (df["pnl_diff_pct"] > 0).mean(),
        "share_hold_worse":      (df["pnl_diff_pct"] < 0).mean(),
        "avg_immediate_pnl_pct": df["immediate_pnl_pct"].mean(),
        "avg_hold_pnl_pct":      df["hold_pnl_pct"].mean(),
    }
