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
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    all_results = []
    seen_ids = set()
    files = (
        sorted(glob.glob("data/manual_verify/grouped/*결과*.json"))
        + sorted(glob.glob("data/manual_verify/result_*.json"))
    )
    for f in files:
        items = json.load(open(f, encoding="utf-8"))
        for item in items:
            pid = item["id"]
            if pid not in seen_ids:
                seen_ids.add(pid)
                all_results.append(item)

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
                   WHERE id = $3 AND is_correct IS NULL""",
                reason, today, r["id"],
            )
            correct_count += 1
        elif verdict == "INCORRECT":
            await conn.execute(
                """UPDATE mer_predictions
                   SET is_correct = false, actual_outcome = $1, verification_date = $2
                   WHERE id = $3 AND is_correct IS NULL""",
                reason, today, r["id"],
            )
            incorrect_count += 1
        else:
            pending_count += 1

    # Summary
    stats = await conn.fetch(
        "SELECT is_correct, COUNT(*) FROM mer_predictions GROUP BY is_correct ORDER BY is_correct"
    )
    print(f"\n=== Import 완료 ===")
    print(f"이번 import: CORRECT={correct_count}, INCORRECT={incorrect_count}, PENDING(skip)={pending_count}")
    print(f"\n=== DB 전체 현황 ===")
    for row in stats:
        label = {True: "CORRECT", False: "INCORRECT", None: "PENDING"}[row[0]]
        print(f"  {label}: {row[1]}")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
