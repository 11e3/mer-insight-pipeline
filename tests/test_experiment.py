"""eval/experiment.py 단위 테스트 — DB/임베딩을 mock."""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
    os.environ.setdefault(_var, "test")

try:
    from src.eval.experiment import _avg_embedding, _vector_search_by_vec
    import numpy as np
except ImportError:
    pytest.skip("scipy/numpy not installed", allow_module_level=True)


# ─── _avg_embedding ──────────────────────────────────────────────────────────

async def test_avg_embedding_basic():
    conn = AsyncMock()
    # Simulate DB returning embeddings as lists
    conn.fetch.return_value = [
        {"embedding": [1.0, 0.0, 0.0]},
        {"embedding": [0.0, 1.0, 0.0]},
    ]

    result = await _avg_embedding(conn, [1, 2])
    assert result is not None
    assert result.startswith("[")
    assert result.endswith("]")


async def test_avg_embedding_no_rows():
    conn = AsyncMock()
    conn.fetch.return_value = []

    result = await _avg_embedding(conn, [999])
    assert result is None


async def test_avg_embedding_string_format():
    """문자열로 저장된 임베딩 파싱."""
    conn = AsyncMock()
    conn.fetch.return_value = [
        {"embedding": "[1.0, 0.0]"},
        {"embedding": "[0.0, 1.0]"},
    ]

    result = await _avg_embedding(conn, [1, 2])
    assert result is not None
    # Average of [1,0] and [0,1] = [0.5, 0.5]
    assert "0.50000000" in result


# ─── _vector_search_by_vec ───────────────────────────────────────────────────

async def test_vector_search_by_vec():
    conn = AsyncMock()
    conn.fetch.return_value = [
        {"id": 10, "similarity": 0.95},
        {"id": 20, "similarity": 0.80},
    ]

    result = await _vector_search_by_vec(conn, "[0.1,0.2,0.3]", 5)
    assert len(result) == 2
    assert result[0] == (10, 0.95)


async def test_vector_search_by_vec_empty():
    conn = AsyncMock()
    conn.fetch.return_value = []

    result = await _vector_search_by_vec(conn, "[0.1]", 5)
    assert result == []


# ─── run_experiment ──────────────────────────────────────────────────────────

async def test_run_experiment(tmp_path):
    """전체 실험 실행 (모든 외부 의존성 mock)."""
    # 데이터셋 생성
    dataset = {
        "test_cases": [
            {
                "query": "금리 전망",
                "relevant_insight_ids": [1, 2],
            },
            {
                "query": "환율 변동",
                "relevant_insight_ids": [3],
            },
        ]
    }
    dataset_path = tmp_path / "gold.json"
    dataset_path.write_text(json.dumps(dataset), encoding="utf-8")

    mock_conn = AsyncMock()
    mock_conn.close = AsyncMock()

    # _avg_embedding mock - return a vec string
    mock_conn.fetch.return_value = [{"embedding": [0.1, 0.2]}]

    mock_bm25 = MagicMock()
    mock_bm25.load.return_value = True
    mock_bm25.search.return_value = [(1, 2.0), (3, 1.5)]

    # Mock _vector_search_by_vec to return results
    vector_results = [(1, 0.9), (2, 0.8)]

    results_dir = tmp_path / "results"
    results_dir.mkdir()

    with patch("src.eval.experiment.asyncpg.connect", AsyncMock(return_value=mock_conn)), \
         patch("src.eval.experiment.BM25Index", return_value=mock_bm25), \
         patch("src.eval.experiment._vector_search_by_vec", AsyncMock(return_value=vector_results)), \
         patch("src.eval.experiment._avg_embedding", AsyncMock(return_value="[0.1,0.2]")):
        # Also patch the output path
        with patch("src.eval.experiment.Path") as mock_path_cls:
            # Need special handling for Path usage
            pass

    # Simpler approach: directly call with mocked internals
    from src.eval.experiment import run_experiment
    with patch("src.eval.experiment.asyncpg.connect", AsyncMock(return_value=mock_conn)), \
         patch("src.eval.experiment.BM25Index", return_value=mock_bm25), \
         patch("src.eval.experiment._vector_search_by_vec", AsyncMock(return_value=vector_results)), \
         patch("src.eval.experiment._avg_embedding", AsyncMock(return_value="[0.1,0.2]")):
        result = await run_experiment(dataset_path, k=5)

    assert isinstance(result, dict)
    assert "vector" in result
    assert "bm25" in result
    assert "hybrid" in result


# ─── run_ablation ────────────────────────────────────────────────────────────

async def test_run_ablation(tmp_path):
    """ablation 실험 기본 동작."""
    dataset = {
        "test_cases": [
            {"query": "금리", "relevant_insight_ids": [1]},
        ]
    }
    dataset_path = tmp_path / "gold.json"
    dataset_path.write_text(json.dumps(dataset), encoding="utf-8")

    mock_conn = AsyncMock()
    mock_conn.close = AsyncMock()
    mock_conn.fetch.return_value = [{"embedding": [0.1, 0.2]}]

    mock_bm25 = MagicMock()
    mock_bm25.load.return_value = True
    mock_bm25.search.return_value = [(1, 1.5)]

    mock_st_model = MagicMock()
    mock_st_model.encode.return_value = np.array([0.1, 0.2])

    vector_results = [(1, 0.9)]

    from src.eval.experiment import run_ablation
    # SentenceTransformer is imported inside run_ablation from sentence_transformers
    mock_st_module = MagicMock()
    mock_st_module.SentenceTransformer.return_value = mock_st_model
    with patch("src.eval.experiment.asyncpg.connect", AsyncMock(return_value=mock_conn)), \
         patch("src.eval.experiment.BM25Index", return_value=mock_bm25), \
         patch("src.eval.experiment._vector_search_by_vec", AsyncMock(return_value=vector_results)), \
         patch.dict("sys.modules", {"sentence_transformers": mock_st_module}):
        result = await run_ablation(dataset_path, k=5, alphas=[0.0, 0.5, 1.0])

    assert len(result) == 3
    assert all("alpha" in r for r in result)
    assert all("precision" in r for r in result)
