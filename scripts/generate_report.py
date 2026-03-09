"""
리포트 즉시 생성 (스케줄 없이 수동 실행).

Usage:
    python scripts/generate_report.py weekly
    python scripts/generate_report.py monthly
    python scripts/generate_report.py quarterly
    python scripts/generate_report.py annual
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncpg
from dotenv import load_dotenv
import os

load_dotenv()

from src.pipeline.report_generator import ReportGenerator
from src.delivery.telegram_bot import TelegramBot


async def main(rtype: str):
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    telegram = TelegramBot()
    reporter = ReportGenerator(conn, telegram)

    generators = {
        "weekly":    reporter.generate_weekly,
        "monthly":   reporter.generate_monthly,
        "quarterly": reporter.generate_quarterly,
        "annual":    reporter.generate_annual,
    }

    fn = generators.get(rtype)
    if not fn:
        print(f"알 수 없는 타입: {rtype}. weekly/monthly/quarterly/annual 중 선택")
        return

    print(f"{rtype} 리포트 생성 중...")
    await fn()
    print("완료")
    await conn.close()


if __name__ == "__main__":
    rtype = sys.argv[1] if len(sys.argv) > 1 else "weekly"
    asyncio.run(main(rtype))
