"""
Kryptowährungen — Marktdaten (Top 50 nach Marktkapitalisierung) + Kurshistorie
über die öffentliche CoinGecko-API. Ohne API-Key gilt das anonyme Free-Tier
(strikt IP-basiert rate-limitiert — beobachtet: auch mit 6s Pause zwischen
Calls zuverlässig 429, teils sogar der reine /ping-Endpunkt). Optionaler
Demo-API-Key (kostenlos unter coingecko.com/en/developer/dashboard, Präfix
"CG-") hebt das Limit deutlich an und wird als 'x-cg-demo-api-key'-Header
mitgeschickt (siehe get_coingecko_key()/save_coingecko_key()), bleibt aber
denselben api.coingecko.com-Endpunkt (kein pro-api.coingecko.com nötig).

Warum nicht yfinance wie bei stock_fetcher.py: Yahoo Finance nutzt für viele
Altcoins uneinheitliche/gesuffixte Ticker (z.B. Toncoin -> "TON11419-USD"
statt "TON-USD") und liefert für etliche Top-50-Coins schlicht keine Daten
("possibly delisted"). CoinGecko-IDs sind dagegen stabil und decken die
komplette Top-50-Liste zuverlässig ab. Die hier erzeugten Ticker folgen
trotzdem demselben "<SYMBOL>-USD"-Schema wie die bereits vorhandenen
BTC-USD/ETH-USD-Einträge, damit sie unverändert in stock_prices/watchlist
und allen bestehenden Chart-/Wellen-/Signal-Tabs weiterverwendet werden
können.
"""
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.coingecko.com/api/v3"
_TIMEOUT = 15

# Gleiche Konfigurationsdatei wie insider_fetcher.py (FMP-Key) — zentral im
# Projektroot, per .gitignore ausgeschlossen (enthält Klartext-Keys).
_ROOT = Path(__file__).parent.parent.parent
_CONFIG_FILE = _ROOT / ".insider_config.json"


def get_coingecko_key() -> str | None:
    """CoinGecko-Demo-API-Key aus Umgebungsvariable oder Konfigurationsdatei
    laden. None, falls keiner hinterlegt ist — dann greift das anonyme
    (stark limitierte) Free-Tier."""
    key = os.environ.get("COINGECKO_API_KEY", "").strip()
    if key:
        return key
    if _CONFIG_FILE.exists():
        try:
            cfg = json.loads(_CONFIG_FILE.read_text())
            return cfg.get("coingecko_api_key", "").strip() or None
        except Exception:
            pass
    return None


def save_coingecko_key(key: str) -> None:
    """CoinGecko-Key in lokaler Konfigurationsdatei speichern (0600, wie
    save_fmp_key() in insider_fetcher.py)."""
    cfg = {}
    if _CONFIG_FILE.exists():
        try:
            cfg = json.loads(_CONFIG_FILE.read_text())
        except Exception:
            pass
    cfg["coingecko_api_key"] = key.strip()
    _CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    os.chmod(_CONFIG_FILE, 0o600)


def _headers() -> dict:
    h = {"Accept": "application/json"}
    key = get_coingecko_key()
    if key:
        h["x-cg-demo-api-key"] = key
    return h

FNG_URL          = "https://api.alternative.me/fng/"
BINANCE_FAPI_URL = "https://fapi.binance.com/fapi/v1"
BINANCE_SPOT_URL = "https://api.binance.com/api/v3"

# Kryptonahe Aktien (Börse, Miner, Broker mit hoher Krypto-Korrelation) — dienen
# im Dashboard (Tab 'Krypto Top 50') als Insider-Signal-Proxy für Coins, die
# selbst keine SEC-Insider-Meldungen (Form 4) haben, und lösen im Tab/Scan
# 'Insider-Papier-Trades' die Aufnahme der Krypto-Top-50 in dessen
# Ticker-Universum aus, sobald eine dieser Aktien aktuell durch Insider
# gehandelt wird. Zentral hier definiert, damit Dashboard (app.py) und
# headless-Pipeline (main.py) dieselbe Liste verwenden.
CRYPTO_PROXY_TICKERS = ["COIN", "MSTR", "MARA", "RIOT", "HOOD"]


