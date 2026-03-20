"""
Batch API로 헤드라인 매칭 예측 일괄 검증.

Usage:
    python -m scripts.ops.batch_verify create    # 배치 생성 + 제출
    python -m scripts.ops.batch_verify status    # 진행 상황 확인
    python -m scripts.ops.batch_verify apply     # 결과 DB 반영
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
from src.verify.headline_matcher import batch_match
from src.verify.prompt import AUTO_VERIFY_MODEL, AUTO_VERIFY_SYSTEM_PROMPT, AUTO_VERIFY_USER_TEMPLATE
from src.verify.auto_verifier import AutoVerifier

BATCH_DIR = Path("data/batch_verify")


def _linkify_headlines(reason: str, headlines: list[dict]) -> str:
    """reason 텍스트의 '헤드라인N'을 [헤드라인 요약](URL) 마크다운 링크로 치환."""
    if not headlines:
        return reason

    def _replace(m):
        # "헤드라인1", "헤드라인 1", "헤드라인1번" 등에서 숫자 추출
        idx = int(m.group(1)) - 1  # 1-based → 0-based
        if 0 <= idx < len(headlines):
            h = headlines[idx]
            title = h["headline"][:30]
            url = h["source_url"]
            return f"[{title}]({url})"
        return m.group(0)

    return re.sub(r"헤드라인\s*(\d+)(?:번)?", _replace, reason)
BATCH_DIR.mkdir(parents=True, exist_ok=True)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


async def create():
    """매칭된 예측을 JSONL로 만들어 Batch API 제출."""
    conn = await asyncpg.connect(DATABASE_URL)
    matches = await batch_match(conn, limit=5000)
    await conn.close()

    if not matches:
        print("매칭된 예측 0건")
        return

    print(f"매칭 {len(matches)}건 → JSONL 생성")

    # JSONL 생성
    jsonl_path = BATCH_DIR / "verify_requests.jsonl"
    match_map = {}  # custom_id → match (source_url 매핑용)

    with open(jsonl_path, "w", encoding="utf-8") as f:
        for m in matches:
            custom_id = f"verify-{m['prediction_id']}"

            headlines_text = "\n".join(
                f"{i+1}. {h['headline']} ({h['published_at']})"
                for i, h in enumerate(m["headlines"])
            )

            user_msg = AUTO_VERIFY_USER_TEMPLATE.format(
                today=date.today().isoformat(),
                prediction_text=m["prediction_text"],
                target_asset=m.get("target_asset", "") or "",
                predicted_direction=m.get("predicted_direction", "neutral"),
                prediction_date=m.get("prediction_date", ""),
                expected_date=m.get("expected_date", "미지정"),
                headlines_text=headlines_text,
            )

            request = {
                "custom_id": custom_id,
                "params": {
                    "model": AUTO_VERIFY_MODEL,
                    "max_tokens": 256,
                    "system": AUTO_VERIFY_SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_msg}],
                },
            }
            f.write(json.dumps(request, ensure_ascii=False) + "\n")

            # 헤드라인 매핑 저장 (후처리에서 reason 텍스트의 "헤드라인N"을 링크로 치환)
            match_map[custom_id] = {
                "prediction_id": m["prediction_id"],
                "source_url": m["headlines"][0]["source_url"] if m.get("headlines") else "",
                "headlines": [
                    {"headline": h["headline"], "source_url": h["source_url"]}
                    for h in m.get("headlines", [])
                ],
            }

    # match_map 저장
    map_path = BATCH_DIR / "match_map.json"
    with open(map_path, "w", encoding="utf-8") as f:
        json.dump(match_map, f, ensure_ascii=False)

    print(f"JSONL: {jsonl_path} ({len(matches)}건)")
    print(f"매핑: {map_path}")

    # Batch API 제출
    with open(jsonl_path, "rb") as f:
        batch = client.messages.batches.create(requests=list(
            json.loads(line) for line in open(jsonl_path, encoding="utf-8")
        ))

    print(f"배치 제출 완료: {batch.id}")
    print(f"상태: {batch.processing_status}")

    # batch_id 저장
    (BATCH_DIR / "batch_id.txt").write_text(batch.id)


def status():
    """배치 진행 상황 확인."""
    batch_id = (BATCH_DIR / "batch_id.txt").read_text().strip()
    batch = client.messages.batches.retrieve(batch_id)

    print(f"배치 ID: {batch.id}")
    print(f"상태: {batch.processing_status}")
    counts = batch.request_counts
    print(f"처리: {counts.succeeded}건 성공, {counts.errored}건 에러, {counts.processing}건 처리중")

    if batch.processing_status == "ended":
        # 결과 다운로드
        results_path = BATCH_DIR / "verify_results.jsonl"
        with open(results_path, "w", encoding="utf-8") as f:
            for result in client.messages.batches.results(batch_id):
                f.write(json.dumps(json.loads(result.json()), ensure_ascii=False) + "\n")
        print(f"결과 저장: {results_path}")


async def apply():
    """배치 결과를 DB에 반영."""
    results_path = BATCH_DIR / "verify_results.jsonl"
    map_path = BATCH_DIR / "match_map.json"

    if not results_path.exists():
        print("결과 파일 없음. 먼저 status로 다운로드하세요.")
        return

    with open(map_path, encoding="utf-8") as f:
        match_map = json.load(f)

    conn = await asyncpg.connect(DATABASE_URL)
    resolved = 0
    pending = 0
    errors = 0

    with open(results_path, encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            custom_id = item.get("custom_id", "")
            mapping = match_map.get(custom_id)
            if not mapping:
                errors += 1
                continue

            prediction_id = mapping["prediction_id"]
            source_url = mapping["source_url"]

            # 결과 파싱
            result = item.get("result", {})
            if result.get("type") != "succeeded":
                errors += 1
                continue

            message = result.get("message", {})
            content = message.get("content", [])
            raw_text = ""
            for block in content:
                if block.get("type") == "text":
                    raw_text = block.get("text", "")
                    break

            verdict_data = AutoVerifier._parse_json(raw_text)
            verdict = verdict_data.get("verdict", "PENDING")
            reason = verdict_data.get("reason", "")

            # "헤드라인N"을 실제 마크다운 링크로 치환
            headlines = mapping.get("headlines", [])
            reason = _linkify_headlines(reason, headlines)

            if verdict in ("CORRECT", "INCORRECT") and source_url:
                is_correct = verdict == "CORRECT"
                status_str = await conn.execute("""
                    UPDATE mer_predictions
                    SET is_correct = $1, actual_outcome = $2, source_url = $3, verification_date = CURRENT_DATE
                    WHERE id = $4 AND is_correct IS NULL
                """, is_correct, reason, source_url, prediction_id)
                if status_str == "UPDATE 1":
                    resolved += 1
                else:
                    pending += 1
            else:
                # PENDING → skipped_at 마킹 (7일간 재시도 방지)
                await conn.execute(
                    "UPDATE mer_predictions SET skipped_at = CURRENT_DATE WHERE id = $1",
                    prediction_id,
                )
                pending += 1

    await conn.close()

    print("=== 배치 검증 결과 ===")
    print(f"  반영: {resolved}건")
    print(f"  보류(PENDING): {pending}건")
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
        print(f"Unknown command: {cmd}")
        print(__doc__)
