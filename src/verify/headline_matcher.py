"""예측 ↔ 헤드라인 매칭 — 키워드 기반.

prediction의 키워드와 news_headlines의 키워드를 GIN 인덱스로 매칭.
expected_date 전후 ±30일 범위 내 헤드라인만 대상.
"""

import logging
from datetime import timedelta

import asyncpg

from src.collect.keyword_extractor import extract_keywords

log = logging.getLogger(__name__)

MATCH_WINDOW_DAYS = 30  # expected_date 전후 검색 범위
MIN_KEYWORD_OVERLAP = 2  # 최소 키워드 겹침 수


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

    # 날짜 범위 결정
    ref_date = expected_date or prediction_date
    if not ref_date:
        return []

    date_start = ref_date - timedelta(days=MATCH_WINDOW_DAYS)
    date_end = ref_date + timedelta(days=MATCH_WINDOW_DAYS)

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


async def batch_match(
    conn: asyncpg.Connection,
    *,
    limit: int = 100,
) -> list[dict]:
    """검증 가능한 PENDING 예측들에 대해 헤드라인 매칭 실행.

    Returns: [{"prediction_id": int, "prediction_text": str,
               "headlines": [matched headlines]}]
    """
    rows = await conn.fetch("""
        SELECT id, prediction_text, target_asset, prediction_date, expected_date
        FROM mer_predictions
        WHERE is_correct IS NULL
          AND (expected_date IS NULL OR expected_date <= CURRENT_DATE)
        ORDER BY prediction_date DESC
        LIMIT $1
    """, limit)

    results = []
    for r in rows:
        headlines = await find_matching_headlines(
            conn,
            prediction_id=r["id"],
            prediction_text=r["prediction_text"],
            target_asset=r["target_asset"] or "",
            expected_date=r["expected_date"],
            prediction_date=r["prediction_date"],
        )
        if headlines:
            results.append({
                "prediction_id": r["id"],
                "prediction_text": r["prediction_text"],
                "target_asset": r["target_asset"],
                "headlines": headlines,
            })

    log.info(f"매칭 완료: {len(rows)}건 중 {len(results)}건에 헤드라인 매칭됨")
    return results
