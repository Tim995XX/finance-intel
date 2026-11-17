import re
from collections import Counter
from pathlib import Path

import pytest

from src.processors.text_processor import COMPANY_TICKER, extract_tickers

SOURCE_FILE = Path(__file__).parent.parent / "src" / "processors" / "text_processor.py"


def test_no_duplicate_keys_in_company_ticker_source():
    """A duplicate key in a dict literal silently overwrites the earlier one
    instead of raising an error, so this only shows up by scanning the source
    for repeats. Regression test for the 'amd' / 'samsung' duplicates."""
    src = SOURCE_FILE.read_text()
    keys = re.findall(r'^\s*"([^"]+)":', src, re.MULTILINE)
    dupes = {k: c for k, c in Counter(keys).items() if c > 1}
    assert dupes == {}


def test_deutsche_telekom_matches_regardless_of_casing():
    """Bug: the 'Deutsche telekom' key was capitalized while every non-ambiguous
    key is matched against extract_tickers()'s lowercased text -> the pattern
    built from that key could never match, making the entry dead code."""
    for text in [
        "Deutsche Telekom reported strong quarterly earnings today.",
        "deutsche telekom reported strong quarterly earnings today.",
        "DEUTSCHE TELEKOM reported strong quarterly earnings today.",
    ]:
        found = dict(extract_tickers(text))
        assert found.get("DTE.DE") == "deutsche telekom"


def test_company_ticker_keys_are_lowercase_unless_ambiguous():
    """Guards against reintroducing a mixed-case key: every non-ambiguous
    entry is checked against a lowercased text, so a key with uppercase
    letters is unreachable."""
    from src.processors.text_processor import _AMBIGUOUS_NAMES

    for name in COMPANY_TICKER:
        if name in _AMBIGUOUS_NAMES:
            continue
        assert name == name.lower(), f"key {name!r} is unreachable (not lowercase)"


@pytest.mark.parametrize("text,expected_ticker", [
    ("AMD launched a new chip today.", "AMD"),
    ("Samsung unveiled its new foldable phone.", "SSNLF"),
])
def test_single_entry_per_company_still_resolves(text, expected_ticker):
    found = dict(extract_tickers(text))
    assert found.get(expected_ticker) is not None
