import re
import logging
import nltk
from collections import Counter
from textblob import TextBlob
from sumy.parsers.plaintext import PlaintextParser
from sumy.nlp.tokenizers import Tokenizer
from sumy.summarizers.lsa import LsaSummarizer
from sumy.nlp.stemmers import Stemmer
from sumy.utils import get_stop_words

logger = logging.getLogger(__name__)


def ensure_nltk_data():
    for pkg, category in [
        ("punkt",                      "tokenizers"),
        ("punkt_tab",                  "tokenizers"),
        ("stopwords",                  "corpora"),
        ("averaged_perceptron_tagger", "taggers"),
    ]:
        try:
            nltk.data.find(f"{category}/{pkg}")
        except LookupError:
            nltk.download(pkg, quiet=True)


# ─── Company → Ticker map ────────────────────────────────────────────────────
# Lowercase keys for case-insensitive matching

COMPANY_TICKER: dict[str, str] = {
    # US Tech — Mag 7 + large caps
    "apple":              "AAPL",
    "microsoft":          "MSFT",
    "google":             "GOOGL",
    "alphabet":           "GOOGL",
    "amazon":             "AMZN",
    "tesla":              "TSLA",
    "meta":               "META",
    "facebook":           "META",
    "nvidia":             "NVDA",
    "netflix":            "NFLX",
    "intel":              "INTC",
    "amd":                "AMD",
    "qualcomm":           "QCOM",
    "broadcom":           "AVGO",
    "salesforce":         "CRM",
    "oracle":             "ORCL",
    "ibm":                "IBM",
    "adobe":              "ADBE",
    "paypal":             "PYPL",
    "uber":               "UBER",
    "lyft":               "LYFT",
    "airbnb":             "ABNB",
    "spotify":            "SPOT",
    "snap":               "SNAP",
    "snapchat":           "SNAP",
    "twitter":            "X",
    "x corp":             "X",
    "palantir":           "PLTR",
    "snowflake":          "SNOW",
    "coinbase":           "COIN",
    # US Finance
    "jpmorgan":           "JPM",
    "jp morgan":          "JPM",
    "goldman sachs":      "GS",
    "goldman":            "GS",
    "morgan stanley":     "MS",
    "bank of america":    "BAC",
    "wells fargo":        "WFC",
    "citigroup":          "C",
    "citi":               "C",
    "blackrock":          "BLK",
    "visa":               "V",
    "mastercard":         "MA",
    "american express":   "AXP",
    "berkshire":          "BRK-B",
    # US Healthcare
    "johnson & johnson":  "JNJ",
    "j&j":                "JNJ",
    "pfizer":             "PFE",
    "moderna":            "MRNA",
    "unitedhealth":       "UNH",
    "abbvie":             "ABBV",
    "eli lilly":          "LLY",
    "lilly":              "LLY",
    "merck":              "MRK",
    "bristol myers":      "BMY",
    "gilead":             "GILD",
    # US Consumer / Retail
    "walmart":            "WMT",
    "costco":             "COST",
    "target":             "TGT",
    "home depot":         "HD",
    "coca cola":          "KO",
    "coca-cola":          "KO",
    "pepsi":              "PEP",
    "pepsico":            "PEP",
    "procter & gamble":   "PG",
    "disney":             "DIS",
    "nike":               "NKE",
    "mcdonalds":          "MCD",
    "mcdonald's":         "MCD",
    "starbucks":          "SBUX",
    # US Energy
    "exxon":              "XOM",
    "chevron":            "CVX",
    "conocophillips":     "COP",
    # European — Germany
    "sap":                "SAP",
    "siemens":            "SIE.DE",
    "bmw":                "BMW.DE",
    "volkswagen":         "VOW3.DE",
    "vw":                 "VOW3.DE",
    "mercedes":           "MBG.DE",
    "daimler":            "MBG.DE",
    "allianz":            "ALV.DE",
    "deutsche bank":      "DBK.DE",
    "basf":               "BAS.DE",
    "bayer":              "BAYN.DE",
    "adidas":             "ADS.DE",
    "porsche":            "P911.DE",
    "infineon":           "IFX.DE",
    "deutsche telekom":   "DTE.DE",
    "telekom":            "DTE.DE",
    # European — France
    "airbus":             "AIR.PA",
    "lvmh":               "LVMH.PA",
    "totalenergies":      "TTE.PA",
    "total":              "TTE.PA",
    "bnp paribas":        "BNP.PA",
    "loreal":             "OR.PA",
    "l'oreal":            "OR.PA",
    # European — UK
    "hsbc":               "HSBC",
    "bp":                 "BP",
    "astrazeneca":        "AZN",
    "shell":              "SHEL",
    "unilever":           "ULVR.L",
    "barclays":           "BCS",
    "lloyds":             "LYG",
    "rio tinto":          "RIO",
    # European — Swiss
    "nestle":             "NESN.SW",
    "nestlé":             "NESN.SW",
    "novartis":           "NOVN.SW",
    "roche":              "ROG.SW",
    "ubs":                "UBS",
    # Asian
    "toyota":             "TM",
    "sony":               "SONY",
    "honda":              "HMC",
    "softbank":           "SFTBY",
    "alibaba":            "BABA",
    "tencent":            "TCEHY",
    "baidu":              "BIDU",
    "jd.com":             "JD",
    "samsung":            "SSNLF",
    "tsmc":               "TSM",
    "taiwan semiconductor": "TSM",
    # Indices
    "s&p 500":            "^GSPC",
    "s&p500":             "^GSPC",
    "sp500":              "^GSPC",
    "nasdaq":             "^IXIC",
    "dow jones":          "^DJI",
    "dax":                "^GDAXI",
    "nikkei":             "^N225",
    "hang seng":          "^HSI",
    "ftse":               "^FTSE",
    "ftse 100":           "^FTSE",
    "euro stoxx":         "FEZ",
    "stoxx 600":          "^STOXX",
    "mdax":               "^MDAXI",
    "tecdax":             "^TECDAX",
    # ETFs
    "spy":                "SPY",
    "qqq":                "QQQ",
    "vti":                "VTI",
    "iwm":                "IWM",
    # Crypto (Top-50-Namen — best effort, siehe crypto_fetcher.py für die
    # tatsächlich zur Laufzeit von CoinGecko bezogene Top-50-Liste; Symbole
    # können sich ändern (z.B. Rebranding), Tagging bleibt dann nur unvollständig)
    "bitcoin":            "BTC-USD",
    "btc":                "BTC-USD",
    "ethereum":           "ETH-USD",
    "ether":              "ETH-USD",
    "eth":                "ETH-USD",
    "dogecoin":           "DOGE-USD",
    "solana":             "SOL-USD",
    "ripple":             "XRP-USD",
    "xrp":                "XRP-USD",
    "cardano":            "ADA-USD",
    "polkadot":           "DOT-USD",
    "avalanche":          "AVAX-USD",
    "chainlink":          "LINK-USD",
    "litecoin":           "LTC-USD",
    "polygon":            "MATIC-USD",
    "uniswap":            "UNI-USD",
    "stellar lumens":     "XLM-USD",
    "cosmos hub":         "ATOM-USD",
    "near protocol":      "NEAR-USD",
    "aptos":              "APT-USD",
    "arbitrum":           "ARB-USD",
    "optimism":           "OP-USD",
    "hedera hashgraph":   "HBAR-USD",
    "algorand":           "ALGO-USD",
    "filecoin":           "FIL-USD",
    "injective protocol": "INJ-USD",
    "sui network":        "SUI-USD",
    "toncoin":            "TON-USD",
    "monero":             "XMR-USD",
    "bitcoin cash":       "BCH-USD",
    "shiba inu":          "SHIB-USD",
    "pepe coin":          "PEPE-USD",
    "binance coin":       "BNB-USD",
    "tron":               "TRX-USD",
    # Macro
    "fed":                "^TNX",    # map to 10Y yield as proxy
    "federal reserve":    "^TNX",
    "ecb":                "FEZ",
    "gold":               "GC=F",
    "silver":             "SI=F",
    "oil":                "CL=F",
    "crude oil":          "CL=F",
    "brent":              "BZ=F",
    "natural gas":        "NG=F",
}

