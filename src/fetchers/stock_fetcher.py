import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

# Regulärer Abruf (jeder Lauf) vs. einmalige lange Historie je Ticker
# (bzw. erneut nach erkanntem Split, siehe src/database/price_history.py).
# Die lange Historie macht Auswertungen über mehrere Marktphasen möglich.
UPDATE_DAYS  = 400
HISTORY_DAYS = 3650


def fetch_prices(ticker: str, days: int = 365) -> pd.DataFrame:
    """Fetch daily OHLCV from Yahoo Finance. Returns empty DataFrame on failure."""
    try:
        end   = datetime.today()
        start = end - timedelta(days=days)
        df = yf.download(
            ticker, start=start, end=end,
            progress=False, auto_adjust=True,
        )
        if df.empty:
            return pd.DataFrame()

        df = df.reset_index()

        # yfinance >= 0.2.x sometimes returns MultiIndex columns
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [str(col[0]).lower() for col in df.columns]
        else:
            df.columns = [str(col).lower() for col in df.columns]

        df = df.rename(columns={"index": "date"})
        df["ticker"] = ticker
        df["date"]   = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

        cols = [c for c in ["ticker", "date", "open", "high", "low", "close", "volume"] if c in df.columns]
        return df[cols].dropna()
    except Exception as e:
        logger.warning("Price fetch failed for %s: %s", ticker, e)
        return pd.DataFrame()


def fetch_prices_batch(tickers: list[str], days: int = 365) -> dict[str, pd.DataFrame]:
    """Fetch daily OHLCV for multiple tickers in a single batched request.
    Much faster than calling fetch_prices() in a loop (one HTTP round-trip
    instead of one per ticker). Returns {ticker: DataFrame}; tickers with
    no data map to an empty DataFrame."""
    if not tickers:
        return {}
    try:
        end   = datetime.today()
        start = end - timedelta(days=days)
        raw = yf.download(
            tickers, start=start, end=end,
            progress=False, auto_adjust=True,
            group_by="ticker", threads=True,
        )
    except Exception as e:
        logger.warning("Batch price fetch failed for %d tickers: %s", len(tickers), e)
        return {t: pd.DataFrame() for t in tickers}

    result: dict[str, pd.DataFrame] = {}
    for t in tickers:
        if isinstance(raw.columns, pd.MultiIndex):
            if t not in raw.columns.get_level_values(0):
                result[t] = pd.DataFrame()
                continue
            df = raw[t]
        else:
            df = raw

        df = df.dropna(how="all")
        if df.empty:
            result[t] = pd.DataFrame()
            continue

        df = df.reset_index()
        df.columns = [str(c).lower() for c in df.columns]
        df = df.rename(columns={"index": "date"})
        df["ticker"] = t
        df["date"]   = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

        cols = [c for c in ["ticker", "date", "open", "high", "low", "close", "volume"] if c in df.columns]
        result[t] = df[cols].dropna()
    return result


def get_ticker_info(ticker: str) -> dict:
    """Fundamental snapshot for a single ticker (name, sector, valuation)."""
    try:
        info = yf.Ticker(ticker).info
        return {
            "name":            info.get("longName") or info.get("shortName", ticker),
            "sector":          info.get("sector", ""),
            "industry":        info.get("industry", ""),
            "currency":        info.get("currency", "USD"),
            "market_cap":      info.get("marketCap"),
            "pe_trailing":     info.get("trailingPE"),
            "pe_forward":      info.get("forwardPE"),
            "dividend_yield":  info.get("dividendYield"),
            "target_mean":     info.get("targetMeanPrice"),
            "recommendation":  info.get("recommendationKey"),
        }
    except Exception:
        return {"name": ticker, "sector": "", "currency": ""}
