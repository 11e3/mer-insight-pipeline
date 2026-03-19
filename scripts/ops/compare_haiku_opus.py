"""Haiku vs Opus 검증 퀄리티 비교.
past_due 77건을 Haiku로 검증하고, Opus 수동 결과와 비교."""
import asyncio
import json
import os

import asyncpg
import anthropic
from dotenv import load_dotenv

load_dotenv()

SYSTEM = """\
너는 경제·금융 예측 검증 전문가다.
네 자신의 지식을 활용해 [예측 목록]의 각 항목을 판단한다.

판단 기준:
1. CORRECT   — 예측 조건이 충족됐고 결과가 예측과 일치. 구체적 근거가 있어야 함
2. INCORRECT — 예측 조건이 충족됐으나 결과가 예측과 반대. 반박 근거가 있어야 함
3. PENDING   — 조건이 아직 충족되지 않았거나 충분한 정보가 없어 판단 불가

규칙:
- 조건부 예측("A가 발생하면 B")은 A가 실제로 발생했는지 먼저 확인
- 확실한 근거가 없으면 반드시 PENDING. 추측으로 CORRECT/INCORRECT 판정 금지
- 미래 시점 예측은 해당 시점이 지나고 결과가 확인된 경우에만 판정
- reason은 구체적 근거를 인용해 한 줄로

출력: JSON 배열만. 설명 텍스트 없이.
[
  {"id": 예측ID, "verdict": "CORRECT|INCORRECT|PENDING", "reason": "판단 근거 (한국어)"},
  ...
]
"""

BATCH_SIZE = 60


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Load Opus results
    with open("data/manual_verify/round5/past_due_results.json", encoding="utf-8") as f:
        opus_results = json.load(f)
    opus_by_id = {r["id"]: r for r in opus_results}
    target_ids = list(opus_by_id.keys())

    # Fetch predictions from DB
    rows = await conn.fetch("""
        SELECT id, prediction_text, predicted_direction,
               target_asset, prediction_date, expected_date
        FROM mer_predictions
        WHERE id = ANY($1)
        ORDER BY prediction_date, id
    """, target_ids)
    await conn.close()

    print(f"대상: {len(rows)}건")

    # Run Haiku verification in batches
    haiku_results = []
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        pred_list = "\n".join(
            f'{r["id"]}. [{r["target_asset"]}] {r["prediction_text"]} '
            f'(방향: {r["predicted_direction"]}, 예측일: {r["prediction_date"]}, 기한: {r["expected_date"]})'
            for r in batch
        )
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=8192,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f"[예측 목록]\n{pred_list}"}],
        )
        raw = next((b.text for b in resp.content if hasattr(b, "text")), "").strip()
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start >= 0 and end > start:
            batch_results = json.loads(raw[start:end])
            haiku_results.extend(batch_results)
            print(f"  배치 {i // BATCH_SIZE + 1}: {len(batch_results)}건 응답")
            print(f"  토큰: input={resp.usage.input_tokens} output={resp.usage.output_tokens}")
        else:
            print(f"  배치 {i // BATCH_SIZE + 1}: 파싱 실패")

    # Compare
    haiku_by_id = {r["id"]: r for r in haiku_results}

    match = 0
    mismatch = 0
    haiku_pending_opus_not = 0
    opus_pending_haiku_not = 0
    both_pending = 0
    details = []

    for pid in target_ids:
        opus = opus_by_id.get(pid, {})
        haiku = haiku_by_id.get(pid, {})
        ov = opus.get("verdict", "MISSING")
        hv = haiku.get("verdict", "MISSING")

        if ov == hv:
            match += 1
            if ov == "PENDING":
                both_pending += 1
        else:
            mismatch += 1
            if hv == "PENDING" and ov != "PENDING":
                haiku_pending_opus_not += 1
            elif ov == "PENDING" and hv != "PENDING":
                opus_pending_haiku_not += 1
            details.append({
                "id": pid,
                "opus": ov,
                "haiku": hv,
                "opus_reason": opus.get("reason", ""),
                "haiku_reason": haiku.get("reason", ""),
            })

    total = match + mismatch
    print(f"\n{'='*60}")
    print(f"비교 결과: {total}건")
    print(f"  일치: {match}건 ({match/total*100:.1f}%)")
    print(f"    - 둘 다 PENDING: {both_pending}건")
    print(f"    - 둘 다 같은 판정: {match - both_pending}건")
    print(f"  불일치: {mismatch}건 ({mismatch/total*100:.1f}%)")
    print(f"    - Haiku=PENDING, Opus≠PENDING: {haiku_pending_opus_not}건")
    print(f"    - Opus=PENDING, Haiku≠PENDING: {opus_pending_haiku_not}건")
    print(f"    - 판정 상반 (CORRECT↔INCORRECT): {mismatch - haiku_pending_opus_not - opus_pending_haiku_not}건")

    if details:
        print("\n불일치 상세:")
        for d in details:
            print(f"  #{d['id']}: Opus={d['opus']} vs Haiku={d['haiku']}")
            print(f"    Opus:  {d['opus_reason'][:80]}")
            print(f"    Haiku: {d['haiku_reason'][:80]}")

    # Save
    with open("data/manual_verify/round5/haiku_comparison.json", "w", encoding="utf-8") as f:
        json.dump({"haiku_results": haiku_results, "mismatches": details}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
