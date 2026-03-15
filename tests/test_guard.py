"""
Guard 모듈 테스트.

src/guard/citation_tracker.py 순수 파싱 함수 + HallucinationGuard.verify() (asyncpg mock).
"""
import pytest
from unittest.mock import AsyncMock

from src.guard.citation_tracker import (
    CitedClaim,
    parse_citations,
    unsupported_ratio,
    ungrounded_ids,
)
from src.guard.guard import HallucinationGuard


# ── citation_tracker 순수 함수 ────────────────────────────────────────────────

def test_parse_citations_extracts_ref_ids():
    text = (
        "미국 금리가 계속 상승하고 있다. [ref: ins_123]\n"
        "전망이 매우 불투명하다고 할 수 있다. [ref: ins_456]"
    )
    claims = parse_citations(text)
    assert len(claims) == 2
    assert claims[0].ref_ids == [123]
    assert claims[1].ref_ids == [456]


def test_parse_citations_none_declared():
    text = "이것은 분석가의 의견이므로 참조가 없다. [ref: none]"
    claims = parse_citations(text)
    assert len(claims) == 1
    assert claims[0].is_none is True
    assert claims[0].ref_ids == []


def test_parse_citations_multiple_ids_in_one_ref():
    text = "복수 출처를 가진 주장이다. [ref: ins_10, ins_20]"
    claims = parse_citations(text)
    assert len(claims) == 1
    assert set(claims[0].ref_ids) == {10, 20}


def test_parse_citations_no_tag_is_unsupported():
    text = "이 문장은 citation 태그가 전혀 없는 긴 문장입니다."
    claims = parse_citations(text)
    assert len(claims) == 1
    assert claims[0].ref_ids == []
    assert claims[0].is_none is False


def test_parse_citations_skips_short_lines():
    # len < 10 줄은 무시
    text = "짧음"
    claims = parse_citations(text)
    assert claims == []


def test_parse_citations_skips_markdown_headers():
    text = "# 섹션 제목\n## 소제목\n실질 내용이 있는 긴 문장이다. [ref: ins_99]"
    claims = parse_citations(text)
    assert len(claims) == 1
    assert claims[0].ref_ids == [99]


def test_unsupported_ratio_all_grounded():
    claims = [
        CitedClaim(text="주장A", ref_ids=[1], is_none=False, raw_ref="ins_1"),
        CitedClaim(text="주장B", ref_ids=[2], is_none=False, raw_ref="ins_2"),
    ]
    assert unsupported_ratio(claims) == 0.0


def test_unsupported_ratio_all_unsupported():
    claims = [
        CitedClaim(text="근거없음", ref_ids=[], is_none=False, raw_ref=""),
        CitedClaim(text="태그없음", ref_ids=[], is_none=False, raw_ref=""),
    ]
    assert unsupported_ratio(claims) == 1.0


def test_unsupported_ratio_none_declared_not_counted():
    # is_none=True 는 NONE_DECLARED → unsupported로 집계 안 함
    claims = [
        CitedClaim(text="명시", ref_ids=[], is_none=True, raw_ref="none"),
        CitedClaim(text="근거없음", ref_ids=[], is_none=False, raw_ref=""),
    ]
    assert unsupported_ratio(claims) == 0.5


def test_unsupported_ratio_empty_list():
    assert unsupported_ratio([]) == 0.0


def test_ungrounded_ids_collects_all():
    claims = [
        CitedClaim(text="A", ref_ids=[1, 2], is_none=False, raw_ref="ins_1,ins_2"),
        CitedClaim(text="B", ref_ids=[2, 3], is_none=False, raw_ref="ins_2,ins_3"),
    ]
    result = ungrounded_ids(claims)
    assert set(result) == {1, 2, 3}


# ── HallucinationGuard.verify() ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_guard_empty_text_passes():
    mock_conn = AsyncMock()
    guard = HallucinationGuard(mock_conn)
    result = await guard.verify("")
    assert result.is_pass is True
    assert result.overall_verdict == "PASS"


@pytest.mark.asyncio
async def test_guard_pass_when_all_grounded():
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [{"id": 123, "content": "금리 인상 내용"}]
    guard = HallucinationGuard(mock_conn)

    text = "미국 금리가 계속 상승하고 있다고 본다. [ref: ins_123]"
    result = await guard.verify(text)

    assert result.is_pass is True
    assert result.ungrounded_ratio == 0.0


@pytest.mark.asyncio
async def test_guard_fail_when_refs_not_in_db():
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []  # DB에 해당 ID 없음

    guard = HallucinationGuard(mock_conn)
    # 5개 문장 모두 존재하지 않는 ID 참조 → ungrounded_ratio = 1.0 > 0.20
    lines = "\n".join(
        f"근거 없이 만들어낸 주장 번호 {i}이다. [ref: ins_9999{i}]"
        for i in range(5)
    )
    result = await guard.verify(lines)

    assert result.is_pass is False
    assert result.overall_verdict == "FAIL"
    assert result.ungrounded_ratio > 0.0


@pytest.mark.asyncio
async def test_guard_none_declared_does_not_cause_fail():
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [{"id": 1, "content": "원본 인사이트 내용"}]
    guard = HallucinationGuard(mock_conn)

    text = (
        "이것은 원본에 근거한 주장이다. [ref: ins_1]\n"
        "이것은 분석가의 주관적 의견이다. [ref: none]"
    )
    result = await guard.verify(text)
    # NONE_DECLARED는 ungrounded로 집계 안 함 → is_pass 유지
    assert result.is_pass is True


@pytest.mark.asyncio
async def test_guard_result_has_verifications():
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [{"id": 42, "content": "인사이트 원본 텍스트"}]
    guard = HallucinationGuard(mock_conn)

    text = "실제 DB에 있는 인사이트를 참조한 주장이다. [ref: ins_42]"
    result = await guard.verify(text)

    assert len(result.verifications) >= 1
    assert result.verifications[0].verdict == "GROUNDED"
