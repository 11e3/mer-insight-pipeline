"""
Event Dispatcher: 다중 소스 수집 → 예측 추출 → 뉴스 수집 → 검증

매일 01:00 단일 잡으로 전체 파이프라인 실행.

Usage:
    python -m src.pipeline.event_dispatcher
"""

import asyncio
import json
import logging
from datetime import date
from pathlib import Path

import anthropic
import asyncpg
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.config.settings import DATABASE_URL
from src.embed import get_embedder
from src.collect.factory import get_collector
from src.extract.realtime import extract_and_save
from src.search.bm25_index import BM25Index
from src.collect.news_collector import NewsCollector
from src.verify import AutoVerifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent
BM25_CACHE = _PROJECT_ROOT / "data" / "bm25_cache.pkl"


class EventDispatcher:

    def __init__(self):
        self.pool: asyncpg.Pool | None = None
        self.embedder = None
        self.bm25_index: BM25Index | None = None
        self.scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    async def _init(self):
        """공유 리소스 초기화 (풀 + 임베더 + BM25)."""
        self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
        self.embedder = get_embedder()

        self.bm25_index = BM25Index()
        async with self.pool.acquire() as conn:
            if not self.bm25_index.load(BM25_CACHE):
                await self.bm25_index.build(conn)
                self.bm25_index.save(BM25_CACHE)

    async def run(self) -> None:
        """전체 파이프라인 1회 실행 후 종료."""
        await self._init()
        try:
            await self._run_pipeline()
        finally:
            await self.pool.close()

    async def start(self):
        """상시 모드 — APScheduler로 매일 01:00 실행."""
        log.info("EventDispatcher 시작 중...")
        await self._init()

        self.scheduler.add_job(
            self._run_pipeline, "cron",
            hour=1, minute=0, id="daily_pipeline"
        )

        self.scheduler.start()
        log.info("스케줄러 시작 완료 (매일 01:00 실행)")

        try:
            await asyncio.Event().wait()
        finally:
            await self.pool.close()

    # --- 파이프라인 ---

    async def _run_pipeline(self):
        """1. 소스별 신규 글 수집 → 2. 뉴스 수집 → 3. 예측 검증"""
        log.info("=== 일일 파이프라인 시작 ===")

        async with self.pool.acquire() as conn:
            any_new = await self._step_collect(conn)

            if any_new:
                await self.bm25_index.build(conn)
                self.bm25_index.save(BM25_CACHE)
                log.info("BM25 인덱스 갱신 완료")

            await self._step_news(conn)
            await self._step_verify(conn)

        log.info("=== 일일 파이프라인 완료 ===")

    async def _step_collect(self, conn: asyncpg.Connection) -> bool:
        """Step 1: 활성 소스별 신규 글 수집 + 예측 추출. 반환: 신규 글 존재 여부."""
        any_new = False
        try:
            collectors = await self._get_active_collectors(conn)
        except (asyncpg.PostgresError, json.JSONDecodeError) as e:
            log.error(f"소스 로딩 오류: {e}")
            return False

        for collector in collectors:
            try:
                new_posts = await collector.check_new(conn)
                if not new_posts:
                    continue

                source_row = await conn.fetchrow(
                    "SELECT id, platform FROM sources WHERE name = $1",
                    collector.source_name,
                )
                source_type = (source_row["platform"] if source_row else None) or "blog"
                source_id = source_row["id"] if source_row else None

                for post in new_posts:
                    await self._save_and_extract(conn, post, source_id, source_type, collector.source_name)

                log.info(f"[{collector.source_name}] 신규 {len(new_posts)}건 처리 완료")
                any_new = True
            except (asyncpg.PostgresError, anthropic.APIError, ValueError) as e:
                log.error(f"[{collector.source_name}] 수집 오류: {e}")

        return any_new

    async def _save_and_extract(
        self,
        conn: asyncpg.Connection,
        post,
        source_id: int | None,
        source_type: str,
        source_name: str,
    ):
        """단일 포스트 저장 + 인사이트 추출."""
        pub_date = None
        if post.published_at:
            try:
                pub_date = date.fromisoformat(post.published_at)
            except (ValueError, TypeError):
                pub_date = None

        post_id = await conn.fetchval("""
            INSERT INTO mer_posts (log_no, title, date, url, content_text, source_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (log_no) DO NOTHING
            RETURNING id
        """, post.external_id, post.title, pub_date, post.url, post.content_text, source_id)

        if not post_id:
            return  # 이미 존재

        post_dict = {
            "log_no": post.external_id,
            "title": post.title,
            "date": post.published_at,
            "url": post.url,
            "content_text": post.content_text,
        }
        extracted = await extract_and_save(conn, self.embedder, post_dict, source_type=source_type)
        log.info(
            f"  [{source_name}] "
            f"인사이트 {extracted['count']}개 추출 ({post.title[:40]})"
        )

    async def _step_news(self, conn: asyncpg.Connection):
        """Step 2: 뉴스 헤드라인 수집."""
        try:
            news_collector = NewsCollector(conn)
            news_count = await news_collector.run_daily()
            log.info(f"뉴스 헤드라인 {news_count}건 수집 완료")
        except (asyncpg.PostgresError, OSError) as e:
            log.error(f"뉴스 수집 오류: {e}")

    async def _step_verify(self, conn: asyncpg.Connection):
        """Step 3: 자동 검증 (헤드라인 매칭 → Haiku 판정)."""
        try:
            auto_verifier = AutoVerifier(conn)
            auto_result = await auto_verifier.run(daily_limit=200)
            log.info(
                f"자동 검증: {auto_result['auto_resolved']}건 확정, "
                f"비용 ${auto_result['cost_usd']:.4f}"
            )
        except (asyncpg.PostgresError, anthropic.APIError) as e:
            log.error(f"자동 검증 오류: {e}")

    async def _get_active_collectors(self, conn: asyncpg.Connection):
        """sources 테이블에서 active 소스의 Collector 인스턴스 목록 반환."""
        rows = await conn.fetch(
            "SELECT source_type, name, config FROM sources WHERE is_active = TRUE"
        )
        collectors = []
        for row in rows:
            try:
                config = row["config"] if isinstance(row["config"], dict) else json.loads(row["config"] or "{}")
                collector = get_collector(row["source_type"], row["name"], config)
                collectors.append(collector)
            except ValueError as e:
                log.warning(f"Collector 생성 실패 ({row['name']}): {e}")
        return collectors


if __name__ == "__main__":
    dispatcher = EventDispatcher()
    asyncio.run(dispatcher.start())
