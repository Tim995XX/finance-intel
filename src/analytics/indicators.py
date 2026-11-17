"""
Klassische technische Indikatoren (SMA, EMA, RSI, MACD) — rein preisbasiert,
im Gegensatz zur regelbasierten Struktur-Erkennung in elliott_wave.py. Dienen
als einfache, unabhängige zweite Bestätigung/Widerlegung eines Wellen- oder
Insider-Signals.
"""
import pandas as pd


def sma(close: pd.Series, window: int) -> pd.Series:
    return close.rolling(window=window, min_periods=window).mean()


def ema(close: pd.Series, window: int) -> pd.Series:
    return close.ewm(span=window, adjust=False, min_periods=window).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI via exponentieller Glättung (Standardvariante, kein
    simpler SMA der Gains/Losses)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss
    out = 100 - (100 / (1 + rs))
    out = out.mask(avg_loss == 0, 100.0)
    out = out.mask((avg_gain == 0) & (avg_loss == 0), 50.0)
    return out


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Gibt (macd_line, signal_line, histogram) zurück."""
    ema_fast = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def compute_indicators(
    prices: pd.DataFrame,
    sma_opt: bool = False,
    ema_opt: bool = False,
    rsi_opt: bool = False,
    macd_opt: bool = False,
) -> dict:
    """Berechnet nur die angeforderten Indikatoren, ausgerichtet auf
    prices['date']/prices['close']. Ein leeres Dict bedeutet: keine Overlays/
    Panels im Chart. Jeder Eintrag trägt seine eigenen 'dates', damit der
    Chart-Code nicht erneut mit prices['date'] abgleichen muss."""
    out: dict = {}
    if prices.empty:
        return out
    close = prices["close"]
    dates = prices["date"]

    if sma_opt:
        out["sma20"] = {"dates": dates, "values": sma(close, 20)}
        out["sma50"] = {"dates": dates, "values": sma(close, 50)}
    if ema_opt:
        out["ema20"] = {"dates": dates, "values": ema(close, 20)}
    if rsi_opt:
        out["rsi"] = {"dates": dates, "values": rsi(close, 14)}
    if macd_opt:
        macd_line, signal_line, hist = macd(close)
        out["macd"] = {"dates": dates, "macd": macd_line, "signal": signal_line, "hist": hist}
    return out
