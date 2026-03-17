"""Export PENDING predictions (no expected_date) for manual verification round 3."""
import asyncio
import os

import asyncpg
from dotenv import load_dotenv

load_dotenv()

KW_MAP = {
    "호르무즈_이란_중동": ["호르무즈", "이란", "중동", "카타르", "사우디", "이스라엘"],
    "에너지_원유_LNG": ["원유", "WTI", "LNG", "천연가스", "에너지", "우라늄", "유가"],
    "관세_무역": ["관세", "무역", "수입", "수출", "상호관세", "덤핑"],
    "트럼프_미국정치": ["트럼프", "파월", "백악관", "공화당", "민주당"],
    "금리_환율_매크로": ["금리", "환율", "달러", "인플레", "CPI", "연준", "FOMC", "기준금리"],
    "부동산_전세": ["부동산", "전세", "아파트", "주택", "매매", "분양", "집값"],
    "AI_반도체_테크": ["AI", "반도체", "엔비디아", "딥시크", "데이터센터", "하이닉스"],
    "주식_금융": ["주식", "KOSPI", "코스피", "주가", "사모", "펀드", "IPO"],
    "방산_안보": ["방산", "천궁", "무인", "안두릴", "방위", "미사일"],
    "원자재_금속": ["금값", "구리", "원자재", "다이아몬드", "헬륨"],
    "조선_해운": ["조선", "해운", "HD현대", "선박"],
}

HEADER = """# {topic} ({count}건) - Round 3 재검토

이전 라운드에서 "근거 부족", "확인 불가" 등으로 PENDING 처리된 예측입니다.
웹 검색으로 확인 후 CORRECT/INCORRECT로 판정해주세요.
정말로 아직 결과를 알 수 없는 미래 예측이면 PENDING + expected_date를 넣어주세요.

출력: [{{"id": N, "verdict": "CORRECT|INCORRECT|PENDING", "reason": "한줄", "expected_date": "YYYY-MM-DD (PENDING일 때만)"}}]

"""


def classify(text):
    for topic, keywords in KW_MAP.items():
        if any(kw in text for kw in keywords):
            return topic
    return "기타"


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    rows = await conn.fetch("""
        SELECT id, prediction_text, predicted_direction,
               target_asset, prediction_date
        FROM mer_predictions
        WHERE is_correct IS NULL
          AND (expected_date IS NULL OR expected_date <= CURRENT_DATE)
        ORDER BY prediction_date, id
    """)

    print(f"검증 대상 PENDING: {len(rows)}건")

    topics: dict[str, list] = {}
    for r in rows:
        p = dict(r)
        text = (p["prediction_text"] or "") + (p["target_asset"] or "")
        topic = classify(text)
        topics.setdefault(topic, []).append(p)

    out_dir = "data/manual_verify/round3"
    os.makedirs(out_dir, exist_ok=True)

    for topic, preds in sorted(topics.items(), key=lambda x: -len(x[1])):
        fname = os.path.join(out_dir, f"{topic}.txt")
        with open(fname, "w", encoding="utf-8") as f:
            f.write(HEADER.format(topic=topic, count=len(preds)))
            for p in preds:
                pid = p["id"]
                asset = p["target_asset"] or "기타"
                text = p["prediction_text"]
                direction = p["predicted_direction"]
                d = p["prediction_date"]
                date_str = d.isoformat() if d else "없음"
                f.write(
                    f"{pid}. [{asset}] {text} "
                    f"(방향: {direction}, 예측일: {date_str})\n"
                )
        print(f"  {topic}: {len(preds)}건")

    print(f"\n총 {sum(len(v) for v in topics.values())}건 -> {out_dir}/")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
