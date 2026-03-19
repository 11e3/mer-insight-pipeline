"""
대표 예측만 주제별로 내보내기.
representatives.json 기반으로 클러스터 대표만 추출.
"""

import asyncio
import json
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv()

BATCH_SIZE = 150
OUT_DIR = Path("data/manual_verify/grouped")

SYSTEM = """\
너는 경제·금융 예측 검증 전문가다.
아래 [예측 목록]의 각 항목을 판단한다. 비슷한 예측은 같은 근거로 일괄 판정 가능.

판단 기준:
1. CORRECT   — 예측 조건이 충족됐고 결과가 예측과 일치. 구체적 근거 필요
2. INCORRECT — 예측 조건이 충족됐으나 결과가 반대. 반박 근거 필요
3. PENDING   — 조건 미충족 또는 정보 부족

규칙:
- 확실한 근거 없으면 반드시 PENDING
- 미래 예측은 결과 확인된 경우에만 판정
- 수치 지어내지 마라. 검색으로 확인된 사실만 사용
- reason은 구체적 한 줄 (수치 포함)

출력: JSON 배열만.
[{"id": 예측ID, "verdict": "CORRECT|INCORRECT|PENDING", "reason": "근거"}, ...]
"""

KW_MAP = {
    "호르무즈_이란_중동": ["호르무즈", "이란", "중동", "카타르", "사우디", "이스라엘", "하마스", "팔레스타인"],
    "에너지_원유_LNG": ["원유", "WTI", "LNG", "천연가스", "에너지", "셰일", "원자력", "우라늄", "유가", "경유"],
    "관세_무역": ["관세", "무역", "WTO", "수출", "232조", "301조", "338조", "상호관세"],
    "트럼프_미국정치": ["트럼프", "파월", "백악관", "공화당", "민주당", "중간선거"],
    "금리_환율_매크로": ["금리", "환율", "달러", "인플레", "CPI", "연준", "FOMC", "기준금리", "원/달러", "엔화"],
    "부동산_전세": ["부동산", "전세", "아파트", "주택", "매매", "분양", "집값", "월세"],
    "AI_반도체_테크": ["AI", "반도체", "엔비디아", "오픈AI", "딥시크", "데이터센터", "SaaS", "하이닉스", "GPU"],
    "주식_금융": ["주식", "KOSPI", "S&P", "코스피", "주가", "사모", "펀드", "IPO", "증시", "채권"],
    "방산_안보": ["방산", "천궁", "무인", "안두릴", "방위", "미사일", "잠수함"],
    "원자재_금속": ["금값", "은 ", "구리", "원자재", "헬륨", "리튬", "희토류"],
    "조선_해운": ["조선", "해운", "HD현대", "선박"],
}


def classify(text):
    for topic, kws in KW_MAP.items():
        if any(kw in text for kw in kws):
            return topic
    return "기타"


async def export():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    data = json.load(open("data/manual_verify/representatives.json"))
    rep_ids = data["representatives"]
    clusters = data["clusters"]

    rows = await conn.fetch("""
        SELECT mp.id, mp.prediction_text, mp.predicted_direction,
               mp.target_asset, mp.prediction_date
        FROM mer_predictions mp
        WHERE mp.id = ANY($1::int[])
        ORDER BY mp.prediction_date DESC
    """, rep_ids)
    await conn.close()

    topics: dict[str, list] = {}
    for r in rows:
        combined = (r["prediction_text"] or "") + " " + (r["target_asset"] or "")
        topic = classify(combined)
        topics.setdefault(topic, []).append(r)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    total_files = 0

    for topic, items in sorted(topics.items(), key=lambda x: -len(x[1])):
        for j in range(0, len(items), BATCH_SIZE):
            batch = items[j:j + BATCH_SIZE]
            part = j // BATCH_SIZE + 1
            total_parts = (len(items) + BATCH_SIZE - 1) // BATCH_SIZE

            lines = []
            for r in batch:
                pid = r["id"]
                cluster_size = len(clusters.get(str(pid), [pid]))
                marker = f" [+{cluster_size-1}건 유사]" if cluster_size > 1 else ""
                lines.append(
                    f"{pid}. [{r['target_asset']}] {r['prediction_text']}{marker} "
                    f"(방향: {r['predicted_direction']}, 예측일: {r['prediction_date']})"
                )

            suffix = f"_part{part}" if total_parts > 1 else ""
            filename = f"{topic}{suffix}.txt"
            content = f"{SYSTEM}\n\n[예측 목록 — {topic} ({len(batch)}건 대표)]\n" + "\n".join(lines)
            (OUT_DIR / filename).write_text(content, encoding="utf-8")
            total_files += 1

        print(f"  {topic}: {len(items)}건 대표 → {total_parts}파일")

    print(f"\n총 {len(rows)}건 대표 → {total_files}개 파일")
    print("검증 후 클러스터 전파: 2,724건 판정 → 5,010건에 적용")


if __name__ == "__main__":
    asyncio.run(export())
