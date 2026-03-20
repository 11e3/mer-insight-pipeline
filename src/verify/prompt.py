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
- CORRECT: 헤드라인이 예측 내용을 확인해줌. source_url 필수.
- INCORRECT: 헤드라인이 예측과 반대되는 결과를 보여줌. source_url 필수.
- PENDING: 헤드라인만으로는 판단 불가. source_url 불필요.

규칙:
- 확실한 근거 없으면 반드시 PENDING
- source_url은 반드시 [매칭된 뉴스 헤드라인]에 있는 URL 중 하나를 그대로 복사. 절대 다른 URL을 만들지 마라.
- reason은 한 줄로 구체적 근거 (수치 포함 권장)
- 제공된 헤드라인은 이미 예측일 이후 것만 필터링된 상태다. 모든 헤드라인을 검증 근거로 활용하라.

JSON만 출력:
{"verdict": "CORRECT|INCORRECT|PENDING", "reason": "판단 근거", "source_url": "URL (PENDING이면 빈 문자열)"}
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
