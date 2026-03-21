"""verify/auto_verifier.py 단위 테스트."""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic

sys.path.insert(0, str(Path(__file__).parent.parent))

for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
    os.environ.setdefault(_var, "test")

from src.verify.auto_verifier import AutoVerifier
from src.verify.prompt import parse_verdict_json


def _make_verifier():
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
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
    "expected_date": "2024-07-31",
    "headlines": [
        {"headline": "BOJ raises rate to 0.5%", "source_url": "https://example.com/1",
         "published_at": "2024-07-31", "overlap_count": 3},
        {"headline": "Japan central bank hikes", "source_url": "https://example.com/2",
         "published_at": "2024-07-31", "overlap_count": 2},
    ],
}


async def test_run_no_matches():
    """매칭 0건이면 즉시 반환."""
    v, conn = _make_verifier()

    with patch("src.verify.auto_verifier.batch_match", AsyncMock(return_value=[])):
        result = await v.run()

    assert result["auto_resolved"] == 0
    conn.execute.assert_not_called()


async def test_run_single_correct():
    """CORRECT 판정 → ref 번호로 source_url 자동 할당."""
    v, conn = _make_verifier()

    api_resp = _mock_api_response(
        '{"verdict": "CORRECT", "reason": "BOJ raised rate" }'
    )
    v.client.messages.create = AsyncMock(return_value=api_resp)

    with patch("src.verify.auto_verifier.batch_match", AsyncMock(return_value=[MATCH_CORRECT])):
        result = await v.run()

    assert result["auto_resolved"] == 1
    args = conn.execute.call_args[0]
    assert args[1] is True  # is_correct
    assert args[3] == "https://example.com/1"  # ref=1 → 첫 번째 헤드라인


async def test_run_single_incorrect():
    """INCORRECT 판정 → 첫 번째(최고 overlap) 헤드라인 URL."""
    v, conn = _make_verifier()

    api_resp = _mock_api_response(
        '{"verdict": "INCORRECT", "reason": "BOJ kept rate" }'
    )
    v.client.messages.create = AsyncMock(return_value=api_resp)

    with patch("src.verify.auto_verifier.batch_match", AsyncMock(return_value=[MATCH_CORRECT])):
        result = await v.run()

    assert result["auto_resolved"] == 1
    args = conn.execute.call_args[0]
    assert args[1] is False
    assert args[3] == "https://example.com/1"  # always first (highest overlap)


async def test_run_pending_skips_db():
    """PENDING이면 DB 미변경."""
    v, conn = _make_verifier()

    api_resp = _mock_api_response(
        '{"verdict": "PENDING", "reason": "insufficient data" }'
    )
    v.client.messages.create = AsyncMock(return_value=api_resp)

    with patch("src.verify.auto_verifier.batch_match", AsyncMock(return_value=[MATCH_CORRECT])):
        result = await v.run()

    assert result["auto_resolved"] == 0
    assert result["pending"] == 1
    # PENDING 시 skipped_at만 마킹 (is_correct는 변경 안 함)
    conn.execute.assert_called_once_with(
        "UPDATE predictions SET skipped_at = CURRENT_DATE WHERE id = $1",
        MATCH_CORRECT["prediction_id"],
    )


async def test_run_always_uses_first_headline_url():
    """source_url은 항상 첫 번째(최고 overlap) 헤드라인."""
    v, conn = _make_verifier()

    api_resp = _mock_api_response(
        '{"verdict": "CORRECT", "reason": "ok"}'
    )
    v.client.messages.create = AsyncMock(return_value=api_resp)

    with patch("src.verify.auto_verifier.batch_match", AsyncMock(return_value=[MATCH_CORRECT])):
        result = await v.run()

    assert result["auto_resolved"] == 1
    args = conn.execute.call_args[0]
    assert args[3] == "https://example.com/1"


async def test_run_respects_daily_limit():
    """daily_limit 이하만 처리."""
    v, conn = _make_verifier()

    matches = [{**MATCH_CORRECT, "prediction_id": i} for i in range(10)]
    api_resp = _mock_api_response('{"verdict": "CORRECT", "reason": "ok" }')
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
    good_resp = _mock_api_response('{"verdict": "CORRECT", "reason": "ok" }')
    v.client.messages.create = AsyncMock(side_effect=[anthropic.APIError(message="API error", request=MagicMock(), body=None), good_resp])

    with patch("src.verify.auto_verifier.batch_match", AsyncMock(return_value=matches)):
        result = await v.run()

    assert result["auto_resolved"] == 1
    assert result["errors"] == 1


async def test_verify_single_prompt_format():
    """프롬프트에 예측+헤드라인 포함, URL은 제외."""
    v, conn = _make_verifier()

    api_resp = _mock_api_response('{"verdict": "PENDING", "reason": "test" }')
    v.client.messages.create = AsyncMock(return_value=api_resp)

    await v._verify_single(MATCH_CORRECT)

    call_args = v.client.messages.create.call_args
    user_msg = call_args[1]["messages"][0]["content"]
    assert "BOJ가 7월에 금리를 인상한다" in user_msg
    assert "BOJ raises rate to 0.5%" in user_msg
    # URL은 프롬프트에 포함되지 않음
    assert "https://example.com" not in user_msg


async def test_cost_tracking():
    """비용 추적 정확성."""
    v, conn = _make_verifier()

    api_resp = _mock_api_response(
        '{"verdict": "PENDING", "reason": "test" }',
        input_tokens=1000, output_tokens=100,
    )
    v.client.messages.create = AsyncMock(return_value=api_resp)

    with patch("src.verify.auto_verifier.batch_match", AsyncMock(return_value=[MATCH_CORRECT])):
        result = await v.run()

    assert abs(result["cost_usd"] - 0.0015) < 0.0001


def test_parse_json_clean():
    """정상 JSON 파싱."""
    result = parse_verdict_json('{"verdict": "CORRECT", "reason": "ok" }')
    assert result["verdict"] == "CORRECT"


def test_parse_json_code_block():
    """코드블록 감싸진 JSON."""
    result = parse_verdict_json('```json\n{"verdict": "INCORRECT", "reason": "no" }\n```')
    assert result["verdict"] == "INCORRECT"


def test_parse_json_garbage():
    """파싱 불가 시 PENDING 반환."""
    result = parse_verdict_json("this is not json")
    assert result["verdict"] == "PENDING"
