"""Export past-due PENDING predictions for manual verification round 5 추가분."""
import asyncio
import os

import asyncpg
from dotenv import load_dotenv

load_dotenv()

HEADER = """# Round 5 수동 검증 — 기한 경과분 ({count}건)

expected_date가 지났는데 아직 PENDING인 예측입니다.
웹 검색으로 확인 후 CORRECT/INCORRECT/PENDING으로 판정해주세요.

출력 형식 (JSON 배열만, 설명 텍스트 없이):
[{{"id": N, "verdict": "CORRECT|INCORRECT|PENDING", "reason": "한줄 근거", "expected_date": "YYYY-MM-DD (PENDING일 때만, 새 기한)"}}]

---

"""


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    rows = await conn.fetch("""
        SELECT id, prediction_text, predicted_direction,
               target_asset, prediction_date, expected_date
        FROM mer_predictions
        WHERE is_correct IS NULL
          AND expected_date IS NOT NULL
          AND expected_date <= CURRENT_DATE
        ORDER BY prediction_date, id
    """)

    print(f"기한 경과 PENDING: {len(rows)}건")

    out_dir = "data/manual_verify/round5"
    os.makedirs(out_dir, exist_ok=True)

    fname = os.path.join(out_dir, "past_due.txt")
    with open(fname, "w", encoding="utf-8") as f:
        f.write(HEADER.format(count=len(rows)))
        for r in rows:
            pid = r["id"]
            asset = r["target_asset"] or "기타"
            text = r["prediction_text"]
            direction = r["predicted_direction"]
            d = r["prediction_date"]
            exp = r["expected_date"]
            date_str = d.isoformat() if d else "없음"
            exp_str = exp.isoformat() if exp else "없음"
            f.write(
                f"{pid}. [{asset}] {text} "
                f"(방향: {direction}, 예측일: {date_str}, 기한: {exp_str})\n"
            )

    print(f"-> {fname}")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
