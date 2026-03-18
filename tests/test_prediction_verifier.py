"""
prediction_verifier.py 단위 테스트 — DB/네트워크 의존성 없음.
주식 파싱 로직과 컨텍스트 포맷을 검증.
"""

import os
import sys
from pathlib import Path
from datetime import date
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

# 필수 환경변수 stub — 실제 연결 없이 import만 허용
for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY", "GCP_PROJECT_ID",
             "TELEGRAM_BOT_TOKEN", "TELEGRAM_TIER1_CHAT_ID",
             "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"):
    os.environ.setdefault(_var, "test")

from src.verify import PredictionVerifier
from src.verify.prompt import SYSTEM as _SYSTEM


# ── _fetch_stock_context 내부 파싱 로직 ───────────────────────────────────────

def _make_verifier():
    """DB 연결 없는 PredictionVerifier 인스턴스."""
    conn = MagicMock()
    with patch("src.verify.verifier.anthropic.AsyncAnthropic"):
        return PredictionVerifier(conn)


def test_system_prompt_has_verdicts():
    """시스템 프롬프트에 CORRECT/INCORRECT/PENDING 기준 포함 여부."""
    assert "CORRECT" in _SYSTEM
    assert "INCORRECT" in _SYSTEM
    assert "PENDING" in _SYSTEM


def test_system_prompt_json_output():
    """출력 포맷이 JSON 배열임을 명시."""
    assert "JSON" in _SYSTEM


# ── _save_results 로직 ────────────────────────────────────────────────────────

import asyncio

def run(coro):
    return asyncio.run(coro)


def test_save_results_skips_pending():
    v = _make_verifier()
    v.conn.execute = AsyncMock()
    results = [{"id": 1, "verdict": "PENDING", "reason": "아직"}]
    resolved = run(v._save_results(results))
    assert resolved == 0
    v.conn.execute.assert_not_called()


def test_save_results_counts_correct():
    v = _make_verifier()
    v.conn.execute = AsyncMock()
    results = [
        {"id": 1, "verdict": "CORRECT",   "reason": "예측 적중"},
        {"id": 2, "verdict": "INCORRECT", "reason": "반대 결과"},
        {"id": 3, "verdict": "PENDING",   "reason": "미결"},
    ]
    resolved = run(v._save_results(results))
    assert resolved == 2
    assert v.conn.execute.call_count == 2


def test_save_results_skips_missing_id():
    v = _make_verifier()
    v.conn.execute = AsyncMock()
    results = [{"verdict": "CORRECT", "reason": "근거"}]  # id 없음
    resolved = run(v._save_results(results))
    assert resolved == 0


def test_save_results_correct_flag():
    """CORRECT → is_correct=True, INCORRECT → is_correct=False 로 저장."""
    v = _make_verifier()
    calls = []

    async def mock_execute(sql, is_correct, reason, pred_id):
        calls.append({"is_correct": is_correct, "id": pred_id})

    v.conn.execute = mock_execute

    results = [
        {"id": 10, "verdict": "CORRECT",   "reason": "맞음"},
        {"id": 11, "verdict": "INCORRECT", "reason": "틀림"},
    ]
    run(v._save_results(results))

    assert calls[0]["is_correct"] is True
    assert calls[1]["is_correct"] is False


# ── _verify_batch JSON 파싱 내성 테스트 ───────────────────────────────────────

def test_verify_batch_handles_malformed_json():
    """Claude가 JSON 외 텍스트를 반환해도 빈 리스트로 graceful 처리."""
    v = _make_verifier()

    async def fake_create(**kwargs):
        msg = MagicMock()
        msg.content = [MagicMock(text="죄송합니다, 판단할 수 없습니다.")]
        return msg

    v._claude.messages.create = fake_create
    batch = [{"id": 1, "prediction_text": "코스피 3000", "predicted_direction": "up",
              "target_asset": "KOSPI", "prediction_date": date(2024, 1, 1)}]
    result = run(v._verify_batch(batch))
    assert result == []


def test_verify_batch_parses_valid_json():
    """올바른 JSON 배열 반환 시 정상 파싱."""
    v = _make_verifier()

    async def fake_create(**kwargs):
        msg = MagicMock()
        msg.content = [MagicMock(text='[{"id": 1, "verdict": "CORRECT", "reason": "적중"}]')]
        return msg

    v._claude.messages.create = fake_create
    batch = [{"id": 1, "prediction_text": "코스피 상승", "predicted_direction": "up",
              "target_asset": "KOSPI", "prediction_date": date(2024, 1, 1)}]
    result = run(v._verify_batch(batch))
    assert len(result) == 1
    assert result[0]["verdict"] == "CORRECT"
