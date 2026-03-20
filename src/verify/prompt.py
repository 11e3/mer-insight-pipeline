"""예측 검증 상수."""

BATCH_SIZE = 20  # 수동 검증용 내보내기 배치 크기 (ID 밀림 방지)

# ─── 자동 검증 (헤드라인 매칭 → Haiku 판정) ───────────────────────────────────

AUTO_VERIFY_MODEL = "claude-haiku-4-5-20251001"
DAILY_LIMIT = 200
CALL_DELAY = 0.5  # 호출 간 대기 (초)

# Haiku pricing (per 1M tokens)
HAIKU_INPUT_COST_PER_M = 1.0
HAIKU_OUTPUT_COST_PER_M = 5.0

AUTO_VERIFY_SYSTEM_PROMPT = """\
너는 경제·금융 예측 검증 전문가다.
[예측]과 [매칭된 뉴스 헤드라인]을 보고 예측의 정확성을 판정한다.

판정 기준:
- CORRECT: 헤드라인이 예측 내용을 확인해줌
- INCORRECT: 헤드라인이 예측과 반대되는 결과를 보여줌
- PENDING: 헤드라인만으로는 판단 불가

규칙:
- 확실한 근거 없으면 반드시 PENDING
- 제공된 헤드라인은 이미 예측일 이후 것만 필터링된 상태다. 모든 헤드라인을 검증 근거로 활용하라.
- reason에 근거를 들 때 반드시 "헤드라인N" 형태로 인용하라 (N은 번호). URL은 넣지 마라.
  예: "헤드라인3에서 BOJ 금리 0.75% 인상 확인, 헤드라인7에서 엔화 강세 전환"
JSON만 출력:
{"verdict": "CORRECT|INCORRECT|PENDING", "reason": "판단 근거 (헤드라인N 인용 필수)"}
"""

AUTO_VERIFY_USER_TEMPLATE = """\
오늘 날짜: {today}

[예측]
{prediction_text}
대상: {target_asset}, 방향: {predicted_direction}, 예측일: {prediction_date}
검증 기한: {expected_date}

[매칭된 뉴스 헤드라인]
{headlines_text}
"""
