"""
Event Dispatcher: 예측 추출 → 데이터 수집 → 자동 검증 파이프라인

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
        """공유 리소스 초기화 (start/run_job 공통)."""
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

    async def run_job(self, job: str) -> None:
        """
        Cloud Run Job 진입점 — 단일 작업 실행 후 종료.

        job 값:
          mer_check          — 메르 신규 글 확인 + 예측 추출
          dart_check         — DART 공시 확인
          verify_predictions — 매크로/뉴스 수집 + 예측 자동 검증
        """
        await self._init()
        try:
            if job == "mer_check":
                await self._check_mer_new_posts()
            elif job == "dart_check":
                await self._check_dart_filings()
            elif job == "verify_predictions":
                await self._collect_and_verify()
            else:
                raise ValueError(f"알 수 없는 job: {job}")
        finally:
            await self.pool.release(self.conn)
            await self.pool.close()

    async def start(self):
        log.info("EventDispatcher 시작 중...")
        await self._init()

        # 스케줄 등록
        self.scheduler.add_job(
            self._check_mer_new_posts, "interval", minutes=5, id="mer"
        )
        self.scheduler.add_job(
            self._check_dart_filings, "cron",
            minute="*/10", hour="8-18", day_of_week="mon-fri", id="dart"
        )
        # 예측 검증: 매일 20:00 (매크로/뉴스 수집 포함)
        self.scheduler.add_job(
            self._collect_and_verify, "cron",
            hour=20, minute=0, id="verify_predictions"
        )

        self.scheduler.start()
        log.info("스케줄러 시작 완료")

        try:
            await asyncio.Event().wait()  # 무한 대기
        finally:
            await self.pool.release(self.conn)
            await self.pool.close()

    # --- 이벤트 체크 ---

    async def _check_mer_new_posts(self):
        try:
            new_posts = await self.mer_monitor.check_new()
            if not new_posts:
                return

            for post in new_posts:
                extracted = await extract_and_save(self.conn, self.embedder, post)
                count = extracted["count"]
                log.info(f"  인사이트 {count}개 추출 ({post.get('title', '')[:40]})")

        except Exception as e:
            log.error(f"메르 신규 글 체크 오류: {e}")

    async def _check_dart_filings(self):
        try:
            filings = await self.dart.fetch_recent()
            for f in filings:
                log.info(f"  DART 공시: {f['title'][:50]}")
        except Exception as e:
            log.error(f"DART 공시 체크 오류: {e}")

    async def _check_news(self):
        try:
            articles = await self.news_collector.fetch_recent()
            for article in articles:
                log.info(f"  뉴스: {article['title'][:50]}")
        except Exception as e:
            log.error(f"뉴스 체크 오류: {e}")

    async def _update_macro_data(self):
        """오늘 매크로 데이터 갱신 (FRED + BOK ECOS)."""
        try:
            from src.ingest.load_macro import load_macro
            today = date.today().isoformat()
            await load_macro(start=today, end=today)
        except Exception as e:
            log.error(f"매크로 업데이트 오류: {e}")

    async def _check_macro_alerts(self):
        """전일 대비 급변 감지."""
        try:
            rows = await self.conn.fetch("""
                SELECT * FROM macro_daily
                WHERE date >= CURRENT_DATE - INTERVAL '2 days'
                ORDER BY date DESC LIMIT 2
            """)
            if len(rows) < 2:
                return

            today_row = dict(rows[0])
            prev_row = dict(rows[1])

            alerts = []
            for col, threshold in MACRO_ALERT_THRESHOLDS.items():
                curr = today_row.get(col)
                prev = prev_row.get(col)
                if not curr or not prev or prev == 0:
                    continue
                change = abs(curr - prev) / abs(prev)
                if change >= threshold:
                    direction = "상승" if curr > prev else "하락"
                    alerts.append(
                        f"{col}: {prev:.2f} -> {curr:.2f} "
                        f"({direction} {change*100:.1f}%)"
                    )

            if alerts:
                log.info(f"매크로 급변 감지: {len(alerts)}건")
        except Exception as e:
            log.error(f"매크로 알림 체크 오류: {e}")

    # --- 데이터 수집 + 예측 검증 (일 1회) ---

    async def _collect_and_verify(self):
        """매크로/뉴스 수집 후 예측 검증 — 하루 1회 실행."""
        log.info("데이터 수집 시작 (매크로 + 뉴스)")
        await self._update_macro_data()
        await self._check_macro_alerts()
        await self._check_news()

        log.info("예측 검증 시작")
        try:
            resolved = await self.verifier.run()
            log.info(f"예측 검증 완료: {resolved}건 확정")
        except Exception as e:
            log.error(f"예측 검증 오류: {e}")


if __name__ == "__main__":
    dispatcher = EventDispatcher()
    asyncio.run(dispatcher.start())
