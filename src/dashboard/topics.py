"""Topic classification: keyword-based topic mapping."""

TOPIC_KEYWORDS = {
    "Energy": ["원유", "WTI", "LNG", "천연가스", "에너지", "셰일", "원자력", "우라늄", "유가", "OPEC", "oil", "crude", "natural gas", "nuclear"],
    "Rates/Macro": ["금리", "기준금리", "금통위", "FOMC", "금리인하", "금리인상", "인플레", "CPI", "연준", "Fed", "inflation", "interest rate", "PCE"],
    "FX": ["환율", "원/달러", "USD/KRW", "원달러", "엔화", "위안", "달러", "yen", "JPY", "yuan", "dollar"],
    "Real Estate": ["부동산", "아파트", "주택", "전세", "매매", "분양", "집값", "월세", "임대"],
    "Equities": ["주식", "KOSPI", "코스피", "코스닥", "주가", "사모", "펀드", "IPO", "증시", "배당", "S&P", "Nasdaq", "stock"],
    "AI/Semiconductor": ["AI", "반도체", "엔비디아", "딥시크", "데이터센터", "하이닉스", "삼성전자", "HBM", "TSMC", "semiconductor", "Nvidia"],
    "Trade/Tariff": ["관세", "무역", "수입", "수출", "상호관세", "덤핑", "WTO", "tariff", "trade"],
    "US Politics": ["트럼프", "파월", "백악관", "공화당", "민주당", "바이든", "Trump", "Powell", "Biden"],
    "Geopolitics": ["호르무즈", "이란", "중동", "카타르", "사우디", "이스라엘", "하마스", "우크라이나", "러시아", "Iran", "Israel", "Russia", "Ukraine"],
    "Shipbuilding": ["조선", "해운", "HD현대", "선박", "한화오션", "삼성중공업", "shipbuilding"],
    "Defense": ["방산", "천궁", "무인", "안두릴", "방위", "미사일", "NATO", "defense", "missile", "Anduril"],
    "Commodities": ["금값", "은값", "구리", "원자재", "금속", "리튬", "희토류", "gold", "silver", "copper", "commodity"],
    "Food/Agri": ["농산물", "곡물", "식품", "수산물", "비료", "food", "grain", "agriculture"],
    "Healthcare": ["오가노이드", "의학", "의료", "바이오", "제약", "biotech", "pharma", "healthcare"],
    "Crypto": ["비트코인", "암호화폐", "가상자산", "Bitcoin", "BTC", "ETH", "Ethereum", "DeFi", "stablecoin", "crypto", "altcoin"],
    "North Korea": ["북한", "김정은", "핵", "North Korea", "Kim Jong"],
    "China": ["중국", "시진핑", "대만", "위구르", "홍콩", "China", "Xi Jinping", "Taiwan"],
}


def classify_topic(text: str) -> str:
    if not text:
        return "Other"
    text_lower = text.lower()
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(kw.lower() in text_lower for kw in keywords):
            return topic
    return "Other"
