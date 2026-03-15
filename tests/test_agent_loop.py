"""
MerAgent 루프 테스트.

Claude API와 asyncpg를 mock해서 에이전트 루프 동작만 검증.
"""
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# 환경변수 사전 설정 (import 전에)
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")


def _make_end_turn_resp(text: str = "분석 완료"):
    """end_turn 응답 mock 생성."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    resp.content = [block]
    return resp


def _make_tool_use_resp(tool_name: str = "search_past_insights"):
    """tool_use 응답 mock 생성. spec으로 text 속성 제외."""
    block = MagicMock(spec=["type", "name", "id", "input"])
    block.type = "tool_use"
    block.name = tool_name
    block.id = "tool_001"
    block.input = {"query": "금리 전망"}
    resp = MagicMock()
    resp.stop_reason = "tool_use"
    resp.content = [block]
    return resp


@pytest.mark.asyncio
async def test_agent_run_end_turn_immediately():
    """Claude가 tool 없이 바로 end_turn → 최종 분석 반환."""
    end_resp = _make_end_turn_resp("최종 분석 텍스트입니다.")

    with patch("src.agent.agent._client") as mock_client, \
         patch("src.agent.agent.ToolExecutor"), \
         patch("src.guard.self_correct.verify_and_correct", new_callable=AsyncMock) as mock_guard:

        mock_client.messages.create = AsyncMock(return_value=end_resp)
        mock_guard.return_value = ("최종 분석 텍스트입니다.", MagicMock(summary="PASS"))

        from src.agent.agent import MerAgent
        agent = MerAgent(
            conn=AsyncMock(),
            embedder=MagicMock(),
            bm25_index=MagicMock(),
        )

        result = await agent.run({"title": "테스트", "content_text": "내용"})

    assert result is not None
    assert len(result) > 0
    mock_client.messages.create.assert_called_once()


@pytest.mark.asyncio
async def test_agent_run_tool_then_end_turn():
    """tool_use 1회 후 end_turn → 2번 API 호출."""
    tool_resp = _make_tool_use_resp()
    end_resp = _make_end_turn_resp("도구 사용 후 분석")

    with patch("src.agent.agent._client") as mock_client, \
         patch("src.agent.agent.ToolExecutor") as mock_executor_cls, \
         patch("src.guard.self_correct.verify_and_correct", new_callable=AsyncMock) as mock_guard:

        mock_client.messages.create = AsyncMock(
            side_effect=[tool_resp, end_resp]
        )
        mock_guard.return_value = ("도구 사용 후 분석", MagicMock(summary="PASS"))

        mock_executor = AsyncMock()
        mock_executor.execute = AsyncMock(return_value='{"results": []}')
        mock_executor_cls.return_value = mock_executor

        from src.agent.agent import MerAgent
        agent = MerAgent(
            conn=AsyncMock(),
            embedder=MagicMock(),
            bm25_index=MagicMock(),
        )

        result = await agent.run({"title": "포스트", "content_text": "내용"})

    assert result is not None
    assert mock_client.messages.create.call_count == 2


@pytest.mark.asyncio
async def test_agent_exits_after_max_iterations():
    """max_iterations 초과 시 루프 탈출, 마지막 텍스트 블록 반환."""
    tool_resp = _make_tool_use_resp()

    with patch("src.agent.agent._client") as mock_client, \
         patch("src.agent.agent.ToolExecutor") as mock_executor_cls, \
         patch("src.guard.self_correct.verify_and_correct", new_callable=AsyncMock) as mock_guard:

        # 항상 tool_use 반환 → max_iterations 소진
        mock_client.messages.create = AsyncMock(return_value=tool_resp)
        mock_guard.return_value = ("(분석 생성 실패)", MagicMock(summary="PASS"))

        mock_executor = AsyncMock()
        mock_executor.execute = AsyncMock(return_value='[]')
        mock_executor_cls.return_value = mock_executor

        from src.agent.agent import MerAgent
        max_iter = 3
        agent = MerAgent(
            conn=AsyncMock(),
            embedder=MagicMock(),
            bm25_index=MagicMock(),
            max_iterations=max_iter,
        )

        result = await agent.run({"title": "포스트", "content_text": "내용"})

    # max_iter번 호출 (이후 루프 탈출)
    assert mock_client.messages.create.call_count == max_iter
    assert result is not None


@pytest.mark.asyncio
async def test_agent_guard_exception_is_ignored():
    """Guard에서 예외가 발생해도 최종 분석은 반환된다."""
    end_resp = _make_end_turn_resp("분석 텍스트")

    with patch("src.agent.agent._client") as mock_client, \
         patch("src.agent.agent.ToolExecutor"), \
         patch("src.guard.self_correct.verify_and_correct",
               new_callable=AsyncMock) as mock_guard:

        mock_client.messages.create = AsyncMock(return_value=end_resp)
        mock_guard.side_effect = RuntimeError("Guard 오류")

        from src.agent.agent import MerAgent
        agent = MerAgent(
            conn=AsyncMock(),
            embedder=MagicMock(),
            bm25_index=MagicMock(),
        )

        result = await agent.run({"title": "테스트", "content_text": "내용"})

    # Guard 실패해도 최종 분석 반환
    assert result == "분석 텍스트"
