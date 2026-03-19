"""Import manual verification results from grouped JSON files into DB."""
import asyncio
import glob
import json
import os
from datetime import date

import asyncpg
from dotenv import load_dotenv

load_dotenv()


async def main():
    from pathlib import Path
    project_root = Path(__file__).parent.parent.parent
    verify_dir = project_root / "data" / "manual_verify"
    assert verify_dir.exists(), f"Run from project root: {verify_dir} not found"

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    # Later rounds override earlier verdicts (last wins per ID)
    by_id = {}
    base = str(verify_dir)
    # grouped/ 제외 — ID-reason 매핑 오류로 DB 오염 확인됨
    files = sorted(
        glob.glob(f"{base}/result_*.json")
        + glob.glob(f"{base}/round*/*결과*.json")
        + glob.glob(f"{base}/round*/*재판정*.json")
        + glob.glob(f"{base}/round*/*_results.json")
    )
    for f in files:
        with open(f, encoding="utf-8") as fh:
            items = json.load(fh)
        for item in items:
            pid = item["id"]
            # Non-PENDING verdict always overrides PENDING
            prev = by_id.get(pid)
            if prev is None or prev["verdict"] == "PENDING":
                by_id[pid] = item
    all_results = list(by_id.values())

    today = date.today()
    correct_count = 0
    incorrect_count = 0
    pending_count = 0

    for r in all_results:
        verdict = r["verdict"]
        reason = r.get("reason", "")

        if verdict == "CORRECT":
            await conn.execute(
                """UPDATE mer_predictions
                   SET is_correct = true, actual_outcome = $1, verification_date = $2
                   WHERE id = $3""",
                reason, today, r["id"],
            )
            correct_count += 1
        elif verdict == "INCORRECT":
            await conn.execute(
                """UPDATE mer_predictions
                   SET is_correct = false, actual_outcome = $1, verification_date = $2
                   WHERE id = $3""",
                reason, today, r["id"],
            )
            incorrect_count += 1
        else:
            pending_count += 1
            exp = r.get("expected_date")
            if exp:
                await conn.execute(
                    "UPDATE mer_predictions SET expected_date = $1 WHERE id = $2 AND expected_date IS NULL",
                    date.fromisoformat(exp), r["id"],
                )

    # Summary
    stats = await conn.fetch(
        "SELECT is_correct, COUNT(*) FROM mer_predictions GROUP BY is_correct ORDER BY is_correct"
    )
    print("\n=== Import 완료 ===")
    print(f"이번 import: CORRECT={correct_count}, INCORRECT={incorrect_count}, PENDING(skip)={pending_count}")
    print("\n=== DB 전체 현황 ===")
    for row in stats:
        label = {True: "CORRECT", False: "INCORRECT", None: "PENDING"}[row[0]]
        print(f"  {label}: {row[1]}")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
