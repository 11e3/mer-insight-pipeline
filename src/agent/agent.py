"""
MerAgent: 새 포스트 → LLM 자율 판단/분기 → 최종 분석

프레임워크 없이 순수 while loop로 구현.
tool_use → tool_result → 반복 → end_turn 패턴.

Usage:
    agent = MerAgent(conn, embedder, bm25_index)
    analysis = await agent.run(post)
"""

import logging
from typing import TYPE_CHECKING

import anthropic
import asyncpg

from config.settings import ANTHROPIC_API_KEY, MODEL_SONNET
from src.extract.vertex_embedder import VertexEmbedder
from src.search.bm25_index import BM25Index
from src.agent.prompts import AGENT_SYSTEM_PROMPT
from src.agent.state import AgentState
from src.agent.tools import TOOL_SPECS, ToolExecutor
from src.guard.self_correct import verify_and_correct

if TYPE_CHECKING:
    from src.observability.tracer import Tracer

log = logging.getLogger(__name__)

_MAX_ITERATIONS = 5
_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


class MerAgent:
    """메르 블로그 포스트 분석 에이전트."""

    def __init__(
        self,
        conn: asyncpg.Connection,
        embedder: VertexEmbedder,
        bm25_index: BM25Index,
        max_iterations: int = _MAX_ITERATIONS,
    ):
        self._executor = ToolExecutor(conn, embedder, bm25_index)
        self._max_iterations = max_iterations

    async def run(self, post: dict, tracer: "Tracer | None" = None) -> str:
        """
        새 포스트를 받아 에이전트 루프를 실행하고 최종 분석을 반환.

        Args:
            post:   {"title": str, "content_text": str, "url": str, "date": str}
            tracer: Tracer 인스턴스 (None이면 추적 안 함)

        Returns:
            최종 분석 텍스트 (citation 태그 포함)
        """
        state = AgentState()
        state.add_user(_format_post_message(post))

        log.info(f"MerAgent 시작: {post.get('title', '')[:50]}")

        while not state.is_done and state.iteration < self._max_iterations:
            state.increment()
            log.info(f"  iteration {state.iteration}/{self._max_iterations}")

            create_kwargs = dict(
                model=MODEL_SONNET,
                max_tokens=4096,
                system=AGENT_SYSTEM_PROMPT,
                tools=TOOL_SPECS,
                messages=state.messages,
            )

            if tracer is not None:
                resp = await tracer.call(
                    _client.messages.create,
                    span_name=f"agent_step_{state.iteration}",
                    **create_kwargs,
                )
            else:
                resp = await _client.messages.create(**create_kwargs)

            # assistant 메시지 히스토리에 추가
            state.add_assistant(resp.content)

            if resp.stop_reason == "end_turn":
                # tool 호출 없이 텍스트로 응답 → 최종 분석 완성
                text_blocks = [b.text for b in resp.content if hasattr(b, "text")]
                state.final_analysis = "\n".join(text_blocks)
                break

            if resp.stop_reason == "tool_use":
                tool_result_blocks = []
                for block in resp.content:
                    if block.type != "tool_use":
                        continue

                    log.info(f"    tool: {block.name}({_summarize_input(block.input)})")
                    result_str = await self._executor.execute(block.name, block.input)

                    tool_result_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    })

                state.add_tool_results(tool_result_blocks)
                continue

            # 예상치 못한 stop_reason
            log.warning(f"  예상치 못한 stop_reason: {resp.stop_reason}")
            break

        if not state.final_analysis:
            # max_iterations 초과 또는 루프 탈출 → 마지막 텍스트 블록 사용
            for msg in reversed(state.messages):
                if msg["role"] == "assistant":
                    content = msg["content"]
                    texts = [b.text for b in content if hasattr(b, "text")]
                    if texts:
                        state.final_analysis = "\n".join(texts)
                        break

        log.info(f"  완료 (iterations={state.iteration})")

        final = state.final_analysis or "(분석 생성 실패)"

        # Hallucination Guard + 자동 재생성
        try:
            from src.guard.self_correct import verify_and_correct
            final, guard_result = await verify_and_correct(
                analysis=final,
                conn=self._executor._conn,
                context_messages=state.messages,
            )
            log.info(f"  Guard: {guard_result.summary}")
        except Exception as e:
            log.warning(f"  Guard 오류 (무시): {e}")

        return final


# ─── 헬퍼 ────────────────────────────────────────────────────────────────────

def _format_post_message(post: dict) -> str:
    title   = post.get("title", "")
    content = (post.get("content_text") or post.get("content", ""))[:3000]
    url     = post.get("url", "")
    dt      = post.get("date", "")
    return (
        f"새 블로그 포스트를 분석해주세요.\n\n"
        f"제목: {title}\n"
        f"작성일: {dt}\n"
        f"URL: {url}\n\n"
        f"본문:\n{content}\n\n"
        f"도구를 활용해 과거 인사이트와 비교하고 최종 분석을 작성하세요."
    )


def _summarize_input(inp: dict) -> str:
    """로그용 tool input 요약."""
    for key in ("query", "topic", "new_claim", "insight_text", "new_post_summary"):
        if key in inp:
            return f"{key}={str(inp[key])[:40]}"
    return str(list(inp.keys()))