def fetch_top50_markets() -> pd.DataFrame:
    """Top 50 Kryptowährungen nach Marktkapitalisierung (USD). Gibt ein leeres
    DataFrame zurück, wenn die API nicht erreichbar ist. Spalte 'ticker' folgt
    dem im Projekt üblichen '<SYMBOL>-USD'-Schema (siehe Moduldoc)."""
    try:
        resp = requests.get(
            f"{BASE_URL}/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": 50,
                "page": 1,
                "price_change_percentage": "24h,7d",
                "sparkline": "false",
            },
            headers=_headers(), timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("CoinGecko Top-50-Abruf fehlgeschlagen: %s", e)
        return pd.DataFrame()

    rows = []
    for c in data:
        symbol = str(c.get("symbol", "")).upper()
        if not symbol:
            continue
        rows.append({
            "rank":          c.get("market_cap_rank"),
            "id":            c["id"],
            "symbol":        symbol,
            "ticker":        f"{symbol}-USD",
            "name":          c.get("name", symbol),
            "price":         c.get("current_price"),
            "change_24h_pct": c.get("price_change_percentage_24h_in_currency"),
            "change_7d_pct":  c.get("price_change_percentage_7d_in_currency"),
            "market_cap":    c.get("market_cap"),
            "volume_24h":    c.get("total_volume"),
            "ath":           c.get("ath"),
            "ath_change_pct": c.get("ath_change_percentage"),
        })
    return pd.DataFrame(rows)


MAX_HISTORY_DAYS = 365  # CoinGecko Free-Tier: market_chart liefert >365 Tage nur mit (kostenpflichtigem) API-Key (sonst HTTP 401)


def fetch_history(coin_id: str, ticker: str, days: int = MAX_HISTORY_DAYS, _retry: bool = True) -> pd.DataFrame:
    """Tägliche OHLCV-Historie für einen Coin, synthetisiert aus dem
    market_chart-Endpunkt (liefert nur Schlusskurse + Volumen je Tag, kein
    echtes Intraday-OHLC im Free-Tier für Zeiträume > 90 Tage). open = Vortages-
    Close, high/low = max/min(open, close) — eine übliche Näherung, wenn nur
    eine Zeitreihe von Schlusskursen vorliegt; für die Zigzag-basierte
    Elliott-Wellen-Erkennung (elliott_wave.py) ausreichend, da diese ohnehin
    nur auf lokale Extrempunkte oberhalb eines Schwellenwerts reagiert.
    Rückgabe im selben Format wie stock_fetcher.fetch_prices() (Spalten
    ticker,date,open,high,low,close,volume), also direkt an store_prices()
    übergebbar.

    Bei HTTP 429 (Free-Tier-Rate-Limit, im Batch-Abruf trotz Pause zwischen
    Requests gelegentlich erreicht) wird einmal nach 20s Wartezeit erneut
    versucht, bevor endgültig aufgegeben wird."""
    days = min(days, MAX_HISTORY_DAYS)
    try:
        resp = requests.get(
            f"{BASE_URL}/coins/{coin_id}/market_chart",
            params={"vs_currency": "usd", "days": days, "interval": "daily"},
            headers=_headers(), timeout=_TIMEOUT,
        )
        if resp.status_code == 429 and _retry:
            time.sleep(20)
            return fetch_history(coin_id, ticker, days=days, _retry=False)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("CoinGecko Historie-Abruf fehlgeschlagen für %s: %s", coin_id, e)
        return pd.DataFrame()

    prices = data.get("prices") or []
    volumes = data.get("total_volumes") or []
    if not prices:
        return pd.DataFrame()

    price_df = pd.DataFrame(prices, columns=["ts", "close"])
    price_df["date"] = pd.to_datetime(price_df["ts"], unit="ms").dt.strftime("%Y-%m-%d")
    price_df = price_df.groupby("date", as_index=False)["close"].last()

    if volumes:
        vol_df = pd.DataFrame(volumes, columns=["ts", "volume"])
        vol_df["date"] = pd.to_datetime(vol_df["ts"], unit="ms").dt.strftime("%Y-%m-%d")
        vol_df = vol_df.groupby("date", as_index=False)["volume"].last()
        price_df = price_df.merge(vol_df, on="date", how="left")
    else:
        price_df["volume"] = 0

    price_df = price_df.sort_values("date").reset_index(drop=True)
    price_df["open"] = price_df["close"].shift(1).fillna(price_df["close"])
    price_df["high"] = price_df[["open", "close"]].max(axis=1)
    price_df["low"]  = price_df[["open", "close"]].min(axis=1)
    price_df["ticker"] = ticker
    price_df["volume"] = price_df["volume"].fillna(0)

    return price_df[["ticker", "date", "open", "high", "low", "close", "volume"]]


