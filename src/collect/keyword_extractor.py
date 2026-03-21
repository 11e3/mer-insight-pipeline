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

# 한국어 금융/경제 불용어 — 너무 흔해서 매칭 precision을 떨어뜨리는 단어
_KO_STOPWORDS = {
    # 일반 경제 용어 (거의 모든 기사에 등장)
    "시장", "경제", "투자", "성장", "상승", "하락", "전망", "리스크", "영향",
    "기업", "산업", "가격", "수준", "정도", "가능", "가능성", "예상", "전문",
    "분석", "의견", "판단", "확인", "발표", "관련", "문제", "상황", "방향",
    "변화", "효과", "결과", "이유", "원인", "대비", "우려", "부담", "위험",
    "기대", "목표", "수요", "공급", "규모", "비율", "증가", "감소", "유지",
    "확대", "축소", "강화", "완화", "기록", "추세", "흐름", "분야", "부문",
    # 지리/정치 일반어
    "미국", "중국", "한국", "일본", "유럽", "글로벌", "세계", "국내", "해외",
    "정부", "당국", "정책", "제도", "규제",
    # 금융 일반어
    "금리", "환율", "물가", "인플레이션", "통화", "자산", "주식", "채권",
    "펀드", "대출", "은행", "금융", "거래", "매수", "매도",
    # 시간 표현
    "올해", "내년", "상반기", "하반기", "분기", "연말", "연초", "최근",
}

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
            if kw in _KO_STOPWORDS:
                continue
            if kw not in seen:
                seen.add(kw)
                result.append(kw)
                if len(result) >= max_kw:
                    break
    return result


# 복합 명사구 — 하나의 키워드로 매칭해야 precision이 올라감
_EN_COMPOUND_TERMS = {
    # 중앙은행/통화정책
    "federal reserve", "interest rate", "rate cut", "rate hike", "rate cuts", "rate hikes",
    "quantitative easing", "quantitative tightening", "balance sheet",
    "monetary policy", "monetary easing", "monetary tightening",
    "bank of japan", "bank of england", "european central bank",
    "yield curve", "treasury yield", "treasury bond",
    "basis points", "fed funds",
    # 크립토
    "bitcoin etf", "ethereum etf", "spot etf",
    "on-chain", "hash rate", "market cap",
    "defi protocol", "stablecoin issuer",
    "crypto market", "crypto exchange",
    # 매크로
    "trade war", "tariff war", "fiscal deficit", "fiscal policy",
    "credit market", "credit spread",
    "dollar index", "real yield", "risk premium",
    "gdp growth", "cpi inflation",
    "oil price", "gold price", "silver price",
    # 자산
    "s&p 500", "sp500", "nasdaq", "dow jones",
    "real estate", "housing market",
}


def _extract_en(text: str, max_kw: int) -> list[str]:
    text_lower = text.lower()

    seen: set[str] = set()
    result: list[str] = []

    # 1. 복합 명사구 매칭 (최우선)
    for term in _EN_COMPOUND_TERMS:
        if term in text_lower and term not in seen:
            seen.add(term)
            result.append(term)

    # 2. 대문자 약어/티커 보존 (BTC, ETH, DeFi, S&P 등)
    tickers = re.findall(r"\b[A-Z][A-Z0-9&]{1,9}\b", text)
    for t in tickers:
        kw = t.upper()
        if kw not in seen and len(kw) >= 2:
            seen.add(kw)
            result.append(kw)

    # 3. 일반 단어 추출 (소문자, 불용어 제거)
    tokens = re.findall(r"[A-Za-z][A-Za-z\-']{1,}", text)
    for t in tokens:
        kw = t.lower()
        if kw not in _EN_STOPWORDS and len(kw) >= 3:
            if kw not in seen and kw.upper() not in seen:
                # 이미 복합어에 포함된 단어는 스킵 (중복 방지)
                if not any(kw in compound for compound in seen if " " in compound):
                    seen.add(kw)
                    result.append(kw)

    # 4. 숫자+단위 (100K, $50K, 3.5% 등)
    numbers = re.findall(r"\$?[\d,]+\.?\d*[KkMmBb%]?\b", text)
    for n in numbers:
        if n not in seen and len(n) >= 2:
            seen.add(n)
            result.append(n)

    return result[:max_kw]
