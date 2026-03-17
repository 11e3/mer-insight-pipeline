"""PENDING 판정 이유에서 연도를 파싱해 expected_date를 설정한다."""
import asyncio
import json
import glob
import os
import re
from datetime import date

import asyncpg
from dotenv import load_dotenv

load_dotenv()


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    updates = []
    for f in sorted(
        glob.glob("data/manual_verify/grouped/*결과*.json")
        + glob.glob("data/manual_verify/round2/*결과*.json")
    ):
        for item in json.load(open(f, encoding="utf-8")):
            if item["verdict"] != "PENDING":
                continue
            reason = item.get("reason", "")
            # 이유 + 예측 텍스트 모두에서 연도 추출
            years = [int(y) for y in re.findall(r"(20[2-9]\d)", reason) if int(y) > 2026]
            if years:
                target_year = max(years)
                updates.append((item["id"], date(target_year, 1, 1)))

    if updates:
        await conn.executemany(
            "UPDATE mer_predictions SET expected_date = $2 WHERE id = $1 AND expected_date IS NULL",
            updates,
        )

    total_skip = await conn.fetchval(
        "SELECT COUNT(*) FROM mer_predictions WHERE is_correct IS NULL AND expected_date > CURRENT_DATE"
    )
    total_pending = await conn.fetchval(
        "SELECT COUNT(*) FROM mer_predictions WHERE is_correct IS NULL"
    )

    print(f"이유에서 파싱: {len(updates)}건 expected_date 설정")
    print(f"총 skip 가능: {total_skip}건")
    print(f"매일 체크 대상: {total_pending - total_skip}건")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