def fetch_top50_history_batch(
    top50: pd.DataFrame, days: int = MAX_HISTORY_DAYS, sleep_sec: float = 6.0,
    progress_cb=None,
) -> dict[str, pd.DataFrame]:
    """Ruft fetch_history() nacheinander für alle Zeilen von top50 ab, mit
    höflicher Pause zwischen den Requests. Das CoinGecko-Free-Tier ohne
    Demo-Key limitiert grob auf ~5-15 Calls/Minute — 1.3s Pause (frühere
    Version) reißt dieses Limit bei 50 Coins zuverlässig gegen Ende des
    Batches (beobachtet: nur ~7 von 50 erfolgreich, Rest 429), 6s pro Call
    (~5min für alle 50) bleibt sicher darunter; fetch_history() versucht bei
    einem 429 zusätzlich einmal automatisch erneut. progress_cb(i, n, ticker),
    falls übergeben, wird vor jedem Abruf aufgerufen (z.B. für einen
    Streamlit-Fortschrittsindikator). Gibt {ticker: DataFrame} zurück, leere
    DataFrames für endgültig fehlgeschlagene Coins."""
    result: dict[str, pd.DataFrame] = {}
    rows = top50.to_dict("records")
    for i, row in enumerate(rows):
        if progress_cb:
            progress_cb(i, len(rows), row["ticker"])
        result[row["ticker"]] = fetch_history(row["id"], row["ticker"], days=days)
        if i < len(rows) - 1:
            time.sleep(sleep_sec)
    return result


