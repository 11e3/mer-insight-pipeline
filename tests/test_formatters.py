"""
formatters.py 단위 테스트 — DB/Claude API 의존성 없음.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.delivery.formatters import _esc, _to_md1, _fmt_mer_post, _fmt_macro_alert, format_short_post


# ── _esc ──────────────────────────────────────────────────────────────────────

def test_esc_underscores():
    assert _esc("삼성_전자") == "삼성\\_전자"

def test_esc_asterisk():
    assert _esc("**bold**") == "\\*\\*bold\\*\\*"

def test_esc_backtick():
    assert _esc("`code`") == "\\`code\\`"

def test_esc_no_special():
    assert _esc("일반 텍스트") == "일반 텍스트"


# ── _to_md1 ───────────────────────────────────────────────────────────────────

def test_to_md1_header_conversion():
    result = _to_md1("### 제목")
    assert result == "*제목*"

def test_to_md1_bold_conversion():
    result = _to_md1("**강조** 텍스트")
    assert result == "*강조* 텍스트"

def test_to_md1_removes_hr():
    result = _to_md1("위\n---\n아래")
    assert "---" not in result

def test_to_md1_removes_blockquote():
    result = _to_md1("> 인용")
    assert result == "인용"

def test_to_md1_collapses_blank_lines():
    result = _to_md1("위\n\n\n\n아래")
    assert "\n\n\n" not in result


# ── _fmt_mer_post ─────────────────────────────────────────────────────────────

def test_fmt_mer_post_basic():
    event = {"title": "테스트 글", "source": "https://example.com"}
    result = _fmt_mer_post(event, "분석 내용")
    assert "테스트 글" in result
    assert "https://example.com" in result
    assert "분석 내용" in result

def test_fmt_mer_post_with_enriched_signal():
    event = {"title": "글", "source": "https://example.com"}
    enriched = {"action_signal": "매수 검토, 관망", "past_position": None, "related_posts": []}
    result = _fmt_mer_post(event, "분석", enriched)
    assert "행동 시그널" in result
    assert "매수 검토" in result

def test_fmt_mer_post_with_past_position():
    event = {"title": "글", "source": "https://example.com"}
    enriched = {"action_signal": None, "past_position": "달러 강세 지속 전망", "related_posts": []}
    result = _fmt_mer_post(event, "분석", enriched)
    assert "메르의 과거 시각" in result
    assert "달러 강세" in result

def test_fmt_mer_post_with_related_posts():
    event = {"title": "글", "source": "https://example.com"}
    related = [{"date": "2024-01-01", "title": "과거 글 제목", "url": "https://past.com"}]
    enriched = {"action_signal": None, "past_position": None, "related_posts": related}
    result = _fmt_mer_post(event, "분석", enriched)
    assert "관련 과거 글" in result
    assert "과거 글 제목" in result

def test_fmt_mer_post_no_enriched():
    event = {"title": "글", "source": "https://example.com"}
    result = _fmt_mer_post(event, "분석", None)
    assert "행동 시그널" not in result
    assert "관련 과거 글" not in result


# ── _fmt_macro_alert ──────────────────────────────────────────────────────────

def test_fmt_macro_alert():
    event = {"title": "매크로 급변 (2024-01-01)"}
    result = _fmt_macro_alert(event, "KOSPI -2.5%")
    assert "매크로 알림" in result
    assert "매크로 급변" in result
    assert "KOSPI -2.5%" in result


# ── format_short_post ─────────────────────────────────────────────────────────

def test_format_short_post():
    post = {"title": "비경제 글", "url": "https://example.com"}
    result = format_short_post(post, "일상", "오늘의 요약")
    assert "비경제 글" in result
    assert "일상" in result
    assert "오늘의 요약" in result
    assert "경제/시장 관련 내용 없음" in result
