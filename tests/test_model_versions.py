import pandas as pd
import pytest

from src.analytics.model_versions import (
    VERSIONS, accepts, signal_metrics, compare_versions, yearly_edge,
    stake_factor, stake_weights, bear_phases, v4_decision,
)
from src.analytics.signal_features import compute_features


def _sig(**kw):
    base = {"state": "wave3_forming", "direction": "bullish", "confidence": 0.8,
            "target_low": 110.0, "in_zone_at_creation": 0}
    return {**base, **kw}


def test_version_keys_are_unique_and_ordered():
    keys = [v.key for v in VERSIONS]
    assert keys == sorted(set(keys))


@pytest.mark.parametrize("signal, expected", [
    (_sig(),                                   {"v1": True,  "v2": True,  "v3": True, "v4": True}),
    (_sig(direction="bearish", mkt_above_sma200=0), {"v3": False, "v4": False}),
    (_sig(direction="bearish"),                {"v1": True,  "v2": False, "v3": False}),
    (_sig(confidence=0.5),                     {"v1": False, "v2": False, "v3": True}),
    (_sig(in_zone_at_creation=1),              {"v1": True,  "v2": False, "v3": False}),
    (_sig(state="correction_forming"),         {"v1": False, "v2": False, "v3": True}),
    (_sig(state="new_impulse_possible", target_low=None), {"v1": False, "v2": False, "v3": False}),
])
def test_version_rules(signal, expected):
    assert {k: accepts(signal, k) for k in expected} == expected


def _outcomes():
    return pd.DataFrame({
        "created_date": ["2020-01-02", "2020-02-03", "2023-01-02", "2023-02-01"],
        "state": "wave3_forming", "confidence": 0.8, "target_low": 1.0,
        "direction": ["bullish", "bearish", "bullish", "bullish"],
        "in_zone_at_creation": 0,
        "status": ["hit", "hit", "invalidated", "hit"],
        "baseline_prob": [0.5, 0.5, 0.5, 0.5],
        "d_target": [0.10, 0.10, 0.10, 0.10],
        "d_stop":   [0.05, 0.05, 0.05, 0.05],
        "fwd20":    [0.03, 0.03, -0.02, 0.04],
        "univ_fwd20": [0.01, 0.01, 0.01, 0.01],
    })


def test_signal_metrics():
    m = signal_metrics(_outcomes())
    assert m["hit_rate"] == pytest.approx(0.75)
    assert m["edge"] == pytest.approx(0.25)
    assert m["avg_result"] == pytest.approx((0.10 * 3 - 0.05) / 4)
    # Short: Richtung gedreht -> -(0.03 - 0.01)
    assert m["excess20"] == pytest.approx((0.02 - 0.02 - 0.03 + 0.03) / 4)


def test_compare_versions_uses_same_signals_per_version():
    df = _outcomes()
    cmp = compare_versions(df)
    n = dict(zip(cmp["version"], cmp["n"]))
    assert n == {"v1": 4, "v2": 3, "v3": 3, "v4": 3}
    assert set(yearly_edge(df).index) == {"2020", "2023"}


def test_compute_features_uses_only_given_history():
    closes = pd.Series(range(1, 251), dtype=float)
    f = compute_features(closes, closes)
    assert f["above_sma200"] is True and f["mkt_above_sma200"] is True
    assert f["mom126"] == pytest.approx(230 / 125 - 1)
    assert compute_features(closes.iloc[:50], None) == {
        "above_sma200": None, "dist_sma200": None, "mom126": None, "mkt_above_sma200": None}


@pytest.mark.parametrize("signal, factor", [
    (_sig(mkt_above_sma200=1), 1.0),
    (_sig(mkt_above_sma200=0), 2.0),
    (_sig(mkt_above_sma200=None), 1.0),          # unbekannt -> kein Bärenmarkt
    (_sig(features_json='{"mkt_above_sma200": false}'), 2.0),
    (_sig(features_json=None), 1.0),
])
def test_v4_stake_doubles_in_bear_market(signal, factor):
    assert stake_factor(signal, "v4") == factor
    assert stake_factor(signal, "v3") == 1.0


def test_weighted_metrics():
    df = _outcomes()
    df["mkt_above_sma200"] = [0, 0, 1, 1]     # nur die ersten beiden im Bärenmarkt
    w = stake_weights(df, "v4")
    assert list(w) == [2.0, 2.0, 1.0, 1.0]
    m = signal_metrics(df, w)
    res = [0.10, 0.10, -0.05, 0.10]
    assert m["stake"] == pytest.approx(6.0)
    assert m["pnl_units"] == pytest.approx(2 * 0.10 + 2 * 0.10 - 0.05 + 0.10)
    assert m["w_result"] == pytest.approx(m["pnl_units"] / 6.0)
    # ungewichtet bleiben die bisherigen Kennzahlen unverändert
    assert m["avg_result"] == pytest.approx(sum(res) / 4)
    assert signal_metrics(df)["w_result"] == pytest.approx(sum(res) / 4)


def test_bear_phases_merges_short_gaps_and_drops_short_dips():
    # 250 Tage steigend, dann 60 Tage tief (Bärenphase), 5 Tage hoch (Lücke),
    # 30 Tage tief, 100 Tage hoch, 10 Tage tief (zu kurz)
    vals = ([100.0] * 250 + [50.0] * 60 + [200.0] * 5 + [50.0] * 30
            + [300.0] * 100 + [10.0] * 10 + [300.0] * 30)
    dates = pd.date_range("2018-01-01", periods=len(vals), freq="B").strftime("%Y-%m-%d")
    ph = bear_phases(pd.Series(vals, index=dates))
    assert ph == [(dates[250], dates[344])]


def test_v4_decision():
    rows = []
    for d in ["2020-03-02", "2020-03-03"] * 15:      # Bärenphase: gute Long-Signale
        rows.append({"created_date": d, "mkt_above_sma200": 0, "status": "hit"})
    for d in ["2021-06-01"] * 30:                    # Bullenmarkt: schwach
        rows.append({"created_date": d, "mkt_above_sma200": 1, "status": "invalidated"})
    df = pd.DataFrame(rows).assign(
        state="wave3_forming", direction="bullish", confidence=0.8, target_low=1.0,
        in_zone_at_creation=0, baseline_prob=0.5, d_target=0.10, d_stop=0.05,
        fwd20=0.0, univ_fwd20=0.0)
    out = v4_decision(df, [("2020-02-27", "2020-05-26")])
    assert out["n_counted"] == 1 and out["n_positive"] == 1
    assert out["w_result_v4"] > out["w_result_v3"]
    assert out["passed"]
    # Bärenphase ohne Vorteil -> durchgefallen
    df.loc[df["mkt_above_sma200"] == 0, "status"] = "invalidated"
    assert not v4_decision(df, [("2020-02-27", "2020-05-26")])["passed"]