def fetch_fear_greed(limit: int = 2) -> list[dict]:
    """Crypto Fear & Greed Index (alternative.me, kein API-Key nötig) —
    krypto-WEITES Stimmungsbarometer (0 = Extreme Fear, 100 = Extreme Greed),
    NICHT coin-spezifisch, daher separat von der Top-50-Tabelle einmalig
    angezeigt statt je Coin. Gibt eine Liste der letzten `limit` Tageswerte
    zurück, neuester zuerst; leere Liste bei Netzwerkfehler."""
    try:
        resp = requests.get(FNG_URL, params={"limit": limit}, headers={"Accept": "application/json"}, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json().get("data") or []
    except Exception as e:
        logger.warning("Fear-&-Greed-Abruf fehlgeschlagen: %s", e)
        return []

    out = []
    for d in data:
        try:
            ts = datetime.fromtimestamp(int(d["timestamp"]), tz=timezone.utc)
            out.append({
                "value":          int(d["value"]),
                "classification": d["value_classification"],
                "date":           ts.strftime("%Y-%m-%d"),
            })
        except (KeyError, ValueError, TypeError):
            continue
    return out


def fetch_funding_rates() -> dict[str, float]:
    """Aktuelle Funding-Rate (Perpetual Futures) für ALLE auf Binance Futures
    gelisteten USDT-Perpetuals, in EINEM Bulk-Request (kein API-Key nötig,
    öffentlicher Endpunkt) — deutlich günstiger als 50 Einzel-Requests je
    Coin. Gibt {SYMBOL: funding_rate} zurück (SYMBOL ohne 'USDT'-Suffix, z.B.
    'BTC' statt 'BTCUSDT', passend zum Top-50-'symbol'-Feld), leeres dict bei
    Netzwerkfehler. Coins ohne gelisteten Perpetual (z.B. Stablecoins wie
    USDT/USDC/DAI) fehlen im Ergebnis — Aufrufer muss das abfangen.

    Funding Rate > 0 heißt: Long-Positionen zahlen Short-Positionen (Markt
    überwiegend long positioniert), < 0 umgekehrt — ein Positionierungs-/
    Crowding-Indikator aus dem Derivatemarkt, unabhängig von der
    Chart-basierten Elliott-Wellen-/RSI-Analyse."""
    try:
        resp = requests.get(f"{BINANCE_FAPI_URL}/premiumIndex", timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("Binance Funding-Rate-Abruf fehlgeschlagen: %s", e)
        return {}

    out: dict[str, float] = {}
    for row in data:
        sym = row.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        try:
            out[sym[:-4]] = float(row["lastFundingRate"])
        except (TypeError, ValueError, KeyError):
            continue
    return out


def fetch_klines(ticker: str, interval: str = "15m", limit: int = 500) -> pd.DataFrame:
    """Intraday-OHLCV-Kerzen für Daytrading-Analysen — bewusst Binance statt
    CoinGecko: CoinGecko liefert im Free-Tier keine Minuten-/15-Min-Granularität
    (nur Tages-Daten bzw. auf 1 Tag beschränkt max. 5-Min-Punkte, und auch nur
    mit synthetisiertem OHLC aus Schlusskursen, siehe fetch_history()-Doc).
    Binance liefert dagegen echte OHLC-Kerzen für jedes Intervall, öffentlich
    und ohne API-Key, mit großzügigem Rate-Limit.

    ticker im üblichen '<SYMBOL>-USD'-Schema (z.B. 'BTC-USD'); wird intern auf
    das Binance-Spot-Paar '<SYMBOL>USDT' gemappt. Nicht jeder CoinGecko-
    Top-50-Coin hat ein Binance-USDT-Paar (z.B. manche Wrapped-/Stablecoin-
    Varianten) — gibt dann ein leeres DataFrame zurück, das der Aufrufer
    abfangen muss. limit=500 Kerzen ist Binance' Standard-Seitengröße (max.
    1000 pro Request, hier bewusst darunter für schnellere Antwortzeiten)."""
    symbol = ticker.replace("-USD", "").upper() + "USDT"
    try:
        resp = requests.get(
            f"{BINANCE_SPOT_URL}/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 400:
            return pd.DataFrame()  # kein Handelspaar mit diesem Symbol
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("Binance Klines-Abruf fehlgeschlagen für %s (%s): %s", ticker, symbol, e)
        return pd.DataFrame()

    if not data:
        return pd.DataFrame()

    df = pd.DataFrame(data, columns=[
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
    ])
    df["date"] = pd.to_datetime(df["open_time"], unit="ms")
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    df["ticker"] = ticker
    return df[["ticker", "date", "open", "high", "low", "close", "volume"]]


def fetch_open_interest(symbol: str) -> dict | None:
    """Offene Kontrakte (Open Interest, in Coin-Einheiten) für GENAU EINEN
    Perpetual (z.B. Symbol 'BTC' -> Binance-Paar 'BTCUSDT') — bewusst nur für
    den in der Coin-Detail-Ansicht gerade ausgewählten Coin abgerufen statt
    für alle 50 (Binance bietet dafür keinen Bulk-Endpunkt wie premiumIndex,
    50 Einzel-Requests je Tabellen-Render wären unnötig teuer). None, wenn
    kein Perpetual für dieses Symbol existiert oder der Abruf fehlschlägt."""
    try:
        resp = requests.get(
            f"{BINANCE_FAPI_URL}/openInterest",
            params={"symbol": f"{symbol}USDT"}, timeout=_TIMEOUT,
        )
        if resp.status_code == 400:
            return None  # Symbol nicht als Perpetual gelistet
        resp.raise_for_status()
        data = resp.json()
        return {"symbol": data["symbol"], "open_interest": float(data["openInterest"])}
    except Exception as e:
        logger.warning("Binance Open-Interest-Abruf fehlgeschlagen für %s: %s", symbol, e)
        return None
