"""eval/eval_runner.py 단위 테스트."""

import os
import sys
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
    os.environ.setdefault(_var, "test")

from src.eval.eval_dataset import TestCase
from src.eval.metrics import CaseResult


# ─── EvalRunner ──────────────────────────────────────────────────────────────

async def test_eval_runner_run():
    """EvalRunner.run() 기본 동작."""
    mock_conn = AsyncMock()
    # First fetch for VectorIndex.search, then for HybridSearcher content fetch
    mock_conn.fetch.side_effect = [
        [{"id": 1, "similarity": 0.9}],  # vector search
        [{"id": 1, "content": "인사이트1", "structured_data": "{}", "insight_type": "rule", "date": "2024-01-01"}],
    ]

    mock_embedder = MagicMock()
    mock_embedder.embed_query = AsyncMock(return_value=[0.1, 0.2])

    mock_bm25 = MagicMock()
    mock_bm25.is_ready = True
    mock_bm25.search.return_value = [(1, 2.0)]

    cases = [
        TestCase(id="t1", query="금리 전망", relevant_insight_ids=[1]),
    ]

    with patch("src.eval.eval_runner.judge_context_relevance",
               AsyncMock(return_value={"score": 4, "reasoning": "ok"})):
        from src.eval.eval_runner import EvalRunner
        runner = EvalRunner(mock_conn, mock_embedder, mock_bm25, k=5)
        results = await runner.run(cases)

    assert len(results) == 1
    assert isinstance(results[0], CaseResult)
    assert results[0].case_id == "t1"


async def test_eval_runner_judge_error():
    """LLM Judge 실패 시에도 결과 반환."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []

    mock_embedder = MagicMock()
    mock_embedder.embed_query = AsyncMock(return_value=[0.1])

    mock_bm25 = MagicMock()
    mock_bm25.is_ready = False

    cases = [TestCase(id="t1", query="테스트", relevant_insight_ids=[1])]

    with patch("src.eval.eval_runner.judge_context_relevance",
               AsyncMock(side_effect=Exception("judge error"))):
        from src.eval.eval_runner import EvalRunner
        runner = EvalRunner(mock_conn, mock_embedder, mock_bm25, k=5)
        results = await runner.run(cases)

    assert len(results) == 1
    assert results[0].generation.context_relevance == 0.0


# ─── main ────────────────────────────────────────────────────────────────────

async def test_main_no_dataset(tmp_path):
    """데이터셋 없으면 템플릿 생성."""
    dataset_path = tmp_path / "nonexistent.json"

    from src.eval.eval_runner import main
    with patch("src.eval.eval_runner.save_template") as mock_save:
        await main(dataset_path, tmp_path / "output", k=5)
        mock_save.assert_called_once()


async def test_main_empty_cases(tmp_path):
    """빈 데이터셋이면 조기 종료."""
    dataset_path = tmp_path / "gold.json"
    dataset_path.write_text('{"test_cases": []}', encoding="utf-8")

    from src.eval.eval_runner import main
    await main(dataset_path, tmp_path / "output", k=5)
    # Should return without error


async def test_main_full_run(tmp_path):
    """전체 main 실행 (모든 외부 mock)."""
    dataset = {
        "test_cases": [
            {"id": "e1", "query": "금리", "relevant_insight_ids": [1]},
        ]
    }
    dataset_path = tmp_path / "gold.json"
    dataset_path.write_text(json.dumps(dataset), encoding="utf-8")

    mock_conn = AsyncMock()
    mock_conn.close = AsyncMock()
    mock_conn.fetch.side_effect = [
        [{"id": 1, "similarity": 0.9}],  # vector search
        [{"id": 1, "content": "c", "structured_data": "{}", "insight_type": "rule", "date": "2024-01-01"}],
    ]

    mock_embedder = MagicMock()
    mock_embedder.embed_query = AsyncMock(return_value=[0.1])

    mock_bm25 = MagicMock()
    mock_bm25.load.return_value = True
    mock_bm25.is_ready = True
    mock_bm25.search.return_value = [(1, 1.0)]

    output_dir = tmp_path / "results"

    from src.eval.eval_runner import main
    with patch("src.eval.eval_runner.asyncpg.connect", AsyncMock(return_value=mock_conn)), \
         patch("src.eval.eval_runner.SentenceTransformer", return_value=mock_embedder), \
         patch("src.eval.eval_runner.BM25Index", return_value=mock_bm25), \
         patch("src.eval.eval_runner.judge_context_relevance",
               AsyncMock(return_value={"score": 3})):
        await main(dataset_path, output_dir, k=5)

    # Should have generated report files
    assert output_dir.exists()