# Explicit ticker patterns: $AAPL  or  (NASDAQ: AAPL)  or  (NYSE: AAPL)
_RE_EXPLICIT = re.compile(
    r"\$([A-Z]{1,5})"
    r"|(?:NASDAQ|NYSE|XETRA|LSE|SIX)\s*:\s*([A-Z]{1,5})"
)

# Company names that are also common English words / finance jargon. A plain
# lowercase substring match on these produces false positives (e.g. "targeting",
# "citizen" contains "citi", "10 bp" = basis points, "oil painting"). For these,
# require the proper-noun capitalization instead of case-insensitive matching.
_AMBIGUOUS_NAMES = {"target", "total", "shell", "gold", "silver", "oil", "fed", "bp"}
_AMBIGUOUS_CASE_OVERRIDE = {"bp": "BP"}


def extract_tickers(text: str) -> list[tuple[str, str]]:
    """Return list of (ticker, matched_name) found in text."""
    found: dict[str, str] = {}
    text_lower = text.lower()

    for name, ticker in COMPANY_TICKER.items():
        if name in _AMBIGUOUS_NAMES:
            variant = _AMBIGUOUS_CASE_OVERRIDE.get(name, name.capitalize())
            pattern = r"\b" + re.escape(variant) + r"\b"
            if re.search(pattern, text):
                found.setdefault(ticker, name)
        else:
            pattern = r"\b" + re.escape(name) + r"\b"
            if re.search(pattern, text_lower):
                found.setdefault(ticker, name)

    for m in _RE_EXPLICIT.finditer(text):
        ticker = m.group(1) or m.group(2)
        if ticker and ticker not in found:
            found[ticker] = ticker

    return list(found.items())


