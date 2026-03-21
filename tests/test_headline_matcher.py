"""verify/headline_matcher.py 단위 테스트."""

import os
import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
    os.environ.setdefault(_var, "test")


# ─── find_matching_headlines (키워드 GIN) ────────────────────────────────────

async def test_find_matching_no_keywords():
    """키워드가 2개 미만이면 빈 리스트 반환."""
    from src.verify.headline_matcher import find_matching_headlines

    conn = AsyncMock()
    with patch("src.verify.headline_matcher.extract_keywords", return_value=["a"]):
        result = await find_matching_headlines(conn, 1, "short", "", None, date(2024, 1, 1))

    assert result == []
    conn.fetch.assert_not_called()


async def test_find_matching_no_prediction_date():
    """prediction_date가 None이면 빈 리스트 반환."""
    from src.verify.headline_matcher import find_matching_headlines

    conn = AsyncMock()
    result = await find_matching_headlines(conn, 1, "test text", "asset", None, None)

    assert result == []


async def test_find_matching_filters_low_overlap():
    """MIN_KEYWORD_OVERLAP 미만은 제외."""
    from src.verify.headline_matcher import find_matching_headlines

    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": 1, "headline": "h1", "source_url": "http://a", "published_at": datetime(2024, 6, 1), "keywords": ["a", "b", "c"], "overlap_count": 3},
        {"id": 2, "headline": "h2", "source_url": "http://b", "published_at": datetime(2024, 6, 2), "keywords": ["a", "b"], "overlap_count": 2},
    ])

    with patch("src.verify.headline_matcher.extract_keywords", return_value=["a", "b", "c", "d"]):
        result = await find_matching_headlines(conn, 1, "test", "asset", None, date(2024, 1, 1))

    assert len(result) == 1
    assert result[0]["headline"] == "h1"


async def test_find_matching_returns_sorted():
    """overlap_count 내림차순 정렬."""
    from src.verify.headline_matcher import find_matching_headlines

    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": 1, "headline": "h1", "source_url": "http://a", "published_at": datetime(2024, 6, 1), "keywords": ["a", "b", "c"], "overlap_count": 5},
        {"id": 2, "headline": "h2", "source_url": "http://b", "published_at": datetime(2024, 6, 2), "keywords": ["a", "b", "c"], "overlap_count": 3},
    ])

    with patch("src.verify.headline_matcher.extract_keywords", return_value=["a", "b", "c", "d"]):
        result = await find_matching_headlines(conn, 1, "test", "asset", None, date(2024, 1, 1))

    assert result[0]["overlap_count"] == 5
    assert result[1]["overlap_count"] == 3


# ─── find_matching_headlines_vector (벡터 fallback) ──────────────────────────

async def test_vector_matching_no_prediction_date():
    """prediction_date None이면 빈 리스트."""
    from src.verify.headline_matcher import find_matching_headlines_vector

    conn = AsyncMock()
    result = await find_matching_headlines_vector(conn, "test", "asset", None)
    assert result == []


async def test_vector_matching_filters_low_similarity():
    """threshold 미만 유사도는 제외."""
    from src.verify.headline_matcher import find_matching_headlines_vector

    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": 1, "headline": "h1", "source_url": "http://a", "published_at": datetime(2024, 6, 1), "similarity": 0.8},
        {"id": 2, "headline": "h2", "source_url": "http://b", "published_at": datetime(2024, 6, 2), "similarity": 0.3},
    ])

    mock_embedder = MagicMock()
    mock_embedder.embed_query_sync.return_value = [0.1] * 1024

    with patch("src.verify.headline_matcher._get_embedder", return_value=mock_embedder), \
         patch("src.verify.headline_matcher.vec_str", return_value="[0.1,...]"):
        result = await find_matching_headlines_vector(conn, "test", "asset", date(2024, 1, 1))

    assert len(result) == 1
    assert result[0]["headline"] == "h1"


# ─── batch_match ─────────────────────────────────────────────────────────────

async def test_batch_match_empty():
    """PENDING 예측 0건이면 빈 리스트."""
    from src.verify.headline_matcher import batch_match

    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])

    result = await batch_match(conn, limit=10)
    assert result == []


async def test_batch_match_keyword_hit():
    """키워드 매칭 성공 시 결과 포함."""
    from src.verify.headline_matcher import batch_match

    conn = AsyncMock()
    # batch_match의 첫 fetch: predictions
    pred_row = {
        "id": 100, "prediction_text": "BOJ will raise rates",
        "target_asset": "JPY", "predicted_direction": "up",
        "prediction_date": date(2024, 1, 1), "expected_date": date(2024, 12, 31),
    }
    # find_matching_headlines의 fetch: headlines
    hl_row = {
        "id": 1, "headline": "BOJ raises rates to 0.75%",
        "source_url": "http://a", "published_at": datetime(2024, 7, 1),
        "keywords": ["BOJ", "rates", "raise"], "overlap_count": 3,
    }

    conn.fetch = AsyncMock(side_effect=[[pred_row], [hl_row]])

    with patch("src.verify.headline_matcher.extract_keywords", return_value=["BOJ", "rates", "raise", "JPY"]):
        result = await batch_match(conn, limit=10)

    assert len(result) == 1
    assert result[0]["prediction_id"] == 100
    assert len(result[0]["headlines"]) == 1


async def test_batch_match_vector_fallback():
    """키워드 매칭 실패 시 벡터 fallback."""
    from src.verify.headline_matcher import batch_match

    conn = AsyncMock()
    pred_row = {
        "id": 200, "prediction_text": "Samsung HBM3E qualification",
        "target_asset": "Samsung", "predicted_direction": "up",
        "prediction_date": date(2024, 1, 1), "expected_date": None,
    }

    # 1st fetch: predictions, 2nd fetch: keyword match empty, 3rd fetch: vector match
    vector_row = {
        "id": 5, "headline": "Samsung passes Nvidia HBM test",
        "source_url": "http://b", "published_at": datetime(2024, 8, 1),
        "similarity": 0.7,
    }
    conn.fetch = AsyncMock(side_effect=[[pred_row], [], [vector_row]])

    mock_embedder = MagicMock()
    mock_embedder.embed_query_sync.return_value = [0.1] * 1024

    with patch("src.verify.headline_matcher.extract_keywords", return_value=["Samsung", "HBM", "qualification"]), \
         patch("src.verify.headline_matcher._get_embedder", return_value=mock_embedder), \
         patch("src.verify.headline_matcher.vec_str", return_value="[0.1,...]"):
        result = await batch_match(conn, limit=10)

    assert len(result) == 1
    assert result[0]["prediction_id"] == 200


async def test_batch_match_respects_limit():
    """limit 초과 결과는 잘림."""
    from src.verify.headline_matcher import batch_match

    conn = AsyncMock()
    preds = [
        {"id": i, "prediction_text": f"pred {i}", "target_asset": "A",
         "predicted_direction": "up", "prediction_date": date(2024, 1, 1),
         "expected_date": None}
        for i in range(5)
    ]
    hl = {"id": 1, "headline": "h", "source_url": "http://a",
          "published_at": datetime(2024, 6, 1), "keywords": ["a", "b", "c"],
          "overlap_count": 3}

    conn.fetch = AsyncMock(side_effect=[preds] + [[hl]] * 5)

    with patch("src.verify.headline_matcher.extract_keywords", return_value=["a", "b", "c", "d"]):
        result = await batch_match(conn, limit=2)

    assert len(result) == 2
