"""
formatters.py 단위 테스트 — DB/Claude API 의존성 없음.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.delivery.formatters import _esc, _to_md1


# -- _esc --

def test_esc_underscores():
    assert _esc("삼성_전자") == "삼성\\_전자"

def test_esc_asterisk():
    assert _esc("**bold**") == "\\*\\*bold\\*\\*"

def test_esc_backtick():
    assert _esc("`code`") == "\\`code\\`"

def test_esc_no_special():
    assert _esc("일반 텍스트") == "일반 텍스트"


# -- _to_md1 --

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
