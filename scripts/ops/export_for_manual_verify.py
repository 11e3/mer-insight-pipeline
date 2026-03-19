"""
수동 검증용 내보내기 — PENDING 예측을 100건씩 텍스트 파일로 생성.

Usage:
    python3 scripts/export_for_manual_verify.py

출력: data/manual_verify/batch_001.txt, batch_002.txt, ...
각 파일을 claude.ai에 붙여넣고 JSON 결과를 data/manual_verify/result_001.json에 저장.
"""

import asyncio
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv()

BATCH_SIZE = 100
OUT_DIR = Path("data/manual_verify")

SYSTEM = """\
너는 경제·금융 예측 검증 전문가다.
아래 [예측 목록]의 각 항목을 판단한다.

판단 기준:
1. CORRECT   — 예측 조건이 충족됐고 결과가 예측과 일치. 구체적 근거가 있어야 함
2. INCORRECT — 예측 조건이 충족됐으나 결과가 예측과 반대. 반박 근거가 있어야 함
3. PENDING   — 조건이 아직 충족되지 않았거나 충분한 정보가 없어 판단 불가

규칙:
- 확실한 근거가 없으면 반드시 PENDING. 추측으로 CORRECT/INCORRECT 판정 금지
- 미래 시점 예측은 해당 시점이 지나고 결과가 확인된 경우에만 판정
- 수치를 지어내지 마라. 네가 확실히 아는 사실만 근거로 사용
- reason은 근거를 구체적으로 한 줄로 (수치 포함 권장)

출력: JSON 배열만. 설명 텍스트 없이.
[
  {"id": 예측ID, "verdict": "CORRECT|INCORRECT|PENDING", "reason": "판단 근거 (한국어)"},
  ...
]
"""


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    rows = await conn.fetch("""
        SELECT id, prediction_text, predicted_direction,
               target_asset, prediction_date
        FROM mer_predictions
        WHERE is_correct IS NULL
        ORDER BY prediction_date DESC
    """)
    await conn.close()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    total_batches = (len(rows) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"PENDING: {len(rows)}건 → {total_batches}개 배치 파일 생성")

    for i in range(0, len(rows), BATCH_SIZE):
        batch_num = i // BATCH_SIZE + 1
        batch = rows[i:i + BATCH_SIZE]

        pred_list = "\n".join(
            f'{r["id"]}. [{r["target_asset"]}] {r["prediction_text"]} '
            f'(방향: {r["predicted_direction"]}, 예측일: {r["prediction_date"]})'
            for r in batch
        )

        content = f"{SYSTEM}\n\n[예측 목록]\n{pred_list}"

        out_file = OUT_DIR / f"batch_{batch_num:03d}.txt"
        out_file.write_text(content, encoding="utf-8")

    print(f"\n생성 완료: {OUT_DIR}/batch_001.txt ~ batch_{total_batches:03d}.txt")
    print("\n사용법:")
    print("  1. 각 batch_NNN.txt 내용을 claude.ai에 붙여넣기")
    print(f"  2. JSON 배열 결과를 {OUT_DIR}/result_NNN.json에 저장")
    print("  3. python3 scripts/import_manual_verify.py 실행")


if __name__ == "__main__":
    asyncio.run(main())
