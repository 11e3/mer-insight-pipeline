"""Export PENDING predictions (no expected_date) for manual verification round 5.
100건씩 배치로 나눠서 claude.ai에서 Opus 4.6으로 수동 검증."""
import asyncio
import os

import asyncpg
from dotenv import load_dotenv

load_dotenv()

BATCH_SIZE = 100

HEADER = """# Round 5 수동 검증 — Part {part} ({count}건)

아래 예측들을 웹 검색으로 확인 후 CORRECT/INCORRECT/PENDING으로 판정해주세요.
- CORRECT: 예측 조건이 충족됐고 결과가 예측과 일치
- INCORRECT: 예측 조건이 충족됐으나 결과가 예측과 반대
- PENDING: 아직 결과를 알 수 없는 미래 예측 → expected_date 필수

출력 형식 (JSON 배열만, 설명 텍스트 없이):
[{{"id": N, "verdict": "CORRECT|INCORRECT|PENDING", "reason": "한줄 근거", "expected_date": "YYYY-MM-DD (PENDING일 때만)"}}]

---

"""


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    rows = await conn.fetch("""
        SELECT id, prediction_text, predicted_direction,
               target_asset, prediction_date
        FROM mer_predictions
        WHERE is_correct IS NULL
          AND expected_date IS NULL
        ORDER BY prediction_date, id
    """)

    print(f"검증 대상: {len(rows)}건 (expected_date 없는 PENDING)")

    out_dir = "data/manual_verify/round5"
    os.makedirs(out_dir, exist_ok=True)

    total_parts = (len(rows) + BATCH_SIZE - 1) // BATCH_SIZE

    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        part = i // BATCH_SIZE + 1
        fname = os.path.join(out_dir, f"part_{part:02d}.txt")

        with open(fname, "w", encoding="utf-8") as f:
            f.write(HEADER.format(part=f"{part}/{total_parts}", count=len(batch)))
            for r in batch:
                pid = r["id"]
                asset = r["target_asset"] or "기타"
                text = r["prediction_text"]
                direction = r["predicted_direction"]
                d = r["prediction_date"]
                date_str = d.isoformat() if d else "없음"
                f.write(
                    f"{pid}. [{asset}] {text} "
                    f"(방향: {direction}, 예측일: {date_str})\n"
                )

        print(f"  part_{part:02d}.txt: {len(batch)}건")

    print(f"\n총 {len(rows)}건 -> {out_dir}/ ({total_parts}개 파일)")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
