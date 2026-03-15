"""
Hybrid Search 테스트.

_rrf_fuse() 순수 함수 + HybridSearcher.search() (mock BM25/Vector/DB).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.search.hybrid import _rrf_fuse, HybridSearcher


# ── _rrf_fuse 순수 함수 ───────────────────────────────────────────────────────

def test_rrf_fuse_alpha_1_bm25_only():
    """alpha=1.0이면 BM25 결과만 점수에 기여."""
    bm25 = [(1, 0.9), (2, 0.5)]
    vec = [(3, 0.8), (1, 0.4)]
    result = _rrf_fuse(bm25, vec, alpha=1.0)
    ids = [r[0] for r in result]
    # BM25 1위(id=1)가 전체 1위, vector-only(id=3)는 점수 0
    assert ids[0] == 1
    # alpha=1.0이면 vector 기여 없음 → id=3은 점수 0, 결과에 없거나 최하위
    scores = dict(result)
    assert scores.get(3, 0.0) == 0.0


def test_rrf_fuse_alpha_0_vector_only():
    """alpha=0.0이면 vector 결과만 점수에 기여."""
    bm25 = [(1, 0.9)]
    vec = [(3, 0.8), (4, 0.7)]
    result = _rrf_fuse(bm25, vec, alpha=0.0)
    ids = [r[0] for r in result]
    assert ids[0] == 3  # vector 1위가 전체 1위
    scores = dict(result)
    assert scores.get(1, 0.0) == 0.0  # BM25-only id=1은 점수 없음


def test_rrf_fuse_merges_both_sources():
    """alpha=0.5이면 두 리스트가 병합된다."""
    bm25 = [(1, 1.0)]
    vec = [(2, 1.0)]
    result = _rrf_fuse(bm25, vec, alpha=0.5)
    ids = {r[0] for r in result}
    assert ids == {1, 2}


def test_rrf_fuse_shared_doc_accumulates_score():
    """두 리스트에 동일 문서가 있으면 점수가 합산된다."""
    bm25 = [(10, 0.9)]
    vec = [(10, 0.8), (20, 0.5)]
    result_hybrid = _rrf_fuse(bm25, vec, alpha=0.5)
    result_vec_only = _rrf_fuse([], vec, alpha=0.5)

    score_hybrid = dict(result_hybrid)[10]
    score_vec = dict(result_vec_only)[10]
    assert score_hybrid > score_vec  # BM25 기여분만큼 더 높아야 함


def test_rrf_fuse_empty_inputs():
    """두 리스트 모두 비어있으면 빈 리스트 반환."""
    result = _rrf_fuse([], [], alpha=0.5)
    assert result == []


def test_rrf_fuse_empty_bm25():
    """BM25 비어있으면 vector 결과만 반환."""
    vec = [(5, 0.9), (6, 0.7)]
    result = _rrf_fuse([], vec, alpha=0.4)
    ids = [r[0] for r in result]
    assert ids[0] == 5


def test_rrf_fuse_sorted_descending():
    """결과는 점수 내림차순 정렬."""
    bm25 = [(1, 0.9), (2, 0.5), (3, 0.1)]
    vec = [(2, 0.8), (1, 0.4), (3, 0.2)]
    result = _rrf_fuse(bm25, vec, alpha=0.5)
    scores = [s for _, s in result]
    assert scores == sorted(scores, reverse=True)


# ── HybridSearcher.search() ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hybrid_searcher_returns_results():
    """정상 경로: BM25+벡터 결과를 RRF 후 DB 조회해서 반환."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [
        {"id": 1, "content": "테스트 인사이트", "structured_data": None,
         "insight_type": "rule", "date": None}
    ]

    mock_bm25 = MagicMock()
    mock_bm25.is_ready = True
    mock_bm25.search.return_value = [(1, 0.9)]

    mock_vector = AsyncMock()
    mock_vector.search = AsyncMock(return_value=[(1, 0.8)])

    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher._conn = mock_conn
    searcher._bm25 = mock_bm25
    searcher._vector = mock_vector
    searcher.alpha = 0.4

    results = await searcher.search("금리", top_k=5)

    assert len(results) >= 1
    assert results[0]["id"] == 1
    assert "content" in results[0]


@pytest.mark.asyncio
async def test_hybrid_searcher_top_k_limit():
    """top_k보다 많은 후보가 있어도 top_k개만 반환."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [
        {"id": i, "content": f"인사이트{i}", "structured_data": None,
         "insight_type": "rule", "date": None}
        for i in range(1, 11)
    ]

    mock_bm25 = MagicMock()
    mock_bm25.is_ready = True
    mock_bm25.search.return_value = [(i, 1.0 / i) for i in range(1, 11)]

    mock_vector = AsyncMock()
    mock_vector.search = AsyncMock(
        return_value=[(i, 1.0 / i) for i in range(1, 11)]
    )

    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher._conn = mock_conn
    searcher._bm25 = mock_bm25
    searcher._vector = mock_vector
    searcher.alpha = 0.4

    results = await searcher.search("테스트", top_k=3)
    assert len(results) <= 3


@pytest.mark.asyncio
async def test_hybrid_searcher_bm25_not_ready_uses_vector_only():
    """BM25 인덱스 미준비 시 vector 결과만으로 검색."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [
        {"id": 7, "content": "벡터 전용 결과", "structured_data": None,
         "insight_type": "evaluation", "date": None}
    ]

    mock_bm25 = MagicMock()
    mock_bm25.is_ready = False  # BM25 미준비

    mock_vector = AsyncMock()
    mock_vector.search = AsyncMock(return_value=[(7, 0.95)])

    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher._conn = mock_conn
    searcher._bm25 = mock_bm25
    searcher._vector = mock_vector
    searcher.alpha = 0.4

    results = await searcher.search("테스트", top_k=5)
    assert len(results) >= 1
    assert results[0]["id"] == 7


@pytest.mark.asyncio
async def test_hybrid_searcher_fetch_content_false():
    """fetch_content=False이면 DB 조회 없이 id/score만 반환."""
    mock_conn = AsyncMock()
    mock_bm25 = MagicMock()
    mock_bm25.is_ready = True
    mock_bm25.search.return_value = [(5, 0.8)]
    mock_vector = AsyncMock()
    mock_vector.search = AsyncMock(return_value=[(5, 0.7)])

    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher._conn = mock_conn
    searcher._bm25 = mock_bm25
    searcher._vector = mock_vector
    searcher.alpha = 0.4

    results = await searcher.search("쿼리", top_k=5, fetch_content=False)

    mock_conn.fetch.assert_not_called()
    assert len(results) >= 1
    assert "id" in results[0]
    assert "score" in results[0]
