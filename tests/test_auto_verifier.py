"""verify/auto_verifier.py 단위 테스트."""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
    os.environ.setdefault(_var, "test")

from src.verify.auto_verifier import AutoVerifier


def _make_verifier():
    conn = AsyncMock()
    conn.execute = AsyncMock()
    with patch("src.verify.auto_verifier.anthropic.AsyncAnthropic"):
        v = AutoVerifier(conn)
    v.client = MagicMock()
    return v, conn


def _mock_api_response(text, input_tokens=100, output_tokens=50):
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    resp.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
    return resp


MATCH_CORRECT = {
    "prediction_id": 1,
    "prediction_text": "BOJ가 7월에 금리를 인상한다",
    "target_asset": "일본 기준금리",
    "predicted_direction": "up",
    "prediction_date": "2024-06-01",
    "headlines": [
        {"headline": "BOJ raises rate to 0.5%", "source_url": "https://example.com/1",
         "published_at": "2024-07-31", "overlap_count": 3},
    ],
}


async def test_run_no_matches():
    """매칭 0건이면 즉시 반환."""
    v, conn = _make_verifier()

    with patch("src.verify.auto_verifier.batch_match", AsyncMock(return_value=[])):
        result = await v.run()

    assert result["auto_resolved"] == 0
    assert result["pending"] == 0
    assert result["errors"] == 0
    conn.execute.assert_not_called()


async def test_run_single_correct():
    """CORRECT 판정 → DB 반영."""
    v, conn = _make_verifier()

    api_resp = _mock_api_response(
        '{"verdict": "CORRECT", "reason": "BOJ raised rate", "source_url": "https://example.com/1"}'
    )
    v.client.messages.create = AsyncMock(return_value=api_resp)

    with patch("src.verify.auto_verifier.batch_match", AsyncMock(return_value=[MATCH_CORRECT])):
        result = await v.run()

    assert result["auto_resolved"] == 1
    conn.execute.assert_called_once()
    args = conn.execute.call_args[0]
    assert args[1] is True  # is_correct
    assert args[3] == "https://example.com/1"  # source_url
    assert args[4] == 1  # prediction_id


async def test_run_single_incorrect():
    """INCORRECT 판정 → DB 반영."""
    v, conn = _make_verifier()

    api_resp = _mock_api_response(
        '{"verdict": "INCORRECT", "reason": "BOJ kept rate", "source_url": "https://example.com/1"}'
    )
    v.client.messages.create = AsyncMock(return_value=api_resp)

    with patch("src.verify.auto_verifier.batch_match", AsyncMock(return_value=[MATCH_CORRECT])):
        result = await v.run()

    assert result["auto_resolved"] == 1
    args = conn.execute.call_args[0]
    assert args[1] is False  # is_correct


async def test_run_pending_skips_db():
    """PENDING이면 DB 미변경."""
    v, conn = _make_verifier()

    api_resp = _mock_api_response(
        '{"verdict": "PENDING", "reason": "insufficient data", "source_url": ""}'
    )
    v.client.messages.create = AsyncMock(return_value=api_resp)

    with patch("src.verify.auto_verifier.batch_match", AsyncMock(return_value=[MATCH_CORRECT])):
        result = await v.run()

    assert result["auto_resolved"] == 0
    assert result["pending"] == 1
    conn.execute.assert_not_called()


async def test_run_no_source_url_skips():
    """source_url 없는 CORRECT는 스킵."""
    v, conn = _make_verifier()

    api_resp = _mock_api_response(
        '{"verdict": "CORRECT", "reason": "matched", "source_url": ""}'
    )
    v.client.messages.create = AsyncMock(return_value=api_resp)

    with patch("src.verify.auto_verifier.batch_match", AsyncMock(return_value=[MATCH_CORRECT])):
        result = await v.run()

    assert result["auto_resolved"] == 0
    assert result["pending"] == 1
    conn.execute.assert_not_called()


