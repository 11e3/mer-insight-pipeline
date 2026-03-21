"""RSS 기반 범용 블로그 수집기 — Substack, Medium, 일반 블로그.

SourceCollector 프로토콜 구현체.
"""

import asyncio
import logging
from datetime import datetime

import asyncpg

from src.collect.source_protocol import CollectedPost

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; mer-insight-bot/1.0)",
}


class RSSCollector:
    """RSS 피드 기반 범용 수집기."""

    source_name: str

    def __init__(self, source_name: str, feed_url: str):
        self.source_name = source_name
        self.feed_url = feed_url

    async def check_new(self, conn: asyncpg.Connection) -> list[CollectedPost]:
        """DB에 없는 신규 글 수집."""
        import feedparser

        parsed = await asyncio.to_thread(
            feedparser.parse, self.feed_url, request_headers=_HEADERS
        )

        if parsed.get("bozo") and not parsed.entries:
            log.warning(f"[{self.source_name}] RSS 파싱 오류")
            return []

        # DB에서 기존 URL 조회
        existing_urls = set()
        rows = await conn.fetch(
            "SELECT url FROM posts WHERE source_id = (SELECT id FROM sources WHERE name = $1)",
            self.source_name,
        )
        for r in rows:
            existing_urls.add(r["url"])

        new_posts = []
        for entry in parsed.entries:
            url = (entry.get("link") or "").strip()
            if not url or url in existing_urls:
                continue

            title = (entry.get("title") or "").strip()
            if not title:
                continue

            # 본문 추출 (content > summary)
            content_text = ""
            if entry.get("content"):
                content_text = entry["content"][0].get("value", "")
            elif entry.get("summary"):
                content_text = entry["summary"]

            # HTML 태그 제거
            content_text = self._strip_html(content_text)

            if len(content_text) < 100:
                log.debug(f"[{self.source_name}] 본문 너무 짧음: {title[:30]}")
                continue

            published_at = self._parse_date(entry)

            new_posts.append(CollectedPost(
                external_id=url,  # RSS는 URL이 고유 ID
                title=title,
                content_text=content_text,
                url=url,
                published_at=published_at,
            ))

        if new_posts:
            log.info(f"[{self.source_name}] 신규 {len(new_posts)}건 감지")

        return new_posts

    @staticmethod
    def _parse_date(entry) -> str | None:
        """feedparser 항목에서 날짜 추출."""
        for attr in ("published_parsed", "updated_parsed"):
            parsed = getattr(entry, attr, None)
            if parsed:
                dt = datetime(*parsed[:6])
                return dt.strftime("%Y-%m-%d")
        return None

    @staticmethod
    def _strip_html(text: str) -> str:
        """HTML 태그 제거."""
        import re
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text
