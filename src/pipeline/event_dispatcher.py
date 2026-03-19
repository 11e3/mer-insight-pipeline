"""
Event Dispatcher: 메르 글 수집 → 예측 추출 → 자동 검증

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
from src.collect.mer_monitor import MerMonitor
from src.extract.realtime import extract_and_save
from src.search.bm25_index import BM25Index
from src.collect.news_collector import NewsCollector
from src.verify import PredictionVerifier

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
        """1. 메르 신규 글 → 2. 예측 검증 (매 실행마다 커넥션 acquire/release)"""
        log.info("=== 일일 파이프라인 시작 ===")

        async with self.pool.acquire() as conn:
            # 1. 메르 신규 글 수집 + 예측 추출
            try:
                monitor = MerMonitor(conn)
                new_posts = await monitor.check_new()
                if new_posts:
                    for post in new_posts:
                        extracted = await extract_and_save(conn, self.embedder, post)
                        log.info(f"  인사이트 {extracted['count']}개 추출 ({post.get('title', '')[:40]})")
                    log.info(f"메르 신규 글 {len(new_posts)}건 처리 완료")
                    # BM25 인덱스 갱신
                    await self.bm25_index.build(conn)
                    self.bm25_index.save(BM25_CACHE)
                    log.info("BM25 인덱스 갱신 완료")
                else:
                    log.info("메르 신규 글 없음")
            except Exception as e:
                log.error(f"메르 글 수집 오류: {e}")

            # 2. 뉴스 헤드라인 수집
            try:
                news_collector = NewsCollector(conn)
                news_count = await news_collector.run_daily()
                log.info(f"뉴스 헤드라인 {news_count}건 수집 완료")
            except Exception as e:
                log.error(f"뉴스 수집 오류: {e}")

            # 3. 예측 검증
            log.info("예측 검증 시작")
            try:
                verifier = PredictionVerifier(conn)
                resolved = await verifier.run()
                log.info(f"예측 검증 완료: {resolved}건 확정")
            except Exception as e:
                log.error(f"예측 검증 오류: {e}")

        log.info("=== 일일 파이프라인 완료 ===")


if __name__ == "__main__":
    dispatcher = EventDispatcher()
    asyncio.run(dispatcher.start())
