"""뉴스 RSS 피드 정의.

Google News RSS URL 패턴:
    https://news.google.com/rss/search?q={query}&hl={lang}&gl={country}&ceid={country}:{lang}
"""

from dataclasses import dataclass

_GN_KR = "https://news.google.com/rss/search?hl=ko&gl=KR&ceid=KR:ko&q="
_GN_EN = "https://news.google.com/rss/search?hl=en&gl=US&ceid=US:en&q="


@dataclass
class FeedSpec:
    feed_id: str
    url: str
    language: str  # 'ko' | 'en'
    topic: str


FEEDS: list[FeedSpec] = [
    # 한국어 — 금융 핵심
    FeedSpec("gn_ko_금리",     _GN_KR + "금리+한국은행",         "ko", "금리_매크로"),
    FeedSpec("gn_ko_환율",     _GN_KR + "환율+달러+원화",        "ko", "환율"),
    FeedSpec("gn_ko_부동산",   _GN_KR + "부동산+아파트+청약",    "ko", "부동산"),
    FeedSpec("gn_ko_코스피",   _GN_KR + "코스피+코스닥+주식",    "ko", "주식_금융"),
    FeedSpec("gn_ko_반도체",   _GN_KR + "반도체+삼성전자+SK하이닉스", "ko", "AI_반도체"),
    FeedSpec("gn_ko_에너지",   _GN_KR + "원유+천연가스+에너지",  "ko", "에너지"),
    FeedSpec("gn_ko_조선",     _GN_KR + "조선+LNG선+HD현대",     "ko", "조선_해운"),
    FeedSpec("gn_ko_방산",     _GN_KR + "방산+한화에어로스페이스+천궁", "ko", "방산"),
    FeedSpec("gn_ko_원자재",   _GN_KR + "금값+구리+원자재",      "ko", "원자재"),
    FeedSpec("gn_ko_식품",     _GN_KR + "식품+물가+농산물",      "ko", "식품_농업"),
    # 영어 — 글로벌 매크로
    FeedSpec("gn_en_fed",      _GN_EN + "Federal+Reserve+interest+rate", "en", "금리_매크로"),
    FeedSpec("gn_en_oil",      _GN_EN + "crude+oil+WTI+Brent+price",    "en", "에너지"),
    FeedSpec("gn_en_gold",     _GN_EN + "gold+price+XAU",               "en", "원자재"),
    FeedSpec("gn_en_inflation", _GN_EN + "inflation+CPI+US+economy",    "en", "금리_매크로"),
    FeedSpec("gn_en_korea",    _GN_EN + "South+Korea+economy+KOSPI",    "en", "주식_금융"),
]
