"""
새 포스트 실시간 인사이트 추출 + mer_insights 저장 + 임베딩 생성.
EventDispatcher가 MER_NEW_POST 이벤트 처리 후 호출.
"""

import json
import logging

import anthropic
import asyncpg

from config.settings import ANTHROPIC_API_KEY, MODEL_HAIKU
from src.extract.vertex_embedder import VertexEmbedder, vec_str
from config.prompts import INSIGHT_SYSTEM_PROMPT, INSIGHT_USER_TEMPLATE

log = logging.getLogger(__name__)

client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

INSIGHT_TYPE_MAP = {
    "rules":       "rule",
    "predictions": "prediction",
    "evaluations": "evaluation",
    "macro_views": "macro_view",
}


def _extract_content(item: dict, insight_type: str) -> str:
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




ECONOMIC_TOPICS = {
    "거시경제", "기업분석", "부동산", "금융시장", "정책", "산업분석",
}

# 제목/본문에 이 키워드가 포함되면 비경제로 강제 판정 (topic 오분류 보정)
_NON_ECON_OVERRIDES = {
    "조선시대", "고려시대", "조선왕조", "삼국시대",
    "법제", "형벌", "처벌", "사형", "옥사",
    "건강관리", "건강법", "식이요법", "다이어트",
    "의학", "질병", "치료", "수술", "의약",
    "음식", "요리", "레시피", "맛집",
    "여행", "관광", "육아", "보육",
    "종교", "불교", "기독교", "명상",
    "스포츠", "운동법", "체력",
    "문학", "소설", "영화", "드라마",
}


def is_economic(primary_topic: str, title: str = "", content: str = "") -> bool:
    """primary_topic 기반 판정 + 제목/본문 블랙리스트 보정."""
    if primary_topic not in ECONOMIC_TOPICS:
        return False
    # 경제 토픽으로 분류됐어도 제목/본문에 비경제 키워드가 지배적이면 비경제
    text = (title + " " + content[:500]).lower()
    hits = sum(1 for kw in _NON_ECON_OVERRIDES if kw in text)
    return hits < 2  # 비경제 키워드 2개 이상이면 비경제로 판정


async def extract_and_save(
    conn: asyncpg.Connection,
    embedder: VertexEmbedder,
    post: dict,
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
            system=INSIGHT_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": INSIGHT_USER_TEMPLATE.format(
                    title=post.get("title", ""),
                    date=post.get("date") or "",
                    url=post.get("url") or "",
                    content_text=(post.get("content_text") or "")[:8000],
                ),
            }],
        )
        raw_text = resp.content[0].text.strip()
    except Exception as e:
        log.error(f"인사이트 추출 API 오류: {e}")
        return result

    # JSON 파싱
    try:
        text = raw_text
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text)
    except json.JSONDecodeError:
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
            content = _extract_content(item, insight_type)

            vecs = await embedder.embed_passages([content])
            vec = vec_str(vecs[0])

            insight_id = await conn.fetchval("""
                INSERT INTO mer_insights
                    (post_id, insight_type, content, structured_data, embedding)
                VALUES ($1, $2, $3, $4, $5::vector)
                ON CONFLICT DO NOTHING
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
                await conn.execute("""
                    INSERT INTO mer_predictions
                        (insight_id, prediction_text, predicted_direction,
                         target_asset, prediction_date)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT DO NOTHING
                """,
                    insight_id,
                    item.get("prediction", content),
                    item.get("direction", "neutral"),
                    item.get("target_asset", ""),
                    post.get("date"),
                )
            count += 1

    result["count"] = count
    log.info(f"  → 인사이트 {count}개 저장 ({post['log_no']}, topic={result['primary_topic']})")
    return result
