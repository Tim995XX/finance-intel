"""
Watchlist auto-updater: keeps top-10 largest + top-10 most volatile per continent.
Rules:
  - Only ADD tickers, never delete or overwrite existing entries.
  - Run daily as part of main.py.
"""
import logging
from src.database.db import get_connection

logger = logging.getLogger(__name__)

# ─── Predefined universe ─────────────────────────────────────────────────────
# (ticker, display_name, asset_type, continent)
# 10 largest by market cap + 10 most volatile (curated, based on 2025/26 data)

UNIVERSE: list[tuple[str, str, str, str]] = [

    # ── Nordamerika ──────────────────────────────────────────────────────────
    # Large cap
    ("AAPL",   "Apple Inc.",            "stock", "Nordamerika"),
    ("MSFT",   "Microsoft Corp.",       "stock", "Nordamerika"),
    ("NVDA",   "NVIDIA Corp.",          "stock", "Nordamerika"),
    ("AMZN",   "Amazon.com",            "stock", "Nordamerika"),
    ("GOOGL",  "Alphabet (Google)",     "stock", "Nordamerika"),
    ("META",   "Meta Platforms",        "stock", "Nordamerika"),
    ("TSLA",   "Tesla Inc.",            "stock", "Nordamerika"),
    ("BRK-B",  "Berkshire Hathaway B",  "stock", "Nordamerika"),
    ("JPM",    "JPMorgan Chase",        "stock", "Nordamerika"),
    ("AVGO",   "Broadcom Inc.",         "stock", "Nordamerika"),
    # Volatile
    ("PLTR",   "Palantir Technologies", "stock", "Nordamerika"),
    ("COIN",   "Coinbase Global",       "stock", "Nordamerika"),
    ("MSTR",   "MicroStrategy",         "stock", "Nordamerika"),
    ("RIVN",   "Rivian Automotive",     "stock", "Nordamerika"),
    ("GME",    "GameStop",              "stock", "Nordamerika"),
    ("SMCI",   "Super Micro Computer",  "stock", "Nordamerika"),
    ("MARA",   "MARA Holdings",         "stock", "Nordamerika"),
    ("SOFI",   "SoFi Technologies",     "stock", "Nordamerika"),
    ("HOOD",   "Robinhood Markets",     "stock", "Nordamerika"),
    ("UPST",   "Upstart Holdings",      "stock", "Nordamerika"),
    # ETFs / Indices
    ("SPY",    "S&P 500 ETF",           "etf",   "Nordamerika"),
    ("QQQ",    "Nasdaq-100 ETF",        "etf",   "Nordamerika"),
    ("^GSPC",  "S&P 500 Index",         "index", "Nordamerika"),

    # ── Europa ───────────────────────────────────────────────────────────────
    # Large cap (US-listed ADRs + local)
    ("ASML",   "ASML Holding",          "stock", "Europa"),
    ("AZN",    "AstraZeneca",           "stock", "Europa"),
    ("NVO",    "Novo Nordisk",          "stock", "Europa"),
    ("SAP",    "SAP SE",                "stock", "Europa"),
    ("SHEL",   "Shell PLC",             "stock", "Europa"),
    ("BP",     "BP PLC",                "stock", "Europa"),
    ("HSBC",   "HSBC Holdings",         "stock", "Europa"),
    ("TTE",    "TotalEnergies",         "stock", "Europa"),
    ("UBS",    "UBS Group",             "stock", "Europa"),
    ("EQNR",   "Equinor ASA",           "stock", "Europa"),
    # Volatile / local exchanges
    ("BAYN.DE","Bayer AG",              "stock", "Europa"),
    ("DBK.DE", "Deutsche Bank",         "stock", "Europa"),
    ("VOW3.DE","Volkswagen VZ",         "stock", "Europa"),
    ("MBG.DE", "Mercedes-Benz",         "stock", "Europa"),
    ("ALV.DE", "Allianz SE",            "stock", "Europa"),
    ("SIE.DE", "Siemens AG",            "stock", "Europa"),
    ("AIR.PA", "Airbus SE",             "stock", "Europa"),
    ("BNP.PA", "BNP Paribas",           "stock", "Europa"),
    ("ING",    "ING Groep",             "stock", "Europa"),
    ("STLA",   "Stellantis NV",         "stock", "Europa"),
    # Index
    ("^GDAXI", "DAX Index",             "index", "Europa"),

    # ── Asien ────────────────────────────────────────────────────────────────
    # Large cap
    ("TSM",    "TSMC (Taiwan Semi.)",   "stock", "Asien"),
    ("TM",     "Toyota Motor",          "stock", "Asien"),
    ("SONY",   "Sony Group",            "stock", "Asien"),
    ("HMC",    "Honda Motor",           "stock", "Asien"),
    ("BABA",   "Alibaba Group",         "stock", "Asien"),
    ("BIDU",   "Baidu Inc.",            "stock", "Asien"),
    ("JD",     "JD.com",                "stock", "Asien"),
    ("PDD",    "PDD Holdings (Temu)",   "stock", "Asien"),
    ("9984.T", "SoftBank Group",        "stock", "Asien"),
    ("005930.KS","Samsung Electronics", "stock", "Asien"),
    # Volatile
    ("NIO",    "NIO Inc.",              "stock", "Asien"),
    ("LI",     "Li Auto",               "stock", "Asien"),
    ("XPEV",   "XPeng Inc.",            "stock", "Asien"),
    ("GRAB",   "Grab Holdings",         "stock", "Asien"),
    ("SE",     "Sea Limited",           "stock", "Asien"),
    ("TCEHY",  "Tencent Holdings",      "stock", "Asien"),
    ("BEKE",   "KE Holdings",           "stock", "Asien"),
    ("HTHT",   "H World Group",         "stock", "Asien"),
    ("YMM",    "Full Truck Alliance",   "stock", "Asien"),
    ("TIGR",   "UP Fintech (Tiger)",    "stock", "Asien"),
    # Index
    ("^N225",  "Nikkei 225",            "index", "Asien"),
    ("^HSI",   "Hang Seng Index",       "index", "Asien"),

    # ── Südamerika ───────────────────────────────────────────────────────────
    # Large cap
    ("VALE",   "Vale S.A.",             "stock", "Südamerika"),
    ("PBR",    "Petrobras",             "stock", "Südamerika"),
    ("ITUB",   "Itaú Unibanco",         "stock", "Südamerika"),
    ("ABEV",   "Ambev S.A.",            "stock", "Südamerika"),
    ("MELI",   "MercadoLibre",          "stock", "Südamerika"),
    ("NU",     "Nu Holdings (Nubank)",  "stock", "Südamerika"),
    ("SQM",    "SQM S.A. (Lithium)",   "stock", "Südamerika"),
    ("BSAC",   "Banco Santander Chile", "stock", "Südamerika"),
    ("GGB",    "Gerdau S.A.",           "stock", "Südamerika"),
    ("CIB",    "Bancolombia",           "stock", "Südamerika"),
    # Volatile
    ("DESP",   "Despegar.com",          "stock", "Südamerika"),
    ("VIST",   "Vista Energy",          "stock", "Südamerika"),
    ("YPF",    "YPF S.A.",              "stock", "Südamerika"),
    ("ARCO",   "Arcos Dorados",         "stock", "Südamerika"),
    ("BRFS",   "BRF S.A.",              "stock", "Südamerika"),
    ("ERJ",    "Embraer",               "stock", "Südamerika"),
    ("AZUL",   "Azul S.A.",             "stock", "Südamerika"),
    ("LOMA",   "Loma Negra",            "stock", "Südamerika"),
    ("CAAP",   "Corp. America Airports","stock", "Südamerika"),
    ("NUVEM",  "Nuvemshop (priv.)",     "stock", "Südamerika"),

    # ── Afrika & Naher Osten ─────────────────────────────────────────────────
    # Large cap (mostly NYSE/OTC ADRs from SA + ME)
    ("GFI",    "Gold Fields Ltd.",      "stock", "Afrika & NO"),
    ("AU",     "AngloGold Ashanti",     "stock", "Afrika & NO"),
    ("SSL",    "Sasol Ltd.",            "stock", "Afrika & NO"),
    ("GLNCY",  "Glencore PLC (OTC)",   "stock", "Afrika & NO"),
    ("NPN.JO", "Naspers Ltd.",          "stock", "Afrika & NO"),
    ("MTN.JO", "MTN Group",             "stock", "Afrika & NO"),
    ("SBK.JO", "Standard Bank",        "stock", "Afrika & NO"),
    ("AGL.JO", "Anglo American JSE",   "stock", "Afrika & NO"),
    ("FSR.JO", "Firstrand Ltd.",        "stock", "Afrika & NO"),
    ("SOL.JO", "Eskom Sasol (JSE)",     "stock", "Afrika & NO"),
    # Volatile / precious metals exposure
    ("GOLD",   "Barrick Gold",          "stock", "Afrika & NO"),
    ("AEM",    "Agnico Eagle",          "stock", "Afrika & NO"),
    ("KGC",    "Kinross Gold",          "stock", "Afrika & NO"),
    ("HL",     "Hecla Mining",          "stock", "Afrika & NO"),
    ("PAAS",   "Pan American Silver",   "stock", "Afrika & NO"),
    ("AG",     "First Majestic Silver", "stock", "Afrika & NO"),
    ("EQX",    "Equinox Gold",          "stock", "Afrika & NO"),
    ("GSS",    "Golden Star Resources", "stock", "Afrika & NO"),
    ("SAND",   "Sandstorm Gold",        "stock", "Afrika & NO"),
    ("TFPM",   "Triple Flag Precious",  "stock", "Afrika & NO"),

    # ── Ozeanien ─────────────────────────────────────────────────────────────
    # Large cap
    ("BHP",    "BHP Group Ltd.",        "stock", "Ozeanien"),
    ("RIO",    "Rio Tinto",             "stock", "Ozeanien"),
    ("CBA.AX", "Commonwealth Bank",     "stock", "Ozeanien"),
    ("CSL.AX", "CSL Limited",           "stock", "Ozeanien"),
    ("WBC.AX", "Westpac Banking",       "stock", "Ozeanien"),
    ("ANZ.AX", "ANZ Banking Group",     "stock", "Ozeanien"),
    ("NAB.AX", "Natl. Australia Bank",  "stock", "Ozeanien"),
    ("WES.AX", "Wesfarmers Ltd.",       "stock", "Ozeanien"),
    ("MQG.AX", "Macquarie Group",       "stock", "Ozeanien"),
    ("FMG.AX", "Fortescue Ltd.",        "stock", "Ozeanien"),
    # Volatile
    ("MIN.AX", "Mineral Resources",     "stock", "Ozeanien"),
    ("PLS.AX", "Pilbara Minerals",      "stock", "Ozeanien"),
    ("S32.AX", "South32 Ltd.",          "stock", "Ozeanien"),
    ("LTR.AX", "Liontown Resources",    "stock", "Ozeanien"),
    ("WDS.AX", "Woodside Energy",       "stock", "Ozeanien"),
    ("STO.AX", "Santos Ltd.",           "stock", "Ozeanien"),
    ("ORG.AX", "Origin Energy",         "stock", "Ozeanien"),
    ("AWC.AX", "Alumina Ltd.",          "stock", "Ozeanien"),
    ("IGO.AX", "IGO Ltd.",              "stock", "Ozeanien"),
    ("NXT.AX", "NextDC Ltd.",           "stock", "Ozeanien"),

    # ── Global ───────────────────────────────────────────────────────────────
    ("BTC-USD", "Bitcoin",              "crypto", "Global"),
    ("ETH-USD", "Ethereum",             "crypto", "Global"),
    ("GC=F",    "Gold Futures",         "commodity", "Global"),
    ("CL=F",    "Crude Oil (WTI)",      "commodity", "Global"),
    ("^VIX",    "CBOE VIX",             "index",  "Global"),
]

