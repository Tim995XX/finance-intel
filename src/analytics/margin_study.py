"""
Mehr Spanne je Trade: zwei Änderungen an der aktiven Version, einzeln und
kombiniert gegen die heutige Regel (TP = Zielzone, SL = Pivot) gerechnet.

  Idee 1 — Mindest-Kursziel: nur Signale, deren Abstand zur Zielzone
           (d_target, zum Signalzeitpunkt bekannt) mindestens X beträgt.
           Gleiche Kosten je Trade, größere mögliche Bewegung.
  Idee 4 — Trailing Stop statt festem Ziel: Gewinner laufen lassen, der Stop
           wird unter dem höchsten Kurs seit Einstieg nachgezogen. Drei
           Varianten: ab Einstieg ohne Pivot-SL, ab Einstieg mit Pivot-SL,
           erst nach Erreichen der Zielzone (vorher Pivot-SL).

Simulation wie exit_rules.simulate (Einstieg Folgetag-Eröffnung, max. 120
Tage, Gebühren Neobroker, 1.000 € je Trade, v4-Gewichtung).

Vorab festgelegte Entscheidungsregel (2026-10-19, vor Ansicht der Ergebnisse):
  Kennzahl für die Auswahl ist der Vorsprung je Kapitaltag (Rendite minus
  gleichgewichtetes Universum über dieselbe Haltedauer, je Euro und Tag
  gebunden, ×365) — so kann keine Variante nur durch längeres Halten gewinnen.
  1. Parameter je Idee werden NUR auf der Watchlist bis TRAIN_END_YEAR
     gewählt (Idee 1 nur Schwellen mit mind. MIN_TRAIN_TRADES Trades).
  2. Kombination = beste Schwelle aus 1 + bester Trailing Stop aus 4,
     ohne erneute Auswahl.
  3. Eine Änderung gilt als bestanden, wenn sie auf dem Test-Universum ab
     TRAIN_END_YEAR + 1 (a) mehr Vorsprung je Kapitaltag hat als heute,
     (b) mehr netto je Trade (die gewünschte Spanne) und (c) in der Mehrheit
     dieser Jahre mehr Vorsprung je Kapitaltag.

Aufruf: python main.py margin
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from src.analytics.exit_rules import (
    ExitRule, CURRENT_RULE, simulate, grid_metrics, load_signals, _price_frame,
)
from src.analytics.model_versions import TRAIN_END_YEAR, FeeModel, DEFAULT_FEES, stake_weights, ACTIVE_VERSION

MIN_TARGETS = (0.0, 0.03, 0.05, 0.08, 0.12, 0.15, 0.20)
TRAILS = (0.05, 0.08, 0.10, 0.15, 0.20)
MIN_TRAIN_TRADES = 1000

CURRENT = ExitRule(CURRENT_RULE, "TP Zielzone · SL Pivot (heute)", "struct", 1.0, "struct", 1.0)
_MODES = {
    "entry":     ("Trailing {t} ab Einstieg", None, None, "entry"),
    "pivot":     ("Trailing {t} ab Einstieg + SL Pivot", "struct", 1.0, "entry"),
    "after_tp":  ("SL Pivot, ab Zielzone Trailing {t}", "struct", 1.0, "after_tp"),
}
TRAIL_RULES = [
    ExitRule(f"trail_{m}_{t:g}", lbl.format(t=f"{t*100:g} %"),
             "struct" if mode == "after_tp" else None, 1.0 if mode == "after_tp" else None,
             sk, sv, t, mode)
    for m, (lbl, sk, sv, mode) in _MODES.items() for t in TRAILS
]
RULES = [CURRENT] + TRAIL_RULES


def _metrics(sim, mask, w, rule, fees, stake):
    rules = [r for r in RULES if r.key == rule]
    gm = grid_metrics(sim[mask], w, fees, stake, rules)
    return gm.iloc[0].to_dict() if len(gm) else None


def _yearly_excess(sim, years, mask, w, rule, fees, stake) -> pd.Series:
    out = {}
    for y in sorted(years[mask].unique()):
        m = _metrics(sim, mask & (years == y), w, rule, fees, stake)
        out[y] = None if m is None else m["excess_per_year"]
    return pd.Series(out, dtype=float)


def run_margin_study(conn: sqlite3.Connection, fees: FeeModel = DEFAULT_FEES,
                     stake_eur: float = 1000.0, key: str = ACTIVE_VERSION) -> dict:
    data = {}
    for uni in ("watchlist", "test"):
        sig = load_signals(conn, uni, key)
        sim = simulate(sig, _price_frame(conn, uni), RULES)
        sig = sig.loc[sim.index]
        data[uni] = (sig, sim, stake_weights(sig, key), sig["created_date"].str[:4].astype(int))

    # 1. Auswahl auf Watchlist-Training
    sig, sim, w, yrs = data["watchlist"]
    train = (yrs <= TRAIN_END_YEAR).to_numpy()
    d_tgt = sig["d_target"].to_numpy()
    sel_target = []
    for thr in MIN_TARGETS:
        m = train & (d_tgt >= thr)
        res = _metrics(sim, m, w, CURRENT_RULE, fees, stake_eur)
        if res and res["trades"] >= MIN_TRAIN_TRADES:
            sel_target.append({"min_target": thr, **res})
    sel_target = pd.DataFrame(sel_target)
    best_thr = float(sel_target.loc[sel_target["excess_per_year"].idxmax(), "min_target"])

    sel_trail = grid_metrics(sim[train], w, fees, stake_eur, RULES)
    best_trail = sel_trail.loc[sel_trail["excess_per_year"].idxmax(), "rule"]

    variants = {
        "heute": (0.0, CURRENT_RULE),
        "Idee 1 (Mindest-Kursziel)": (best_thr, CURRENT_RULE),
        "Idee 4 (Trailing Stop)": (0.0, best_trail),
        "Idee 1 + 4": (best_thr, best_trail),
    }

    # 2. Auswertung Training (Watchlist) und Test (Test-Universum)
    rows, yearly = [], {}
    for period, uni in (("Training", "watchlist"), ("Test", "test")):
        sig, sim, w, yrs = data[uni]
        pm = ((yrs <= TRAIN_END_YEAR) if period == "Training" else (yrs > TRAIN_END_YEAR)).to_numpy()
        for name, (thr, rule) in variants.items():
            m = pm & (sig["d_target"].to_numpy() >= thr)
            res = _metrics(sim, m, w, rule, fees, stake_eur)
            rows.append({"variant": name, "period": period, "min_target": thr, "rule": rule, **res})
            if period == "Test":
                yearly[name] = _yearly_excess(sim, yrs, m, w, rule, fees, stake_eur)
    table = pd.DataFrame(rows)
    yearly = pd.DataFrame(yearly)

    # 3. Entscheidung
    test = table[table.period == "Test"].set_index("variant")
    base = test.loc["heute"]
    decisions = {}
    for name in list(variants)[1:]:
        t = test.loc[name]
        yrs_better = int((yearly[name] > yearly["heute"]).sum())
        decisions[name] = {
            "excess_ok": t["excess_per_year"] > base["excess_per_year"],
            "net_ok": t["net"] > base["net"],
            "years_better": yrs_better, "n_years": len(yearly),
            "passed": bool(t["excess_per_year"] > base["excess_per_year"] and t["net"] > base["net"]
                           and yrs_better > len(yearly) / 2),
        }
    return {"best_threshold": best_thr, "best_trail": best_trail,
            "best_trail_label": next(r.label for r in RULES if r.key == best_trail),
            "select_target": sel_target, "select_trail": sel_trail,
            "table": table, "yearly": yearly, "decisions": decisions}
