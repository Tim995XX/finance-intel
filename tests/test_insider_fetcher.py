import os
import stat

import pytest

from src.fetchers import insider_fetcher
from src.fetchers.insider_fetcher import _norm_amount, _norm_trade_type, save_fmp_key


# ─── _norm_trade_type ──────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("Sale (Full)", "sell"),
    ("Sale (Partial)", "sell"),
    ("sell", "sell"),
    ("Purchase", "buy"),
    ("buy", "buy"),
    ("Exchange", "exchange"),
])
def test_norm_trade_type_maps_known_variants(raw, expected):
    assert _norm_trade_type(raw) == expected


def test_norm_trade_type_is_case_insensitive():
    assert _norm_trade_type("SALE (FULL)") == "sell"
    assert _norm_trade_type("PURCHASE") == "buy"


def test_norm_trade_type_unknown_value_passed_through():
    assert _norm_trade_type("Gift") == "gift"


def test_norm_trade_type_empty_string_returns_unknown():
    assert _norm_trade_type("") == "unknown"
    assert _norm_trade_type("   ") == "unknown"


def test_norm_trade_type_sale_wins_over_incidental_buy_substring():
    """'sale' must not be misclassified if a type string mixes signals;
    sell-check runs first by design (e.g. FMP's 'Sale (Full)' vs 'Purchase')."""
    assert _norm_trade_type("Sale") == "sell"


# ─── _norm_amount ──────────────────────────────────────────────────────────

def test_norm_amount_range_returns_low_and_high():
    assert _norm_amount("$1,001 - $15,000") == (1001.0, 15000.0)


def test_norm_amount_single_value_returns_same_value_twice():
    assert _norm_amount("$50,000") == (50000.0, 50000.0)


def test_norm_amount_no_digits_returns_none_none():
    assert _norm_amount("N/A") == (None, None)
    assert _norm_amount("") == (None, None)


def test_norm_amount_strips_dollar_signs_and_commas():
    lo, hi = _norm_amount("$1,000,001 - $5,000,000")
    assert lo == 1000001.0
    assert hi == 5000000.0


# ─── save_fmp_key ──────────────────────────────────────────────────────────

def test_save_fmp_key_restricts_file_permissions_to_owner_only(tmp_path, monkeypatch):
    monkeypatch.setattr(insider_fetcher, "_CONFIG_FILE", tmp_path / ".insider_config.json")
    save_fmp_key("testkey123")
    mode = stat.S_IMODE(os.stat(insider_fetcher._CONFIG_FILE).st_mode)
    assert mode == 0o600
