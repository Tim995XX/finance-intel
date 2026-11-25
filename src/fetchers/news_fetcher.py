import feedparser
from bs4 import BeautifulSoup
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Accept":     "application/rss+xml, application/xml, text/xml, */*",
}


def _parse_date(entry) -> str:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                dt = datetime(*val[:6], tzinfo=timezone.utc)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator=" ", strip=True)
    return " ".join(text.split())


def _get_content(entry) -> str:
    if hasattr(entry, "content") and entry.content:
        raw = entry.content[0].get("value", "")
        if raw:
            return _clean_html(raw)[:4000]
    for attr in ("summary", "description"):
        raw = getattr(entry, attr, None)
        if raw:
            return _clean_html(raw)[:4000]
    return ""


def fetch_feed(feed_url: str) -> list[dict]:
    """Fetch an RSS/Atom feed and return list of raw item dicts."""
    try:
        feed = feedparser.parse(feed_url, request_headers=_HEADERS)
        items = []
        for entry in feed.entries[:30]:
            content = _get_content(entry)
            if not content or len(content) < 40:
                continue
            items.append({
                "title":        getattr(entry, "title", "").strip(),
                "url":          getattr(entry, "link",  "").strip(),
                "published_at": _parse_date(entry),
                "raw_content":  content,
            })
        return items
    except Exception as e:
        logger.warning("Feed error %s: %s", feed_url, e)
        return []
