"""뉴스 헤드라인 수집기 — Google News RSS + 선택적 Naver News API.

Usage:
    collector = NewsCollector(conn)
    count = await collector.run_daily()
"""

import asyncio
import logging
from datetime import datetime, timedelta

import asyncpg

from src.collect.feeds import FEEDS, FeedSpec
from src.collect.keyword_extractor import extract_keywords

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; mer-insight-bot/1.0)",
    "Accept": "application/rss+xml, application/xml, text/xml",
}
_REQUEST_DELAY = 2.0  # 피드 간 대기 (초)


class NewsCollector:

    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn

    async def run_daily(self) -> int:
        """24h 이내 헤드라인 수집. 반환: 신규 삽입 건수."""
        cutoff = datetime.utcnow() - timedelta(hours=26)
        total = 0

        for feed in FEEDS:
            try:
                count = await self._collect_feed(feed, cutoff)
                if count > 0:
                    log.info(f"  [{feed.feed_id}] {count}건 삽입")
                total += count
            except Exception as e:
                log.error(f"  [{feed.feed_id}] 수집 실패: {e}")
            await asyncio.sleep(_REQUEST_DELAY)

        log.info(f"뉴스 수집 완료: {total}건 신규")
        return total

    async def _collect_feed(self, feed: FeedSpec, cutoff: datetime) -> int:
        """단일 피드 수집: RSS 파싱(thread) → DB 삽입(async)."""
        import feedparser

        parsed = await asyncio.to_thread(
            feedparser.parse, feed.url, request_headers=_HEADERS
        )

        if parsed.get("bozo") and not parsed.entries:
            log.warning(f"[{feed.feed_id}] 피드 파싱 오류")
            return 0

        inserted = 0
        for entry in parsed.entries:
            pub = self._parse_date(entry)
            if pub and pub < cutoff:
                continue

            headline = (entry.get("title") or "").strip()
            url = (entry.get("link") or "").strip()
            if not headline or not url:
                continue

            source_name = (
                entry.get("source", {}).get("title")
                or parsed.feed.get("title", feed.feed_id)
            )

            keywords = await asyncio.to_thread(
                extract_keywords, headline, feed.language
            )

            try:
                result = await self.conn.execute("""
                    INSERT INTO news_headlines
                        (headline, source_url, source_name, language,
                         published_at, keywords, feed_topic)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (md5(source_url)) DO NOTHING
                """,
                    headline, url, source_name, feed.language,
                    pub or datetime.utcnow(), keywords, feed.topic,
                )
                if result == "INSERT 0 1":
                    inserted += 1
            except Exception as e:
                log.debug(f"INSERT 실패: {e}")

        # 커서 업데이트
        await self.conn.execute("""
            INSERT INTO news_feed_cursors (feed_id, last_fetched_at, item_count)
            VALUES ($1, $2, $3)
            ON CONFLICT (feed_id) DO UPDATE SET
                last_fetched_at = $2, item_count = news_feed_cursors.item_count + $3
        """, feed.feed_id, datetime.utcnow(), inserted)

        return inserted

    @staticmethod
    def _parse_date(entry) -> datetime | None:
        """feedparser 항목에서 날짜 추출 (naive UTC)."""
        for attr in ("published_parsed", "updated_parsed"):
            parsed = getattr(entry, attr, None)
            if parsed:
                return datetime(*parsed[:6])  # naive — DB TIMESTAMP 호환
        return None
