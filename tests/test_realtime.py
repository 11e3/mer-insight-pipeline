"""extract/realtime.py 단위 테스트."""

import os
import sys
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
    os.environ.setdefault(_var, "test")


def _make_mock_embedder():
    embedder = MagicMock()
    embedder.embed_passages = AsyncMock(return_value=[[0.1] * 10])
    return embedder


def _make_post():
    return {
        "log_no": "1234567890",
        "title": "테스트 포스트",
        "date": "2024-01-15",
        "url": "https://blog.naver.com/ranto28/1234567890",
        "content_text": "본문 내용",
    }


async def test_extract_and_save_no_post_id():
    """post_id가 없으면 빈 결과 반환."""
    conn = AsyncMock()
    conn.fetchval.return_value = None  # no post_id

    with patch("src.extract.realtime.client"):
        from src.extract.realtime import extract_and_save
        result = await extract_and_save(conn, _make_mock_embedder(), _make_post())

    assert result["count"] == 0
    assert result["primary_topic"] == "기타"


async def test_extract_and_save_api_error():
    """Claude API 에러 시 빈 결과."""
    conn = AsyncMock()
    conn.fetchval.return_value = 42

    with patch("src.extract.realtime.client") as mock_client:
        mock_client.messages.create = AsyncMock(side_effect=Exception("API error"))
        from src.extract.realtime import extract_and_save
        result = await extract_and_save(conn, _make_mock_embedder(), _make_post())

    assert result["count"] == 0


async def test_extract_and_save_json_parse_error():
    """JSON 파싱 실패 시 빈 결과."""
    conn = AsyncMock()
    conn.fetchval.return_value = 42

    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text="not json at all")]

    with patch("src.extract.realtime.client") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        from src.extract.realtime import extract_and_save
        result = await extract_and_save(conn, _make_mock_embedder(), _make_post())

    assert result["count"] == 0


async def test_extract_and_save_success():
    """정상적인 인사이트 추출 + 저장."""
    conn = AsyncMock()
    conn.fetchval.return_value = 42  # post_id first, then insight_id

    data = {
        "primary_topic": "거시경제",
        "post_summary": "금리 전망",
        "rules": [{"rule_statement": "금리 오르면 부동산 하락"}],
        "predictions": [],
        "evaluations": [],
        "macro_views": [],
    }
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text=json.dumps(data))]

    embedder = _make_mock_embedder()

    with patch("src.extract.realtime.client") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        from src.extract.realtime import extract_and_save
        result = await extract_and_save(conn, embedder, _make_post())

    assert result["count"] == 1
    assert result["primary_topic"] == "거시경제"
    assert result["post_summary"] == "금리 전망"


async def test_extract_and_save_prediction_with_verifiable():
    """verifiable prediction이면 mer_predictions에도 삽입."""
    conn = AsyncMock()
    # First fetchval = post_id, second = insight_id
    conn.fetchval.side_effect = [42, 100]

    data = {
        "primary_topic": "금융시장",
        "post_summary": "주가 전망",
        "rules": [],
        "predictions": [{
            "prediction": "코스피 상승",
            "direction": "up",
            "target_asset": "KOSPI",
            "verifiable": True,
        }],
        "evaluations": [],
        "macro_views": [],
    }
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text=json.dumps(data))]

    with patch("src.extract.realtime.client") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        from src.extract.realtime import extract_and_save
        result = await extract_and_save(conn, _make_mock_embedder(), _make_post())

    assert result["count"] == 1
    # Should have called execute for mer_predictions insert
    assert conn.execute.call_count >= 1


async def test_extract_and_save_code_block_json():
    """```json ... ``` 형태 응답 처리."""
    conn = AsyncMock()
    conn.fetchval.return_value = 42

    data = {
        "primary_topic": "기타",
        "post_summary": "요약",
        "rules": [],
        "predictions": [],
        "evaluations": [{"entity_name": "A", "mer_assessment": "positive", "reasoning": "좋음"}],
        "macro_views": [],
    }
    raw_text = "```json\n" + json.dumps(data) + "\n```"
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text=raw_text)]

    with patch("src.extract.realtime.client") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        from src.extract.realtime import extract_and_save
        result = await extract_and_save(conn, _make_mock_embedder(), _make_post())

    assert result["count"] == 1
