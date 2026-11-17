import pandas as pd
import pytest

from src.analytics.exit_rules import ExitRule, simulate, grid_metrics
from src.analytics.model_versions import FeeModel, signal_metrics


# ─── Gebühren ─────────────────────────────────────────────────────────────

def test_fee_cost_fraction_and_breakeven():
    f = FeeModel("t", per_order_eur=1.0, slippage_pct=0.001)
    assert f.cost_fraction(1000) == pytest.approx(0.002 + 0.002)
    # 1 % brutto: 2 € / S + 0,2 % <= 1 %  ->  S >= 250 €
    assert f.breakeven_stake(0.01) == pytest.approx(250, rel=1e-3)
    assert f.breakeven_stake(0.001) is None          # Spread allein frisst mehr
    assert FeeModel("0").breakeven_stake(0.001) == 1.0


def test_fee_minimum_charge():
    f = FeeModel("t", pct=0.0025, min_eur=9.90)
    assert f.order_fee(1000) == 9.90
    assert f.order_fee(10_000) == pytest.approx(25.0)
    # unterhalb 3.960 € greift die Mindestgebühr: 19,80 € / S <= 1 %  ->  S >= 1.980 €
    assert f.breakeven_stake(0.01) == pytest.approx(1980, rel=1e-3)


def test_signal_metrics_net():
    df = pd.DataFrame({"status": ["hit", "invalidated"], "baseline_prob": 0.5, "in_zone_at_creation": 0,
                       "d_target": [0.05, 0.05], "d_stop": [0.01, 0.01], "direction": "bullish",
                       "fwd20": None, "univ_fwd20": None})
    m = signal_metrics(df, fees=FeeModel("t", per_order_eur=1.0), stake_eur=100)
    assert m["w_result"] == pytest.approx(0.02)
    assert m["pnl_net_eur"] == pytest.approx(2 * 100 * 0.02 - 2 * 2)   # 4 € Gewinn − 4 € Gebühren
    assert m["w_result_net"] == pytest.approx(0.0)
    assert m["breakeven_stake"] == pytest.approx(100, rel=1e-3)


# ─── Exit-Simulation ──────────────────────────────────────────────────────

def _prices(rows):
    return pd.DataFrame([{"ticker": "X", "date": d, "open": o, "high": h, "low": l, "close": c}
                         for d, o, h, l, c in rows])


SIG = pd.DataFrame([{"ticker": "X", "created_date": "2020-01-01", "price": 100.0,
                     "target_low": 110.0, "last_pivot_price": 95.0}])
RULES = [ExitRule("cur", "", "struct", 1.0, "struct", 1.0),
         ExitRule("half", "", "struct", 0.5, None, None),
         ExitRule("pct", "", "pct", 0.03, "pct", 0.03)]


def _late_rows():
    # ein Tag weit nach dem Horizont, damit kein Trade als "noch offen" gilt
    return [("2021-01-01", 100, 100, 100, 100)]


def test_simulate_tp_sl_and_next_day_entry():
    px = _prices([("2020-01-01", 100, 100, 100, 100),
                  ("2020-01-02", 101, 104, 100, 103),     # Einstieg 101; +3 % TP (104,03) nicht erreicht
                  ("2020-01-03", 103, 106, 102, 105),     # half-TP 105 und pct-TP 104,03 erreicht
                  ("2020-01-06", 105, 111, 104, 110)]     # Zielzone 110
                 + _late_rows())
    sim = simulate(SIG, px, RULES).iloc[0]
    assert sim["ret:cur"] == pytest.approx(110 / 101 - 1)
    assert sim["ret:half"] == pytest.approx(105 / 101 - 1)
    assert sim["ret:pct"] == pytest.approx(0.03)
    assert sim["days:cur"] == 5


def test_simulate_same_day_counts_stop_and_gap():
    px = _prices([("2020-01-01", 100, 100, 100, 100),
                  ("2020-01-02", 100, 112, 90, 100)]       # TP und SL am selben Tag -> SL
                 + _late_rows())
    assert simulate(SIG, px, RULES).iloc[0]["ret:cur"] == pytest.approx(95 / 100 - 1)
    px = _prices([("2020-01-01", 100, 100, 100, 100),
                  ("2020-01-02", 100, 101, 99, 100),
                  ("2020-01-03", 90, 91, 89, 90)]          # Kurslücke unter den Stop
                 + _late_rows())
    assert simulate(SIG, px, RULES).iloc[0]["ret:cur"] == pytest.approx(-0.10)


def test_simulate_skips_open_trades_and_grid_metrics():
    px = _prices([("2020-01-01", 100, 100, 100, 100), ("2020-01-02", 100, 101, 99, 100)])
    assert simulate(SIG, px, RULES).empty or simulate(SIG, px, RULES).iloc[0].isna().all()

    sim = pd.DataFrame({"ret:cur": [0.05, -0.01], "days:cur": [10, 10], "bench:cur": [0.01, 0.01]})
    gm = grid_metrics(sim, pd.Series(1.0, index=sim.index), FeeModel("t", per_order_eur=1.0),
                      100.0, RULES[:1]).iloc[0]
    assert gm["gross"] == pytest.approx(0.02)
    assert gm["net"] == pytest.approx(0.0)
    assert gm["excess"] == pytest.approx(0.01)
    assert gm["win_rate"] == 0.5


# ─── Trailing Stop ────────────────────────────────────────────────────────

def test_trailing_stop_from_entry_follows_highs():
    px = _prices([("2020-01-01", 100, 100, 100, 100),
                  ("2020-01-02", 100, 120, 99, 118),      # Einstieg 100, Hoch 120
                  ("2020-01-03", 117, 118, 107, 110)]     # Stop 120 × 0,9 = 108 gerissen
                 + _late_rows())
    r = ExitRule("tr", "", None, None, None, None, 0.10, "entry")
    assert simulate(SIG, px, [r]).iloc[0]["ret:tr"] == pytest.approx(108 / 100 - 1)


def test_trailing_after_target_zone_lets_winner_run():
    px = _prices([("2020-01-01", 100, 100, 100, 100),
                  ("2020-01-02", 100, 111, 99, 110),      # Zielzone 110 erreicht -> kein Verkauf
                  ("2020-01-03", 110, 130, 109, 128),     # Stop ab hier 111 × 0,9 = 99,9 (über Pivot 95)
                  ("2020-01-06", 125, 126, 110, 112)]     # Stop 130 × 0,9 = 117 gerissen
                 + _late_rows())
    r = ExitRule("at", "", "struct", 1.0, "struct", 1.0, 0.10, "after_tp")
    assert simulate(SIG, px, [r]).iloc[0]["ret:at"] == pytest.approx(117 / 100 - 1)
    # vor Erreichen der Zielzone gilt nur der Pivot-SL
    px = _prices([("2020-01-01", 100, 100, 100, 100),
                  ("2020-01-02", 100, 108, 99, 107),
                  ("2020-01-03", 107, 107, 96, 97)]       # 10 % unter Hoch, aber über Pivot 95
                 + _late_rows())
    assert simulate(SIG, px, [r]).iloc[0]["ret:at"] == pytest.approx(97 / 100 - 1)   # Horizontende
