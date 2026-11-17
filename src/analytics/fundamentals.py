"""
Fundamentaldaten-Scoring nach drei veröffentlichten Methoden:

  - Piotroski F-Score (Piotroski 2000): 9 binäre Bilanz-/GuV-Signale aus
    Profitabilität, Verschuldung/Liquidität, operativer Effizienz -> 0-9.
  - Novy-Marx Gross Profitability (2013): Bruttogewinn / Bilanzsumme als
    robusteste Näherung an die ökonomische Kapitalrendite (ROIC).
  - Fama-French-Proxy (Fama/French 2015): Value (KBV), Profitabilität
    (operative Marge) und Investment (Bilanzsummen-Wachstum) - anders als
    im Original-Paper hier nicht gegen den Gesamtmarkt, sondern als
    Perzentil-Rang INNERHALB der übergebenen Ticker-Menge (z.B. der
    aktuellen Watchlist) berechnet. Das ist methodisch näher am Original
    als feste Schwellenwerte (z.B. "KBV < 3"), weil auch die akademischen
    Faktor-Portfolios relativ zum jeweiligen Anlage-Universum sortieren -
    aber mit einem kleineren, nicht repräsentativen Universum als der
    Gesamtmarkt. Bei sehr wenigen Tickern (< 5) sind die Perzentile
    entsprechend wackelig.

Nur für Einzelaktien sinnvoll (Bilanz-/GuV-Historie nötig) - ETFs, Indizes
und Krypto liefern keine auswertbaren Jahresabschlüsse und werden mit
data_ok=False markiert statt mit einer (falschen) schlechten Bewertung.

Kein ML - reine, dokumentierte Kennzahlen-Berechnung. Ergebnisse werden in
fundamentals_raw gecacht (Fundamentaldaten ändern sich nur quartalsweise),
damit weder das Dashboard noch main.py bei jedem Lauf hunderte
yfinance-Requests auslösen.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

MAX_AGE_DAYS = 7  # Fundamentaldaten ändern sich nur quartalsweise -> Cache-Gültigkeit

F_SCORE_LABELS = {
    "roa_positive":       "ROA > 0",
    "cfo_positive":       "Operativer Cashflow > 0",
    "roa_improving":      "ROA gestiegen ggü. Vorjahr",
    "accruals":           "Operativer Cashflow > Nettogewinn (Ergebnisqualität)",
    "leverage_down":      "Verschuldungsgrad gesunken",
    "current_ratio_up":   "Liquidität 3. Grades gestiegen",
    "no_dilution":        "Keine Kapitalverwässerung (Aktienzahl nicht gestiegen)",
    "gross_margin_up":    "Bruttomarge gestiegen",
    "asset_turnover_up":  "Kapitalumschlag gestiegen",
}


def _row(df: pd.DataFrame | None, *candidates: str) -> pd.Series | None:
    """Sucht die erste vorhandene Zeile aus mehreren möglichen yfinance-
    Zeilennamen (die je nach Ticker/Version leicht variieren, z.B.
    'Long Term Debt' vs 'Long Term Debt And Capital Lease Obligation')."""
    if df is None or df.empty:
        return None
    for name in candidates:
        if name in df.index:
            return df.loc[name]
    return None


def _at(series: pd.Series | None, pos: int) -> float | None:
    if series is None or pos >= len(series):
        return None
    v = series.iloc[pos]
    if pd.isna(v):
        return None
    return float(v)


def fetch_raw_fundamentals(ticker: str) -> dict:
    """Holt die letzten zwei Geschäftsjahre aus yfinance und leitet daraus
    alle Rohwerte für Piotroski-F-Score, Gross-Profitability und den
    Fama-French-Proxy ab. data_ok=False, wenn keine zwei vollständigen
    Jahresabschlüsse vorliegen (ETF/Index/Krypto/Neubörsengang) oder ein
    für den F-Score benötigter Posten fehlt."""
    try:
        t    = yf.Ticker(ticker)
        fin  = t.financials       # Spalten = Geschäftsjahre, neuestes zuerst laut yfinance
        bs   = t.balance_sheet
        cf   = t.cashflow
        info = t.info
    except Exception as e:
        logger.warning("Fundamentals-Abruf fehlgeschlagen für %s: %s", ticker, e)
        return {"ticker": ticker, "data_ok": False, "data_note": f"Abruf fehlgeschlagen: {e}"}

    if fin is None or fin.empty or bs is None or bs.empty or fin.shape[1] < 2 or bs.shape[1] < 2:
        return {
            "ticker": ticker, "data_ok": False,
            "data_note": "Keine zwei Jahresabschlüsse verfügbar (kein Einzelunternehmen "
                         "wie ETF/Index/Krypto, oder zu kurze Börsenhistorie)",
        }

    net_income   = _row(fin, "Net Income")
    revenue      = _row(fin, "Total Revenue", "Operating Revenue")
    gross_profit = _row(fin, "Gross Profit")
    op_income    = _row(fin, "Operating Income", "Total Operating Income As Reported")

    total_assets = _row(bs, "Total Assets")
    curr_assets  = _row(bs, "Current Assets")
    curr_liab    = _row(bs, "Current Liabilities")
    lt_debt      = _row(bs, "Long Term Debt", "Long Term Debt And Capital Lease Obligation")
    shares_out   = _row(bs, "Ordinary Shares Number", "Share Issued")

    cfo = _row(cf, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities")

    ni0, ni1     = _at(net_income, 0), _at(net_income, 1)
    rev0, rev1   = _at(revenue, 0), _at(revenue, 1)
    gp0, gp1     = _at(gross_profit, 0), _at(gross_profit, 1)
    opinc0       = _at(op_income, 0)

    ta0, ta1     = _at(total_assets, 0), _at(total_assets, 1)
    ca0, ca1     = _at(curr_assets, 0), _at(curr_assets, 1)
    cl0, cl1     = _at(curr_liab, 0), _at(curr_liab, 1)
    ltd0, ltd1   = _at(lt_debt, 0), _at(lt_debt, 1)
    so0, so1     = _at(shares_out, 0), _at(shares_out, 1)

    cfo0 = _at(cfo, 0)

    required = [ni0, ni1, rev0, rev1, gp0, gp1, opinc0, ta0, ta1, ca0, ca1, cl0, cl1,
                ltd0, ltd1, so0, so1, cfo0]
    if any(v is None for v in required) or ta0 == 0 or ta1 == 0 or rev0 == 0 or rev1 == 0 \
            or cl0 == 0 or cl1 == 0:
        return {
            "ticker": ticker, "data_ok": False,
            "data_note": "Einzelne Bilanz-/GuV-Posten fehlen bei yfinance für diesen Ticker "
                         "(F-Score braucht alle 9 Signale vollständig)",
        }

    roa0, roa1   = ni0 / ta0, ni1 / ta1
    lev0, lev1   = ltd0 / ta0, ltd1 / ta1
    cr0, cr1     = ca0 / cl0, ca1 / cl1
    gm0, gm1     = gp0 / rev0, gp1 / rev1
    at0, at1     = rev0 / ta0, rev1 / ta1

    f_signals = {
        "roa_positive":      roa0 > 0,
        "cfo_positive":      cfo0 > 0,
        "roa_improving":     roa0 > roa1,
        "accruals":          cfo0 > ni0,
        "leverage_down":     lev0 < lev1,
        "current_ratio_up":  cr0 > cr1,
        "no_dilution":       so0 <= so1 * 1.001,   # kleine Toleranz gegen Rundungsrauschen
        "gross_margin_up":   gm0 > gm1,
        "asset_turnover_up": at0 > at1,
    }
    f_score = sum(f_signals.values())

    return {
        "ticker":              ticker,
        "data_ok":             True,
        "data_note":           None,
        "price_to_book":       info.get("priceToBook"),
        "operating_margin":    opinc0 / rev0,
        "asset_growth":        (ta0 / ta1) - 1,
        "gross_profitability": gp0 / ta0,           # Novy-Marx GP/A
        "f_score":             int(f_score),
        "f_score_detail":      json.dumps(f_signals),
    }


def refresh_fundamentals(
    conn: sqlite3.Connection, tickers: list[str],
    max_age_days: int = MAX_AGE_DAYS, force: bool = False,
) -> dict:
    """Aktualisiert fundamentals_raw für alle übergebenen Ticker, deren
    Cache-Eintrag fehlt oder älter als max_age_days ist. Committet selbst
    (im Gegensatz zu den meisten anderen DB-Funktionen dieses Projekts),
    damit ein Abbruch mitten im Batch die bereits abgerufenen Ticker nicht
    verwirft - bei 100+ Tickern und einem externen API-Call pro Ticker ist
    ein Teilabbruch der Normalfall, kein Ausnahmefall."""
    today = datetime.today().date()
    cutoff = str(today - timedelta(days=max_age_days))

    if force:
        stale = tickers
    else:
        fresh = {
            r["ticker"] for r in conn.execute(
                f"SELECT ticker FROM fundamentals_raw WHERE computed_date >= ? "
                f"AND ticker IN ({','.join('?' * len(tickers))})",
                [cutoff, *tickers],
            ).fetchall()
        } if tickers else set()
        stale = [t for t in tickers if t not in fresh]

    refreshed, failed = 0, 0
    for ticker in stale:
        data = fetch_raw_fundamentals(ticker)
        conn.execute(
            """INSERT INTO fundamentals_raw
                   (ticker, computed_date, data_ok, data_note, price_to_book,
                    operating_margin, asset_growth, gross_profitability,
                    f_score, f_score_detail, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(ticker) DO UPDATE SET
                   computed_date=excluded.computed_date, data_ok=excluded.data_ok,
                   data_note=excluded.data_note, price_to_book=excluded.price_to_book,
                   operating_margin=excluded.operating_margin,
                   asset_growth=excluded.asset_growth,
                   gross_profitability=excluded.gross_profitability,
                   f_score=excluded.f_score, f_score_detail=excluded.f_score_detail,
                   updated_at=CURRENT_TIMESTAMP""",
            (
                data["ticker"], str(today), int(data["data_ok"]), data.get("data_note"),
                data.get("price_to_book"), data.get("operating_margin"),
                data.get("asset_growth"), data.get("gross_profitability"),
                data.get("f_score"), data.get("f_score_detail"),
            ),
        )
        conn.commit()
        if data["data_ok"]:
            refreshed += 1
        else:
            failed += 1

    return {"checked": len(tickers), "refreshed": refreshed, "no_data": failed,
            "skipped_fresh": len(tickers) - len(stale)}


def load_scores(conn: sqlite3.Connection, tickers: list[str]) -> pd.DataFrame:
    """Lädt die gecachten Rohwerte für die übergebenen Ticker und berechnet
    daraus die drei Perzentil-Scores (Value, Profitabilität, Investment ->
    Fama-French-Composite; Gross-Profitability -> Quality) sowie den
    Piotroski-F-Score. fundamental_score ist der Mittelwert aus FF-Composite,
    F-Score (auf 0-100 normiert) und Quality-Perzentil - eine einzelne Zahl
    zum Sortieren/Filtern. Ticker ohne verwertbare Daten (ETF/Index/Krypto)
    bekommen NaN/None statt einer erfundenen Bewertung, damit ein
    Vorfilter sie bewusst durchlassen statt fälschlich ausschließen kann."""
    if not tickers:
        return pd.DataFrame()
    df = pd.read_sql(
        f"SELECT * FROM fundamentals_raw WHERE ticker IN ({','.join('?' * len(tickers))})",
        conn, params=tickers,
    )
    # Ticker ganz ohne Cache-Eintrag (noch nie abgerufen) als data_ok=0 ergänzen
    missing = set(tickers) - set(df["ticker"]) if not df.empty else set(tickers)
    if missing:
        df = pd.concat([df, pd.DataFrame([{"ticker": t, "data_ok": 0} for t in missing])],
                        ignore_index=True)

    ok = df["data_ok"] == 1
    n_universe = int(ok.sum())

    for col in ["value_pctl", "profitability_pctl", "investment_pctl",
                "quality_pctl", "ff_composite", "fundamental_score"]:
        df[col] = None

    if n_universe >= 2:
        sub = df.loc[ok]
        # Niedriges KBV / niedriges Bilanzsummen-Wachstum = besser -> ascending=False,
        # damit der kleinste Rohwert das höchste Perzentil bekommt.
        df.loc[ok, "value_pctl"]          = sub["price_to_book"].rank(pct=True, ascending=False) * 100
        df.loc[ok, "profitability_pctl"]  = sub["operating_margin"].rank(pct=True) * 100
        df.loc[ok, "investment_pctl"]     = sub["asset_growth"].rank(pct=True, ascending=False) * 100
        df.loc[ok, "quality_pctl"]        = sub["gross_profitability"].rank(pct=True) * 100

        ff_cols = ["value_pctl", "profitability_pctl", "investment_pctl"]
        df.loc[ok, "ff_composite"] = df.loc[ok, ff_cols].astype(float).mean(axis=1)

        f_score_pct = sub["f_score"].astype(float) / 9 * 100
        df.loc[ok, "fundamental_score"] = (
            df.loc[ok, "ff_composite"].astype(float)
            + f_score_pct.reindex(df.loc[ok].index)
            + df.loc[ok, "quality_pctl"].astype(float)
        ) / 3

    df.attrs["n_universe"] = n_universe
    return df


def filter_promising(
    df_scores: pd.DataFrame, min_score: float,
) -> tuple[list[str], list[str]]:
    """Teilt eine load_scores()-Tabelle in (eingeschlossen, ausgeschlossen)
    auf. Ticker ohne Fundamentaldaten (fundamental_score ist None -> ETF/
    Index/Krypto oder noch nicht abgerufen) werden NICHT ausgeschlossen,
    weil die drei zugrundeliegenden Studien nur für Einzelaktien gelten -
    ein Vorfilter, der Krypto pauschal rausfiltert, wäre eine unbeabsichtigte
    Nebenwirkung statt eine bewusste Entscheidung."""
    included, excluded = [], []
    for _, r in df_scores.iterrows():
        score = r["fundamental_score"]
        if score is None or (isinstance(score, float) and pd.isna(score)) or score >= min_score:
            included.append(r["ticker"])
        else:
            excluded.append(r["ticker"])
    return included, excluded
