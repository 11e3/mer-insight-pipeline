"""
Batch API 결과 JSONL 파싱 → mer_insights 테이블 적재.

Usage:
    python -m src.extract.parse_results <results.jsonl>
"""

import sys
import json
import asyncio
import asyncpg
from tqdm import tqdm

from src.config.settings import DATABASE_URL


INSIGHT_TYPE_MAP = {
    "rules":       "rule",
    "predictions": "prediction",
    "evaluations": "evaluation",
    "macro_views": "macro_view",
}


def extract_content(item: dict, insight_type: str) -> str:
    """structured_data에서 대표 텍스트 추출."""
    if insight_type == "rule":
        return item.get("rule_statement", json.dumps(item, ensure_ascii=False)[:200])
    if insight_type == "prediction":
        return item.get("prediction", json.dumps(item, ensure_ascii=False)[:200])
    if insight_type == "evaluation":
        name = item.get("entity_name", "")
        assessment = item.get("mer_assessment", "")
        reasoning = item.get("reasoning", "")
        return f"{name} ({assessment}): {reasoning}"[:300]
    if insight_type == "macro_view":
        return item.get("mer_interpretation", json.dumps(item, ensure_ascii=False)[:200])
    return json.dumps(item, ensure_ascii=False)[:200]


async def parse_and_insert(jsonl_path: str):
    conn = await asyncpg.connect(DATABASE_URL)

    # log_no → post_id 캐시
    rows = await conn.fetch("SELECT log_no, id FROM mer_posts")
    log_no_map = {r["log_no"]: r["id"] for r in rows}

    total_insights = 0
    errors = 0

    with open(jsonl_path, encoding="utf-8") as f:
        lines = f.readlines()

    print(f"결과 파일 파싱: {len(lines)}개 응답\n")

    for line in tqdm(lines, desc="파싱 중"):
        try:
            result = json.loads(line.strip())
        except json.JSONDecodeError:
            continue

        custom_id = result.get("custom_id", "")
        log_no = custom_id.replace("mer-", "")
        post_id = log_no_map.get(log_no)

        if not post_id:
            print(f"\n[경고] post_id 없음: {log_no}")
            continue

        # 응답 파싱 (Batch API: type="succeeded" 또는 "error")
        result_obj = result.get("result", {})
        if result_obj.get("type") == "error":
            errors += 1
            continue

        raw_text = result_obj.get("message", {}).get("content", [{}])[0].get("text", "")

        try:
            # JSON 블록 추출 (```json ... ``` 형태도 처리)
            text = raw_text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text)
        except json.JSONDecodeError:
            errors += 1
            continue

        # 각 인사이트 타입별 삽입
        for key, insight_type in INSIGHT_TYPE_MAP.items():
            items = data.get(key, [])
            if not isinstance(items, list):
                continue

            for item in items:
                content = extract_content(item, insight_type)
                await conn.execute("""
                    INSERT INTO mer_insights
                        (post_id, insight_type, content, structured_data)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (post_id, insight_type, md5(content)) DO NOTHING
                """,
                    post_id,
                    insight_type,
                    content,
                    json.dumps(item, ensure_ascii=False),
                )
                total_insights += 1

    await conn.close()
    print(f"\n완료: 인사이트 {total_insights}개 삽입 | 오류 {errors}건")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m src.extract.parse_results <results.jsonl>")
        sys.exit(1)
    asyncio.run(parse_and_insert(sys.argv[1]))
