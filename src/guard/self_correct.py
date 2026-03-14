"""
Self-Correct: Guard 실패 시 에이전트에게 재생성 요청.

에이전트 루프의 마지막 단계로 통합된다.
max_retries=2, 각 재시도는 agent.run()의 iteration count에 포함.
"""

import logging

import anthropic
import asyncpg

from config.settings import ANTHROPIC_API_KEY, MODEL_SONNET
from src.guard.guard import HallucinationGuard, GuardResult

log = logging.getLogger(__name__)

_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
_MAX_RETRIES = 2


async def verify_and_correct(
    analysis: str,
    conn: asyncpg.Connection,
    context_messages: list[dict] | None = None,
    max_retries: int = _MAX_RETRIES,
) -> tuple[str, GuardResult]:
    """
    분석 텍스트를 Guard로 검증하고, 실패 시 재생성.

    Args:
        analysis: 초기 분석 텍스트
        conn: DB 연결
        context_messages: 에이전트 루프의 messages 히스토리 (재생성 시 컨텍스트)
        max_retries: 최대 재시도 횟수

    Returns:
        (최종 분석 텍스트, 마지막 GuardResult)
    """
    guard = HallucinationGuard(conn)
    current = analysis

    for attempt in range(max_retries + 1):
        result = await guard.verify(current)
        if result.is_pass:
            log.info(f"Guard PASS (attempt={attempt})")
            return current, result

        if attempt == max_retries:
            log.warning(f"Guard 최종 FAIL — 원본 반환 (attempt={attempt})")
            return current, result

        log.info(f"Guard FAIL — 재생성 시도 {attempt + 1}/{max_retries}")
        corrected = await _request_correction(current, result, context_messages)
        if corrected:
            current = corrected

    return current, result


async def _request_correction(
    analysis: str,
    guard_result: GuardResult,
    context_messages: list[dict] | None,
) -> str | None:
    """Guard 실패 정보를 포함해 재생성 요청."""
    problematic = "\n".join(f"- {c}" for c in guard_result.problematic_claims[:5])

    correction_prompt = (
        f"아래 분석에서 다음 주장들은 원본 인사이트에 근거가 없거나 불확실해요:\n\n"
        f"{problematic}\n\n"
        f"이 주장들을:\n"
        f"1. 원본 인사이트에 근거가 있으면 올바른 [ref: ins_ID]를 추가하고,\n"
        f"2. 근거를 찾을 수 없으면 해당 주장을 제거하거나 [ref: none]으로 명시하세요.\n\n"
        f"수정된 전체 분석을 반환하세요.\n\n"
        f"원본 분석:\n{analysis}"
    )

    messages = list(context_messages or [])
    messages.append({"role": "user", "content": correction_prompt})

    try:
        resp = await _client.messages.create(
            model=MODEL_SONNET,
            max_tokens=4096,
            messages=messages,
        )
        return resp.content[0].text
    except Exception as e:
        log.error(f"재생성 오류: {e}")
        return None
