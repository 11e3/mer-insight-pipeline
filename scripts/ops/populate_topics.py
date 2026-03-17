"""Populate topic column in mer_predictions based on keyword matching."""
import asyncio
import os

import asyncpg
from dotenv import load_dotenv

load_dotenv()

TOPIC_KEYWORDS = {
    "에너지/원유": ["원유", "WTI", "LNG", "천연가스", "에너지", "셰일", "원자력", "우라늄", "유가", "OPEC"],
    "금리/매크로": ["금리", "기준금리", "금통위", "FOMC", "금리인하", "금리인상", "인플레", "CPI", "연준", "기금금리"],
    "환율": ["환율", "원/달러", "원달러", "달러/원", "엔화", "위안", "달러 약세", "달러 강세"],
    "부동산": ["부동산", "아파트", "주택", "전세", "매매", "분양", "집값", "월세", "임대"],
    "주식/금융": ["주식", "KOSPI", "코스피", "코스닥", "주가", "사모", "펀드", "IPO", "증시", "배당"],
    "AI/반도체": ["AI", "반도체", "엔비디아", "딥시크", "데이터센터", "하이닉스", "삼성전자", "클라우드"],
    "관세/무역": ["관세", "무역", "수입", "수출", "상호관세", "덤핑", "WTO"],
    "트럼프/미국": ["트럼프", "파월", "백악관", "공화당", "민주당", "중간선거", "바이든"],
    "중동/지정학": ["호르무즈", "이란", "중동", "카타르", "사우디", "이스라엘", "하마스", "우크라이나", "러시아"],
    "조선/해운": ["조선", "해운", "HD현대", "선박", "한화오션", "삼성중공업"],
    "방산/안보": ["방산", "천궁", "무인", "안두릴", "방위", "미사일", "NATO"],
    "원자재/금속": ["금값", "은값", "구리", "원자재", "다이아몬드", "헬륨", "금속", "리튬", "희토류"],
    "식품/농업": ["농산물", "곡물", "식품", "수산물", "비료", "요소"],
    "의료/바이오": ["오가노이드", "의학", "의료", "바이오", "제약"],
    "북한": ["북한", "김정은"],
    "중국": ["중국", "시진핑", "대만", "위구르", "홍콩"],
}


def classify(text: str) -> str:
    if not text:
        return "기타"
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return topic
    return "기타"


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    rows = await conn.fetch("SELECT id, prediction_text FROM mer_predictions")
    updates = [(classify(r["prediction_text"] or ""), r["id"]) for r in rows]

    await conn.executemany(
        "UPDATE mer_predictions SET topic = $1 WHERE id = $2", updates
    )

    stats = await conn.fetch(
        "SELECT topic, COUNT(*) as cnt FROM mer_predictions GROUP BY topic ORDER BY cnt DESC"
    )
    for s in stats:
        print(f"  {s['topic']}: {s['cnt']}")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
