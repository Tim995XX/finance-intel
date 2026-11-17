import pandas as pd
import pytest

from src.database import db
from src.database.db import init_db, get_connection, store_prices
from src.database.price_history import clean_implausible_prices
from src.database.price_plausibility import implausible_mask, implausible_segments_mask
from src.analytics.wave_tracker import record_snapshot, load_predictions


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    init_db()
    c = get_connection()
    yield c
    c.close()


def _prices(ticker, closes, volume=1000, start="2026-01-01"):
    dates = pd.date_range(start, periods=len(closes)).strftime("%Y-%m-%d")
    return pd.DataFrame({
        "ticker": ticker, "date": dates,
        "open": closes, "high": closes, "low": closes, "close": closes, "volume": volume,
    })


# ─── implausible_mask (beim Speichern) ──────────────────────────────────

def test_single_day_spike_is_rejected():
    closes = [1.0, 1.0, 0.0002, 1.0, 1.0]
    assert implausible_mask(closes, [1000] * 5) == [False, False, True, False, False]


def test_wrong_coin_without_volume_is_rejected_permanently():
    """M-USD ab 29.09.: dauerhaft anderer Coin, Volumen ~0."""
    closes  = [1.2] * 5 + [0.0003] * 6
    volumes = [1e6] * 5 + [0] * 6
    assert implausible_mask(closes, volumes) == [False] * 5 + [True] * 6


def test_real_crash_with_volume_is_accepted_once_confirmed():
    closes  = [100.0] * 5 + [15.0, 14.0, 16.0]
    volumes = [1e5] * 5 + [5e6] * 3
    assert implausible_mask(closes, volumes) == [False] * 8
    # Erst der jüngste Tag: noch nicht bestätigt → (vorerst) verworfen
    assert implausible_mask(closes[:6], volumes[:6])[-1] is True


def test_moderate_moves_and_negative_prices_pass():
    """CL=F 20.04.2020 (−37,63 $) ist echt; ±50 % ebenso."""
    closes = [18.0, -37.6, 10.0, 15.0, 7.5]
    assert implausible_mask(closes, [1000] * 5) == [False] * 5


def test_reference_from_stored_prices():
    assert implausible_mask([0.0002], [0], ref_close=1.0, ref_volumes=[1e6] * 5) == [True]


def test_store_prices_drops_implausible_rows(conn):
    store_prices(conn, _prices("M-USD", [1.2] * 5, volume=1e6))
    fresh = _prices("M-USD", [1.2] * 5 + [0.0003] * 3, volume=0)
    assert store_prices(conn, fresh) == 0
    closes = [r[0] for r in conn.execute("SELECT close FROM stock_prices WHERE ticker='M-USD'")]
    assert closes == [1.2] * 5


# ─── implausible_segments_mask (nachträgliche Bereinigung) ─────────────

def test_wrong_coin_at_start_of_crypto_history():
    """M-USD/USDG-USD: Anfang der Historie stammt vom falschen Yahoo-Coin."""
    closes = [0.0005] * 4 + [1.5] * 10 + [0.0003] * 3
    assert implausible_segments_mask(closes, crypto=True) == [True] * 4 + [False] * 10 + [True] * 3


def test_real_stock_explosion_is_kept():
    """BMNR 06/2025: ~5 $ → ~35 $ — echter, dauerhafter Sprung."""
    closes = [5.0] * 16 + [35.0] * 40
    assert not any(implausible_segments_mask(closes, crypto=False))


def test_single_stock_spike_is_removed():
    """SBK.JO 25.04.2025: 228 statt ~17.500 an einem Tag."""
    closes = [17500.0] * 5 + [228.0] + [17500.0] * 5
    assert implausible_segments_mask(closes, crypto=False) == [False] * 5 + [True] + [False] * 5


def _wave(date, price):
    return {
        "current_date": date, "state": "wave3_forming", "direction": "bullish",
        "confidence": 0.8, "current_price": price, "target_low": 2.0, "target_high": 3.0,
        "last_pivot_date": date, "last_pivot_price": 0.5,
        "expectation_text": "x", "entry_limit_price": None,
    }


def test_clean_deletes_contaminated_predictions_and_reopens_others(conn):
    closes = [1.0] * 10 + [0.0002] + [1.0] * 9
    df = _prices("ABC-USD", closes, volume=1e6)
    conn.executemany(
        "INSERT INTO stock_prices (ticker,date,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?)",
        df[["ticker", "date", "open", "high", "low", "close", "volume"]].values.tolist(),
    )
    dates = df["date"].tolist()
    record_snapshot(conn, "ABC-USD", _wave(dates[5], 1.0), 5.0, 365)    # vor dem Ausreißer
    record_snapshot(conn, "ABC-USD", _wave(dates[15], 1.0), 5.0, 365)   # Fenster enthält ihn
    conn.execute("UPDATE wave_predictions SET status='invalidated', resolved_date=?, "
                 "resolved_price=0.0002 WHERE created_date=?", (dates[10], dates[5]))
    conn.commit()

    res = clean_implausible_prices(conn)

    assert res["tickers"] == {"ABC-USD": 1}
    assert res["deleted"] == 1
    assert res["reopened"] == 1
    preds = load_predictions(conn)
    assert list(preds["created_date"]) == [dates[5]]
    assert list(preds["status"]) == ["open"]


def test_stored_days_stay_reference_for_overlapping_window(conn):
    """USDG-USD: Yahoo liefert für das gesamte Fenster den falschen Coin
    (~4–7 $ statt 1 $). Bei Krypto bleiben überlappende Tage unverändert —
    der gespeicherte Wert muss Referenz bleiben, sonst wandert sie über
    einen Tag knapp unter Faktor 4 auf das falsche Niveau."""
    store_prices(conn, _prices("USDG-USD", [1.0] * 6, volume=1e9))
    fresh = _prices("USDG-USD", [5.4, 3.9, 5.5, 5.4, 5.5, 5.4, 5.5, 5.4], volume=4e4)
    assert store_prices(conn, fresh) == 0


# ─── Mindestkurs (Penny-Aktien) ─────────────────────────────────────────

from src.database.price_plausibility import is_penny, penny_tickers, MIN_STOCK_PRICE


def test_is_penny():
    assert is_penny(0.0003)
    assert is_penny(MIN_STOCK_PRICE * 0.99)
    assert not is_penny(MIN_STOCK_PRICE)
    assert not is_penny(None)
    assert not is_penny(float("nan"))
    assert not is_penny(-37.63)   # negativer Ölpreis ist echt, kein Penny-Fall


def test_penny_tickers_skips_crypto_and_uses_latest_close(conn):
    conn.execute("INSERT INTO watchlist (ticker,name,asset_type,continent) VALUES ('ADA-USD','ADA','crypto','Global')")
    conn.execute("INSERT INTO watchlist (ticker,name,asset_type,continent) VALUES ('MICR','MICR','stock','Global')")
    store_prices(conn, _prices("ADA-USD", [0.15] * 5))
    store_prices(conn, _prices("MICR", [0.0003] * 5))
    store_prices(conn, _prices("FALL", [5.0, 3.0, 2.0, 1.5, 0.8]))   # ohne Watchlist-Eintrag = Aktie
    store_prices(conn, _prices("UP", [0.8, 0.9, 1.0, 1.1, 1.2]))
    assert penny_tickers(conn) == {"MICR", "FALL"}
