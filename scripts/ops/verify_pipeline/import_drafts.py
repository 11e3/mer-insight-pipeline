"""드래프트 검증 결과를 DB에 반영.

사용법:
    1. data/verify_pipeline/stage4_drafts.json 검토
    2. 잘못된 건의 draft_verdict를 수정하거나 "SKIP"으로 변경
    3. python -m scripts.ops.verify_pipeline.import_drafts

source_url 없는 CORRECT/INCORRECT는 자동 스킵 (기존 정책 유지).
"""

import asyncio
import json
import os
from datetime import date

import asyncpg
from dotenv import load_dotenv

from .config import OUTPUT_DIR

load_dotenv()


async def main():
    drafts_path = OUTPUT_DIR / "stage4_drafts.json"
    if not drafts_path.exists():
        print(f"파일 없음: {drafts_path}")
        print("먼저 파이프라인을 실행하세요.")
        return

    drafts = json.loads(drafts_path.read_text(encoding="utf-8"))
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    today = date.today()

    correct = incorrect = pending = skipped_url = skipped_skip = 0

    for d in drafts:
        verdict = d.get("draft_verdict", "").upper().strip()
        source_url = d.get("source_url", "")
        fact = d.get("fact", "")
        pid = d["id"]

        if verdict == "SKIP":
            skipped_skip += 1
            continue

        if verdict in ("CORRECT", "INCORRECT") and not source_url:
            skipped_url += 1
            continue

        if verdict == "CORRECT":
            await conn.execute(
                """UPDATE mer_predictions
                   SET is_correct = true, actual_outcome = $1, source_url = $2,
                       verification_date = $3
                   WHERE id = $4""",
                fact, source_url or None, today, pid,
            )
            correct += 1
        elif verdict == "INCORRECT":
            await conn.execute(
                """UPDATE mer_predictions
                   SET is_correct = false, actual_outcome = $1, source_url = $2,
                       verification_date = $3
                   WHERE id = $4""",
                fact, source_url or None, today, pid,
            )
            incorrect += 1
        else:
            pending += 1

    stats = await conn.fetch(
        "SELECT is_correct, COUNT(*) FROM mer_predictions "
        "GROUP BY is_correct ORDER BY is_correct"
    )
    await conn.close()

    print("\n=== Import 완료 ===")
    print(f"CORRECT={correct}, INCORRECT={incorrect}, PENDING={pending}")
    print(f"source_url 누락 스킵={skipped_url}, SKIP 마킹={skipped_skip}")
    print("\n=== DB 전체 현황 ===")
    for row in stats:
        label = {True: "CORRECT", False: "INCORRECT", None: "PENDING"}[row[0]]
        print(f"  {label}: {row[1]}")


if __name__ == "__main__":
    asyncio.run(main())
