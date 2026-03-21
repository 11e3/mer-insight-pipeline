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

        # Naver News API 수집
        naver_count = await self._collect_naver()
        total += naver_count

        log.info(f"뉴스 수집 완료: {total}건 신규 (RSS {total - naver_count} + Naver {naver_count})")
        return total

    # ── Naver News API ─────────────────────────────────────────────

    _NAVER_QUERIES = [
        ("금리 한국은행 기준금리", "ko", "금리_매크로"),
        ("환율 달러 원화", "ko", "환율"),
        ("엔화 일본은행 BOJ", "ko", "환율"),
        ("부동산 아파트 전세", "ko", "부동산"),
        ("반도체 삼성전자 SK하이닉스", "ko", "AI_반도체"),
        ("관세 무역 수출", "ko", "관세_무역"),
        ("유가 원유 에너지", "ko", "에너지"),
        ("조선 LNG 수주", "ko", "조선_해운"),
        ("방산 수출 미사일", "ko", "방산"),
        ("비트코인 암호화폐 가상자산", "ko", "주식_금융"),
        ("중동 이란 이스라엘", "ko", "중동_지정학"),
        ("트럼프 관세 미국", "ko", "트럼프_미국"),
        ("금값 금 가격", "ko", "원자재"),
        ("식품 물가 농산물", "ko", "식품_농업"),
    ]

    async def _collect_naver(self) -> int:
        """Naver News API로 최신 뉴스 수집."""
        import os
        from functools import partial
        import requests as req

        client_id = os.environ.get("NAVER_CLIENT_ID")
        client_secret = os.environ.get("NAVER_CLIENT_SECRET")
        if not client_id or not client_secret:
            return 0

        headers = {
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
        }
        total = 0

        for query, lang, topic in self._NAVER_QUERIES:
            try:
                resp = await asyncio.to_thread(
                    partial(
                        req.get,
                        "https://openapi.naver.com/v1/search/news.json",
                        headers=headers,
                        params={"query": query, "display": 100, "sort": "date"},
                        timeout=10,
                    )
                )
                if resp.status_code != 200:
                    log.warning(f"[naver:{query[:10]}] HTTP {resp.status_code}")
                    continue

                items = resp.json().get("items", [])
                for item in items:
                    headline = (item.get("title") or "").replace("<b>", "").replace("</b>", "").replace("&quot;", '"').strip()
                    url = (item.get("link") or "").strip()
                    if not headline or not url:
                        continue

                    pub = self._parse_naver_date(item.get("pubDate", ""))
                    keywords = await asyncio.to_thread(extract_keywords, headline, lang)

                    try:
                        result = await self.conn.execute("""
                            INSERT INTO news_headlines
                                (headline, source_url, source_name, language,
                                 published_at, keywords, feed_topic)
                            VALUES ($1, $2, $3, $4, $5, $6, $7)
                            ON CONFLICT (md5(source_url)) DO NOTHING
                        """,
                            headline, url, "naver_news", lang,
                            pub or datetime.utcnow(), keywords, topic,
                        )
                        if result == "INSERT 0 1":
                            total += 1
                    except Exception as e:
                        log.debug(f"Naver INSERT 실패: {e}")

                await asyncio.sleep(0.5)  # rate limit

            except Exception as e:
                log.error(f"[naver:{query[:10]}] 수집 실패: {e}")

        if total > 0:
            log.info(f"  [naver] {total}건 삽입")
        return total

    @staticmethod
    def _parse_naver_date(date_str: str) -> datetime | None:
        """Naver pubDate 파싱: 'Fri, 20 Mar 2026 17:48:00 +0900'"""
        from email.utils import parsedate_to_datetime
        try:
            dt = parsedate_to_datetime(date_str)
            return dt.replace(tzinfo=None)  # naive UTC 호환
        except Exception:
            return None

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
