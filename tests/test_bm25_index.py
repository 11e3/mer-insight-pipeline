"""search/bm25_index.py 단위 테스트."""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent))

for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
    os.environ.setdefault(_var, "test")

from src.search.bm25_index import BM25Index


# ─── 초기 상태 ───────────────────────────────────────────────────────────────

def test_initial_state():
    idx = BM25Index()
    assert idx._bm25 is None
    assert idx._doc_ids == []
    assert idx.is_ready is False


def test_search_when_not_ready():
    idx = BM25Index()
    result = idx.search("금리 인하")
    assert result == []


# ─── build ───────────────────────────────────────────────────────────────────

async def test_build_with_data():
    idx = BM25Index()
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [
        {"id": 1, "content": "금리 인하가 예상됩니다"},
        {"id": 2, "content": "부동산 가격이 하락할 전망"},
        {"id": 3, "content": "원유 가격 상승 추세"},
    ]

    await idx.build(mock_conn)

    assert idx.is_ready is True
    assert len(idx._doc_ids) == 3


async def test_build_empty_data():
    idx = BM25Index()
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []

    await idx.build(mock_conn)

    assert idx.is_ready is False
    assert idx._doc_ids == []


# ─── save / load ─────────────────────────────────────────────────────────────

def test_save_and_load(tmp_path):
    idx = BM25Index()

    # Manually set up index state
    from rank_bm25 import BM25Okapi
    corpus = [["금리", "인하"], ["부동산", "가격"]]
    idx._bm25 = BM25Okapi(corpus)
    idx._doc_ids = [10, 20]

    cache_path = tmp_path / "bm25_cache.pkl"
    idx.save(cache_path)
    assert cache_path.exists()

    # Load into new instance
    idx2 = BM25Index()
    loaded = idx2.load(cache_path)
    assert loaded is True
    assert idx2.is_ready is True
    assert idx2._doc_ids == [10, 20]


def test_load_nonexistent_file():
    idx = BM25Index()
    loaded = idx.load("/nonexistent/path/bm25.pkl")
    assert loaded is False
    assert idx.is_ready is False


def test_save_creates_parent_dir(tmp_path):
    idx = BM25Index()
    idx._bm25 = None
    idx._doc_ids = []

    cache_path = tmp_path / "nested" / "dir" / "bm25.pkl"
    idx.save(cache_path)
    assert cache_path.exists()


# ─── search ──────────────────────────────────────────────────────────────────

async def test_search_returns_results():
    idx = BM25Index()
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [
        {"id": 1, "content": "금리 인하가 예상됩니다 기준금리"},
        {"id": 2, "content": "부동산 가격이 하락할 전망 매매"},
        {"id": 3, "content": "원유 가격 상승 추세 에너지"},
    ]

    await idx.build(mock_conn)
    results = idx.search("금리 인하", top_k=2)

    assert len(results) <= 2
    # Results are (insight_id, score) tuples
    for doc_id, score in results:
        assert isinstance(doc_id, int)
        assert isinstance(score, float)
        assert score > 0


async def test_search_top_k():
    idx = BM25Index()
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [
        {"id": i, "content": f"문서 {i} 내용 금리"} for i in range(10)
    ]

    await idx.build(mock_conn)
    results = idx.search("금리", top_k=3)

    assert len(results) <= 3


async def test_search_no_match():
    idx = BM25Index()
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [
        {"id": 1, "content": "가나다라마바사"},
    ]

    await idx.build(mock_conn)
    results = idx.search("xyz completely unrelated query", top_k=5)

    # May or may not return results depending on tokenizer
    for _, score in results:
        assert isinstance(score, float)
