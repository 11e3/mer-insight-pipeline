"""예측 ↔ 헤드라인 매칭 — 키워드 + 벡터 하이브리드.

1차: GIN 키워드 매칭 (빠름, precision 높음)
2차: 벡터 유사도 매칭 (느리지만 recall 높음) — 키워드 매칭 실패 시 fallback
검색 범위: prediction_date ~ 오늘 (예측일 이후 뉴스만 검증 근거로 사용).
"""

import functools
import logging
from datetime import datetime, timedelta

import asyncpg

from src.collect.keyword_extractor import extract_keywords
from src.embed import get_embedder, vec_str

log = logging.getLogger(__name__)

MIN_KEYWORD_OVERLAP = 3  # 최소 키워드 겹침 수 (2는 false positive 많음)


async def find_matching_headlines(
    conn: asyncpg.Connection,
    prediction_id: int,
    prediction_text: str,
    target_asset: str,
    expected_date,
    prediction_date,
    *,
    max_results: int = 10,
) -> list[dict]:
    """예측에 매칭되는 헤드라인 검색.

    Returns: [{"headline_id": int, "headline": str, "source_url": str,
               "published_at": datetime, "overlap_count": int}]
    """
    # 예측 텍스트에서 키워드 추출
    text = f"{target_asset} {prediction_text}"
    keywords = extract_keywords(text, "ko")

    # 영어 키워드도 추가 (target_asset이 영어일 수 있음)
    en_keywords = extract_keywords(text, "en")
    all_keywords = list(dict.fromkeys(keywords + en_keywords))  # 중복 제거, 순서 유지

    if len(all_keywords) < 2:
        return []

    # 날짜 범위: 예측 다음날 ~ 현재 (예측 당일 뉴스 제외, 오늘 뉴스 포함)
    if prediction_date:
        date_start = datetime.combine(prediction_date + timedelta(days=1), datetime.min.time())
    else:
        return []  # prediction_date NULL이면 매칭 건너뛰기

    date_end = datetime.now()

    # GIN 인덱스 활용: keywords && ARRAY[...] (overlap 연산자)
    rows = await conn.fetch("""
        SELECT id, headline, source_url, published_at, keywords,
               array_length(
                   ARRAY(SELECT unnest(keywords) INTERSECT SELECT unnest($1::text[])),
                   1
               ) AS overlap_count
        FROM news_headlines
        WHERE keywords && $1::text[]
          AND published_at BETWEEN $2 AND $3
        ORDER BY overlap_count DESC NULLS LAST, published_at DESC
        LIMIT $4
    """, all_keywords, date_start, date_end, max_results)

    results = []
    for r in rows:
        overlap = r["overlap_count"] or 0
        if overlap >= MIN_KEYWORD_OVERLAP:
            results.append({
                "headline_id": r["id"],
                "headline": r["headline"],
                "source_url": r["source_url"],
                "published_at": r["published_at"],
                "overlap_count": overlap,
            })

    return results


# ─── 벡터 유사도 매칭 (fallback) ─────────────────────────────────────────────

VECTOR_SIMILARITY_THRESHOLD = 0.45  # cosine similarity 최소값

@functools.lru_cache(maxsize=1)
def _get_embedder():
    return get_embedder()


async def find_matching_headlines_vector(
    conn: asyncpg.Connection,
    prediction_text: str,
    target_asset: str,
    prediction_date,
    *,
    max_results: int = 10,
    embedder=None,
) -> list[dict]:
    """벡터 유사도로 헤드라인 매칭 (키워드 매칭 실패 시 fallback)."""
    if not prediction_date:
        return []

    date_start = datetime.combine(prediction_date + timedelta(days=1), datetime.min.time())
    date_end = datetime.now()

    embedder = embedder or _get_embedder()
    query_text = f"{target_asset} {prediction_text}"
    query_vec = embedder.embed_query_sync(query_text)
    query_str = vec_str(query_vec)

    rows = await conn.fetch("""
        SELECT id, headline, source_url, published_at,
               1 - (embedding <=> $1::vector) AS similarity
        FROM news_headlines
        WHERE embedding IS NOT NULL
          AND published_at BETWEEN $2 AND $3
        ORDER BY embedding <=> $1::vector
        LIMIT $4
    """, query_str, date_start, date_end, max_results)

    results = []
    for r in rows:
        sim = r["similarity"] or 0
        if sim >= VECTOR_SIMILARITY_THRESHOLD:
            results.append({
                "headline_id": r["id"],
                "headline": r["headline"],
                "source_url": r["source_url"],
                "published_at": r["published_at"],
                "overlap_count": int(sim * 10),  # 호환용 (0-10 스케일)
            })

    return results


async def batch_match(
    conn: asyncpg.Connection,
    *,
    limit: int = 100,
) -> list[dict]:
    """검증 가능한 PENDING 예측들에 대해 헤드라인 매칭 실행.

    Returns: [{"prediction_id": int, "prediction_text": str,
               "headlines": [matched headlines]}]
    """
    # limit * 5로 넉넉히 가져와서 매칭 후 limit 적용
    # (SQL LIMIT이 매칭 전 적용되면 매칭 가능한 예측이 스킵됨)
    prefetch = limit * 5
    rows = await conn.fetch("""
        SELECT id, prediction_text, target_asset, predicted_direction, prediction_date, expected_date
        FROM mer_predictions
        WHERE is_correct IS NULL
          AND is_verifiable IS NOT FALSE
          AND (expected_date IS NULL OR expected_date <= CURRENT_DATE)
          AND (skipped_at IS NULL OR skipped_at < CURRENT_DATE - INTERVAL '7 days')
        ORDER BY prediction_date DESC
        LIMIT $1
    """, prefetch)

    results = []
    for r in rows:
        # 1차: 키워드 매칭
        headlines = await find_matching_headlines(
            conn,
            prediction_id=r["id"],
            prediction_text=r["prediction_text"],
            target_asset=r["target_asset"] or "",
            expected_date=r["expected_date"],
            prediction_date=r["prediction_date"],
        )

        # 2차: 벡터 매칭 (키워드 매칭 실패 시 fallback)
        if not headlines:
            headlines = await find_matching_headlines_vector(
                conn,
                prediction_text=r["prediction_text"],
                target_asset=r["target_asset"] or "",
                prediction_date=r["prediction_date"],
            )

        if headlines:
            results.append({
                "prediction_id": r["id"],
                "prediction_text": r["prediction_text"],
                "target_asset": r["target_asset"],
                "predicted_direction": r["predicted_direction"],
                "prediction_date": str(r["prediction_date"] or ""),
                "expected_date": str(r["expected_date"] or "미지정"),
                "headlines": headlines,
            })

    results = results[:limit]  # 최종 limit 적용
    log.info(f"매칭 완료: {len(rows)}건 조회 → {len(results)}건 헤드라인 매칭")
    return results
