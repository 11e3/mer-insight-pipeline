"""
검증 대기 예측을 Haiku Batch API로 재분류 + 가능한 건 직접 판정.

Usage:
    python -m scripts.ops.reclassify_pending create
    python -m scripts.ops.reclassify_pending status
    python -m scripts.ops.reclassify_pending apply
"""

import asyncio
import json
import re
import sys
from pathlib import Path

import anthropic
import asyncpg
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.config.settings import ANTHROPIC_API_KEY, DATABASE_URL

BATCH_DIR = Path("data/reclassify_pending")
BATCH_DIR.mkdir(parents=True, exist_ok=True)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM = """\
너는 경제/금융 예측 검증 전문가다.
주어진 예측에 대해 너의 지식만으로 판정하라. 웹 검색 없이.

출력 (JSON만):
{"verdict": "CORRECT|INCORRECT|UNVERIFIABLE", "reason": "한줄 근거"}

규칙:
- CORRECT: 너의 학습 데이터 기준으로 예측이 실현됨
- INCORRECT: 예측과 반대 결과가 발생함
- UNVERIFIABLE: 모호하거나 조건부여서 판정 자체가 불가능, 또는 너의 지식으로 확인 불가
- 확실하지 않으면 UNVERIFIABLE
"""


async def create():
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch("""
        SELECT id, prediction_text, target_asset, predicted_direction, prediction_date
        FROM mer_predictions
        WHERE is_correct IS NULL AND is_verifiable = true
          AND (expected_date IS NULL OR expected_date <= CURRENT_DATE)
        ORDER BY id
    """)
    await conn.close()

    if not rows:
        print("대상 0건")
        return

    print(f"대상: {len(rows)}건")

    jsonl_path = BATCH_DIR / "requests.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for r in rows:
            request = {
                "custom_id": f"rcl-{r['id']}",
                "params": {
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 128,
                    "system": SYSTEM,
                    "messages": [{"role": "user", "content":
                        f"{r['prediction_text']}\n대상: {r['target_asset'] or ''}, "
                        f"방향: {r['predicted_direction']}, 예측일: {r['prediction_date']}"}],
                },
            }
            f.write(json.dumps(request, ensure_ascii=False) + "\n")

    batch = client.messages.batches.create(requests=list(
        json.loads(line) for line in open(jsonl_path, encoding="utf-8")
    ))

    print(f"배치 제출: {batch.id} ({len(rows)}건)")
    (BATCH_DIR / "batch_id.txt").write_text(batch.id)


def status():
    batch_id = (BATCH_DIR / "batch_id.txt").read_text().strip()
    batch = client.messages.batches.retrieve(batch_id)
    counts = batch.request_counts
    print(f"상태: {batch.processing_status}")
    print(f"처리: {counts.succeeded}건 성공, {counts.errored}건 에러, {counts.processing}건 처리중")

    if batch.processing_status == "ended":
        results_path = BATCH_DIR / "results.jsonl"
        with open(results_path, "w", encoding="utf-8") as f:
            for result in client.messages.batches.results(batch_id):
                f.write(json.dumps(json.loads(result.json()), ensure_ascii=False) + "\n")
        print(f"결과 저장: {results_path}")


async def apply():
    results_path = BATCH_DIR / "results.jsonl"
    if not results_path.exists():
        print("결과 파일 없음")
        return

    conn = await asyncpg.connect(DATABASE_URL)
    correct = 0
    incorrect = 0
    unverifiable = 0
    errors = 0

    with open(results_path, encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            custom_id = item.get("custom_id", "")
            pred_id = int(custom_id.replace("rcl-", ""))

            result = item.get("result", {})
            if result.get("type") != "succeeded":
                errors += 1
                continue

            content = result.get("message", {}).get("content", [])
            raw = ""
            for block in content:
                if block.get("type") == "text":
                    raw = block.get("text", "")
                    break

            # 코드블록 제거 후 JSON 파싱
            cleaned = re.sub(r"```(?:json)?\s*", "", raw)
            cleaned = re.sub(r"\s*```", "", cleaned).strip()

            m = re.search(r"\{[^{}]+\}", cleaned, re.DOTALL)
            if not m:
                errors += 1
                continue

            try:
                data = json.loads(m.group())
            except json.JSONDecodeError:
                errors += 1
                continue

            verdict = data.get("verdict", "UNVERIFIABLE")
            reason = data.get("reason", "")

            if verdict == "CORRECT":
                await conn.execute("""
                    UPDATE mer_predictions
                    SET is_correct = TRUE, actual_outcome = $1, verification_date = CURRENT_DATE
                    WHERE id = $2 AND is_correct IS NULL
                """, reason, pred_id)
                correct += 1
            elif verdict == "INCORRECT":
                await conn.execute("""
                    UPDATE mer_predictions
                    SET is_correct = FALSE, actual_outcome = $1, verification_date = CURRENT_DATE
                    WHERE id = $2 AND is_correct IS NULL
                """, reason, pred_id)
                incorrect += 1
            else:
                await conn.execute(
                    "UPDATE mer_predictions SET is_verifiable = FALSE WHERE id = $1",
                    pred_id,
                )
                unverifiable += 1

    await conn.close()
    print("=== 결과 ===")
    print(f"  CORRECT: {correct}")
    print(f"  INCORRECT: {incorrect}")
    print(f"  UNVERIFIABLE (재분류): {unverifiable}")
    print(f"  에러: {errors}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "create":
        asyncio.run(create())
    elif cmd == "status":
        status()
    elif cmd == "apply":
        asyncio.run(apply())
    else:
        print(f"Unknown: {cmd}")
