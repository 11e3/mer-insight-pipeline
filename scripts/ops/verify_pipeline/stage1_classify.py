"""Stage 1: 주제 분류 — Haiku, no search."""

import asyncio
import json
import os

import asyncpg
import anthropic
from dotenv import load_dotenv

from .config import MODEL, OUTPUT_DIR, CostTracker

load_dotenv()

SYSTEM = """\
예측 목록을 주제별로 분류하라. 각 예측 ID에 주제 라벨 하나를 배정.

주제 목록:
에너지, 금리_매크로, 환율, 부동산, 주식_금융, AI_반도체, 관세_무역,
트럼프_미국, 중동_지정학, 조선_해운, 방산, 원자재, 식품_농업, 의료_바이오, 기타

출력: JSON만. {"results": [{"id": N, "topic": "주제"}, ...]}
"""

BATCH_SIZE = 100


def _parse_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
    return None


async def run(tracker: CostTracker, dry_run: bool = False) -> dict:
    """주제 분류 실행. 반환: {"에너지": [id1, ...], ...}"""
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    rows = await conn.fetch(
        "SELECT id, prediction_text, target_asset "
        "FROM mer_predictions WHERE is_correct IS NULL "
        "ORDER BY id"
    )
    await conn.close()

    preds = [dict(r) for r in rows]
    total = len(preds)
    batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

    print(f"[Stage 1] 주제 분류: {total}건, {batches}배치")

    if dry_run:
        est = batches * (2000 * 0.80 + 1000 * 4.00) / 1_000_000
        print(f"  예상 비용: ${est:.2f}")
        return {}

    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    groups: dict[str, list[int]] = {}

    for i in range(0, total, BATCH_SIZE):
        batch = preds[i:i + BATCH_SIZE]
        part = i // BATCH_SIZE + 1

        user_msg = "\n".join(
            f'{p["id"]}. [{p["target_asset"] or ""}] {p["prediction_text"][:100]}'
            for p in batch
        )

        resp = await client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )

        tracker.add("stage1", resp.usage.input_tokens, resp.usage.output_tokens)
        tracker.check_budget()

        parsed = _parse_json(resp.content[0].text)
        if parsed and "results" in parsed:
            for item in parsed["results"]:
                topic = item.get("topic", "기타")
                groups.setdefault(topic, []).append(item["id"])
        else:
            # 파싱 실패 시 기타로 분류
            for p in batch:
                groups.setdefault("기타", []).append(p["id"])

        print(f"  배치 {part}/{batches} 완료")
        await asyncio.sleep(0.3)

    # 저장
    out_path = OUTPUT_DIR / "stage1_groups.json"
    out_path.write_text(json.dumps(groups, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[Stage 1] 완료: {len(groups)}개 주제")
    for topic, ids in sorted(groups.items(), key=lambda x: -len(x[1])):
        print(f"  {topic}: {len(ids)}건")
    print(tracker.summary())

    return groups
