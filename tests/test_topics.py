"""dashboard/topics.py 단위 테스트 — 순수 함수."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
    os.environ.setdefault(_var, "test")

from src.dashboard.topics import classify_topic, TOPIC_KEYWORDS


def test_classify_empty():
    assert classify_topic("") == "기타"


def test_classify_none():
    assert classify_topic(None) == "기타"


def test_classify_energy():
    assert classify_topic("원유 가격이 상승했다") == "에너지/원유"


def test_classify_interest_rate():
    assert classify_topic("기준금리 인하 전망") == "금리/매크로"


def test_classify_exchange():
    assert classify_topic("원/달러 환율 급등") == "환율"


def test_classify_real_estate():
    assert classify_topic("아파트 매매 가격 하락") == "부동산"


def test_classify_stock():
    assert classify_topic("KOSPI 3000 돌파") == "주식/금융"


def test_classify_ai():
    assert classify_topic("엔비디아 반도체 실적") == "AI/반도체"


def test_classify_tariff():
    assert classify_topic("상호관세 부과 예정") == "관세/무역"


def test_classify_trump():
    assert classify_topic("트럼프 대통령 발언") == "트럼프/미국"


def test_classify_middle_east():
    assert classify_topic("이란 호르무즈 해협 긴장") == "중동/지정학"


def test_classify_shipbuilding():
    assert classify_topic("HD현대 조선 수주") == "조선/해운"


def test_classify_defense():
    assert classify_topic("안두릴 방위 산업") == "방산/안보"


def test_classify_commodity():
    assert classify_topic("금값 사상 최고치") == "원자재/금속"


def test_classify_food():
    assert classify_topic("곡물 가격 상승") == "식품/농업"


def test_classify_bio():
    assert classify_topic("오가노이드 기술 발전") == "의료/바이오"


def test_classify_north_korea():
    assert classify_topic("북한 김정은 동향") == "북한"


def test_classify_china():
    assert classify_topic("중국 시진핑 정책") == "중국"


def test_classify_unknown():
    assert classify_topic("오늘 날씨가 좋다") == "기타"


def test_classify_case_insensitive():
    assert classify_topic("wti 가격 변동") == "에너지/원유"


def test_all_topics_have_keywords():
    for topic, kws in TOPIC_KEYWORDS.items():
        assert len(kws) > 0, f"{topic} has no keywords"
