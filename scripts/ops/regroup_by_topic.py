"""
남은 예측을 주제별로 묶어서 재생성.
기존 batch_010~048 대신 topic_*.txt로 생성.

Usage:
    python3 scripts/regroup_by_topic.py
"""

import re
from pathlib import Path

BATCH_SIZE = 100
IN_DIR = Path("data/manual_verify")
OUT_DIR = Path("data/manual_verify/by_topic")

SYSTEM = """\
너는 경제·금융 예측 검증 전문가다.
아래 [예측 목록]의 각 항목을 판단한다. 같은 주제의 예측이 묶여 있으니 관련 정보를 검색해서 한번에 판단해라.

판단 기준:
1. CORRECT   — 예측 조건이 충족됐고 결과가 예측과 일치. 구체적 근거가 있어야 함
2. INCORRECT — 예측 조건이 충족됐으나 결과가 예측과 반대. 반박 근거가 있어야 함
3. PENDING   — 조건이 아직 충족되지 않았거나 충분한 정보가 없어 판단 불가

규칙:
- 확실한 근거가 없으면 반드시 PENDING. 추측으로 CORRECT/INCORRECT 판정 금지
- 미래 시점 예측은 해당 시점이 지나고 결과가 확인된 경우에만 판정
- 수치를 지어내지 마라. 검색으로 확인된 사실만 근거로 사용
- reason은 근거를 구체적으로 한 줄로 (수치 포함 권장)
- 비슷한 예측이 여러 개 있으면 같은 근거로 일괄 판정 가능

출력: JSON 배열만. 설명 텍스트 없이.
[
  {"id": 예측ID, "verdict": "CORRECT|INCORRECT|PENDING", "reason": "판단 근거 (한국어)"},
  ...
]
"""

KW_MAP = {
    "호르무즈_이란_중동": ["호르무즈", "이란", "중동", "카타르", "사우디", "이스라엘", "하마스", "헤즈볼라", "시아파", "수니파", "네타냐후", "팔레스타인"],
    "에너지_원유_LNG": ["원유", "WTI", "LNG", "천연가스", "에너지", "셰일", "원자력", "우라늄", "유가", "경유", "휘발유"],
    "관세_무역": ["관세", "무역", "WTO", "수입", "수출", "232조", "301조", "338조", "상호관세", "덤핑"],
    "트럼프_미국정치": ["트럼프", "파월", "백악관", "공화당", "민주당", "중간선거", "바이든"],
    "금리_환율_매크로": ["금리", "환율", "달러", "인플레", "CPI", "연준", "FOMC", "MBS", "기준금리", "원/달러", "엔화", "위안", "통화"],
    "부동산_전세": ["부동산", "전세", "아파트", "주택", "매매", "분양", "집값", "월세", "임대", "모기지"],
    "AI_반도체_테크": ["AI", "반도체", "엔비디아", "오픈AI", "딥시크", "데이터센터", "클라우드", "SaaS", "하이닉스", "삼성전자", "TSMC", "GPU"],
    "주식_금융": ["주식", "KOSPI", "S&P", "코스피", "주가", "사모", "펀드", "IPO", "증시", "채권", "국채"],
    "방산_안보": ["방산", "천궁", "무인", "안두릴", "방위", "미사일", "잠수함", "NATO", "F-35"],
    "원자재_금속": ["금값", "은 ", "구리", "원자재", "다이아몬드", "헬륨", "금속", "리튬", "희토류"],
    "식품_농업": ["농산물", "곡물", "명태", "게맛살", "어묵", "요소", "수산물", "식품", "비료"],
    "의료_바이오": ["오가노이드", "디스크", "의학", "의료", "바이오", "제약", "줄기세포"],
    "조선_해운": ["조선", "해운", "HD현대", "선박", "조선소"],
}


def classify(text: str, asset: str) -> str:
    combined = text + " " + asset
    for topic, keywords in KW_MAP.items():
        if any(kw in combined for kw in keywords):
            return topic
    return "기타"


def main():
    preds = []
    for i in range(1, 49):
        f = IN_DIR / f"batch_{i:03d}.txt"
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").split("\n"):
            m = re.match(r"^(\d+)\. \[(.+?)\] (.+?) \(방향: (\w+), 예측일: ([\d-]+)\)$", line)
            if m:
                preds.append({
                    "id": int(m.group(1)),
                    "asset": m.group(2),
                    "text": m.group(3),
                    "dir": m.group(4),
                    "date": m.group(5),
                })

    # 주제별 분류
    topics: dict[str, list] = {}
    for p in preds:
        topic = classify(p["text"], p["asset"])
        topics.setdefault(topic, []).append(p)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    total_files = 0
    for topic, items in sorted(topics.items(), key=lambda x: -len(x[1])):
        # 주제 내에서 BATCH_SIZE씩 분할
        for j in range(0, len(items), BATCH_SIZE):
            batch = items[j:j + BATCH_SIZE]
            part = j // BATCH_SIZE + 1
            total_parts = (len(items) + BATCH_SIZE - 1) // BATCH_SIZE

            pred_list = "\n".join(
                f'{p["id"]}. [{p["asset"]}] {p["text"]} '
                f'(방향: {p["dir"]}, 예측일: {p["date"]})'
                for p in batch
            )

            suffix = f"_part{part}" if total_parts > 1 else ""
            filename = f"{topic}{suffix}.txt"
            out_file = OUT_DIR / filename
            out_file.write_text(f"{SYSTEM}\n\n[예측 목록 — {topic} ({len(batch)}건)]\n{pred_list}", encoding="utf-8")
            total_files += 1

        print(f"  {topic}: {len(items)}건 → {(len(items) + BATCH_SIZE - 1) // BATCH_SIZE}개 파일")

    print(f"\n생성 완료: {OUT_DIR}/ ({total_files}개 파일, 총 {len(preds)}건)")
    print("\n사용법:")
    print("  1. 각 .txt 파일을 claude.ai에 붙여넣기 (같은 주제끼리 묶여 있어 효율적)")
    print(f"  2. JSON 결과를 {IN_DIR}/result_NNN.json에 저장 (번호 이어서)")
    print("  3. python3 scripts/import_manual_verify.py 실행")


if __name__ == "__main__":
    main()
