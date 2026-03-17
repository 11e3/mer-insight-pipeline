"""Haiku vs Sonnet 판정 비교 — 100건 샘플."""

import asyncio
import json
import os

import anthropic
import asyncpg
from dotenv import load_dotenv

load_dotenv()

SYSTEM = """\
너는 경제·금융 예측 검증 전문가다.
[예측]과 [Haiku 판정]을 검토하고, 네 자신의 지식과 판단으로 동의 여부를 답한다.

출력: JSON만. 설명 없이.
{"agree": true/false, "your_verdict": "CORRECT|INCORRECT|PENDING", "reason": "한국어 한줄"}
"""


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    rows = await conn.fetch("""
        SELECT id, prediction_text, predicted_direction, target_asset,
               prediction_date, is_correct, actual_outcome
        FROM mer_predictions
        WHERE is_correct IS NOT NULL
        ORDER BY verification_date DESC
        LIMIT 100
    """)
    await conn.close()

    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    agree = 0
    disagree = 0
    errors = 0
    disagree_details = []

    for i, r in enumerate(rows):
        haiku_verdict = "CORRECT" if r["is_correct"] else "INCORRECT"
        pred_text = r["prediction_text"] or ""
        asset = r["target_asset"] or ""
        direction = r["predicted_direction"] or ""
        pred_date = str(r["prediction_date"] or "")
        outcome = r["actual_outcome"] or ""

        msg = (
            f"[예측] {pred_text}\n"
            f"대상: {asset}, 방향: {direction}, 예측일: {pred_date}\n\n"
            f"[Haiku 판정] {haiku_verdict}\n"
            f"[Haiku 근거] {outcome}"
        )
        try:
            resp = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=256,
                system=SYSTEM,
                messages=[{"role": "user", "content": msg}],
            )
            raw = resp.content[0].text.strip()
            start = raw.find("{")
            end = raw.rfind("}") + 1
            result = json.loads(raw[start:end])

            if result.get("agree"):
                agree += 1
            else:
                disagree += 1
                disagree_details.append({
                    "id": r["id"],
                    "pred": pred_text[:60],
                    "haiku": haiku_verdict,
                    "sonnet": result.get("your_verdict"),
                    "reason": result.get("reason", "")[:80],
                })
        except Exception as e:
            errors += 1

        if (i + 1) % 20 == 0:
            print(f"진행: {i+1}/100 (동의={agree}, 불일치={disagree}, 에러={errors})")

    print("\n=== 최종 결과 ===")
    print(f"동의: {agree}/100 ({agree}%)")
    print(f"불일치: {disagree}/100 ({disagree}%)")
    print(f"에러: {errors}")

    if disagree_details:
        print("\n=== 불일치 상세 (상위 10건) ===")
        for d in disagree_details[:10]:
            print(f"  #{d['id']} Haiku={d['haiku']} Sonnet={d['sonnet']}: {d['pred']}")
            print(f"    이유: {d['reason']}")


if __name__ == "__main__":
    asyncio.run(main())
