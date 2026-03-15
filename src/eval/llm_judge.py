"""
LLM-as-Judge: Claude Sonnet을 judge로 사용해 생성 품질을 평가.

평가 항목:
- Context Relevance: 검색 결과가 쿼리에 얼마나 관련 있는지
- Faithfulness: 분석이 검색된 원본에만 근거하는지 (hallucination 역수)
- Answer Relevance: 최종 분석이 원래 질문에 얼마나 답하는지
"""

import json
import logging

import anthropic

from config.settings import ANTHROPIC_API_KEY, MODEL_SONNET

log = logging.getLogger(__name__)
_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


async def judge_context_relevance(query: str, retrieved_texts: list[str]) -> dict:
    """검색된 인사이트가 쿼리와 얼마나 관련 있는지 1-5점 평가."""
    context_block = "\n".join(f"[{i+1}] {t[:200]}" for i, t in enumerate(retrieved_texts))
    prompt = f"""\
다음 [쿼리]에 대해 [검색 결과]가 얼마나 관련 있는지 평가하세요.

[쿼리]
{query}

[검색 결과]
{context_block}

평가 기준:
1점 = 전혀 관련 없음
2점 = 약간 관련 있음
3점 = 어느 정도 관련 있음
4점 = 관련 있음
5점 = 매우 관련 있음

JSON으로만 응답하세요:
{{"score": 1-5, "reasoning": "한 문장 이유"}}"""

    return await _call_judge(prompt)


async def judge_faithfulness(analysis: str, source_insights: list[str]) -> dict:
    """분석이 원본 인사이트에만 근거하는지 평가 (환각 감지)."""
    sources_block = "\n".join(f"[출처{i+1}] {t[:300]}" for i, t in enumerate(source_insights))
    prompt = f"""\
다음 [분석]이 [원본 인사이트]에만 근거하는지 평가하세요.
원본에 없는 주장이 포함되어 있으면 점수를 낮게 주세요.

[원본 인사이트]
{sources_block}

[분석]
{analysis[:1500]}

평가 기준:
1점 = 대부분 원본에 없는 주장 (심각한 환각)
2점 = 원본 외 주장이 많음
3점 = 일부 근거 없는 주장
4점 = 대부분 원본 근거, 사소한 확장
5점 = 완전히 원본에 근거

JSON으로만 응답하세요:
{{"score": 1-5, "reasoning": "한 문장 이유", "hallucinated_claims": ["없으면 빈 배열"]}}"""

    return await _call_judge(prompt)


async def judge_answer_relevance(query: str, analysis: str) -> dict:
    """분석이 원래 쿼리/포스트에 얼마나 답하는지 평가."""
    prompt = f"""\
다음 [분석]이 [질문/포스트]에 얼마나 잘 답하는지 평가하세요.

[질문/포스트]
{query[:500]}

[분석]
{analysis[:1500]}

평가 기준:
1점 = 질문과 무관
2점 = 일부만 답함
3점 = 어느 정도 답함
4점 = 잘 답함
5점 = 완벽하게 답함

JSON으로만 응답하세요:
{{"score": 1-5, "reasoning": "한 문장 이유"}}"""

    return await _call_judge(prompt)


async def _call_judge(prompt: str) -> dict:
    """Judge 호출 공통 함수. JSON 파싱 실패 시 기본값 반환."""
    try:
        resp = await _client.messages.create(
            model=MODEL_SONNET,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(text[start:end])
    except Exception as e:
        log.warning(f"Judge 호출 오류: {e}", exc_info=True)
    return {"score": 0, "reasoning": "평가 실패", "hallucinated_claims": []}


def normalize_score(score: int | float) -> float:
    """1-5 점수를 0-1로 정규화."""
    return max(0.0, min(1.0, (score - 1) / 4))
