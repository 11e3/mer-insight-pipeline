"""embed/factory.py + embed/backfill.py 단위 테스트."""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
    os.environ.setdefault(_var, "test")

from src.embed.factory import vec_str


# ─── vec_str ─────────────────────────────────────────────────────────────────

def test_vec_str_basic():
    result = vec_str([1.0, 2.0, 3.0])
    assert result.startswith("[")
    assert result.endswith("]")
    assert "1.00000000" in result
    assert "2.00000000" in result
    assert "3.00000000" in result


def test_vec_str_empty():
    result = vec_str([])
    assert result == "[]"


def test_vec_str_single():
    result = vec_str([0.5])
    assert result == "[0.50000000]"


def test_vec_str_negative():
    result = vec_str([-1.0, 0.0, 1.0])
    assert "-1.00000000" in result
    assert "0.00000000" in result


# ─── get_embedder ────────────────────────────────────────────────────────────

def test_get_embedder_local():
    """GCP_PROJECT_ID 없으면 LocalEmbedder."""
    mock_local_instance = MagicMock()
    mock_local_cls = MagicMock(return_value=mock_local_instance)

    import src.embed.factory as fmod
    orig_gcp = fmod.GCP_PROJECT_ID
    fmod.GCP_PROJECT_ID = ""
    try:
        with patch.dict("sys.modules", {"src.embed.local": MagicMock(LocalEmbedder=mock_local_cls)}):
            result = fmod.get_embedder()
            assert result == mock_local_instance
    finally:
        fmod.GCP_PROJECT_ID = orig_gcp


def test_get_embedder_vertex():
    """GCP_PROJECT_ID 있으면 VertexEmbedder."""
    mock_vertex_instance = MagicMock()
    mock_vertex_cls = MagicMock(return_value=mock_vertex_instance)

    import src.embed.factory as fmod
    orig_gcp = fmod.GCP_PROJECT_ID
    fmod.GCP_PROJECT_ID = "my-project"
    try:
        with patch.dict("sys.modules", {"src.embed.vertex": MagicMock(VertexEmbedder=mock_vertex_cls)}):
            result = fmod.get_embedder()
            assert result == mock_vertex_instance
    finally:
        fmod.GCP_PROJECT_ID = orig_gcp


# ─── fill_embeddings ─────────────────────────────────────────────────────────

async def test_fill_embeddings_no_rows():
    """embedding 생성 대상 없으면 조기 종료."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []
    mock_conn.close = AsyncMock()

    mock_embedder = MagicMock()

    with patch("src.db.connection.asyncpg.connect", AsyncMock(return_value=mock_conn)), \
         patch("src.embed.backfill.get_embedder", return_value=mock_embedder):
        from src.embed.backfill import fill_embeddings
        await fill_embeddings()

    mock_conn.execute.assert_not_called()


async def test_fill_embeddings_with_rows():
    """embedding 생성 대상이 있으면 임베딩 생성 + UPDATE."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [
        {"id": 1, "content": "인사이트 1"},
        {"id": 2, "content": "인사이트 2"},
    ]
    mock_conn.close = AsyncMock()

    mock_embedder = MagicMock()
    mock_embedder.embed_passages = AsyncMock(return_value=[[0.1, 0.2], [0.3, 0.4]])

    with patch("src.db.connection.asyncpg.connect", AsyncMock(return_value=mock_conn)), \
         patch("src.embed.backfill.get_embedder", return_value=mock_embedder):
        from src.embed.backfill import fill_embeddings
        await fill_embeddings()

    assert mock_conn.execute.call_count == 2
    sql = mock_conn.execute.call_args_list[0][0][0]
    assert "UPDATE mer_insights SET embedding" in sql
