"""keyword_extractor.py 단위 테스트."""

from src.collect.keyword_extractor import extract_keywords


def test_korean_basic():
    kw = extract_keywords("한국은행 기준금리 동결 결정", "ko")
    assert any("한국" in k or "금리" in k or "동결" in k for k in kw)


def test_korean_empty():
    assert extract_keywords("", "ko") == []


def test_korean_max_kw():
    kw = extract_keywords("삼성전자 반도체 HBM 엔비디아 AI 데이터센터 파운드리", "ko", max_kw=3)
    assert len(kw) <= 3


def test_english_basic():
    kw = extract_keywords("Federal Reserve cuts interest rate by 25 basis points", "en")
    assert "federal" in kw
    assert "reserve" in kw
    assert "interest" in kw
    assert "the" not in kw


def test_english_stopword_removal():
    kw = extract_keywords("The market is very volatile amid new data", "en")
    assert "the" not in kw
    assert "is" not in kw
    assert "very" not in kw
    assert "volatile" in kw


def test_english_empty():
    assert extract_keywords("", "en") == []


def test_english_max_kw():
    kw = extract_keywords("oil gold silver copper aluminum zinc nickel lead tin", "en", max_kw=3)
    assert len(kw) <= 3


def test_unknown_language_falls_back_to_english():
    kw = extract_keywords("test keyword extraction", "fr")
    assert len(kw) > 0
