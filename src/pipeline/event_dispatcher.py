"""
Event Dispatcher: 이벤트 소스 폴링 → 분석 트리거 → 결과 저장/발송

Usage:
    python -m src.pipeline.event_dispatcher
"""

import asyncio
import json
import logging
from datetime import datetime, date

import asyncpg
import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sentence_transformers import SentenceTransformer

from config.settings import (
    DATABASE_URL, MACRO_ALERT_THRESHOLDS,
    EMBEDDING_MODEL, BLOG_RSS,
)
from src.pipeline.event_types import EventType, ANALYSIS_CONFIGS
from src.pipeline.context_assembler import ContextAssembler
from src.pipeline.analysis_generator import AnalysisGenerator
from src.pipeline.mer_monitor import MerMonitor
from src.pipeline.dart_collector import DartCollector
from src.pipeline.news_collector import NewsCollector
from src.delivery.telegram_bot import TelegramBot
from src.extract.realtime_extractor import extract_and_save, is_economic
from src.delivery.formatters import format_short_post
from src.pipeline.report_generator import ReportGenerator
from src.agent.agent import MerAgent
from src.search.bm25_index import BM25Index

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


class EventDispatcher:

    def __init__(self):
        self.conn: asyncpg.Connection | None = None
        self.embedder: SentenceTransformer | None = None
        self.assembler: ContextAssembler | None = None
        self.generator = AnalysisGenerator()
        self.telegram  = TelegramBot()
        self.mer_monitor: MerMonitor | None = None
        self.dart: DartCollector | None = None
        self.news_collector: NewsCollector | None = None
        self.reporter: ReportGenerator | None = None
        self.bm25_index: BM25Index | None = None
        self.mer_agent: MerAgent | None = None
        self.scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    async def start(self):
        log.info("EventDispatcher 시작 중...")
        self.conn = await asyncpg.connect(DATABASE_URL)
        self.embedder = SentenceTransformer(EMBEDDING_MODEL)
        self.assembler = ContextAssembler(self.conn, self.embedder)
        self.mer_monitor = MerMonitor(self.conn)
        self.dart = DartCollector(self.conn)
        self.news_collector = NewsCollector(self.conn)
        self.reporter = ReportGenerator(self.conn, self.telegram)

        # BM25 인덱스 로드 (없으면 빌드)
        from pathlib import Path
        bm25_cache = Path("data/bm25_cache.pkl")
        self.bm25_index = BM25Index()
        if not self.bm25_index.load(bm25_cache):
            await self.bm25_index.build(self.conn)
            self.bm25_index.save(bm25_cache)
        self.mer_agent = MerAgent(self.conn, self.embedder, self.bm25_index)

        # 스케줄 등록
        self.scheduler.add_job(
            self._check_mer_new_posts, "interval", minutes=5, id="mer"
        )
        self.scheduler.add_job(
            self._check_dart_filings, "cron",
            minute="*/10", hour="8-18", day_of_week="mon-fri", id="dart"
        )
        self.scheduler.add_job(
            self._update_macro_data, "interval", hours=1, id="macro_update"
        )
        self.scheduler.add_job(
            self._check_macro_alerts, "interval", minutes=30, id="macro_alert"
        )
        self.scheduler.add_job(
            self._check_news, "interval", minutes=30, id="news"
        )
        # 일간 리포트: 매일 21:00
        self.scheduler.add_job(
            self._send_daily_report, "cron",
            hour=21, minute=0, id="report_daily"
        )
        # 주간 리포트: 매주 월요일 08:00
        self.scheduler.add_job(
            self._send_weekly_report, "cron",
            day_of_week="mon", hour=8, minute=0, id="report_weekly"
        )
        # 월간 리포트: 매월 1일 08:30
        self.scheduler.add_job(
            self._send_monthly_report, "cron",
            day=1, hour=8, minute=30, id="report_monthly"
        )
        # 분기 리포트: 1/4/7/10월 1일 09:00
        self.scheduler.add_job(
            self._send_quarterly_report, "cron",
            month="1,4,7,10", day=1, hour=9, minute=0, id="report_quarterly"
        )
        # 연간 리포트: 1월 1일 10:00
        self.scheduler.add_job(
            self._send_annual_report, "cron",
            month=1, day=1, hour=10, minute=0, id="report_annual"
        )

        self.scheduler.start()
        log.info("스케줄러 시작 완료")

        try:
            await asyncio.Event().wait()  # 무한 대기
        finally:
            await self.conn.close()

    # ─── 이벤트 체크 ─────────────────────────────────────────────────────────

    async def _check_mer_new_posts(self):
        try:
            new_posts = await self.mer_monitor.check_new()
            for post in new_posts:
                # 인사이트 추출 + primary_topic 파악 (Haiku)
                extracted = await extract_and_save(self.conn, self.embedder, post)
                topic   = extracted["primary_topic"]
                summary = extracted["post_summary"]

                if is_economic(topic, post.get("title", ""), post.get("content_text", "")):
                    # 경제 관련 → 에이전트 루프 분석 + 텔레그램 발송
                    await self._process_mer_post_with_agent(post)
                else:
                    # 비경제 (건강/라이프/기타) → 짧은 요약만 발송
                    log.info(f"  비경제 포스팅 ({topic}) — 짧은 포맷 발송")
                    msg = format_short_post(post, topic, summary)
                    await self.telegram.send_raw(
                        channel="tier1",
                        text=msg,
                    )
        except Exception as e:
            log.error(f"메르 신규 글 체크 오류: {e}")

    async def _process_mer_post_with_agent(self, post: dict):
        """MER 신규 포스트를 에이전트 루프로 분석. 실패 시 기존 방식 fallback."""
        event = {
            "event_type": EventType.MER_NEW_POST,
            "title":      post["title"],
            "content":    post["content_text"],
            "source":     post["url"],
            "event_date": datetime.now(),
        }

        # events 테이블 저장
        raw_vec = self.embedder.encode(
            [f"passage: {post['title']} {post.get('content_text', '')[:300]}"],
            normalize_embeddings=True,
        )[0].tolist()
        vec = "[" + ",".join(f"{v:.8f}" for v in raw_vec) + "]"
        event_id = await self.conn.fetchval("""
            INSERT INTO events (event_type, source, title, content, event_date, embedding)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
        """, event["event_type"].value, event["source"], event["title"],
            event["content"], event["event_date"], vec)

        # 에이전트 루프 실행 (실패 시 fallback)
        try:
            analysis = await self.mer_agent.run(post)
            log.info("  에이전트 분석 완료")
        except Exception as e:
            log.warning(f"  에이전트 실패, fallback 실행: {e}")
            config  = ANALYSIS_CONFIGS[EventType.MER_NEW_POST]
            context = await self.assembler.assemble(event, config)
            analysis = await self.generator.generate(context, config)

        sent = await self.telegram.send_analysis(
            channel="tier1",
            event=event,
            analysis=analysis,
            rules_count=0,
        )
        await self.conn.execute("""
            INSERT INTO auto_analyses
                (event_id, analysis_text, rules_used, macro_context, telegram_sent, telegram_sent_at)
            VALUES ($1, $2, $3, $4, $5, $6)
        """, event_id, analysis, [], json.dumps({}), sent,
            datetime.now() if sent else None)

        log.info(f"  MER 포스트 처리 완료 (텔레그램: {'발송' if sent else '실패'})")

    async def _check_dart_filings(self):
        try:
            filings = await self.dart.fetch_recent()
            for f in filings:
                await self._process_event({
                    "event_type": EventType.DART_FILING,
                    "title":      f["title"],
                    "content":    f["content"],
                    "source":     f["url"],
                    "event_date": f["date"],
                })
        except Exception as e:
            log.error(f"DART 공시 체크 오류: {e}")

    async def _check_news(self):
        try:
            articles = await self.news_collector.fetch_recent()
            for article in articles:
                await self._process_event({
                    "event_type": EventType.NEWS_ARTICLE,
                    "title":      article["title"],
                    "content":    article["content"],
                    "source":     article["url"],
                    "event_date": article["date"],
                })
        except Exception as e:
            log.error(f"뉴스 체크 오류: {e}")

    async def _update_macro_data(self):
        """오늘 매크로 데이터 갱신 (yfinance 장중 데이터)."""
        try:
            from src.ingest.load_macro import load_macro
            today = date.today().isoformat()
            await load_macro(start=today, end=today)
        except Exception as e:
            log.error(f"매크로 업데이트 오류: {e}")

    async def _check_macro_alerts(self):
        """전일 대비 급변 감지 → MACRO_ALERT 이벤트 생성."""
        try:
            rows = await self.conn.fetch("""
                SELECT * FROM macro_daily
                WHERE date >= CURRENT_DATE - INTERVAL '2 days'
                ORDER BY date DESC LIMIT 2
            """)
            if len(rows) < 2:
                return

            today_row = dict(rows[0])
            prev_row  = dict(rows[1])

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
                        f"{col}: {prev:.2f} → {curr:.2f} "
                        f"({direction} {change*100:.1f}%)"
                    )

            if alerts:
                await self._process_event({
                    "event_type": EventType.MACRO_ALERT,
                    "title":      f"매크로 급변 감지 ({date.today()})",
                    "content":    "\n".join(alerts),
                    "source":     "macro_monitor",
                    "event_date": datetime.now(),
                })
        except Exception as e:
            log.error(f"매크로 알림 체크 오류: {e}")

    # ─── 주기 리포트 ─────────────────────────────────────────────────────────

    async def _send_daily_report(self):
        try:
            await self.reporter.generate_daily()
        except Exception as e:
            log.error(f"일간 리포트 오류: {e}")

    async def _send_weekly_report(self):
        try:
            await self.reporter.generate_weekly()
        except Exception as e:
            log.error(f"주간 리포트 오류: {e}")

    async def _send_monthly_report(self):
        try:
            await self.reporter.generate_monthly()
        except Exception as e:
            log.error(f"월간 리포트 오류: {e}")

    async def _send_quarterly_report(self):
        try:
            await self.reporter.generate_quarterly()
        except Exception as e:
            log.error(f"분기 리포트 오류: {e}")

    async def _send_annual_report(self):
        try:
            await self.reporter.generate_annual()
        except Exception as e:
            log.error(f"연간 리포트 오류: {e}")

    # ─── 이벤트 처리 ─────────────────────────────────────────────────────────

    async def _process_event(self, event: dict):
        event_type = event["event_type"]
        config     = ANALYSIS_CONFIGS[event_type]
        log.info(f"이벤트 처리: {event_type.value} — {event.get('title', '')[:50]}")

        # events 테이블 저장 + embedding
        raw_vec = self.embedder.encode(
            [f"passage: {event.get('title', '')} {event.get('content', '')[:300]}"],
            normalize_embeddings=True
        )[0].tolist()
        vec = "[" + ",".join(f"{v:.8f}" for v in raw_vec) + "]"

        event_id = await self.conn.fetchval("""
            INSERT INTO events (event_type, source, title, content, event_date, embedding)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
        """,
            event_type.value,
            event.get("source", ""),
            event.get("title", ""),
            event.get("content", ""),
            event.get("event_date", datetime.now()),
            vec,
        )

        # 컨텍스트 조립 + 분석 생성
        context  = await self.assembler.assemble(event, config)
        analysis = await self.generator.generate(context, config)

        # 텔레그램 발송
        sent = await self.telegram.send_analysis(
            channel=config.telegram_channel,
            event=event,
            analysis=analysis,
            rules_count=len(context["rules"]),
        )

        # 결과 저장
        await self.conn.execute("""
            INSERT INTO auto_analyses
                (event_id, analysis_text, rules_used, macro_context, telegram_sent, telegram_sent_at)
            VALUES ($1, $2, $3, $4, $5, $6)
        """,
            event_id,
            analysis,
            [r["id"] for r in context["rules"]],
            json.dumps(context["macro_context"], default=str),
            sent,
            datetime.now() if sent else None,
        )

        log.info(f"  → 분석 완료 (텔레그램: {'발송' if sent else '실패'})")


if __name__ == "__main__":
    dispatcher = EventDispatcher()
    asyncio.run(dispatcher.start())
