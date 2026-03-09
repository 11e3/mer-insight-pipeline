"""
Analysis Generator: 컨텍스트 → Claude API → 분석 텍스트 생성
"""

import json
from datetime import date

import anthropic

from config.settings import ANTHROPIC_API_KEY, MODEL_SONNET
from config.prompts import (
    ANALYSIS_SUMMARY_PROMPT,
    ANALYSIS_FULL_PROMPT,
    ANALYSIS_ALERT_PROMPT,
)
from src.pipeline.event_types import AnalysisConfig, EventType

client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_TEMPLATES = {
    "summary":       ANALYSIS_SUMMARY_PROMPT,
    "full_analysis": ANALYSIS_FULL_PROMPT,
    "alert":         ANALYSIS_ALERT_PROMPT,
}


class AnalysisGenerator:

    async def generate(self, context: dict, config: AnalysisConfig) -> str:
        system_prompt = SYSTEM_TEMPLATES[config.output_format].format(
            rules_text=_format_rules(context["rules"]),
            similar_analyses_text=_format_similar(context["similar_analyses"]),
            macro_context=_format_macro(context["macro_context"]),
        )
        user_message = _format_event(context["event"], config.event_type)

        resp = await client.messages.create(
            model=MODEL_SONNET,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return resp.content[0].text


# ─── 포맷 헬퍼 ───────────────────────────────────────────────────────────────

def _format_rules(rules: list[dict]) -> str:
    if not rules:
        return "(관련 프레임워크 없음)"
    parts = []
    for rule in rules:
        sd = rule.get("structured_data") or {}
        if isinstance(sd, str):
            try:
                sd = json.loads(sd)
            except Exception:
                sd = {}
        statement = (
            sd.get("rule_statement")
            or sd.get("mer_interpretation")
            or rule.get("content", "")
        )
        domain = sd.get("applicable_domain") or sd.get("sector") or "N/A"
        caveat = sd.get("exceptions_or_caveats") or "없음"
        parts.append(
            f"- {statement}\n"
            f"  적용영역: {domain}\n"
            f"  주의사항: {caveat}"
        )
    return "\n\n".join(parts)


def _format_similar(analyses: list[dict]) -> str:
    if not analyses:
        return "(유사 과거 분석 없음)"
    parts = []
    for a in analyses:
        dt = a.get("event_date", "")
        title = a.get("title", "")
        text = (a.get("analysis_text") or "")[:300]
        parts.append(f"[{dt}] {title}\n{text}…")
    return "\n\n".join(parts)


def _format_macro(macro: dict) -> str:
    if not macro:
        return "(매크로 데이터 없음)"
    dt = macro.get("date", "")
    return (
        f"기준일: {dt} | "
        f"KOSPI: {macro.get('kospi', 'N/A')} | "
        f"USD/KRW: {macro.get('usd_krw', 'N/A')} | "
        f"미국10년물: {macro.get('us_10y', 'N/A')}% | "
        f"WTI: {macro.get('wti', 'N/A')} | "
        f"BTC: ${macro.get('btc_usd', 'N/A')} | "
        f"VIX: {macro.get('vix', 'N/A')}"
    )


def _format_event(event: dict, event_type: EventType) -> str:
    title   = event.get("title", "")
    content = event.get("content", "")[:3000]
    source  = event.get("source", "")
    return (
        f"이벤트 유형: {event_type.value}\n"
        f"제목: {title}\n"
        f"출처: {source}\n\n"
        f"내용:\n{content}\n\n"
        f"위 이벤트를 제공된 메르 분석 프레임워크를 적용하여 분석하세요."
    )
