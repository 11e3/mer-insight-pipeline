"""
Event Dispatcher: 다중 소스 수집 → 예측 추출 → 뉴스 수집 → 검증

매일 01:00 단일 잡으로 전체 파이프라인 실행.

Usage:
    python -m src.pipeline.event_dispatcher
"""

import asyncio
import logging
from pathlib import Path

import asyncpg
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.config.settings import DATABASE_URL
from src.embed import get_embedder
from src.collect.factory import get_collector
from src.extract.realtime import extract_and_save
from src.search.bm25_index import BM25Index
from src.collect.news_collector import NewsCollector
from src.verify import AutoVerifier, PredictionVerifier

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
            # 1. 활성 소스별 신규 글 수집 + 예측 추출
            any_new = False
            try:
                collectors = await self._get_active_collectors(conn)
                for collector in collectors:
                    try:
                        new_posts = await collector.check_new(conn)
                        if new_posts:
                            source_type = await conn.fetchval(
                                "SELECT source_type FROM sources WHERE name = $1",
                                collector.source_name,
                            ) or "blog"
                            for post in new_posts:
                                post_dict = {
                                    "log_no": post.external_id,
                                    "title": post.title,
                                    "date": post.published_at,
                                    "url": post.url,
                                    "content_text": post.content_text,
                                }
                                extracted = await extract_and_save(
                                    conn, self.embedder, post_dict,
                                    source_type=source_type,
                                )
                                log.info(
                                    f"  [{collector.source_name}] "
                                    f"인사이트 {extracted['count']}개 추출 ({post.title[:40]})"
                                )
                            log.info(f"[{collector.source_name}] 신규 {len(new_posts)}건 처리 완료")
                            any_new = True
                    except Exception as e:
                        log.error(f"[{collector.source_name}] 수집 오류: {e}")
            except Exception as e:
                log.error(f"소스 로딩 오류: {e}")

            if any_new:
                await self.bm25_index.build(conn)
                self.bm25_index.save(BM25_CACHE)
                log.info("BM25 인덱스 갱신 완료")

            # 2. 뉴스 헤드라인 수집
            try:
                news_collector = NewsCollector(conn)
                news_count = await news_collector.run_daily()
                log.info(f"뉴스 헤드라인 {news_count}건 수집 완료")
            except Exception as e:
                log.error(f"뉴스 수집 오류: {e}")

            # 3a. 자동 검증 (헤드라인 매칭 → Haiku 판정)
            try:
                auto_verifier = AutoVerifier(conn)
                auto_result = await auto_verifier.run(daily_limit=200)
                log.info(
                    f"자동 검증: {auto_result['auto_resolved']}건 확정, "
                    f"비용 ${auto_result['cost_usd']:.4f}"
                )
            except Exception as e:
                log.error(f"자동 검증 오류: {e}")

            # 3b. 나머지 수동 검증 내보내기
            try:
                verifier = PredictionVerifier(conn)
                exported = await verifier.run()
                log.info(f"수동 검증 대기 {exported}건 내보내기")
            except Exception as e:
                log.error(f"수동 검증 내보내기 오류: {e}")

        log.info("=== 일일 파이프라인 완료 ===")

    async def _get_active_collectors(self, conn: asyncpg.Connection):
        """sources 테이블에서 active 소스의 Collector 인스턴스 목록 반환."""
        rows = await conn.fetch(
            "SELECT source_type, name, config FROM sources WHERE is_active = TRUE"
        )
        collectors = []
        for row in rows:
            try:
                import json
                config = row["config"] if isinstance(row["config"], dict) else json.loads(row["config"] or "{}")
                collector = get_collector(row["source_type"], row["name"], config)
                collectors.append(collector)
            except ValueError as e:
                log.warning(f"Collector 생성 실패 ({row['name']}): {e}")
        return collectors


if __name__ == "__main__":
    dispatcher = EventDispatcher()
    asyncio.run(dispatcher.start())