ALL_CONTINENTS = [
    "Alle",
    "Nordamerika",
    "Europa",
    "Asien",
    "Südamerika",
    "Afrika & NO",
    "Ozeanien",
    "Global",
]


def update_watchlist() -> int:
    """
    Add any UNIVERSE tickers missing from watchlist.
    Never deletes or modifies existing entries.
    Returns count of newly added tickers.
    """
    conn = get_connection()
    existing = {
        r["ticker"]
        for r in conn.execute("SELECT ticker FROM watchlist").fetchall()
    }

    added = 0
    for ticker, name, asset_type, continent in UNIVERSE:
        if ticker not in existing:
            conn.execute(
                """INSERT OR IGNORE INTO watchlist
                       (ticker, name, asset_type, continent)
                   VALUES (?,?,?,?)""",
                (ticker, name, asset_type, continent),
            )
            added += 1

    # Also backfill continent for existing entries that have 'Global' as default
    continent_map = {t: c for t, _, _, c in UNIVERSE}
    for ticker, continent in continent_map.items():
        conn.execute(
            """UPDATE watchlist SET continent=?
               WHERE ticker=? AND (continent IS NULL OR continent='Global')""",
            (continent, ticker),
        )

    conn.commit()
    conn.close()

    if added:
        logger.info("Watchlist: %d neue Ticker hinzugefügt", added)
    return added


