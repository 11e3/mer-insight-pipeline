"""
수동 검증 결과 가져오기 — result_NNN.json 파일들을 DB에 반영.

Usage:
    python3 scripts/import_manual_verify.py

data/manual_verify/result_*.json 파일을 읽어서 mer_predictions 업데이트.
"""

import asyncio
import json
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv()

RESULTS_DIR = Path("data/manual_verify")


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    result_files = sorted(RESULTS_DIR.glob("result_*.json"))
    if not result_files:
        print(f"결과 파일 없음: {RESULTS_DIR}/result_*.json")
        return

    total_resolved = 0
    total_pending = 0
    total_errors = 0

    for f in result_files:
        try:
            results = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"  JSON 파싱 실패: {f.name} — {e}")
            total_errors += 1
            continue

        resolved = 0
        for r in results:
            verdict = r.get("verdict", "PENDING")
            if verdict == "PENDING":
                total_pending += 1
                continue
            pred_id = r.get("id")
            if not pred_id:
                continue
            await conn.execute("""
                UPDATE mer_predictions
                SET is_correct = $1,
                    actual_outcome = $2,
                    verification_date = CURRENT_DATE
                WHERE id = $3 AND is_correct IS NULL
            """, verdict == "CORRECT", r.get("reason", ""), pred_id)
            resolved += 1

        total_resolved += resolved
        print(f"  {f.name}: {resolved}건 확정, {total_pending}건 PENDING")

    await conn.close()
    print(f"\n완료: {total_resolved}건 확정, {total_pending}건 PENDING, {total_errors}건 에러")


if __name__ == "__main__":
    asyncio.run(main())
