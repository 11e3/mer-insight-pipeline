"""
새 포스트 실시간 인사이트 추출 + mer_insights 저장 + 임베딩 생성.
EventDispatcher가 MER_NEW_POST 이벤트 처리 후 호출.
"""

import json
import logging
from datetime import date

import anthropic
import asyncpg

from src.config.settings import ANTHROPIC_API_KEY, MODEL_HAIKU
from src.embed import Embedder, vec_str
from src.extract.parse_results import INSIGHT_TYPE_MAP, extract_content
from src.config.prompts import (
    ENGLISH_USER_TEMPLATE,
    INSIGHT_USER_TEMPLATE,
    get_extraction_prompt,
)

# 영문 소스 타입 (ENGLISH_USER_TEMPLATE 사용)
_ENGLISH_SOURCE_TYPES = {"substack", "web"}
from src.verify.prompt import parse_llm_json

log = logging.getLogger(__name__)

client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)



async def extract_and_save(
    conn: asyncpg.Connection,
    embedder: Embedder,
    post: dict,
    source_type: str = "blog",
) -> dict:
    """
    단건 포스트의 인사이트를 추출하여 mer_insights에 저장.
    반환값: {"count": int, "primary_topic": str, "post_summary": str}
    """
    result = {"count": 0, "primary_topic": "기타", "post_summary": ""}

    post_id = await conn.fetchval(
        "SELECT id FROM mer_posts WHERE log_no = $1", post["log_no"]
    )
    if not post_id:
        log.warning(f"post_id 없음: {post['log_no']}")
        return result

    # Claude API 호출 (Haiku — 비용 절감)
    try:
        resp = await client.messages.create(
            model=MODEL_HAIKU,
            max_tokens=4096,
            system=get_extraction_prompt(source_type),
            messages=[{
                "role": "user",
                "content": (
                    ENGLISH_USER_TEMPLATE if source_type in _ENGLISH_SOURCE_TYPES
                    else INSIGHT_USER_TEMPLATE
                ).format(
                    title=post.get("title", ""),
                    date=post.get("date") or "",
                    url=post.get("url") or "",
                    content_text=(post.get("content_text") or "")[:8000],
                ),
            }],
        )
        raw_text = resp.content[0].text.strip()
    except anthropic.APIError as e:
        log.error(f"인사이트 추출 API 오류: {e}")
        return result

    # JSON 파싱
    data = parse_llm_json(raw_text)
    if not data:
        log.error(f"인사이트 JSON 파싱 실패: {post['log_no']}")
        return result

    result["primary_topic"] = data.get("primary_topic", "기타")
    result["post_summary"]   = data.get("post_summary", "")

    # mer_insights 삽입 + 임베딩 생성
    count = 0
    for key, insight_type in INSIGHT_TYPE_MAP.items():
        items = data.get(key, [])
        if not isinstance(items, list):
            continue

        for item in items:
            content = extract_content(item, insight_type)

            vecs = await embedder.embed_passages([content])
            vec = vec_str(vecs[0])

            insight_id = await conn.fetchval("""
                INSERT INTO mer_insights
                    (post_id, insight_type, content, structured_data, embedding)
                VALUES ($1, $2, $3, $4, $5::vector)
                ON CONFLICT (post_id, insight_type, md5(content)) DO NOTHING
                RETURNING id
            """,
                post_id,
                insight_type,
                content,
                json.dumps(item, ensure_ascii=False),
                vec,
            )
            # prediction 타입이면 mer_predictions에도 삽입
            if insight_type == "prediction" and insight_id and item.get("verifiable"):
                # expected_date 파싱
                exp_date = None
                if item.get("expected_date"):
                    try:
                        exp_date = date.fromisoformat(item["expected_date"])
                    except (ValueError, TypeError):
                        pass

                pred_date = post.get("date")
                if isinstance(pred_date, str):
                    try:
                        pred_date = date.fromisoformat(pred_date)
                    except (ValueError, TypeError):
                        pred_date = None

                _dir_map = {
                    "bullish": "up", "bearish": "down", "mixed": "neutral",
                    "flat": "neutral", "sideways": "neutral", "positive": "up",
                    "negative": "down", "long": "up", "short": "down",
                }
                raw_dir = (item.get("direction") or "neutral").lower().strip()
                direction = _dir_map.get(raw_dir, "neutral") if raw_dir not in ("up", "down", "neutral") else raw_dir

                # source_id 조회
                source_id = await conn.fetchval(
                    "SELECT source_id FROM mer_posts WHERE id = $1", post_id
                )

                await conn.execute("""
                    INSERT INTO mer_predictions
                        (insight_id, prediction_text, predicted_direction,
                         target_asset, prediction_date, expected_date, source_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT DO NOTHING
                """,
                    insight_id,
                    item.get("prediction", content),
                    direction,
                    item.get("target_asset", ""),
                    pred_date,
                    exp_date,
                    source_id,
                )
            count += 1

    result["count"] = count
    log.info(f"  → 인사이트 {count}개 저장 ({post['log_no']}, topic={result['primary_topic']})")
    return result
