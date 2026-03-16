"""Export unreviewed PENDING predictions for manual verification round 2."""
import asyncio
import glob
import json
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

HEADER = """# {topic} Part {part} ({count}건)

각 예측에 대해 JSON으로 판정해주세요:
[{{"id": N, "verdict": "CORRECT|INCORRECT|PENDING", "reason": "한줄"}}]

PENDING은 아직 실현 시점이 안 온 미래 예측에만 사용하세요.
과거 예측은 반드시 CORRECT 또는 INCORRECT로 판정하세요.

"""


def classify(text):
    for topic, keywords in KW_MAP.items():
        if any(kw in text for kw in keywords):
            return topic
    return "기타"


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    # Already-reviewed IDs (from round 1)
    verified_ids = set()
    for f in sorted(
        glob.glob("data/manual_verify/grouped/*결과*.json")
        + glob.glob("data/manual_verify/result_*.json")
    ):
        for item in json.load(open(f, encoding="utf-8")):
            verified_ids.add(item["id"])

    rows = await conn.fetch("""
        SELECT mp.id, mp.prediction_text, mp.predicted_direction,
               mp.target_asset, mp.prediction_date
        FROM mer_predictions mp
        WHERE mp.is_correct IS NULL
        ORDER BY mp.prediction_date, mp.id
    """)

    unreviewed = [dict(r) for r in rows if r["id"] not in verified_ids]

    # Classify by topic
    topics: dict[str, list] = {}
    for p in unreviewed:
        text = (p["prediction_text"] or "") + (p["target_asset"] or "")
        topic = classify(text)
        topics.setdefault(topic, []).append(p)

    out_dir = "data/manual_verify/round2"
    os.makedirs(out_dir, exist_ok=True)

    for topic, preds in sorted(topics.items(), key=lambda x: -len(x[1])):
        for i in range(0, len(preds), 50):
            part = i // 50 + 1
            chunk = preds[i : i + 50]
            fname = os.path.join(out_dir, f"{topic}_part{part}.txt")
            with open(fname, "w", encoding="utf-8") as f:
                f.write(HEADER.format(topic=topic, part=part, count=len(chunk)))
                for p in chunk:
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
        print(f"  {topic}: {len(preds)}건 ({(len(preds) - 1) // 50 + 1}파트)")

    print(f"\n총 {len(unreviewed)}건 -> {out_dir}/")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
