"""
Citation Tracker: 분석 텍스트에서 [ref: ins_ID] 태그를 파싱하고
출처 없는 주장([ref: none])을 식별한다.

에이전트 system prompt에 다음 지시가 포함되어야 작동:
  각 주장 뒤에 [ref: ins_ID] 형식으로 참조를 표기하세요.
  참조 없는 주장은 [ref: none]으로 표기하세요.
"""

import re
from dataclasses import dataclass


# [ref: ins_123, ins_456] 또는 [ref: none] 패턴
_REF_PATTERN = re.compile(r'\[ref:\s*([^\]]+)\]')
# 문장 단위 분리 (마침표/느낌표/물음표 뒤 공백 또는 줄바꿈)
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?。])\s+|\n')


@dataclass
class CitedClaim:
    text: str
    ref_ids: list[int]      # 정수 ID 목록 (비어있으면 unsupported)
    is_none: bool           # [ref: none] 명시 여부
    raw_ref: str            # 원본 ref 문자열


def parse_citations(analysis_text: str) -> list[CitedClaim]:
    """
    분석 텍스트를 파싱해 CitedClaim 리스트 반환.

    줄 단위로 파싱. [ref: ins_ID] 태그가 있는 줄은 태그를 분리하고,
    없는 줄은 UNSUPPORTED로 분류한다.
    """
    claims: list[CitedClaim] = []

    for line in analysis_text.splitlines():
        line = line.strip()
        # 빈 줄, 마크다운 섹션 헤더, 짧은 단편 제외
        if not line or line.startswith("*") or line.startswith("#") or len(line) < 10:
            continue
        # 이모지+섹션 제목 줄 (ref 없고 특수문자로만 시작하는 짧은 줄) 제외
        if _REF_PATTERN.search(line) is None and len(line) < 15 and not any(c.isalpha() for c in line):
            continue
        # 고정 면책 문구 제외 (⚠️ AI 안내 문구)
        if line.startswith("⚠️") or line.startswith("_AI가"):
            continue
        # 에이전트 메타 코멘트 제외 (모순 검증 결과 등 툴 실행 요약)
        if line.startswith("- 모순 검증") or line.startswith("모순 검증"):
            continue

        match = _REF_PATTERN.search(line)
        if not match:
            # ref 없는 줄 중 순수 섹션 헤더(볼드 텍스트만 남는 줄)는 제외
            # 예: "📌 **2021년 이후 경고의 연장선**", "📊 **영향받는 자산·섹터**"
            content_no_bold = re.sub(r'\*\*[^*]+\*\*', '', line).strip()
            content_no_bold = re.sub(r'[^\w가-힣a-zA-Z]', '', content_no_bold)
            if not content_no_bold:
                continue  # 볼드 제거 후 실질 내용 없음 → 헤더, 건너뜀
            claims.append(CitedClaim(
                text=line,
                ref_ids=[],
                is_none=False,
                raw_ref="",
            ))
            continue

        raw_ref = match.group(1).strip()
        clean_text = _REF_PATTERN.sub("", line).strip()
        if not clean_text:
            continue

        if raw_ref.lower() == "none":
            claims.append(CitedClaim(
                text=clean_text,
                ref_ids=[],
                is_none=True,
                raw_ref=raw_ref,
            ))
        else:
            ids = []
            for part in raw_ref.split(","):
                part = part.strip()
                digits = re.sub(r'\D', '', part)
                if digits:
                    ids.append(int(digits))
            claims.append(CitedClaim(
                text=clean_text,
                ref_ids=ids,
                is_none=False,
                raw_ref=raw_ref,
            ))

    return claims


def unsupported_ratio(claims: list[CitedClaim]) -> float:
    """출처 없는 주장 비율 (ref_ids=[] and not is_none → 태그 미표기)."""
    if not claims:
        return 0.0
    unsupported = sum(1 for c in claims if not c.ref_ids and not c.is_none)
    return unsupported / len(claims)


def ungrounded_ids(claims: list[CitedClaim]) -> list[int]:
    """ref_ids가 있는 claim이 참조하는 모든 insight ID."""
    ids: list[int] = []
    for c in claims:
        ids.extend(c.ref_ids)
    return list(set(ids))
