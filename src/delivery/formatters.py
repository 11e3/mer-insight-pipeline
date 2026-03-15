"""
마크다운 유틸리티 (Markdown v1 호환).
"""

import re


def _esc(text: str) -> str:
    """Markdown v1에서 문제가 되는 특수문자 이스케이프."""
    return text.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")


def _to_md1(text: str) -> str:
    """Claude 마크다운 출력 -> 텔레그램 Markdown v1 호환 변환."""
    # 마크다운 표 제거 -> 목록으로 전환
    text = re.sub(r"\|[-:| ]+\|\n?", "", text)
    text = re.sub(r"\|(.+)\|", lambda m: "\n".join(
        f"- {c.strip()}" for c in m.group(1).split("|") if c.strip()
    ), text)
    # ### 헤더 -> 볼드
    text = re.sub(r"^#{1,3} (.+)$", r"*\1*", text, flags=re.MULTILINE)
    # **bold** -> *bold*  (v1은 단일 *)
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    # --- 구분선 제거
    text = re.sub(r"^-{3,}$", "", text, flags=re.MULTILINE)
    # > 인용부호 제거
    text = re.sub(r"^> ?", "", text, flags=re.MULTILINE)
    # 연속 빈줄 정리
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
