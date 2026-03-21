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
    # ─── 한국어 ──────────────────────────────────────────────────────────
    # 매크로
    FeedSpec("gn_ko_금리",     _GN_KR + "금리+한국은행",             "ko", "금리_매크로"),
    FeedSpec("gn_ko_환율",     _GN_KR + "환율+달러+원화",            "ko", "환율"),
    FeedSpec("gn_ko_엔화",     _GN_KR + "엔화+엔달러+일본은행",      "ko", "환율"),
    FeedSpec("gn_ko_물가",     _GN_KR + "소비자물가+CPI+인플레이션", "ko", "금리_매크로"),
    # 자산
    FeedSpec("gn_ko_부동산",   _GN_KR + "부동산+아파트+청약",        "ko", "부동산"),
    FeedSpec("gn_ko_코스피",   _GN_KR + "코스피+코스닥+주식",        "ko", "주식_금융"),
    FeedSpec("gn_ko_국채",     _GN_KR + "국채+채권+금리",            "ko", "금리_매크로"),
    # 산업
    FeedSpec("gn_ko_반도체",   _GN_KR + "반도체+삼성전자+SK하이닉스", "ko", "AI_반도체"),
    FeedSpec("gn_ko_에너지",   _GN_KR + "원유+천연가스+에너지",      "ko", "에너지"),
    FeedSpec("gn_ko_조선",     _GN_KR + "조선+LNG선+HD현대",         "ko", "조선_해운"),
    FeedSpec("gn_ko_방산",     _GN_KR + "방산+한화에어로스페이스+천궁", "ko", "방산"),
    FeedSpec("gn_ko_원자재",   _GN_KR + "금값+구리+원자재",          "ko", "원자재"),
    FeedSpec("gn_ko_은",       _GN_KR + "은값+은가격+은시세",        "ko", "원자재"),
    FeedSpec("gn_ko_식품",     _GN_KR + "식품+물가+농산물",          "ko", "식품_농업"),
    FeedSpec("gn_ko_금융",     _GN_KR + "저축은행+새마을금고+부실",  "ko", "주식_금융"),
    FeedSpec("gn_ko_파운드리", _GN_KR + "파운드리+TSMC+삼성전자+3나노", "ko", "AI_반도체"),
    # 기업
    FeedSpec("gn_ko_테슬라",   _GN_KR + "테슬라+일론머스크",         "ko", "주식_금융"),
    FeedSpec("gn_ko_중국경제", _GN_KR + "중국+GDP+경제성장",         "ko", "금리_매크로"),
    # ─── 영어 ──────────────────────────────────────────────────────────
    # 매크로
    FeedSpec("gn_en_fed",       _GN_EN + "Federal+Reserve+interest+rate",  "en", "금리_매크로"),
    FeedSpec("gn_en_treasury",  _GN_EN + "US+Treasury+10+year+yield",      "en", "금리_매크로"),
    FeedSpec("gn_en_inflation", _GN_EN + "inflation+CPI+US+economy",       "en", "금리_매크로"),
    FeedSpec("gn_en_boj",       _GN_EN + "Bank+of+Japan+interest+rate+yen", "en", "환율"),
    # 자산
    FeedSpec("gn_en_oil",       _GN_EN + "crude+oil+WTI+Brent+price",     "en", "에너지"),
    FeedSpec("gn_en_gold",      _GN_EN + "gold+price+XAU",                "en", "원자재"),
    FeedSpec("gn_en_silver",    _GN_EN + "silver+price+XAG",              "en", "원자재"),
    FeedSpec("gn_en_copper",    _GN_EN + "copper+price+LME",              "en", "원자재"),
    FeedSpec("gn_en_sp500",     _GN_EN + "S%26P+500+stock+market",        "en", "주식_금융"),
    # 산업
    FeedSpec("gn_en_korea",     _GN_EN + "South+Korea+economy+KOSPI",     "en", "주식_금융"),
    FeedSpec("gn_en_semicon",   _GN_EN + "semiconductor+TSMC+Samsung+HBM", "en", "AI_반도체"),
    FeedSpec("gn_en_tesla",     _GN_EN + "Tesla+TSLA+Elon+Musk",          "en", "주식_금융"),
    FeedSpec("gn_en_china",     _GN_EN + "China+GDP+economy+growth",       "en", "금리_매크로"),
    FeedSpec("gn_en_defense",   _GN_EN + "defense+Korea+Hanwha+KAI",       "en", "방산"),
    # 크립토
    FeedSpec("gn_en_btc",       _GN_EN + "Bitcoin+BTC+price",              "en", "crypto"),
    FeedSpec("gn_en_eth",       _GN_EN + "Ethereum+ETH+price",             "en", "crypto"),
    FeedSpec("gn_en_defi",      _GN_EN + "DeFi+stablecoin+USDT+USDC",     "en", "crypto"),
    FeedSpec("gn_en_crypto",    _GN_EN + "crypto+market+altcoin",          "en", "crypto"),
    FeedSpec("gn_en_onchain",   _GN_EN + "on-chain+whale+exchange+flow",   "en", "crypto"),
    FeedSpec("gn_ko_비트코인",  _GN_KR + "비트코인+BTC+암호화폐",         "ko", "crypto"),
]
