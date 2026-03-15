"""
Event Dispatcher: 메르 글 수집 → 예측 추출 → 데이터 수집 → 자동 검증

매일 20:00 단일 잡으로 전체 파이프라인 실행.

Usage:
    python -m src.pipeline.event_dispatcher
"""

import asyncio
import logging
from datetime import date

import asyncpg
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config.settings import (
    DATABASE_URL, MACRO_ALERT_THRESHOLDS,
)
from src.extract.embedder import get_embedder
from src.pipeline.mer_monitor import MerMonitor
from src.pipeline.dart_collector import DartCollector
from src.pipeline.news_collector import NewsCollector
from src.extract.realtime_extractor import extract_and_save
from src.search.bm25_index import BM25Index
from src.pipeline.prediction_verifier import PredictionVerifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


class EventDispatcher:

    def __init__(self):
        self.pool: asyncpg.Pool | None = None
        self.conn: asyncpg.Connection | None = None
        self.embedder = None
        self.mer_monitor: MerMonitor | None = None
        self.dart: DartCollector | None = None
        self.news_collector: NewsCollector | None = None
        self.bm25_index: BM25Index | None = None
        self.verifier: PredictionVerifier | None = None
        self.scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    async def _init(self):
        """공유 리소스 초기화."""
        from pathlib import Path
        self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
        self.conn = await self.pool.acquire()
        self.embedder = get_embedder()
        self.mer_monitor = MerMonitor(self.conn)
        self.dart = DartCollector(self.conn)
        self.news_collector = NewsCollector(self.conn)

        bm25_cache = Path("data/bm25_cache.pkl")
        self.bm25_index = BM25Index()
        if not self.bm25_index.load(bm25_cache):
            await self.bm25_index.build(self.conn)
            self.bm25_index.save(bm25_cache)
        self.verifier = PredictionVerifier(self.conn)

    async def run(self) -> None:
        """전체 파이프라인 1회 실행 후 종료."""
        await self._init()
        try:
            await self._run_pipeline()
        finally:
            await self.pool.release(self.conn)
            await self.pool.close()

    async def start(self):
        """상시 모드 — APScheduler로 매일 20:00 실행."""
        log.info("EventDispatcher 시작 중...")
        await self._init()

        self.scheduler.add_job(
            self._run_pipeline, "cron",
            hour=22, minute=0, id="daily_pipeline"
        )

        self.scheduler.start()
        log.info("스케줄러 시작 완료 (매일 20:00 실행)")

        try:
            await asyncio.Event().wait()
        finally:
            await self.pool.release(self.conn)
            await self.pool.close()

    # --- 파이프라인 ---

    async def _run_pipeline(self):
        """1. 메르 신규 글 → 2. 외부 데이터 수집 → 3. 예측 검증"""
        log.info("=== 일일 파이프라인 시작 ===")

        # 1. 메르 신규 글 수집 + 예측 추출
        try:
            new_posts = await self.mer_monitor.check_new()
            if new_posts:
                for post in new_posts:
                    extracted = await extract_and_save(self.conn, self.embedder, post)
                    log.info(f"  인사이트 {extracted['count']}개 추출 ({post.get('title', '')[:40]})")
                log.info(f"메르 신규 글 {len(new_posts)}건 처리 완료")
            else:
                log.info("메르 신규 글 없음")
        except Exception as e:
            log.error(f"메르 글 수집 오류: {e}")

        # 2. 외부 데이터 수집
        log.info("외부 데이터 수집 시작")
        await self._collect_dart()
        await self._collect_macro()
        await self._collect_news()

        # 3. 예측 검증
        log.info("예측 검증 시작")
        try:
            resolved = await self.verifier.run()
            log.info(f"예측 검증 완료: {resolved}건 확정")
        except Exception as e:
            log.error(f"예측 검증 오류: {e}")

        log.info("=== 일일 파이프라인 완료 ===")

    # --- 데이터 수집 ---

    async def _collect_dart(self):
        try:
            filings = await self.dart.fetch_recent()
            if filings:
                log.info(f"  DART 공시 {len(filings)}건 수집")
        except Exception as e:
            log.error(f"DART 수집 오류: {e}")

    async def _collect_macro(self):
        try:
            from src.ingest.load_macro import load_macro
            today = date.today().isoformat()
            await load_macro(start=today, end=today)

            # 급변 감지
            rows = await self.conn.fetch("""
                SELECT * FROM macro_daily
                WHERE date >= CURRENT_DATE - INTERVAL '2 days'
                ORDER BY date DESC LIMIT 2
            """)
            if len(rows) >= 2:
                today_row, prev_row = dict(rows[0]), dict(rows[1])
                for col, threshold in MACRO_ALERT_THRESHOLDS.items():
                    curr, prev = today_row.get(col), prev_row.get(col)
                    if not curr or not prev or prev == 0:
                        continue
                    change = abs(curr - prev) / abs(prev)
                    if change >= threshold:
                        direction = "상승" if curr > prev else "하락"
                        log.info(f"  매크로 급변: {col} {prev:.2f} → {curr:.2f} ({direction} {change*100:.1f}%)")
        except Exception as e:
            log.error(f"매크로 수집 오류: {e}")

    async def _collect_news(self):
        try:
            articles = await self.news_collector.fetch_recent()
            if articles:
                log.info(f"  뉴스 {len(articles)}건 수집")
        except Exception as e:
            log.error(f"뉴스 수집 오류: {e}")


if __name__ == "__main__":
    dispatcher = EventDispatcher()
    asyncio.run(dispatcher.start())
