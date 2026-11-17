"""
Versionierte Signal-Regeln für das Elliott-Wellen-Modell.

Die Wellenerkennung selbst (elliott_wave.analyze_current_wave) ist für alle
Versionen identisch — eine Version legt nur fest, welche der erkannten
Signale als Einstieg gelten. Dadurch lassen sich alle Versionen auf exakt
denselben Signalen messen und vergleichen:
  - Live: auf allen in wave_predictions protokollierten Vorhersagen
    (live_signal_frame), auch rückwirkend auf älteren Einträgen.
  - Rückrechnung: auf replay_signals (model_replay.py, ~10 Jahre).

Neue Version anlegen: Eintrag in VERSIONS anhängen (bestehende nie ändern,
sonst ist der Vergleich nicht mehr nachvollziehbar) und ACTIVE_VERSION
umstellen. Regeln dürfen nur Spalten nutzen, die zum Signalzeitpunkt bekannt
sind (state, direction, confidence, in_zone_at_creation, target_low sowie
die Merkmale aus signal_features.py).

Optional legt eine Version zusätzlich eine Einsatz-Gewichtung fest (stake:
Faktor je Signal, Standard 1 = 100 €). Verglichen wird dann neben dem
ungewichteten Ergebnis die Rendite je eingesetztem Euro.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from src.analytics.signal_features import from_json

# Rückrechnung: Regeln für v3 wurden nur auf Signalen bis einschließlich
# dieses Jahres ausgewählt; alles danach ist echter Test-Zeitraum.
TRAIN_END_YEAR = 2022


@dataclass(frozen=True)
class ModelVersion:
    key: str
    label: str
    introduced: str
    rule_text: str
    rationale: str
    rule: Callable[[pd.DataFrame], pd.Series]
    stake: Callable[[pd.DataFrame], pd.Series] | None = None
    stake_text: str = "100 € je Signal"


def _has_zone(df):
    return df["target_low"].notna()


def _not_in_zone(df):
    return df["in_zone_at_creation"].fillna(0) != 1


def _long(df):
    return df["direction"] == "bullish"


def market_bear(df: pd.DataFrame) -> pd.Series:
    """True, wenn der S&P 500 zum Signalzeitpunkt unter seiner 200-Tage-Linie
    lag (Merkmal mkt_above_sma200; aus features_json, falls die Spalte fehlt).
    Unbekannt (zu wenig Markthistorie) zählt nicht als Bärenmarkt."""
    if "mkt_above_sma200" in df.columns:
        flag = df["mkt_above_sma200"]
    elif "features_json" in df.columns:
        flag = df["features_json"].map(lambda s: from_json(s).get("mkt_above_sma200"))
    else:
        return pd.Series(False, index=df.index)
    return flag.map(lambda v: v is not None and not pd.isna(v) and not bool(v)).astype(bool)


BEAR_STAKE_FACTOR = 2.0


VERSIONS: list[ModelVersion] = [
    ModelVersion(
        key="v1", label="v1 · Original", introduced="2026-08-03",
        rule_text="Welle 3 in Bildung · Confidence > 0,70 · Long und Short",
        rationale="Ausgangsstand der Papier-Trades vor der Überarbeitung.",
        rule=lambda df: _has_zone(df) & (df["state"] == "wave3_forming") & (df["confidence"] > 0.70),
    ),
    ModelVersion(
        key="v2", label="v2 · Long-only", introduced="2026-10-01",
        rule_text="wie v1, aber nur Long und nicht schon in der Zielzone",
        rationale="Short-Signale lagen über 10 Jahre in 9 von 10 Jahren falsch; "
                  "Signale mit Kurs bereits in der Zielzone haben keine Aussagekraft.",
        rule=lambda df: _has_zone(df) & (df["state"] == "wave3_forming") & (df["confidence"] > 0.70)
                        & _long(df) & _not_in_zone(df),
    ),
    ModelVersion(
        key="v3", label="v3 · Evidenzbasiert", introduced="2026-10-01",
        rule_text="Long · nicht schon in der Zielzone · alle Zustände mit Zielzone "
                  "(Welle 3, Welle 5, Korrektur) · kein Confidence-Filter",
        rationale="Auf 2016–2022 ausgewählt: Confidence-Filter ohne Effekt, alle Long-"
                  "Zustände mit positivem Edge (Korrektur am stärksten); Trend-, Momentum- "
                  "und Markt-Filter ohne stabile Verbesserung, Shorts auch im Bärenmarkt negativ.",
        rule=lambda df: _has_zone(df) & _long(df) & _not_in_zone(df),
    ),
    ModelVersion(
        key="v4", label="v4 · Bärenmarkt-Gewichtung", introduced="2026-10-02",
        rule_text="Signale wie v3 · doppelter Einsatz (200 €), wenn der S&P 500 "
                  "unter seiner 200-Tage-Linie liegt",
        rationale="Long-Signale hatten 2018–2026 in 9 von 10 Bärenphasen einen positiven Edge "
                  "(meist +9 bis +19 pp). Auf dem unberührten Test-Universum (452 Aktien, "
                  "2026-10-02) bestätigt: Edge positiv in 4 von 4 Bärenphasen, Ergebnis je "
                  "Euro +0,83 % statt +0,75 % (v3).",
        rule=lambda df: _has_zone(df) & _long(df) & _not_in_zone(df),
        stake=lambda df: pd.Series(np.where(market_bear(df), BEAR_STAKE_FACTOR, 1.0), index=df.index),
        stake_text="100 € je Signal, 200 € im Bärenmarkt (S&P 500 < SMA200)",
    ),
]
VERSION_BY_KEY = {v.key: v for v in VERSIONS}
ACTIVE_VERSION = "v4"


def accept_mask(df: pd.DataFrame, key: str) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=bool)
    return VERSION_BY_KEY[key].rule(df).fillna(False).astype(bool)


def accepts(signal: dict, key: str = ACTIVE_VERSION) -> bool:
    """Einzelnes Signal (Spalten wie in wave_predictions) gegen eine Version prüfen."""
    return bool(accept_mask(pd.DataFrame([signal]), key).iloc[0])


def stake_weights(df: pd.DataFrame, key: str) -> pd.Series:
    """Einsatz-Faktor je Signal (1 = Standardeinsatz) der Version `key`."""
    v = VERSION_BY_KEY[key]
    if df.empty or v.stake is None:
        return pd.Series(1.0, index=df.index)
    return v.stake(df).astype(float)


def stake_factor(signal: dict, key: str = ACTIVE_VERSION) -> float:
    return float(stake_weights(pd.DataFrame([signal]), key).iloc[0])


# ─── Ordergebühren ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FeeModel:
    """Kosten je Order (Kauf und Verkauf zählen je einmal):
    Gebühr = max(min_eur, per_order_eur + pct × Ordervolumen), dazu
    slippage_pct je Seite (Spread/Ausführung, als Anteil des Volumens).
    Steuern sind nicht enthalten — sie mindern nur Gewinne und verschieben
    den Break-even nicht."""
    label: str
    per_order_eur: float = 0.0
    pct: float = 0.0
    min_eur: float = 0.0
    slippage_pct: float = 0.0

    def order_fee(self, volume_eur: float) -> float:
        return max(self.min_eur, self.per_order_eur + self.pct * volume_eur)

    def round_trip(self, stake_eur: float) -> float:
        """Gebühren für Kauf + Verkauf in Euro (ohne Slippage)."""
        return 2 * self.order_fee(stake_eur)

    def cost_fraction(self, stake_eur: float) -> float:
        """Gesamtkosten Kauf + Verkauf als Anteil des Einsatzes, inkl. Slippage."""
        return self.round_trip(stake_eur) / stake_eur + 2 * self.slippage_pct

    def breakeven_stake(self, gross_result: float | None) -> float | None:
        """Kleinster Einsatz je Trade, ab dem das Brutto-Ergebnis je Euro die
        Kosten deckt; None, wenn es bei keinem Einsatz reicht (prozentuale
        Kosten allein schon höher)."""
        if gross_result is None or gross_result <= self.cost_fraction(1e9):
            return None
        lo, hi = 1.0, 1e9          # Kosten je Euro fallen mit steigendem Einsatz
        if gross_result >= self.cost_fraction(lo):
            return lo
        for _ in range(100):
            mid = (lo * hi) ** 0.5
            lo, hi = (lo, mid) if gross_result >= self.cost_fraction(mid) else (mid, hi)
        return hi


FEE_PRESETS: dict[str, FeeModel] = {
    "ohne": FeeModel("ohne Kosten"),
    "neobroker": FeeModel("Neobroker (1 € je Order, 0,1 % Spread)", per_order_eur=1.0, slippage_pct=0.001),
    "online": FeeModel("Online-Broker (5,90 € je Order, 0,05 % Spread)",
                       per_order_eur=5.90, slippage_pct=0.0005),
    "hausbank": FeeModel("Hausbank (0,25 %, mind. 9,90 € je Order, 0,05 % Spread)",
                         pct=0.0025, min_eur=9.90, slippage_pct=0.0005),
}
DEFAULT_FEES = FEE_PRESETS["neobroker"]


# ─── Kennzahlen ───────────────────────────────────────────────────────────

def signal_metrics(df: pd.DataFrame, weights: pd.Series | None = None,
                   fees: FeeModel | None = None, stake_eur: float = 100.0) -> dict:
    """Kennzahlen einer Signalmenge. Erwartet die Spalten status,
    baseline_prob, in_zone_at_creation, d_target, d_stop, fwd20, univ_fwd20,
    direction.
      hit_rate/baseline/edge: Zielzone vor Pivot erreicht, gegen die
        Zufalls-Basis (nur entschiedene Fälle, ohne Start in der Zielzone).
      avg_result: Ø Ergebnis je Signal in % (Treffer = Abstand bis zur
        Zielzone, invalidiert = Abstand bis zum Pivot als Verlust).
      excess20: Ø Rendite der folgenden 20 Handelstage in Signalrichtung,
        abzüglich des Durchschnitts aller Titel im selben Zeitraum.
    Mit Einsatz-Gewichten (weights, Faktor je Signal; Standard 1):
      stake: Summe der Einsätze aller entschiedenen Signale (in Standardeinsätzen).
      w_result: Ergebnis je eingesetztem Euro (wie avg_result, gewichtet).
      pnl_units: Summe Einsatz × Ergebnis — mal 100 € = Gewinn in Euro.
      w_fwd20: absolute 20-Tage-Rendite in Signalrichtung je eingesetztem Euro.
    Mit Gebühren (fees, Standardeinsatz stake_eur je Signal):
      w_result_net: Ergebnis je eingesetztem Euro nach Kauf-/Verkaufskosten.
      pnl_net_eur: Gewinn in Euro nach Kosten.
      breakeven_stake: Mindesteinsatz je Trade, ab dem w_result die Kosten deckt."""
    out = {"n": len(df), "decided": 0, "hit_rate": None, "baseline": None, "edge": None,
           "avg_result": None, "excess20": None, "excess20_t": None, "n_excess": 0,
           "stake": 0.0, "w_result": None, "pnl_units": 0.0, "w_fwd20": None,
           "w_result_net": None, "pnl_net_eur": None, "breakeven_stake": None}
    if df.empty:
        return out
    w = pd.Series(1.0, index=df.index) if weights is None else weights.reindex(df.index).fillna(1.0)
    dec = df[df["status"].isin(["hit", "invalidated"]) & (df["in_zone_at_creation"].fillna(0) != 1)]
    if len(dec):
        hit = dec["status"] == "hit"
        out["decided"] = len(dec)
        out["hit_rate"] = float(hit.mean())
        out["baseline"] = float(dec["baseline_prob"].mean())
        out["edge"] = out["hit_rate"] - out["baseline"]
        res = np.where(hit, dec["d_target"], -dec["d_stop"])
        out["avg_result"] = float(res.mean())
        wd = w[dec.index].to_numpy()
        out["stake"] = float(wd.sum())
        out["pnl_units"] = float((wd * res).sum())
        out["w_result"] = out["pnl_units"] / out["stake"]
        if fees is not None:
            eur = wd * stake_eur
            cost = np.array([fees.cost_fraction(e) * e for e in eur])
            out["pnl_net_eur"] = float((eur * res).sum() - cost.sum())
            out["w_result_net"] = out["pnl_net_eur"] / float(eur.sum())
            out["breakeven_stake"] = fees.breakeven_stake(out["w_result"])
    sign = np.where(df["direction"] == "bullish", 1.0, -1.0)
    fwd = pd.Series(sign * df["fwd20"], index=df.index).dropna()
    if len(fwd):
        out["w_fwd20"] = float((w[fwd.index] * fwd).sum() / w[fwd.index].sum())
    ex = pd.Series(sign * (df["fwd20"] - df["univ_fwd20"]), index=df.index).dropna()
    if len(ex) >= 2:
        out["n_excess"] = len(ex)
        out["excess20"] = float(ex.mean())
        sd = ex.std(ddof=1)
        out["excess20_t"] = float(ex.mean() / (sd / np.sqrt(len(ex)))) if sd > 0 else None
    return out


def compare_versions(df: pd.DataFrame, periods: dict[str, pd.Series] | None = None,
                     fees: FeeModel | None = None, stake_eur: float = 100.0) -> pd.DataFrame:
    """Eine Zeile je Version (und je Zeitraum, falls periods übergeben —
    {Name: Bool-Maske über df}) mit den signal_metrics-Kennzahlen; mit fees
    zusätzlich die Netto-Kennzahlen bei stake_eur je Standardsignal."""
    periods = periods or {"Gesamt": pd.Series(True, index=df.index)}
    rows = []
    for v in VERSIONS:
        mask = accept_mask(df, v.key) if not df.empty else pd.Series(dtype=bool)
        for name, pmask in periods.items():
            sub = df[mask & pmask] if not df.empty else df
            rows.append({"version": v.key, "label": v.label, "period": name,
                         **signal_metrics(sub, stake_weights(sub, v.key), fees, stake_eur)})
    return pd.DataFrame(rows)


def replay_periods(df: pd.DataFrame) -> dict[str, pd.Series]:
    year = df["created_date"].str[:4].astype(int)
    return {
        f"Training (bis {TRAIN_END_YEAR})": year <= TRAIN_END_YEAR,
        f"Test (ab {TRAIN_END_YEAR + 1})": year > TRAIN_END_YEAR,
        "Gesamt": pd.Series(True, index=df.index),
    }


def yearly_edge(df: pd.DataFrame) -> pd.DataFrame:
    """Edge (Trefferquote − Zufalls-Basis) je Jahr und Version — zeigt, ob
    ein Unterschied stabil ist oder nur aus einzelnen Jahren stammt."""
    if df.empty:
        return pd.DataFrame()
    year = df["created_date"].str[:4]
    out = {}
    for v in VERSIONS:
        if v.stake is not None:
            continue   # gleiche Signale wie die Basisversion -> identischer Edge
        sub = df[accept_mask(df, v.key)]
        out[v.label] = {y: signal_metrics(g)["edge"] for y, g in sub.groupby(year[sub.index])}
    return pd.DataFrame(out).sort_index()


def yearly_return_per_euro(df: pd.DataFrame, keys: tuple[str, ...] = ("v3", "v4")) -> pd.DataFrame:
    """Ergebnis je eingesetztem Euro (w_result) je Jahr — für Versionen, die
    sich nur im Einsatz unterscheiden."""
    if df.empty:
        return pd.DataFrame()
    year = df["created_date"].str[:4]
    out = {}
    for k in keys:
        sub = df[accept_mask(df, k)]
        w = stake_weights(sub, k)
        out[VERSION_BY_KEY[k].label] = {
            y: signal_metrics(g, w[g.index])["w_result"] for y, g in sub.groupby(year[sub.index])}
    return pd.DataFrame(out).sort_index()


# ─── Bärenphasen und v4-Entscheidungsregel ────────────────────────────────

BEAR_MERGE_GAP = 10       # kürzere Unterbrechungen (Handelstage über SMA200) zählen zur Phase
BEAR_MIN_DAYS = 20        # kürzere Phasen unter SMA200 sind Rauschen
PHASE_MIN_DECIDED = 20    # Phasen mit weniger entschiedenen Signalen werden nicht gewertet


def market_closes(conn: sqlite3.Connection) -> pd.Series:
    from src.analytics.signal_features import MARKET_TICKER
    rows = conn.execute("SELECT date, close FROM stock_prices WHERE ticker=? ORDER BY date",
                        (MARKET_TICKER,)).fetchall()
    return pd.Series([r[1] for r in rows], index=[r[0] for r in rows], dtype=float)


def bear_phases(closes: pd.Series) -> list[tuple[str, str]]:
    """Zusammenhängende Zeiträume, in denen der Index unter seiner 200-Tage-
    Linie lag (wie das Merkmal mkt_above_sma200). Lücken bis BEAR_MERGE_GAP
    Handelstage werden überbrückt, Phasen unter BEAR_MIN_DAYS verworfen."""
    sma = closes.rolling(200).mean()
    below = (closes < sma) & sma.notna()
    phases, start, last, gap = [], None, None, 0
    for date, b in below.items():
        if b:
            start = start or date
            last, gap = date, 0
        elif start is not None:
            gap += 1
            if gap > BEAR_MERGE_GAP:
                phases.append((start, last))
                start, gap = None, 0
    if start is not None:
        phases.append((start, last))
    days = below.index.to_series()
    return [(a, b) for a, b in phases if ((days >= a) & (days <= b)).sum() >= BEAR_MIN_DAYS]


def phase_table(df: pd.DataFrame, phases: list[tuple[str, str]], key: str = "v3") -> pd.DataFrame:
    """Kennzahlen der Signale von Version `key` je Bärenphase (nur Signale,
    die zum Signalzeitpunkt als Bärenmarkt markiert waren)."""
    acc = df[accept_mask(df, key) & market_bear(df)] if not df.empty else df
    rows = []
    for a, b in phases:
        sub = acc[(acc["created_date"] >= a) & (acc["created_date"] <= b)]
        m = signal_metrics(sub)
        rows.append({"start": a, "end": b, "n": m["n"], "decided": m["decided"],
                     "edge": m["edge"], "avg_result": m["avg_result"],
                     "counted": m["decided"] >= PHASE_MIN_DECIDED})
    return pd.DataFrame(rows)


def v4_decision(df: pd.DataFrame, phases: list[tuple[str, str]]) -> dict:
    """Vorab festgelegte Entscheidungsregel für v4 (am 2026-10-01 vereinbart,
    vor Ansicht der Testdaten), auszuwerten auf dem Test-Universum:
      1. Edge der Long-Signale im Bärenmarkt in der Mehrheit der Bärenphasen
         positiv (nur Phasen mit mind. PHASE_MIN_DECIDED entschiedenen Signalen),
      2. Rendite je eingesetztem Euro (w_result) von v4 über der von v3.
    v4 wird nur aktiv, wenn beides gilt."""
    pt = phase_table(df, phases)
    counted = pt[pt["counted"]] if not pt.empty else pt
    n_pos = int((counted["edge"] > 0).sum()) if len(counted) else 0
    cmp = compare_versions(df).set_index("version")
    w3, w4 = cmp.loc["v3", "w_result"], cmp.loc["v4", "w_result"]
    edge_ok = len(counted) > 0 and n_pos > len(counted) / 2
    return_ok = w3 is not None and w4 is not None and w4 > w3
    return {"phases": pt, "n_counted": len(counted), "n_positive": n_pos,
            "w_result_v3": w3, "w_result_v4": w4,
            "pnl_units_v3": cmp.loc["v3", "pnl_units"], "pnl_units_v4": cmp.loc["v4", "pnl_units"],
            "edge_ok": edge_ok, "return_ok": return_ok, "passed": edge_ok and return_ok}


# ─── Live-Vorhersagen in dasselbe Format bringen ──────────────────────────

def live_signal_frame(conn: sqlite3.Connection, since: str | None = None) -> pd.DataFrame:
    """wave_predictions im Format von replay_signals: inkl. d_target/d_stop,
    der 20-Tage-Rendite (sobald 20 Handelstage vergangen sind) und der
    Durchschnittsrendite aller Titel im selben Zeitraum."""
    q = "SELECT * FROM wave_predictions" + (" WHERE created_date >= ?" if since else "")
    df = pd.read_sql(q, conn, params=[since] if since else None)
    if df.empty:
        return df
    feats = pd.json_normalize(df["features_json"].map(from_json).tolist())
    df = pd.concat([df.reset_index(drop=True), feats.reset_index(drop=True)], axis=1)

    s = np.where(df["direction"] == "bullish", 1.0, -1.0)
    price = df["price_at_prediction"]
    near = np.where(s > 0, df["target_low"], df["target_high"])
    df["d_target"] = (near - price) * s / price
    df["d_stop"] = (price - df["last_pivot_price"]) * s / price

    start = df["created_date"].min()
    px = pd.read_sql(
        """SELECT p.ticker, p.date, p.close FROM stock_prices p
           LEFT JOIN watchlist w ON w.ticker = p.ticker
           WHERE p.date >= ? AND COALESCE(w.asset_type, 'stock') NOT IN ('crypto', 'index')""",
        conn, params=[start],
    )
    # Penny-Aktien (z.B. MICR, 0,0003 $ ↔ 0,05 $) würden den Durchschnitt
    # sprengen — siehe MIN_STOCK_PRICE in price_plausibility.py
    from src.database.price_plausibility import penny_tickers
    px = px[~px["ticker"].isin(penny_tickers(conn))]
    # ffill über einzelne Feiertage einer Börse, sonst fehlt dort die 20-Tage-Rendite
    closes = px.pivot(index="date", columns="ticker", values="close").sort_index().ffill(limit=3)
    fwd = closes.shift(-20) / closes - 1
    univ = fwd.mean(axis=1)
    fwd_long = fwd.stack().rename("fwd20")
    keys = pd.MultiIndex.from_arrays([df["created_date"], df["ticker"]])
    df["fwd20"] = fwd_long.reindex(keys).to_numpy()
    df["univ_fwd20"] = df["created_date"].map(univ)
    return df