def get_volatility_ranking(continent: str | None = None) -> list[dict]:
    """
    Calculate 30-day annualised volatility for all watchlist tickers
    that have price data. Returns list sorted by volatility desc.
    Used for informational display in dashboard.
    """
    conn = get_connection()
    if continent and continent != "Alle":
        tickers = [
            r["ticker"]
            for r in conn.execute(
                "SELECT ticker FROM watchlist WHERE continent=?", (continent,)
            ).fetchall()
        ]
    else:
        tickers = [
            r["ticker"]
            for r in conn.execute("SELECT ticker FROM watchlist").fetchall()
        ]

    results = []
    for ticker in tickers:
        rows = conn.execute(
            """SELECT close FROM stock_prices
               WHERE ticker=? ORDER BY date DESC LIMIT 32""",
            (ticker,),
        ).fetchall()
        if len(rows) < 5:
            continue
        closes = [r["close"] for r in rows]
        rets = [(closes[i] - closes[i + 1]) / closes[i + 1] for i in range(len(closes) - 1)]
        daily_vol = (sum(r ** 2 for r in rets) / len(rets)) ** 0.5
        ann_vol   = daily_vol * (252 ** 0.5) * 100   # annualised %
        results.append({"ticker": ticker, "volatility_pct": round(ann_vol, 1)})

    conn.close()
    return sorted(results, key=lambda x: -x["volatility_pct"])
