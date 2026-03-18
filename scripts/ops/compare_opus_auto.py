"""Opus 자동 검증 vs Opus 수동 검증 비교.
past_due 77건을 Opus API로 검증하고, 수동 결과와 비교."""
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

    # Load manual Opus results
    with open("data/manual_verify/round5/past_due_results.json", encoding="utf-8") as f:
        manual_results = json.load(f)
    manual_by_id = {r["id"]: r for r in manual_results}
    target_ids = list(manual_by_id.keys())

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

    # Run Opus verification
    opus_results = []
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_create = 0

    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        pred_list = "\n".join(
            f'{r["id"]}. [{r["target_asset"]}] {r["prediction_text"]} '
            f'(방향: {r["predicted_direction"]}, 예측일: {r["prediction_date"]}, 기한: {r["expected_date"]})'
            for r in batch
        )
        for attempt in range(5):
            try:
                resp = await client.messages.create(
                    model="claude-opus-4-6",
                    max_tokens=8192,
                    system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": f"[예측 목록]\n{pred_list}"}],
                )
                break
            except anthropic.InternalServerError:
                wait = 10 * (attempt + 1)
                print(f"    API 과부하, {wait}초 대기 후 재시도 ({attempt + 1}/5)")
                await asyncio.sleep(wait)
        else:
            print(f"  배치 {i // BATCH_SIZE + 1}: 5회 재시도 실패, 스킵")
            continue
        raw = next((b.text for b in resp.content if hasattr(b, "text")), "").strip()

        input_tokens = resp.usage.input_tokens
        output_tokens = resp.usage.output_tokens
        cache_read = getattr(resp.usage, "cache_read_input_tokens", 0) or 0
        cache_create = getattr(resp.usage, "cache_creation_input_tokens", 0) or 0

        total_input += input_tokens
        total_output += output_tokens
        total_cache_read += cache_read
        total_cache_create += cache_create

        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start >= 0 and end > start:
            batch_results = json.loads(raw[start:end])
            opus_results.extend(batch_results)
            print(f"  배치 {i // BATCH_SIZE + 1}: {len(batch_results)}건 응답")
        else:
            print(f"  배치 {i // BATCH_SIZE + 1}: 파싱 실패")

        print(f"    input={input_tokens} output={output_tokens} cache_read={cache_read} cache_create={cache_create}")

    # Cost calculation (Opus 4.6)
    # Input: $15/1M, Output: $75/1M, Cache read: $1.50/1M, Cache create: $18.75/1M
    uncached_input = total_input - total_cache_read - total_cache_create
    cost_input = uncached_input * 15 / 1_000_000
    cost_cache_read = total_cache_read * 1.50 / 1_000_000
    cost_cache_create = total_cache_create * 18.75 / 1_000_000
    cost_output = total_output * 75 / 1_000_000
    total_cost = cost_input + cost_cache_read + cost_cache_create + cost_output

    print(f"\n{'='*60}")
    print(f"비용 (Opus 4.6)")
    print(f"  Input: {total_input} tokens (uncached: {uncached_input}, cache_read: {total_cache_read}, cache_create: {total_cache_create})")
    print(f"  Output: {total_output} tokens")
    print(f"  비용: ${total_cost:.4f} (input=${cost_input:.4f} + cache_read=${cost_cache_read:.4f} + cache_create=${cost_cache_create:.4f} + output=${cost_output:.4f})")
    print(f"  1건당: ${total_cost / len(rows):.5f} (약 {total_cost / len(rows) * 1400:.1f}원)")

    # Compare with manual
    auto_by_id = {r["id"]: r for r in opus_results}

    match = 0
    mismatch = 0
    both_pending = 0
    auto_pending_manual_not = 0
    manual_pending_auto_not = 0
    verdict_flip = 0
    details = []

    for pid in target_ids:
        manual = manual_by_id.get(pid, {})
        auto = auto_by_id.get(pid, {})
        mv = manual.get("verdict", "MISSING")
        av = auto.get("verdict", "MISSING")

        if mv == av:
            match += 1
            if mv == "PENDING":
                both_pending += 1
        else:
            mismatch += 1
            if av == "PENDING" and mv != "PENDING":
                auto_pending_manual_not += 1
            elif mv == "PENDING" and av != "PENDING":
                manual_pending_auto_not += 1
            elif mv in ("CORRECT", "INCORRECT") and av in ("CORRECT", "INCORRECT"):
                verdict_flip += 1
            details.append({
                "id": pid,
                "manual": mv,
                "auto": av,
                "manual_reason": manual.get("reason", ""),
                "auto_reason": auto.get("reason", ""),
            })

    total = match + mismatch
    print(f"\n{'='*60}")
    print(f"수동 Opus vs 자동 Opus 비교: {total}건")
    print(f"  일치: {match}건 ({match/total*100:.1f}%)")
    print(f"    - 둘 다 PENDING: {both_pending}건")
    print(f"    - 둘 다 같은 판정: {match - both_pending}건")
    print(f"  불일치: {mismatch}건 ({mismatch/total*100:.1f}%)")
    print(f"    - 자동=PENDING, 수동≠PENDING: {auto_pending_manual_not}건")
    print(f"    - 수동=PENDING, 자동≠PENDING: {manual_pending_auto_not}건")
    print(f"    - 판정 상반 (CORRECT↔INCORRECT): {verdict_flip}건")

    if details:
        print(f"\n불일치 상세:")
        for d in details:
            print(f"  #{d['id']}: 수동={d['manual']} vs 자동={d['auto']}")
            print(f"    수동: {d['manual_reason'][:80]}")
            print(f"    자동: {d['auto_reason'][:80]}")

    # Save
    with open("data/manual_verify/round5/opus_auto_comparison.json", "w", encoding="utf-8") as f:
        json.dump({"opus_auto_results": opus_results, "mismatches": details}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
