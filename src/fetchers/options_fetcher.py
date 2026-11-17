"""
Optionsfluss-Snapshot je Ticker.

Hinweis zur Methodik: yfinance liefert nur die aktuelle Optionsketten-
Momentaufnahme (Volumen/Open-Interest der nächsten Verfallstermine), keinen
historischen Order-Flow (Sweeps, Blocktrades, Dark-Pool-Prints) — echte
"Smart-Money"-Flow-Daten sind i.d.R. kostenpflichtig (z.B. Unusual Whales,
Cboe LiveVol). Als kostenlose Annäherung wird deshalb pro Kontrakt das
Verhältnis von Tagesvolumen zu bestehendem Open Interest herangezogen: ein
Volumen deutlich über dem Open Interest deutet auf frisch eröffnete
Positionen an diesem Tag hin statt auf simples Schließen bestehender
Positionen.
"""
import logging
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

_UNUSUAL_MIN_VOLUME = 100        # Mindestvolumen, filtert Rauschen aus Mikro-Strikes
_UNUSUAL_VOL_OI_RATIO = 2.0      # Volumen >= 2x Open Interest gilt als auffällig


def _side_summary(df: pd.DataFrame, opt_type: str, expiration: str) -> tuple[float, float, list[dict]]:
    d = df[["strike", "volume", "openInterest"]].fillna(0)
    total_vol = float(d["volume"].sum())
    total_oi = float(d["openInterest"].sum())

    unusual_mask = (d["volume"] >= _UNUSUAL_MIN_VOLUME) & (
        (d["openInterest"] == 0) | (d["volume"] >= d["openInterest"] * _UNUSUAL_VOL_OI_RATIO)
    )
    unusual = [
        {
            "expiration": expiration, "type": opt_type, "strike": float(r.strike),
            "volume": float(r.volume), "open_interest": float(r.openInterest),
            "vol_oi_ratio": (float(r.volume) / r.openInterest) if r.openInterest else None,
        }
        for r in d[unusual_mask].itertuples(index=False)
    ]
    return total_vol, total_oi, unusual


def get_options_snapshot(ticker: str, max_expirations: int = 2, top_n: int = 8) -> dict:
    """Aggregiertes Put/Call-Bild + auffällige Einzel-Strikes über die
    nächsten `max_expirations` Verfallstermine. {'available': False} für
    Ticker ohne gehandelte Optionen (z.B. viele Indizes/ausländische Werte)
    oder bei Netzwerkfehlern."""
    try:
        t = yf.Ticker(ticker)
        expirations = t.options
        if not expirations:
            return {"available": False}

        cutoff = datetime.today() + timedelta(days=60)
        near = [e for e in expirations if datetime.strptime(e, "%Y-%m-%d") <= cutoff]
        used = (near or list(expirations))[:max_expirations]

        call_vol = put_vol = call_oi = put_oi = 0.0
        unusual: list[dict] = []
        for exp in used:
            chain = t.option_chain(exp)
            cv, coi, cu = _side_summary(chain.calls, "call", exp)
            pv, poi, pu = _side_summary(chain.puts, "put", exp)
            call_vol += cv
            call_oi += coi
            put_vol += pv
            put_oi += poi
            unusual += cu + pu

        unusual.sort(key=lambda x: x["volume"], reverse=True)

        return {
            "available": True,
            "expirations_used": used,
            "call_volume": call_vol,
            "put_volume": put_vol,
            "pcr_volume": (put_vol / call_vol) if call_vol else None,
            "call_oi": call_oi,
            "put_oi": put_oi,
            "pcr_oi": (put_oi / call_oi) if call_oi else None,
            "unusual": unusual[:top_n],
        }
    except Exception as e:
        logger.warning("Optionsfluss-Abruf fehlgeschlagen für %s: %s", ticker, e)
        return {"available": False}
