"""
Fetcher für Insider-Trades:
  - US Senat & Repräsentantenhaus via Financial Modeling Prep (kostenloser API-Key)
  - ARK Invest ETFs (Cathie Wood) via arkfunds.io (kein Key nötig)
  - Trump-Familie via SEC-EDGAR Form-4-Meldungen von Trump Media & Technology
    Group (DJT) (kein Key nötig)

Konfiguration:
  FMP-Key in Datei '.insider_config.json' im Projektroot:
    {"fmp_api_key": "DEIN_KEY"}
  Oder Umgebungsvariable FMP_API_KEY setzen.

Hinweis zur FMP-API: seit August 2025 sind die alten /api/v3 und /api/v4
Endpunkte abgeschaltet, verwendet werden die neuen /stable-Endpunkte
(senate-latest, house-latest). Die kostenlose Stufe erlaubt dort keine
Pagination mehr (nur page=0) — pro Abruf kommen die ca. 100 aktuellsten
Offenlegungen zurück, kein Rückblick weiter in die Vergangenheit.

Jeder Trade trägt neben dem Handelstag (trade_date) den Tag, an dem er
öffentlich wurde (disclosure_date). Congress-Mitglieder dürfen bis zu 45 Tage
nach dem Handel melden — wer Trades als Signal auswertet, darf erst ab
disclosure_date einsteigen, sonst rechnet er mit Wissen aus der Zukunft.

Hinweis zu DJT-Insider-Trades: Trump selbst hat kein eigenes börsennotiertes
Gesamtportfolio-Reporting in Echtzeit — sein breiteres Portfolio wird nur alle
~45 Tage per OGE Form 278-T offengelegt. Näher an Echtzeit sind Form-4-Meldungen
von Insidern seiner eigenen Firma Trump Media & Technology Group (DJT): die
müssen innerhalb von 2 Handelstagen nach Transaktion bei der SEC eingereicht
werden. Wir holen die vollständige DJT-Insider-Liste und filtern anschließend
auf Meldepflichtige mit "TRUMP" im Namen (Familie), keine Fremdinsider.
"""
import json
import logging
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, */*",
}

# SEC verlangt laut Fair-Access-Policy eine aussagekräftige User-Agent-Kennung
# (Name/Zweck + Kontakt) statt eines gefälschten Browser-UA.
_SEC_HEADERS = {
    "User-Agent": "finance-intel personal-project (contact: local-user@localhost)",
    "Accept": "application/json, text/html, application/xml, */*",
}

# Trump Media & Technology Group Corp. (Ticker DJT) — SEC-Issuer-CIK
DJT_ISSUER_CIK = "0001849635"

_ROOT = Path(__file__).parent.parent.parent
_CONFIG_FILE = _ROOT / ".insider_config.json"

# Personen aus Trumps engerem Umfeld (im Dashboard unter "Trump-Kreis"
# hervorgehoben). Zwei Gruppen:
#   1. Congress-Mitglieder mit enger Trump-Verbindung — deren Trades laufen
#      über die normalen Senat/House-Quellen (FMP).
#   2. Trump-Administration (Kabinett + Vizepräsident, Stand 2026) — einige
#      davon waren zuvor im Congress (z.B. Rubio, Vance, Mullin, Chavez-DeRemer,
#      Duffy, Collins) und haben dadurch ggf. historische Senat/House-Trades in
#      der DB; für die übrigen Kabinettsmitglieder gibt es keine automatisierte
#      Trade-Quelle (kein STOCK-Act-Reporting wie im Congress), der Name dient
#      hier nur der Kennzeichnung, falls doch einmal Daten auftauchen.
TRUMP_CIRCLE = {
    # Congress
    "Tommy Tuberville", "Rand Paul", "Ted Cruz", "Josh Hawley",
    "Rick Scott", "Marjorie Taylor Greene", "Jim Jordan",
    "Matt Gaetz", "Lauren Boebert", "Ron Johnson",
    "Mike Johnson", "Steve Scalise", "Kevin McCarthy",
    "Lindsey Graham", "Tom Cotton", "Marco Rubio",
    "Pete Ricketts", "Roger Marshall", "Markwayne Mullin",
    # Trump-Administration (Kabinett + VP)
    "JD Vance", "Pete Hegseth", "Scott Bessent", "Pam Bondi",
    "Doug Burgum", "Brooke Rollins", "Howard Lutnick",
    "Lori Chavez-DeRemer", "Robert F. Kennedy Jr.", "Linda McMahon",
    "Chris Wright", "Scott Turner", "Sean Duffy", "Doug Collins",
}

