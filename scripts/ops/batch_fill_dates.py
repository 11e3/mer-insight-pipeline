"""
expected_date 없는 검증 가능 예측에 Haiku Batch API로 expected_date 채우기.

Usage:
    python -m scripts.ops.batch_fill_dates create
    python -m scripts.ops.batch_fill_dates status
    python -m scripts.ops.batch_fill_dates apply
"""

import asyncio
import json
import re
import sys
from datetime import date
from pathlib import Path

import anthropic
import asyncpg
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.config.settings import ANTHROPIC_API_KEY, DATABASE_URL

BATCH_DIR = Path("data/batch_fill_dates")
BATCH_DIR.mkdir(parents=True, exist_ok=True)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM = """\
주어진 경제/금융 예측의 검증 가능 시점(expected_date)을 판단하라.

규칙:
- 예측이 실현되었는지 확인할 수 있는 가장 빠른 날짜를 YYYY-MM-DD로 반환
- 예측일 이후여야 함
- "2025년 하반기" -> 2025-12-31, "내년" -> 예측일+1년 말
- "당분간" / "향후" -> 예측일+6개월
- 구체적 날짜 언급 시 그 날짜 사용
- 이미 과거인 경우에도 해당 날짜 그대로 반환

JSON만 출력: {"expected_date": "YYYY-MM-DD"}
"""


async def create():
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch("""
        SELECT id, prediction_text, target_asset, prediction_date
        FROM mer_predictions
        WHERE expected_date IS NULL AND is_verifiable = true AND is_correct IS NULL
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
                "custom_id": f"date-{r['id']}",
                "params": {
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 32,
                    "system": SYSTEM,
                    "messages": [{"role": "user", "content":
                        f"{r['prediction_text']}\n대상: {r['target_asset'] or ''}\n예측일: {r['prediction_date']}"}],
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
    filled = 0
    future = 0
    past = 0
    errors = 0

    with open(results_path, encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            custom_id = item.get("custom_id", "")
            pred_id = int(custom_id.replace("date-", ""))

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

            m = re.search(r"\{[^{}]+\}", raw)
            if not m:
                errors += 1
                continue

            try:
                data = json.loads(m.group())
                date_str = data.get("expected_date", "")
            except json.JSONDecodeError:
                errors += 1
                continue

            if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
                errors += 1
                continue

            try:
                parsed = date.fromisoformat(date_str)
            except ValueError:
                errors += 1
                continue

            await conn.execute(
                "UPDATE mer_predictions SET expected_date = $1 WHERE id = $2",
                parsed, pred_id,
            )
            filled += 1
            if parsed > date.today():
                future += 1
            else:
                past += 1

    await conn.close()
    print("=== 결과 ===")
    print(f"  채워진 건: {filled} (미래: {future}, 과거: {past})")
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
