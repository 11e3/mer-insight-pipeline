"""
Week 1 전체 파이프라인 실행 스크립트.

단계별 실행:
    python scripts/run_batch.py phase1       # DB 적재
    python scripts/run_batch.py test100      # Haiku 100개 테스트
    python scripts/run_batch.py batch        # Sonnet 전체 배치 실행
    python scripts/run_batch.py status <id>  # 배치 진행 확인
    python scripts/run_batch.py parse <id>   # 결과 파싱 + DB 적재
    python scripts/run_batch.py embed        # 임베딩 생성
    python scripts/run_batch.py all          # 전체 자동 실행 (단계별 확인 포함)
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio

from src.ingest.load_posts import load_posts
from src.extract.batch_api import create_batch, check_status, download_results, create_retokenize_batch
from src.extract.parse_results import parse_and_insert
from src.extract.embeddings import fill_embeddings
from config.settings import MODEL_HAIKU, MODEL_SONNET


def ask(prompt: str) -> bool:
    return input(f"\n{prompt} [y/N] ").strip().lower() == "y"


async def phase1():
    print("=" * 50)
    print("Phase 1: 데이터 적재")
    print("=" * 50)

    print("\n[1/1] 포스트 JSON → PostgreSQL")
    await load_posts()


async def run_all():
    print("=" * 60)
    print("  메르 인사이트 파이프라인 — 전체 실행")
    print("=" * 60)

    # Phase 1
    await phase1()

    # 테스트 배치 (Haiku 100개)
    if ask("Haiku 100개 테스트 배치 실행?"):
        batch_id = await create_batch(model=MODEL_HAIKU, limit=100)
        print(f"\n배치 ID: {batch_id}")
        print("완료 후 'python scripts/run_batch.py parse <batch_id>' 실행")
        return

    # 전체 배치 (Sonnet)
    if ask("Sonnet 전체 배치 실행? (~$25, 약 30분)"):
        batch_id = await create_batch(model=MODEL_SONNET)
        print(f"\n배치 ID: {batch_id}")
        print(f"상태 확인: python scripts/run_batch.py status {batch_id}")
        print(f"결과 파싱: python scripts/run_batch.py parse {batch_id}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "phase1":
        asyncio.run(phase1())

    elif cmd == "test100":
        asyncio.run(create_batch(model=MODEL_HAIKU, limit=100))

    elif cmd == "batch":
        asyncio.run(create_batch(model=MODEL_SONNET))

    elif cmd == "status":
        batch_id = sys.argv[2]
        done = check_status(batch_id)
        if done:
            print(f"\n완료! 결과 다운로드: python scripts/run_batch.py parse {batch_id}")

    elif cmd == "parse":
        batch_id = sys.argv[2]
        results_path = download_results(batch_id)
        asyncio.run(parse_and_insert(results_path))
        print("\n임베딩 생성 시작...")
        asyncio.run(fill_embeddings())

    elif cmd == "embed":
        asyncio.run(fill_embeddings())

    elif cmd == "retokenize":
        # 짤린 247개 재처리: max_tokens=8192
        if len(sys.argv) < 3:
            print("Usage: python scripts/run_batch.py retokenize <results.jsonl>")
            sys.exit(1)
        results_path = sys.argv[2]
        batch_id = asyncio.run(create_retokenize_batch(results_path))
        if batch_id:
            print("\n재배치 완료되면:")
            print(f"  python scripts/run_batch.py parse {batch_id}")

    elif cmd == "all":
        asyncio.run(run_all())

    else:
        print(__doc__)