async def test_run_respects_daily_limit():
    """daily_limit 이하만 처리."""
    v, conn = _make_verifier()

    matches = [
        {**MATCH_CORRECT, "prediction_id": i}
        for i in range(10)
    ]

    api_resp = _mock_api_response(
        '{"verdict": "CORRECT", "reason": "ok", "source_url": "https://example.com/1"}'
    )
    v.client.messages.create = AsyncMock(return_value=api_resp)

    with patch("src.verify.auto_verifier.batch_match", AsyncMock(return_value=matches)):
        result = await v.run(daily_limit=3)

    assert result["auto_resolved"] == 3
    assert conn.execute.call_count == 3


async def test_run_api_error_continues():
    """API 에러 1건이어도 나머지 계속 처리."""
    v, conn = _make_verifier()

    matches = [
        {**MATCH_CORRECT, "prediction_id": 1},
        {**MATCH_CORRECT, "prediction_id": 2},
    ]

    good_resp = _mock_api_response(
        '{"verdict": "CORRECT", "reason": "ok", "source_url": "https://example.com/1"}'
    )
    v.client.messages.create = AsyncMock(
        side_effect=[Exception("API error"), good_resp]
    )

    with patch("src.verify.auto_verifier.batch_match", AsyncMock(return_value=matches)):
        result = await v.run()

    assert result["auto_resolved"] == 1
    assert result["errors"] == 1


async def test_verify_single_prompt_format():
    """프롬프트에 예측+헤드라인 포함."""
    v, conn = _make_verifier()

    api_resp = _mock_api_response(
        '{"verdict": "PENDING", "reason": "test", "source_url": ""}'
    )
    v.client.messages.create = AsyncMock(return_value=api_resp)

    result, valid_urls = await v._verify_single(MATCH_CORRECT)

    call_args = v.client.messages.create.call_args
    user_msg = call_args[1]["messages"][0]["content"]
    assert "BOJ가 7월에 금리를 인상한다" in user_msg
    assert "BOJ raises rate to 0.5%" in user_msg
    assert "https://example.com/1" in user_msg


async def test_cost_tracking():
    """비용 추적 정확성."""
    v, conn = _make_verifier()

    api_resp = _mock_api_response(
        '{"verdict": "PENDING", "reason": "test", "source_url": ""}',
        input_tokens=1000,
        output_tokens=100,
    )
    v.client.messages.create = AsyncMock(return_value=api_resp)

    with patch("src.verify.auto_verifier.batch_match", AsyncMock(return_value=[MATCH_CORRECT])):
        result = await v.run()

    # Haiku: 1000 * 1.0/1M + 100 * 5.0/1M = 0.001 + 0.0005 = 0.0015
    assert abs(result["cost_usd"] - 0.0015) < 0.0001


async def test_run_hallucinated_url_skips():
    """Haiku가 헤드라인에 없는 URL을 반환하면 스킵."""
    v, conn = _make_verifier()

    api_resp = _mock_api_response(
        '{"verdict": "CORRECT", "reason": "ok", "source_url": "https://hallucinated.com/fake"}'
    )
    v.client.messages.create = AsyncMock(return_value=api_resp)

    with patch("src.verify.auto_verifier.batch_match", AsyncMock(return_value=[MATCH_CORRECT])):
        result = await v.run()

    assert result["auto_resolved"] == 0
    assert result["pending"] == 1
    conn.execute.assert_not_called()


def test_parse_json_clean():
    """정상 JSON 파싱."""
    result = AutoVerifier._parse_json('{"verdict": "CORRECT", "reason": "ok", "source_url": "url"}')
    assert result["verdict"] == "CORRECT"


def test_parse_json_code_block():
    """코드블록 감싸진 JSON."""
    result = AutoVerifier._parse_json('```json\n{"verdict": "INCORRECT", "reason": "no", "source_url": "u"}\n```')
    assert result["verdict"] == "INCORRECT"


def test_parse_json_garbage():
    """파싱 불가 시 PENDING 반환."""
    result = AutoVerifier._parse_json("this is not json")
    assert result["verdict"] == "PENDING"
