"""eval/llm_judge.py 단위 테스트."""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
    os.environ.setdefault(_var, "test")

from src.eval.llm_judge import (
    judge_context_relevance,
    judge_faithfulness,
    judge_answer_relevance,
    _call_judge,
    normalize_score,
)


# ─── normalize_score ─────────────────────────────────────────────────────────

def test_normalize_score_min():
    assert normalize_score(1) == 0.0


def test_normalize_score_max():
    assert normalize_score(5) == 1.0


def test_normalize_score_mid():
    assert normalize_score(3) == 0.5


def test_normalize_score_below_min():
    assert normalize_score(0) == 0.0


def test_normalize_score_above_max():
    assert normalize_score(10) == 1.0


def test_normalize_score_float():
    assert abs(normalize_score(2.0) - 0.25) < 1e-9


# ─── _call_judge ─────────────────────────────────────────────────────────────

async def test_call_judge_success():
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text='{"score": 4, "reasoning": "good"}')]

    with patch("src.eval.llm_judge._client") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        result = await _call_judge("test prompt")

    assert result["score"] == 4
    assert result["reasoning"] == "good"


async def test_call_judge_json_with_extra_text():
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text='Here is my answer: {"score": 3, "reasoning": "ok"} done')]

    with patch("src.eval.llm_judge._client") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        result = await _call_judge("test prompt")

    assert result["score"] == 3


async def test_call_judge_api_error():
    with patch("src.eval.llm_judge._client") as mock_client:
        mock_client.messages.create = AsyncMock(side_effect=Exception("API error"))
        result = await _call_judge("test prompt")

    assert result["score"] == 0
    assert "실패" in result["reasoning"]


async def test_call_judge_invalid_json():
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text="no json here")]

    with patch("src.eval.llm_judge._client") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        result = await _call_judge("test prompt")

    assert result["score"] == 0


# ─── judge_context_relevance ────────────────────────────────────────────────

async def test_judge_context_relevance():
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text='{"score": 5, "reasoning": "very relevant"}')]

    with patch("src.eval.llm_judge._client") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        result = await judge_context_relevance("금리 전망", ["금리 인하 가능성"])

    assert result["score"] == 5


# ─── judge_faithfulness ──────────────────────────────────────────────────────

async def test_judge_faithfulness():
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text='{"score": 4, "reasoning": "ok", "hallucinated_claims": []}')]

    with patch("src.eval.llm_judge._client") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        result = await judge_faithfulness("분석 내용", ["원본1", "원본2"])

    assert result["score"] == 4
    assert result["hallucinated_claims"] == []


# ─── judge_answer_relevance ──────────────────────────────────────────────────

async def test_judge_answer_relevance():
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text='{"score": 3, "reasoning": "partial"}')]

    with patch("src.eval.llm_judge._client") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        result = await judge_answer_relevance("질문", "분석")

    assert result["score"] == 3
