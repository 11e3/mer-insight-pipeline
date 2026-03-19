"""embed/local.py 단위 테스트 — SentenceTransformer를 mock."""

import os
import sys
import numpy as np
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
    os.environ.setdefault(_var, "test")

from src.embed.local import LocalEmbedder


def _make_embedder():
    """SentenceTransformer를 mock한 LocalEmbedder."""
    embedder = LocalEmbedder.__new__(LocalEmbedder)
    embedder._model_name = "test-model"
    mock_model = MagicMock()
    # encode returns numpy arrays
    mock_model.encode.return_value = np.array([[0.1, 0.2, 0.3]])
    embedder._model = mock_model
    return embedder


def test_init():
    embedder = LocalEmbedder.__new__(LocalEmbedder)
    embedder._model_name = "test-model"
    embedder._model = None
    assert embedder._model is None


def test_lazy_model_loading():
    """model 프로퍼티가 lazy load."""
    embedder = LocalEmbedder.__new__(LocalEmbedder)
    embedder._model_name = "test-model"
    embedder._model = None

    mock_st = MagicMock()
    mock_instance = MagicMock()
    mock_st.return_value = mock_instance

    with patch.dict("sys.modules", {"sentence_transformers": MagicMock(SentenceTransformer=mock_st)}):
        result = embedder.model
        assert result == mock_instance
        # Second access should not reload
        result2 = embedder.model
        assert result2 == mock_instance
        mock_st.assert_called_once()


def test_embed_passages_sync():
    embedder = _make_embedder()
    embedder._model.encode.return_value = np.array([[0.1, 0.2], [0.3, 0.4]])

    result = embedder.embed_passages_sync(["text1", "text2"])
    assert len(result) == 2
    assert result[0] == [0.1, 0.2]
    assert result[1] == [0.3, 0.4]

    # Check prefix
    call_args = embedder._model.encode.call_args[0][0]
    assert call_args[0].startswith("passage: ")


def test_embed_query_sync():
    embedder = _make_embedder()

    result = embedder.embed_query_sync("금리 전망")
    assert result == [0.1, 0.2, 0.3]

    call_args = embedder._model.encode.call_args[0][0]
    assert call_args[0].startswith("query: ")


async def test_embed_passages_async():
    embedder = _make_embedder()
    embedder._model.encode.return_value = np.array([[0.5, 0.6]])

    result = await embedder.embed_passages(["async text"])
    assert len(result) == 1
    assert result[0] == [0.5, 0.6]


async def test_embed_query_async():
    embedder = _make_embedder()

    result = await embedder.embed_query("async query")
    assert result == [0.1, 0.2, 0.3]


def test_embed_passages_sync_batching():
    """큰 배치가 EMBEDDING_BATCH_SIZE로 분할."""
    embedder = _make_embedder()

    # Mock to return different results per call
    batch1 = np.array([[0.1, 0.2]] * 32)
    batch2 = np.array([[0.3, 0.4]] * 5)
    embedder._model.encode.side_effect = [batch1, batch2]

    texts = [f"text_{i}" for i in range(37)]

    with patch("src.embed.local.EMBEDDING_BATCH_SIZE", 32):
        result = embedder.embed_passages_sync(texts)

    assert len(result) == 37
    assert embedder._model.encode.call_count == 2
