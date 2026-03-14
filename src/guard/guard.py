"""
Hallucination Guard: 에이전트 분석 출력의 환각 비율을 측정하고 결과를 반환.

검증 단계:
1. citation_tracker로 [ref:] 태그 파싱
2. 참조 ID의 실제 인사이트 내용을 DB에서 조회
3. 주장과 원본 내용 간 일치 여부를 키워드 레벨에서 1차 검증
4. 환각 비율이 임계치 초과 시 FAIL 판정 → self_correct.py가 재생성 트리거
"""

import logging
from dataclasses import dataclass, field

import asyncpg

from src.guard.citation_tracker import (
    CitedClaim,
    parse_citations,
    unsupported_ratio,
    ungrounded_ids,
)

log = logging.getLogger(__name__)

_UNGROUNDED_THRESHOLD = 0.20   # 20% 초과 시 재생성


@dataclass
class ClaimVerification:
    claim: CitedClaim
    verdict: str            # GROUNDED | UNGROUNDED | UNSUPPORTED | NONE_DECLARED
    explanation: str = ""


@dataclass
class GuardResult:
    is_pass: bool
    overall_verdict: str    # PASS | FAIL
    ungrounded_ratio: float
    unsupported_ratio: float
    verifications: list[ClaimVerification] = field(default_factory=list)
    problematic_claims: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return (
            f"[Guard {self.overall_verdict}] "
            f"ungrounded={self.ungrounded_ratio:.0%}, "
            f"unsupported={self.unsupported_ratio:.0%}"
        )


class HallucinationGuard:
    """분석 텍스트의 환각 비율 검증."""

    def __init__(self, conn: asyncpg.Connection):
        self._conn = conn

    async def verify(self, analysis_text: str) -> GuardResult:
        """
        분석 텍스트를 검증하고 GuardResult 반환.

        Args:
            analysis_text: 에이전트가 생성한 분석 (citation 태그 포함)
        """
        claims = parse_citations(analysis_text)
        if not claims:
            return GuardResult(
                is_pass=True,
                overall_verdict="PASS",
                ungrounded_ratio=0.0,
                unsupported_ratio=0.0,
            )

        # 참조 ID → 원본 내용 일괄 조회
        all_ref_ids = ungrounded_ids(claims)
        insight_map: dict[int, str] = {}
        if all_ref_ids:
            rows = await self._conn.fetch(
                "SELECT id, content FROM mer_insights WHERE id = ANY($1::int[])",
                all_ref_ids,
            )
            insight_map = {r["id"]: r["content"] for r in rows}

        verifications: list[ClaimVerification] = []
        ungrounded_count = 0

        for claim in claims:
            if claim.is_none:
                verifications.append(ClaimVerification(
                    claim=claim,
                    verdict="NONE_DECLARED",
                    explanation="출처 없음 명시",
                ))
                ungrounded_count += 1
                continue

            if not claim.ref_ids:
                # 태그 자체 없음 → UNSUPPORTED
                verifications.append(ClaimVerification(
                    claim=claim,
                    verdict="UNSUPPORTED",
                    explanation="citation 태그 없음",
                ))
                continue

            # 참조 ID의 원본 내용 확인
            missing_ids = [i for i in claim.ref_ids if i not in insight_map]
            if missing_ids:
                verifications.append(ClaimVerification(
                    claim=claim,
                    verdict="UNGROUNDED",
                    explanation=f"존재하지 않는 인사이트 ID: {missing_ids}",
                ))
                ungrounded_count += 1
                continue

            # 키워드 레벨 일치 검증
            grounded = _keyword_check(
                claim.text,
                [insight_map[i] for i in claim.ref_ids if i in insight_map],
            )
            if grounded:
                verifications.append(ClaimVerification(
                    claim=claim,
                    verdict="GROUNDED",
                ))
            else:
                verifications.append(ClaimVerification(
                    claim=claim,
                    verdict="UNGROUNDED",
                    explanation="원본 인사이트와 키워드 불일치",
                ))
                ungrounded_count += 1

        u_ratio = unsupported_ratio(claims)
        g_ratio = ungrounded_count / len(claims) if claims else 0.0
        combined = max(u_ratio, g_ratio)
        is_pass = combined <= _UNGROUNDED_THRESHOLD

        problematic = [
            v.claim.text[:100]
            for v in verifications
            if v.verdict in ("UNGROUNDED", "UNSUPPORTED")
        ]

        result = GuardResult(
            is_pass=is_pass,
            overall_verdict="PASS" if is_pass else "FAIL",
            ungrounded_ratio=g_ratio,
            unsupported_ratio=u_ratio,
            verifications=verifications,
            problematic_claims=problematic,
        )
        log.info(result.summary)
        return result


def _keyword_check(claim_text: str, source_texts: list[str]) -> bool:
    """
    claim의 핵심 키워드가 source_texts 중 하나 이상에 존재하는지 확인.
    간단한 substring 매칭 (LLM 호출 없이 빠르게 처리).
    """
    # 2글자 이상 한국어/영문 토큰 추출
    import re
    tokens = re.findall(r'[가-힣a-zA-Z]{2,}', claim_text)
    if not tokens:
        return True  # 토큰 없으면 통과

    # 핵심 토큰 상위 3개만 검사 (길이 기준)
    key_tokens = sorted(tokens, key=len, reverse=True)[:3]
    combined_source = " ".join(source_texts)

    hits = sum(1 for t in key_tokens if t in combined_source)
    return hits >= max(1, len(key_tokens) // 2)
