"""경량 키워드 추출 — LLM 없음, kiwipiepy (한국어) + regex (영문).

한국어: kiwipiepy 형태소 분석 → NNG(일반명사), NNP(고유명사), SL(외래어) 추출
영어: regex 분리 + 금융 도메인 불용어 제거
"""

import logging
import re

log = logging.getLogger(__name__)

_EN_STOPWORDS = {
    "the", "a", "an", "in", "on", "of", "to", "for", "is", "are", "was",
    "were", "be", "been", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "can", "with", "at", "by",
    "from", "into", "through", "during", "before", "after", "between", "out",
    "over", "under", "again", "then", "once", "here", "there", "when", "where",
    "why", "how", "all", "both", "each", "more", "most", "other", "some", "no",
    "not", "only", "same", "so", "than", "too", "very", "just", "but", "and",
    "or", "as", "if", "its", "it", "this", "that", "these", "those", "about",
    "up", "down", "says", "said", "new", "report", "reports", "amid",
    "according", "data", "year", "years", "month", "months", "week",
    "percent", "vs", "per", "market", "markets", "news", "update", "latest",
}

_KO_POS_KEEP = {"NNG", "NNP", "SL", "XR"}  # 일반명사, 고유명사, 외래어, 어근

try:
    from kiwipiepy import Kiwi
    _kiwi = Kiwi()
    _KIWI_AVAILABLE = True
except ImportError:
    _KIWI_AVAILABLE = False


def extract_keywords(text: str, language: str = "ko", max_kw: int = 20) -> list[str]:
    """헤드라인에서 키워드 추출. LLM 없이 형태소/regex 기반."""
    if not text:
        return []
    if language == "ko":
        if not _KIWI_AVAILABLE:
            log.warning("kiwipiepy 미설치 — 한국어 키워드 추출 불가")
            return []
        return _extract_ko(text, max_kw)
    return _extract_en(text, max_kw)


def _extract_ko(text: str, max_kw: int) -> list[str]:
    tokens = _kiwi.tokenize(text)
    seen: set[str] = set()
    result: list[str] = []
    for t in tokens:
        if t.tag in _KO_POS_KEEP and len(t.form) > 1:
            kw = t.form.lower()
            if kw not in seen:
                seen.add(kw)
                result.append(kw)
                if len(result) >= max_kw:
                    break
    return result


def _extract_en(text: str, max_kw: int) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z\-']{1,}", text)
    seen: set[str] = set()
    result: list[str] = []
    for t in tokens:
        kw = t.lower()
        if kw not in _EN_STOPWORDS and len(kw) >= 2:
            if kw not in seen:
                seen.add(kw)
                result.append(kw)
                if len(result) >= max_kw:
                    break
    return result
