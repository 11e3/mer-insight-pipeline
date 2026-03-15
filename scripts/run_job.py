"""
Cloud Run Job 진입점.

각 잡은 이 스크립트를 --job 인자만 다르게 해서 실행.

Usage (로컬):
    python -m scripts.run_job --job mer_check
    python -m scripts.run_job --job dart_check
    python -m scripts.run_job --job macro_check
    python -m scripts.run_job --job verify_predictions

Cloud Run Job 배포 시 --args 에 해당 값 전달.
"""

import argparse
import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

VALID_JOBS = {
    "mer_check", "dart_check", "macro_check",
    "verify_predictions",
}


async def main(job: str) -> None:
    from src.pipeline.event_dispatcher import EventDispatcher
    dispatcher = EventDispatcher()
    await dispatcher.run_job(job)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MER Pipeline Job Runner")
    parser.add_argument(
        "--job",
        required=True,
        choices=sorted(VALID_JOBS),
        help="실행할 잡 이름",
    )
    args = parser.parse_args()
    asyncio.run(main(args.job))
