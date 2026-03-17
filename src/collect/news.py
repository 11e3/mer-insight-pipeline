"""
뉴스 RSS 수집기.
- Fed (FOMC/금리 결정)
- 한국은행 보도자료
- AP/Reuters (Google News 집계)
"""

import re
import logging
from datetime import datetime
from xml.etree import ElementTree as ET

import asyncpg
import requests

from src.config.settings import FED_RSS_URL, BOK_RSS_URL, NEWS_RSS_URLS

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MerPipeline/1.0)",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

# Fed: 금리 관련 키워드
FED_KEYWORDS = {
    "federal funds rate", "fomc", "monetary policy", "interest rate",
    "rate decision", "rate hike", "rate cut", "balance sheet",
}

# BOK: 금통위 관련 키워드
BOK_KEYWORDS = {
    "금통위", "기준금리", "통화정책", "금리 결정", "한국은행 총재",
}

# 뉴스: 지정학/거시 키워드
GEO_KEYWORDS = {
    "sanction", "tariff", "trade war", "geopolit", "invasion",
    "관세", "제재", "무역전쟁", "지정학", "전쟁", "분쟁",
}


def _parse_rss(url: str, timeout: int = 10) -> list[ET.Element]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        return root.findall(".//item")
    except Exception as e:
        log.warning(f"RSS 수집 실패 ({url[:60]}): {e}")
        return []


def _text(el: ET.Element, tag: str) -> str:
    child = el.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _parse_date(date_str: str) -> datetime:
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S GMT"):
        try:
            dt = datetime.strptime(date_str[:31].strip(), fmt)
            return dt.replace(tzinfo=None)
        except ValueError:
            continue
    return datetime.now()


def _has_keyword(text: str, keywords: set[str]) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in keywords)


class NewsCollector:

    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn

    async def fetch_recent(self) -> list[dict]:
        """새 뉴스 이벤트 반환 (DB에 없는 것만)."""
        candidates = (
            self._fetch_fed()
            + self._fetch_bok()
            + self._fetch_general_news()
        )
        results = []
        for item in candidates:
            exists = await self.conn.fetchval(
                "SELECT 1 FROM events WHERE source = $1 AND event_type = 'news'",
                item["url"],
            )
            if not exists:
                results.append(item)
        return results

    def _fetch_fed(self) -> list[dict]:
        items = _parse_rss(FED_RSS_URL)
        results = []
        for item in items:
            title   = _text(item, "title")
            link    = _text(item, "link")
            desc    = _text(item, "description")
            pub     = _text(item, "pubDate")
            combined = title + " " + desc
            if not _has_keyword(combined, FED_KEYWORDS):
                continue
            results.append({
                "title":      f"[Fed] {title}",
                "content":    desc or title,
                "url":        link,
                "date":       _parse_date(pub),
                "source_tag": "fed",
            })
        return results

    def _fetch_bok(self) -> list[dict]:
        items = _parse_rss(BOK_RSS_URL)
        results = []
        for item in items:
            title = _text(item, "title")
            link  = _text(item, "link")
            desc  = _text(item, "description")
            pub   = _text(item, "pubDate")
            combined = title + " " + desc
            if not _has_keyword(combined, BOK_KEYWORDS):
                continue
            results.append({
                "title":      f"[한국은행] {title}",
                "content":    desc or title,
                "url":        link,
                "date":       _parse_date(pub),
                "source_tag": "bok",
            })
        return results

    def _fetch_general_news(self) -> list[dict]:
        results = []
        for rss_url in NEWS_RSS_URLS:
            items = _parse_rss(rss_url)
            for item in items:
                title = _text(item, "title")
                link  = _text(item, "link")
                desc  = _text(item, "description")
                pub   = _text(item, "pubDate")
                combined = title + " " + desc
                if not _has_keyword(combined, GEO_KEYWORDS):
                    continue
                results.append({
                    "title":      title,
                    "content":    re.sub(r"<[^>]+>", "", desc) if desc else title,
                    "url":        link,
                    "date":       _parse_date(pub),
                    "source_tag": "news",
                })
        return results
