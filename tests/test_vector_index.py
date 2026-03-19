"""search/vector_index.py 단위 테스트."""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
    os.environ.setdefault(_var, "test")

from src.search.vector_index import VectorIndex


def _make_vector_index():
    conn = AsyncMock()
    embedder = MagicMock()
    embedder.embed_query = AsyncMock(return_value=[0.1, 0.2, 0.3])
    return VectorIndex(conn, embedder)


async def test_search_basic():
    vi = _make_vector_index()
    vi.conn.fetch.return_value = [
        {"id": 1, "similarity": 0.95},
        {"id": 2, "similarity": 0.80},
    ]

    results = await vi.search("금리 전망", top_k=5)

    assert len(results) == 2
    assert results[0] == (1, 0.95)
    assert results[1] == (2, 0.80)
    vi.embedder.embed_query.assert_called_once()


async def test_search_empty_results():
    vi = _make_vector_index()
    vi.conn.fetch.return_value = []

    results = await vi.search("없는 쿼리")
    assert results == []


async def test_search_truncates_query():
    vi = _make_vector_index()
    vi.conn.fetch.return_value = []

    long_query = "x" * 1000
    await vi.search(long_query)

    # embed_query should receive truncated query (500 chars)
    called_query = vi.embedder.embed_query.call_args[0][0]
    assert len(called_query) == 500
