"""
Rastersuche über Take-Profit (TP) und Stop-Loss (SL) für die Long-Signale
der Rückrechnung (replay_signals) — mit echten Tageskursen und Ordergebühren.

Simulation je Signal (realistischer als die Trefferquote in replay_signals):
  - Einstieg zum Eröffnungskurs des Handelstages NACH dem Signal (der Scan
    läuft nach Börsenschluss; zum Signal-Schlusskurs kann man nicht kaufen).
  - TP/SL als Kursniveau, festgelegt beim Signal, entweder strukturell
    (Vielfaches des Abstands zur Zielzone bzw. zum Wellen-Pivot) oder als
    fester Prozentsatz vom Einstieg.
  - Tag für Tag: SL greift, wenn das Tagestief ihn erreicht (Ausführung zum
    SL oder zum tieferen Eröffnungskurs bei Kurslücke), TP, wenn das Tageshoch
    ihn erreicht (Ausführung zum TP oder höheren Eröffnungskurs). Berühren
    beide denselben Tag, zählt der SL (konservativ — die Reihenfolge innerhalb
    des Tages ist aus OHLC-Daten unbekannt).
  - Ohne Auslösung: Verkauf zum Schlusskurs nach MAX_HOLD_DAYS Kalendertagen.
  - Gebühren über model_versions.FeeModel (je Order und Seite).

Vorab festgelegte Entscheidungsregel (2026-10-10, vor Ansicht der Ergebnisse):
  1. Die beste Kombination wird NUR auf der Watchlist bis TRAIN_END_YEAR
     gewählt (Kennzahl: Netto-Ergebnis je eingesetztem Euro).
  2. Sie wird übernommen, wenn sie auf dem Test-Universum im Zeitraum ab
     TRAIN_END_YEAR + 1 netto je Euro besser ist als die heutige Regel
     (TP = Zielzone, SL = Pivot) UND in der Mehrheit dieser Jahre besser.
  Gleiches Vorgehen für die Frage "nur Welle 3": Welle-3-Signale allein
  müssen auf dem Test-Universum ab TRAIN_END_YEAR + 1 netto je Euro besser
  sein als alle Zustände zusammen, und das in der Mehrheit der Jahre.

Aufruf: python main.py exits  (schreibt Tabelle exit_grid und gibt die
Entscheidung aus).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.analytics.model_versions import (
    TRAIN_END_YEAR, FeeModel, DEFAULT_FEES, accept_mask, stake_weights, ACTIVE_VERSION,
)

MAX_HOLD_DAYS = 120   # wie wave_tracker.MAX_HORIZON_DAYS


@dataclass(frozen=True)
class ExitRule:
    key: str
    label: str
    tp_kind: str | None   # "struct" (× Abstand Zielzone), "pct" (% vom Einstieg), None = kein TP
    tp: float | None
    sl_kind: str | None   # "struct" (× Abstand Pivot), "pct", None = kein SL
    sl: float | None
    # Nachgezogener Stop (Trailing Stop): trail = Abstand unter dem höchsten
    # Tageshoch seit Einstieg (bis Vortag). trail_mode "entry" = ab Einstieg
    # aktiv (ein TP beendet den Trade weiterhin), "after_tp" = der TP verkauft
    # nicht, sondern schaltet den Trailing Stop ab dem Folgetag scharf.
    # Ein fester SL gilt jeweils zusätzlich (der höhere Stop zählt).
    trail: float | None = None
    trail_mode: str | None = None


def _tp_label(kind, v):
    if kind is None:
        return "kein TP"
    return f"TP {v:g}× Zielzone" if kind == "struct" else f"TP +{v*100:g} %"


def _sl_label(kind, v):
    if kind is None:
        return "kein SL"
    return f"SL {v:g}× Pivot" if kind == "struct" else f"SL −{v*100:g} %"


TP_OPTIONS = [("struct", x) for x in (0.5, 0.75, 1.0, 1.5, 2.0, 3.0)] + \
             [("pct", x) for x in (0.03, 0.05, 0.08, 0.12, 0.20)] + [(None, None)]
SL_OPTIONS = [("struct", x) for x in (0.5, 0.75, 1.0, 1.5)] + \
             [("pct", x) for x in (0.03, 0.05, 0.08, 0.12)] + [(None, None)]

RULES: list[ExitRule] = [
    ExitRule(f"{tk or 'none'}{tv or ''}|{sk or 'none'}{sv or ''}",
             f"{_tp_label(tk, tv)} · {_sl_label(sk, sv)}", tk, tv, sk, sv)
    for tk, tv in TP_OPTIONS for sk, sv in SL_OPTIONS
]
CURRENT_RULE = "struct1.0|struct1.0"   # heutige Regel: Zielzone / Pivot


def _price_frame(conn: sqlite3.Connection, universe: str) -> pd.DataFrame:
    table = "test_prices" if universe == "test" else "stock_prices"
    return pd.read_sql(f"SELECT ticker, date, open, high, low, close FROM {table} ORDER BY ticker, date", conn)


def universe_index(prices: pd.DataFrame) -> pd.Series:
    """Gleichgewichteter Index aller Titel des Universums (Schlusskurse,
    Ø Tagesrendite) — Vergleichsmaßstab "irgendeine Aktie gleich lang halten"."""
    closes = prices.pivot(index="date", columns="ticker", values="close").sort_index()
    daily = closes.pct_change(fill_method=None).clip(-0.5, 1.0).mean(axis=1).fillna(0.0)
    return (1 + daily).cumprod()


def simulate(signals: pd.DataFrame, prices: pd.DataFrame,
             rules: list[ExitRule] = RULES) -> pd.DataFrame:
    """Brutto-Rendite je Signal und Regel. signals braucht ticker,
    created_date, price, target_low, last_pivot_price. Rückgabe: eine Zeile je
    Signal (Index wie signals) mit Spalten ret:<key>, days:<key>
    (Kalendertage gehalten) und bench:<key> (Rendite des gleichgewichteten
    Universums vom Signaltag bis zum Ausstiegstag); Signale ohne Folgetag fehlen."""
    idx_level = universe_index(prices)
    out = {}
    by_ticker = {t: g for t, g in prices.groupby("ticker")}
    for ticker, sig in signals.groupby("ticker"):
        g = by_ticker.get(ticker)
        if g is None:
            continue
        dates = g["date"].to_numpy()
        dt = pd.to_datetime(g["date"]).to_numpy()
        o, h, l, c = (g[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
        for idx, s in sig.iterrows():
            i = np.searchsorted(dates, s["created_date"], side="right")   # Folgetag
            if i >= len(dates) or not np.isfinite(o[i]) or o[i] <= 0:
                continue
            end = np.searchsorted(dt, dt[i] + np.timedelta64(MAX_HOLD_DAYS, "D"), side="right")
            if end <= i:
                continue
            entry = o[i]
            hh, ll, oo, cc = h[i:end], l[i:end], o[i:end], c[i:end]
            open_ended = end >= len(dates)   # Horizont noch nicht vorbei
            d_tgt = s["target_low"] - s["price"]
            d_piv = s["price"] - s["last_pivot_price"]
            row = {}
            for r in rules:
                tp = None if r.tp_kind is None else (
                    s["price"] + r.tp * d_tgt if r.tp_kind == "struct" else entry * (1 + r.tp))
                sl = None if r.sl_kind is None else (
                    s["price"] - r.sl * d_piv if r.sl_kind == "struct" else entry * (1 - r.sl))
                if tp is not None and tp <= entry:
                    tp = None       # schon beim Einstieg erreicht: kein sinnvoller TP
                if sl is not None and sl <= 0:
                    sl = None       # SL liegt bei/unter 0 — wirkt wie kein SL
                stops = np.full(len(ll), -np.inf if sl is None else sl)
                if r.trail is not None:
                    prev_max = np.maximum.accumulate(np.concatenate(([entry], hh[:-1])))
                    trail = prev_max * (1 - r.trail)
                    if r.trail_mode == "after_tp":
                        if tp is not None:
                            reached = np.maximum.accumulate(hh >= tp)
                            trail = np.where(np.concatenate(([False], reached[:-1])), trail, -np.inf)
                        tp = None   # TP verkauft nicht, er aktiviert nur
                    stops = np.maximum(stops, trail)
                hit_sl = ll <= stops
                k_sl = np.argmax(hit_sl) if hit_sl.any() else None
                k_tp = np.argmax(hh >= tp) if tp is not None and (hh >= tp).any() else None
                if k_sl is not None and (k_tp is None or k_sl <= k_tp):
                    px, k = min(stops[k_sl], oo[k_sl]), k_sl
                elif k_tp is not None:
                    px, k = max(tp, oo[k_tp]), k_tp
                elif open_ended:
                    continue        # noch offen — nicht werten
                else:
                    px, k = cc[-1], len(cc) - 1
                row[f"ret:{r.key}"] = px / entry - 1
                row[f"days:{r.key}"] = int((dt[i + k] - dt[i]) / np.timedelta64(1, "D")) + 1
                row[f"bench:{r.key}"] = idx_level.get(dates[i + k], np.nan) / \
                    idx_level.get(dates[i - 1], np.nan) - 1
            out[idx] = row
    return pd.DataFrame.from_dict(out, orient="index")


def grid_metrics(sim: pd.DataFrame, weights: pd.Series, fees: FeeModel = DEFAULT_FEES,
                 stake_eur: float = 100.0, rules: list[ExitRule] = RULES) -> pd.DataFrame:
    """Je Regel: Anzahl Trades, Trefferquote (Gewinn-Trades), Ø Haltedauer,
    Brutto/Netto-Ergebnis je eingesetztem Euro, Netto-Ergebnis je Kapitaltag
    (Rendite je Euro und Tag gebunden ×365 = grob annualisiert, falls das
    Kapital ständig investiert wäre) und Mindesteinsatz für Break-even.
    bench/excess: Rendite des gleichgewichteten Universums über dieselbe
    Haltedauer und der Vorsprung des Signals davor (brutto) — trennt
    Signalqualität vom allgemeinen Kursanstieg."""
    rows = []
    for r in rules:
        col = f"ret:{r.key}"
        if col not in sim.columns:
            continue
        ret = sim[col].dropna()
        if ret.empty:
            continue
        days = sim.loc[ret.index, f"days:{r.key}"]
        w = weights.reindex(ret.index).fillna(1.0)
        eur = w * stake_eur
        gross = float((w * ret).sum() / w.sum())
        cost = eur.map({e: fees.cost_fraction(e) * e for e in eur.unique()})
        net_eur = (eur * ret - cost).to_numpy()
        net = float(net_eur.sum() / eur.sum())
        cap_days = float((eur * days).sum())
        bench = sim.loc[ret.index, f"bench:{r.key}"]
        bench_w = float((w * bench).sum() / w[bench.notna()].sum()) if bench.notna().any() else None
        has_b = bench.notna()
        b_cap_days = float((eur[has_b] * days[has_b]).sum())
        rows.append({
            "rule": r.key, "label": r.label, "trades": len(ret),
            "win_rate": float((ret > 0).mean()), "avg_days": float(days.mean()),
            "gross": gross, "net": net, "net_pnl_eur": float(net_eur.sum()),
            "net_per_year": net_eur.sum() / cap_days * 365 if cap_days else None,
            "breakeven_stake": fees.breakeven_stake(gross),
            "bench": bench_w, "excess": None if bench_w is None else gross - bench_w,
            "bench_per_year": float((eur * bench).sum()) / cap_days * 365 if cap_days else None,
            # Vorsprung je Kapitaltag ×365: steigt nicht durch bloß längeres Halten
            "excess_per_year": float((eur[has_b] * (ret[has_b] - bench[has_b])).sum()) / b_cap_days * 365
                               if b_cap_days else None,
            "worst": float(ret.min()), "q05": float(ret.quantile(0.05)),
            "share_below_20": float((ret < -0.20).mean()),
        })
    return pd.DataFrame(rows)


def yearly_net(sim: pd.DataFrame, signals: pd.DataFrame, weights: pd.Series, rule_keys: list[str],
               fees: FeeModel = DEFAULT_FEES, stake_eur: float = 100.0) -> pd.DataFrame:
    year = signals.loc[sim.index, "created_date"].str[:4]
    rules = [r for r in RULES if r.key in rule_keys]
    out = {}
    for y, idx in sim.groupby(year).groups.items():
        gm = grid_metrics(sim.loc[idx], weights, fees, stake_eur, rules).set_index("rule")
        out[y] = gm["net"]
    return pd.DataFrame(out).T.sort_index()


def load_signals(conn: sqlite3.Connection, universe: str, key: str = ACTIVE_VERSION) -> pd.DataFrame:
    from src.analytics.model_replay import load_replay
    df = load_replay(conn, universe)
    return df[accept_mask(df, key)] if not df.empty else df


def run_exit_study(conn: sqlite3.Connection, fees: FeeModel = DEFAULT_FEES,
                   stake_eur: float = 1000.0, key: str = ACTIVE_VERSION) -> dict:
    """Komplette Studie inkl. Entscheidungsregel (siehe Modul-Docstring).
    Schreibt das Raster je Universum/Zeitraum/Zustand in die Tabelle exit_grid."""
    tables, res = [], {}
    sims = {}
    for uni in ("watchlist", "test"):
        sig = load_signals(conn, uni, key)
        sim = simulate(sig, _price_frame(conn, uni))
        sims[uni] = (sig, sim)
        w = stake_weights(sig, key)
        year = sig.loc[sim.index, "created_date"].str[:4].astype(int)
        for period, pmask in (("Training", year <= TRAIN_END_YEAR), ("Test", year > TRAIN_END_YEAR),
                              ("Gesamt", year > 0)):
            for state in ("alle", "wave3_forming", "wave5_forming", "correction_forming"):
                smask = pmask if state == "alle" else pmask & (sig.loc[sim.index, "state"] == state)
                gm = grid_metrics(sim[smask.to_numpy()], w, fees, stake_eur)
                gm["universe"], gm["period"], gm["state"] = uni, period, state
                tables.append(gm)
    grid = pd.concat(tables, ignore_index=True)
    grid["fees"] = fees.label
    grid["stake_eur"] = stake_eur
    grid["run_date"] = str(pd.Timestamp.now().date())
    grid.to_sql("exit_grid", conn, if_exists="replace", index=False)
    conn.commit()

    def sel(uni, period, state="alle"):
        return grid[(grid.universe == uni) & (grid.period == period) & (grid.state == state)].set_index("rule")

    # Regel 1: Auswahl nur auf Watchlist-Training
    train = sel("watchlist", "Training")
    best = train["net"].idxmax()
    test = sel("test", "Test")
    sig_t, sim_t = sims["test"]
    w_t = stake_weights(sig_t, key)
    yrs = sig_t.loc[sim_t.index, "created_date"].str[:4].astype(int)
    ty = yearly_net(sim_t[(yrs > TRAIN_END_YEAR).to_numpy()], sig_t, w_t, [best, CURRENT_RULE], fees, stake_eur)
    years_better = int((ty[best] > ty[CURRENT_RULE]).sum()) if best != CURRENT_RULE else 0
    res["exit"] = {
        "best": best, "best_label": train.loc[best, "label"],
        "train_best": train.loc[best].to_dict(), "train_current": train.loc[CURRENT_RULE].to_dict(),
        "test_best": test.loc[best].to_dict(), "test_current": test.loc[CURRENT_RULE].to_dict(),
        "yearly": ty, "years_better": years_better, "n_years": len(ty),
    }
    res["exit"]["passed"] = (best != CURRENT_RULE and test.loc[best, "net"] > test.loc[CURRENT_RULE, "net"]
                             and years_better > len(ty) / 2)

    # Nur Welle 3? (heutige Exit-Regel, Test-Universum ab TRAIN_END_YEAR+1)
    w3 = sel("test", "Test", "wave3_forming").loc[CURRENT_RULE]
    al = test.loc[CURRENT_RULE]
    t_idx = sim_t.index[(yrs > TRAIN_END_YEAR).to_numpy()]
    is_w3 = sig_t.loc[t_idx, "state"] == "wave3_forming"
    y3 = yearly_net(sim_t.loc[t_idx[is_w3.to_numpy()]], sig_t, w_t, [CURRENT_RULE], fees, stake_eur)[CURRENT_RULE]
    ya = yearly_net(sim_t.loc[t_idx], sig_t, w_t, [CURRENT_RULE], fees, stake_eur)[CURRENT_RULE]
    better = int((y3 > ya.reindex(y3.index)).sum())
    res["wave3"] = {"wave3": w3.to_dict(), "all": al.to_dict(), "years_better": better, "n_years": len(y3),
                    "passed": w3["net"] > al["net"] and better > len(y3) / 2}
    res["grid"] = grid
    return res