ARK_FUNDS = ["ARKK", "ARKG", "ARKW", "ARKF", "ARKQ", "ARKX"]


# ─── Konfiguration ───────────────────────────────────────────────────────────

def get_fmp_key() -> str | None:
    """FMP-API-Key aus Umgebungsvariable oder Konfigurationsdatei laden."""
    key = os.environ.get("FMP_API_KEY", "").strip()
    if key:
        return key
    if _CONFIG_FILE.exists():
        try:
            cfg = json.loads(_CONFIG_FILE.read_text())
            return cfg.get("fmp_api_key", "").strip() or None
        except Exception:
            pass
    return None


def save_fmp_key(key: str) -> None:
    """FMP-Key in lokaler Konfigurationsdatei speichern."""
    cfg = {}
    if _CONFIG_FILE.exists():
        try:
            cfg = json.loads(_CONFIG_FILE.read_text())
        except Exception:
            pass
    cfg["fmp_api_key"] = key.strip()
    _CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    os.chmod(_CONFIG_FILE, 0o600)


# ─── Normalisierung ───────────────────────────────────────────────────────────

def _norm_trade_type(raw: str) -> str:
    raw = str(raw).lower()
    if any(x in raw for x in ("sale", "sell")):
        return "sell"
    if any(x in raw for x in ("purchase", "buy")):
        return "buy"
    if "exchange" in raw:
        return "exchange"
    return raw.strip() or "unknown"


def _norm_amount(raw: str) -> tuple[float | None, float | None]:
    """'$1,001 - $15,000' → (1001.0, 15000.0)"""
    import re
    nums = re.findall(r"[\d]+", str(raw).replace(",", "").replace("$", ""))
    vals = []
    for n in nums:
        try:
            vals.append(float(n))
        except ValueError:
            pass
    if len(vals) >= 2:
        return vals[0], vals[1]
    if len(vals) == 1:
        return vals[0], vals[0]
    return None, None


# ─── Congress-Trades (FMP) ────────────────────────────────────────────────────

def _fmp_trader_name(item: dict) -> str:
    name = f"{item.get('firstName') or ''} {item.get('lastName') or ''}".strip()
    return name or "Unknown"


def _fmp_filing_fields(item: dict) -> dict:
    """Felder, die mehrere Trades derselben Person am selben Tag in derselben
    Aktie unterscheiden (eigenes Depot vs. Ehepartner, verschiedene Meldungen)
    und den Tag der Veröffentlichung festhalten."""
    return {
        "owner":           str(item.get("owner") or "").strip(),
        "disclosure_date": str(item.get("disclosureDate") or "")[:10] or None,
        "filing_url":      str(item.get("link") or "").strip(),
    }


