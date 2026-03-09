"""
예시 발송 스크립트 — 6가지 메시지 타입을 각각 한 번씩 실제 채널로 전송.

  1. 메르 글 요약      (tier1)
  2. 이벤트 분석       (tier2)
  3. 주간 리포트       (tier2)
  4. 월간 리포트       (tier2)
  5. 분기별 리포트     (tier2)
  6. 연간 리포트       (tier2)

Usage:
    python scripts/send_examples.py
    python scripts/send_examples.py --only weekly   # 특정 타입만
"""

import asyncio
import argparse
import logging
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import asyncpg
import os
from sentence_transformers import SentenceTransformer

from config.settings import DATABASE_URL, EMBEDDING_MODEL
from src.pipeline.event_types import EventType, ANALYSIS_CONFIGS
from src.pipeline.context_assembler import ContextAssembler
from src.pipeline.analysis_generator import AnalysisGenerator
from src.pipeline.report_generator import ReportGenerator
from src.delivery.telegram_bot import TelegramBot
from src.delivery.formatters import format_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def send_mer_summary(conn, assembler, generator, telegram):
    """1. 메르 최신 글 요약 → tier1."""
    log.info("=== [1/6] 메르 글 요약 ===")

    row = await conn.fetchrow("""
        SELECT id, title, content_text AS content, url AS source, date
        FROM mer_posts
        ORDER BY date DESC
        LIMIT 1
    """)
    if not row:
        log.warning("  mer_posts 데이터 없음 — 건너뜀")
        return

    post = dict(row)
    log.info(f"  대상 글: {post['title'][:60]}")

    event = {
        "event_type": EventType.MER_NEW_POST,
        "title":      post["title"],
        "content":    (post["content"] or "")[:3000],
        "source":     post["source"] or "",
        "event_date": datetime.now(),
    }
    config = ANALYSIS_CONFIGS[EventType.MER_NEW_POST]
    context = await assembler.assemble(event, config)
    analysis = await generator.generate(context, config)

    text = format_message(EventType.MER_NEW_POST, event, analysis, len(context["rules"]))
    sent = await telegram.send_raw("tier1", text)
    log.info(f"  → tier1 발송: {'완료' if sent else '실패'}")


async def send_event_analysis(conn, assembler, generator, telegram):
    """2. 최근 이벤트 AI 분석 → tier2."""
    log.info("=== [2/6] 이벤트 분석 ===")

    # 기존 분석이 있는 뉴스/DART 이벤트 우선, 없으면 최신 macro_alert 신규 분석
    row = await conn.fetchrow("""
        SELECT e.id, e.title, e.content, e.source, e.event_type, e.event_date,
               aa.analysis_text, aa.rules_used
        FROM events e
        LEFT JOIN auto_analyses aa ON aa.event_id = e.id
        WHERE e.event_type IN ('news', 'dart')
          AND aa.analysis_text IS NOT NULL
        ORDER BY e.event_date DESC
        LIMIT 1
    """)

    if row:
        event = dict(row)
        log.info(f"  기존 분석 재발송: {event['title'][:60]}")
        try:
            etype = EventType(event["event_type"])
        except ValueError:
            etype = EventType.NEWS_ARTICLE
        text = format_message(etype, event, event["analysis_text"],
                              len(event["rules_used"] or []))
        sent = await telegram.send_raw("tier2", text)
        log.info(f"  → tier2 발송: {'완료' if sent else '실패'}")
        return

    # macro_alert 이벤트로 신규 분석 생성
    row = await conn.fetchrow("""
        SELECT id, title, content, source, event_type, event_date
        FROM events
        WHERE event_type = 'macro_alert'
          AND source = 'macro_monitor'
        ORDER BY event_date DESC
        LIMIT 1
    """)
    if not row:
        log.info("  분석 가능한 이벤트 없음 — 건너뜀")
        return

    event = dict(row)
    log.info(f"  macro_alert 신규 분석: {event['title'][:60]}")

    config = ANALYSIS_CONFIGS[EventType.MACRO_ALERT]
    context = await assembler.assemble(event, config)
    analysis = await generator.generate(context, config)

    text = format_message(EventType.MACRO_ALERT, event, analysis, len(context["rules"]))
    sent = await telegram.send_raw("tier2", text)
    log.info(f"  → tier2 발송: {'완료' if sent else '실패'}")


async def send_reports(conn, telegram, only=None):
    """3~7. 일간/주간/월간/분기/연간 리포트 → tier2."""
    reporter = ReportGenerator(conn, telegram)

    tasks = [
        ("daily",     "=== [3/7] 일간 리포트 ===",    reporter.generate_daily),
        ("weekly",    "=== [4/7] 주간 리포트 ===",    reporter.generate_weekly),
        ("monthly",   "=== [5/7] 월간 리포트 ===",    reporter.generate_monthly),
        ("quarterly", "=== [6/7] 분기 리포트 ===",    reporter.generate_quarterly),
        ("annual",    "=== [7/7] 연간 리포트 ===",    reporter.generate_annual),
    ]

    for key, label, fn in tasks:
        if only and key != only:
            continue
        log.info(label)
        try:
            await fn()
            log.info(f"  → {key} 리포트 발송 완료")
        except Exception as e:
            log.error(f"  → {key} 리포트 오류: {e}")


async def main(only: str | None):
    conn = await asyncpg.connect(DATABASE_URL)
    telegram = TelegramBot()

    report_only = only in ("daily", "weekly", "monthly", "quarterly", "annual") if only else False

    if not report_only:
        log.info("임베딩 모델 로딩...")
        embedder = SentenceTransformer(EMBEDDING_MODEL)
        assembler = ContextAssembler(conn, embedder)
        generator = AnalysisGenerator()

        if not only or only == "mer":
            await send_mer_summary(conn, assembler, generator, telegram)

        if not only or only == "event":
            await send_event_analysis(conn, assembler, generator, telegram)

    if not only or report_only:
        await send_reports(conn, telegram, only=only if report_only else None)

    await conn.close()
    log.info("=== 전체 완료 ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--only",
        choices=["mer", "event", "daily", "weekly", "monthly", "quarterly", "annual"],
        default=None,
        help="특정 타입만 실행",
    )
    args = parser.parse_args()
    asyncio.run(main(args.only))
