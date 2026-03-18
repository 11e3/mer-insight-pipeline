"""
Cloud Run Job / 로컬 실행 진입점.

전체 파이프라인을 1회 실행: 메르 글 수집 → 예측 검증.

Usage:
    python -m scripts.run_job
"""

import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)


async def main() -> None:
    from src.pipeline.event_dispatcher import EventDispatcher
    dispatcher = EventDispatcher()
    await dispatcher.run()


if __name__ == "__main__":
    asyncio.run(main())