def fetch_senate_trades(api_key: str) -> list[dict]:
    """Senat-Trades via FMP /stable/senate-latest. Free-Tier erlaubt kein
    Pagination (nur page=0) — liefert die ca. 100 aktuellsten Offenlegungen."""
    trades = []
    try:
        r = requests.get(
            "https://financialmodelingprep.com/stable/senate-latest",
            params={"page": 0, "apikey": api_key},
            headers=_HEADERS, timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            logger.warning("FMP Senate API Fehler: %s", data)
            return trades
        for item in data:
            ticker = str(item.get("symbol") or "").strip().upper()
            if not ticker or ticker in ("N/A", "--", ""):
                continue
            lo, hi = _norm_amount(item.get("amount", ""))
            trades.append({
                "source":       "senate",
                "trader":       _fmp_trader_name(item),
                "ticker":       ticker,
                "company":      str(item.get("assetDescription") or ""),
                "trade_date":   str(item.get("transactionDate") or "")[:10],
                "trade_type":   _norm_trade_type(item.get("type", "")),
                "amount_min":   lo,
                "amount_max":   hi,
                "raw_amount":   str(item.get("amount", "")),
                "fund":         None,
                "shares_delta": None,
                **_fmp_filing_fields(item),
            })
    except Exception as e:
        logger.warning("FMP Senate Fehler: %s", e)
    return trades


def fetch_house_trades(api_key: str) -> list[dict]:
    """Repräsentantenhaus-Trades via FMP /stable/house-latest (gleiche
    Free-Tier-Einschränkung wie fetch_senate_trades)."""
    trades = []
    try:
        r = requests.get(
            "https://financialmodelingprep.com/stable/house-latest",
            params={"page": 0, "apikey": api_key},
            headers=_HEADERS, timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            logger.warning("FMP House API Fehler: %s", data)
            return trades
        for item in data:
            ticker = str(item.get("symbol") or "").strip().upper()
            if not ticker or ticker in ("N/A", "--", ""):
                continue
            lo, hi = _norm_amount(item.get("amount", ""))
            trades.append({
                "source":       "house",
                "trader":       _fmp_trader_name(item),
                "ticker":       ticker,
                "company":      str(item.get("assetDescription") or ""),
                "trade_date":   str(item.get("transactionDate") or item.get("disclosureDate") or "")[:10],
                "trade_type":   _norm_trade_type(item.get("type", "")),
                "amount_min":   lo,
                "amount_max":   hi,
                "raw_amount":   str(item.get("amount", "")),
                "fund":         None,
                "shares_delta": None,
                **_fmp_filing_fields(item),
            })
    except Exception as e:
        logger.warning("FMP House Fehler: %s", e)
    return trades


def fetch_congress_trades() -> tuple[list[dict], str | None]:
    """
    Lädt Congress-Trades (Senat + Repräsentantenhaus).
    Rückgabe: (trades_list, error_msg_or_None)
    """
    api_key = get_fmp_key()
    if not api_key:
        return [], "no_key"

    with ThreadPoolExecutor(max_workers=2) as pool:
        senate = pool.submit(fetch_senate_trades, api_key)
        house  = pool.submit(fetch_house_trades, api_key)
        return senate.result() + house.result(), None


# ─── ARK Invest (arkfunds.io) ─────────────────────────────────────────────────

def fetch_ark_trades(period: str = "3M") -> list[dict]:
    """
    Lädt ARK-ETF Trades von arkfunds.io.
    period: 1D, 1W, 1M, 3M, 1Y, ALL
    Liefert direkt Kauf/Verkauf-Deltas — kein eigener Snapshot-Vergleich nötig.
    Die sechs Fonds werden parallel abgefragt; Reihenfolge wie ARK_FUNDS.
    """
    with ThreadPoolExecutor(max_workers=len(ARK_FUNDS)) as pool:
        per_fund = pool.map(lambda fund: _fetch_ark_fund_trades(fund, period), ARK_FUNDS)
        return [t for fund_trades in per_fund for t in fund_trades]


def _fetch_ark_fund_trades(fund: str, period: str) -> list[dict]:
    trades = []
    try:
        r = requests.get(
            "https://arkfunds.io/api/v2/etf/trades",
            params={"symbol": fund, "period": period},
            headers=_HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        for item in data.get("trades", []):
            ticker = str(item.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            direction  = str(item.get("direction", "")).strip().lower()
            trade_date = str(item.get("date") or "")[:10]
            trades.append({
                "source":       "ark",
                "trader":       f"Cathie Wood / {fund}",
                "ticker":       ticker,
                "company":      str(item.get("company") or ""),
                "trade_date":   trade_date,
                "trade_type":   "buy" if direction == "buy" else "sell",
                "amount_min":   None,
                "amount_max":   None,
                "raw_amount":   "",
                "fund":         fund,
                "shares_delta": float(item.get("shares") or 0) * (1 if direction == "buy" else -1),
                # ARK veröffentlicht die Trades noch am Handelsabend
                "owner":           "",
                "disclosure_date": trade_date or None,
                "filing_url":      "",
            })
    except Exception as e:
        logger.warning("ARK %s Fehler: %s", fund, e)
    return trades


def fetch_ark_holdings() -> list[dict]:
    """Lädt aktuelle ARK-ETF Holdings von arkfunds.io (Fonds parallel)."""
    with ThreadPoolExecutor(max_workers=len(ARK_FUNDS)) as pool:
        per_fund = pool.map(_fetch_ark_fund_holdings, ARK_FUNDS)
        return [h for fund_holdings in per_fund for h in fund_holdings]


def _fetch_ark_fund_holdings(fund: str) -> list[dict]:
    holdings = []
    try:
        r = requests.get(
            "https://arkfunds.io/api/v2/etf/holdings",
            params={"symbol": fund},
            headers=_HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        for item in data.get("holdings", []):
            ticker = str(item.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            holdings.append({
                "fund":         fund,
                "ticker":       ticker,
                "company":      str(item.get("company") or ""),
                "shares":       float(item.get("shares") or 0),
                "market_value": float(item.get("market_value") or 0),
                "weight_pct":   float(item.get("weight") or 0),
                "snapshot_date": str(item.get("date") or "")[:10],
            })
    except Exception as e:
        logger.warning("ARK Holdings %s Fehler: %s", fund, e)
    return holdings


# ─── Trump-Familie (SEC EDGAR, Form 4 von Trump Media & Technology Group) ─────

# SEC Fair-Access-Policy: höchstens 10 Anfragen/Sekunde über alle Threads.
_SEC_MIN_INTERVAL = 0.125
_SEC_WORKERS      = 4
_sec_lock         = threading.Lock()
_sec_next_slot    = 0.0


def _sec_get(url: str, **kwargs) -> requests.Response:
    """requests.get mit globalem Ratenlimit für sec.gov (threadsicher)."""
    global _sec_next_slot
    with _sec_lock:
        now  = time.monotonic()
        wait = _sec_next_slot - now
        _sec_next_slot = max(now, _sec_next_slot) + _SEC_MIN_INTERVAL
    if wait > 0:
        time.sleep(wait)
    r = requests.get(url, headers=_SEC_HEADERS, timeout=15, **kwargs)
    r.raise_for_status()
    return r


def list_djt_form4_filings(count: int = 40) -> list[tuple[str, str]]:
    """(Index-URL, Einreichungsdatum) der jüngsten Form-4-Meldungen zu DJT.
    Der EDGAR-Filter type=4 ist ein Präfix-Filter und liefert auch 424B/425
    (Prospekte) — die werden hier aussortiert."""
    r = _sec_get(
        "https://www.sec.gov/cgi-bin/browse-edgar",
        params={
            "action": "getcompany", "CIK": DJT_ISSUER_CIK, "type": "4",
            "dateb": "", "owner": "include", "count": count, "output": "atom",
        },
    )
    filings = []
    for entry in re.findall(r"<entry>.*?</entry>", r.text, re.S):
        form = re.search(r"<filing-type>(.*?)</filing-type>", entry)
        href = re.search(r"<filing-href>(.*?)</filing-href>", entry)
        date = re.search(r"<filing-date>(.*?)</filing-date>", entry)
        if form and href and form.group(1).strip() in ("4", "4/A"):
            filings.append((href.group(1).strip(), date.group(1).strip() if date else ""))
    return filings


def parse_djt_filing(index_url: str, filing_date: str) -> list[dict]:
    """Lädt eine Form-4-Meldung und liefert ihre Transaktionen, falls der
    Meldepflichtige zur Familie Trump gehört (sonst leere Liste).
    Netzwerk-/Parsefehler werden durchgereicht, damit der Aufrufer die
    Meldung nicht als erledigt markiert."""
    idx = _sec_get(index_url)
    xml_paths = [
        p for p in re.findall(r'href="([^"]+\.xml)"', idx.text)
        if "xslF345X" not in p
    ]
    if not xml_paths:
        return []
    doc  = _sec_get("https://www.sec.gov" + xml_paths[0])
    root = ET.fromstring(doc.content)

    owner_name = (root.findtext(".//reportingOwner/reportingOwnerId/rptOwnerName") or "").strip()
    if "TRUMP" not in owner_name.upper():
        return []
    ticker  = (root.findtext(".//issuer/issuerTradingSymbol") or "DJT").strip().upper()
    company = (root.findtext(".//issuer/issuerName") or "").strip()

    trades = []
    for tx in root.findall(".//nonDerivativeTransaction"):
        trade_date = (tx.findtext("./transactionDate/value") or "")[:10]
        code       = (tx.findtext("./transactionCoding/transactionCode") or "").strip()
        ad_code    = (tx.findtext("./transactionAmounts/transactionAcquiredDisposedCode/value") or "").strip()
        shares_raw = tx.findtext("./transactionAmounts/transactionShares/value")
        price_raw  = tx.findtext("./transactionAmounts/transactionPricePerShare/value")
        if not trade_date or not shares_raw:
            continue
        shares = float(shares_raw)
        price  = float(price_raw) if price_raw else None
        value  = shares * price if price else None

        trades.append({
            "source":       "djt_insider",
            "trader":       owner_name.title(),
            "ticker":       ticker,
            "company":      company,
            "trade_date":   trade_date,
            "trade_type":   "buy" if ad_code == "A" else "sell",
            "amount_min":   value,
            "amount_max":   value,
            "shares_delta": shares * (1 if ad_code == "A" else -1),
            "fund":         f"Code {code}",
            "raw_amount":   f"{shares:,.0f} Stk. @ ${price:,.2f}" if price else f"{shares:,.0f} Stk.",
            "owner":           "",
            "disclosure_date": filing_date or None,
            "filing_url":      index_url,
        })
    return trades


def fetch_djt_family_trades(count: int = 40, skip_filings: frozenset[str] = frozenset()
                            ) -> tuple[list[dict], list[str]]:
    """
    Lädt die jüngsten Form-4-Insider-Meldungen von Trump Media & Technology
    Group (DJT) via SEC EDGAR und filtert auf Meldepflichtige mit "TRUMP" im
    Namen. Kein API-Key nötig. Meldefrist per SEC-Regel: 2 Handelstage nach
    Transaktion — das ist die zeitnahste öffentlich zugängliche, kostenlose
    Quelle für Trades von Trump/Familie (im Gegensatz zum ~45 Tage verzögerten
    OGE Form 278-T für sein Gesamtportfolio).

    skip_filings: Index-URLs, die schon früher vollständig verarbeitet wurden
    (eine eingereichte Meldung ändert sich nicht mehr; Korrekturen kommen als
    eigene 4/A-Meldung). Rückgabe: (Trades, erfolgreich verarbeitete URLs) —
    fehlgeschlagene Meldungen fehlen in der URL-Liste und werden beim nächsten
    Lauf erneut versucht.
    """
    try:
        filings = [f for f in list_djt_form4_filings(count) if f[0] not in skip_filings]
    except Exception as e:
        logger.warning("SEC EDGAR DJT-Feed Fehler: %s", e)
        return [], []

    def work(filing):
        index_url, filing_date = filing
        try:
            return index_url, parse_djt_filing(index_url, filing_date)
        except Exception as e:
            logger.warning("SEC EDGAR DJT-Filing Fehler (%s): %s", index_url, e)
            return index_url, None

    trades, done = [], []
    with ThreadPoolExecutor(max_workers=_SEC_WORKERS) as pool:
        for index_url, filing_trades in pool.map(work, filings):
            if filing_trades is None:
                continue
            trades += filing_trades
            done.append(index_url)
    return trades, done


# ─── Alle Quellen ─────────────────────────────────────────────────────────────

def fetch_all_insider_sources(djt_skip_filings: frozenset[str] = frozenset()) -> dict:
    """Ruft Congress, ARK-Trades, ARK-Holdings und DJT gleichzeitig ab (die
    Quellen sind voneinander unabhängig). Jede Quelle fängt ihre Fehler selbst
    ab, ein Ausfall betrifft also nur ihren eigenen Eintrag.
    Rückgabe-Keys: congress, congress_error, ark_trades, ark_holdings,
    djt_trades, djt_done_filings."""
    with ThreadPoolExecutor(max_workers=4) as pool:
        congress = pool.submit(fetch_congress_trades)
        ark_tr   = pool.submit(fetch_ark_trades, "3M")
        ark_hold = pool.submit(fetch_ark_holdings)
        djt      = pool.submit(fetch_djt_family_trades, 40, djt_skip_filings)
        congress_trades, congress_error = congress.result()
        djt_trades, djt_done = djt.result()
        return {
            "congress":         congress_trades,
            "congress_error":   congress_error,
            "ark_trades":       ark_tr.result(),
            "ark_holdings":     ark_hold.result(),
            "djt_trades":       djt_trades,
            "djt_done_filings": djt_done,
        }
