"""3단계 예측 검증 파이프라인 오케스트레이터.

Usage:
    python -m scripts.ops.verify_pipeline.run              # 전체 실행
    python -m scripts.ops.verify_pipeline.run --stage 1    # 특정 단계만
    python -m scripts.ops.verify_pipeline.run --stage 3    # stage3부터 재개
    python -m scripts.ops.verify_pipeline.run --dry-run    # 비용 추정만
"""

import argparse
import asyncio
import sys

from .config import CostTracker
from . import stage1_classify, stage2_extract, stage3_verify, stage4_map


async def main():
    parser = argparse.ArgumentParser(description="3단계 검증 파이프라인")
    parser.add_argument("--stage", type=int, default=0, help="특정 단계부터 시작 (1-4)")
    parser.add_argument("--dry-run", action="store_true", help="비용 추정만")
    args = parser.parse_args()

    tracker = CostTracker()
    start = args.stage or 1

    print("=" * 60)
    print("3단계 예측 검증 파이프라인")
    if args.dry_run:
        print("(DRY RUN - 비용 추정만)")
    print("=" * 60)

    try:
        if start <= 1:
            print("\n--- Stage 1: 주제 분류 ---")
            await stage1_classify.run(tracker, dry_run=args.dry_run)

        if start <= 2:
            print("\n--- Stage 2: 검증 포인트 추출 ---")
            await stage2_extract.run(tracker, dry_run=args.dry_run)

        if start <= 3:
            print("\n--- Stage 3: 검색 + 판정 ---")
            await stage3_verify.run(tracker, dry_run=args.dry_run)

        if start <= 4 and not args.dry_run:
            print("\n--- Stage 4: 매핑 ---")
            stage4_map.run()

    except RuntimeError as e:
        print(f"\n⚠️ {e}")
        print("중간 결과는 data/verify_pipeline/에 저장되어 있습니다.")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("파이프라인 완료")
    print(tracker.summary())
    if not args.dry_run:
        print("\n다음 단계:")
        print("  1. data/verify_pipeline/stage4_drafts.json 검토")
        print("  2. python -m scripts.ops.verify_pipeline.import_drafts")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