# ─── Summarizer ──────────────────────────────────────────────────────────────

def summarize(text: str, sentences: int = 3, language: str = "english") -> str:
    """Extractive summary using LSA (no model download required)."""
    try:
        if len(text.split()) < 35:
            return text[:500]
        lang = language if language in ("english", "german") else "english"
        parser    = PlaintextParser.from_string(text, Tokenizer(lang))
        stemmer   = Stemmer(lang)
        summarizer = LsaSummarizer(stemmer)
        summarizer.stop_words = get_stop_words(lang)
        result = summarizer(parser.document, sentences)
        summary = " ".join(str(s) for s in result)
        return summary if summary else text[:400]
    except Exception as e:
        logger.debug("Summarize error: %s", e)
        return text[:400]


# ─── Sentiment ───────────────────────────────────────────────────────────────

def analyze_sentiment(text: str) -> tuple[str, float]:
    """Return (label, score) using TextBlob polarity (-1 to +1)."""
    try:
        score = round(TextBlob(text[:2000]).sentiment.polarity, 4)
        if score > 0.05:
            label = "positive"
        elif score < -0.05:
            label = "negative"
        else:
            label = "neutral"
        return label, score
    except Exception:
        return "neutral", 0.0


# ─── Combined entry point ────────────────────────────────────────────────────

def process_item(title: str, content: str, language: str = "en") -> dict:
    full_text = f"{title}. {content}"
    lang      = "german" if language == "de" else "english"
    label, score = analyze_sentiment(full_text)
    return {
        "summary":         summarize(full_text, sentences=3, language=lang),
        "sentiment_label": label,
        "sentiment_score": score,
        "tickers":         extract_tickers(full_text),
    }
