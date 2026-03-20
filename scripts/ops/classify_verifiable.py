"""
검증 가능 여부 분류 — Batch API로 is_verifiable 마킹.

Usage:
    python -m scripts.ops.classify_verifiable create   # 배치 생성 + 제출
    python -m scripts.ops.classify_verifiable status   # 진행 확인
    python -m scripts.ops.classify_verifiable apply    # 결과 DB 반영
"""

import asyncio
import json
import sys
from pathlib import Path

import anthropic
import asyncpg
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.config.settings import ANTHROPIC_API_KEY, DATABASE_URL

BATCH_DIR = Path("data/classify_verifiable")
BATCH_DIR.mkdir(parents=True, exist_ok=True)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM = """\
너는 경제·금융 예측 분류 전문가다.
주어진 예측이 뉴스 헤드라인으로 검증 가능한지 판단하라.

검증 가능 (yes):
- 구체적 사건/수치를 예측: "BOJ가 금리를 인상할 것", "유가가 100달러 돌파", "삼성전자 HBM 납품 성공"
- 특정 시점의 결과를 확인할 수 있음

검증 불가 (no):
- 모호하거나 조건부: "~하면 ~할 수 있다", "가능성이 있다"
- 장기 추세/의견: "당분간 지속될 것", "구조적 변화가 올 것"
- 개인 행동/계획: "비중을 줄일 계획", "매수할 예정"

JSON만 출력: {"verifiable": true|false}
"""


async def create():
    """배치 생성 + 제출."""
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch("""
        SELECT id, prediction_text, target_asset, predicted_direction
        FROM mer_predictions
        WHERE is_correct IS NULL AND expected_date IS NULL AND is_verifiable IS NULL
        ORDER BY id
    """)
    await conn.close()

    if not rows:
        print("분류 대상 0건")
        return

    print(f"분류 대상: {len(rows)}건")

    jsonl_path = BATCH_DIR / "classify_requests.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for r in rows:
            request = {
                "custom_id": f"clf-{r['id']}",
                "params": {
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 32,
                    "system": SYSTEM,
                    "messages": [{"role": "user", "content":
                        f"{r['prediction_text']}\n대상: {r['target_asset'] or ''}, 방향: {r['predicted_direction']}"}],
                },
            }
            f.write(json.dumps(request, ensure_ascii=False) + "\n")

    print(f"JSONL: {jsonl_path} ({len(rows)}건)")

    batch = client.messages.batches.create(requests=list(
        json.loads(line) for line in open(jsonl_path, encoding="utf-8")
    ))

    print(f"배치 제출: {batch.id}")
    print(f"상태: {batch.processing_status}")
    (BATCH_DIR / "batch_id.txt").write_text(batch.id)


def status():
    """배치 상태 확인."""
    batch_id = (BATCH_DIR / "batch_id.txt").read_text().strip()
    batch = client.messages.batches.retrieve(batch_id)

    print(f"배치 ID: {batch.id}")
    print(f"상태: {batch.processing_status}")
    counts = batch.request_counts
    print(f"처리: {counts.succeeded}건 성공, {counts.errored}건 에러, {counts.processing}건 처리중")

    if batch.processing_status == "ended":
        results_path = BATCH_DIR / "classify_results.jsonl"
        with open(results_path, "w", encoding="utf-8") as f:
            for result in client.messages.batches.results(batch_id):
                f.write(json.dumps(json.loads(result.json()), ensure_ascii=False) + "\n")
        print(f"결과 저장: {results_path}")


async def apply():
    """결과 DB 반영."""
    results_path = BATCH_DIR / "classify_results.jsonl"
    if not results_path.exists():
        print("결과 파일 없음")
        return

    conn = await asyncpg.connect(DATABASE_URL)
    verifiable = 0
    unverifiable = 0
    errors = 0

    with open(results_path, encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            custom_id = item.get("custom_id", "")
            pred_id = int(custom_id.replace("clf-", ""))

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

            # JSON 파싱
            import re
            m = re.search(r"\{[^{}]+\}", raw)
            if m:
                try:
                    data = json.loads(m.group())
                    is_v = data.get("verifiable", False)
                except json.JSONDecodeError:
                    is_v = False
            else:
                is_v = False

            await conn.execute(
                "UPDATE mer_predictions SET is_verifiable = $1 WHERE id = $2",
                is_v, pred_id,
            )
            if is_v:
                verifiable += 1
            else:
                unverifiable += 1

    await conn.close()
    print(f"=== 분류 결과 ===")
    print(f"  검증 가능: {verifiable}건")
    print(f"  검증 불가: {unverifiable}건")
    print(f"  에러: {errors}건")


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
