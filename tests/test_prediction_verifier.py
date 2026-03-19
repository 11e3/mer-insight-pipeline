"""
prediction_verifier.py 단위 테스트 — DB/네트워크 의존성 없음.
내보내기 + 알림 로직 검증.
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
from src.verify.prompt import BATCH_SIZE

import asyncio


def run(coro):
    return asyncio.run(coro)


def _make_verifier():
    """DB 연결 없는 PredictionVerifier 인스턴스."""
    conn = MagicMock()
    return PredictionVerifier(conn)


def test_batch_size_is_reasonable():
    """배치 크기가 적절한 범위."""
    assert 10 <= BATCH_SIZE <= 200


def test_fetch_verifiable_query():
    """_fetch_verifiable이 올바른 SQL 조건을 사용하는지."""
    v = _make_verifier()
    v.conn.fetch = AsyncMock(return_value=[])
    run(v._fetch_verifiable())
    sql = v.conn.fetch.call_args[0][0]
    assert "is_correct IS NULL" in sql
    assert "expected_date" in sql


def test_export_creates_files(tmp_path):
    """내보내기가 파일을 올바르게 생성하는지."""
    v = _make_verifier()

    # Monkey-patch EXPORT_DIR
    import src.verify.verifier as mod
    orig = mod.EXPORT_DIR
    mod.EXPORT_DIR = tmp_path

    preds = [
        {
            "id": 1, "prediction_text": "코스피 3000",
            "predicted_direction": "up", "target_asset": "KOSPI",
            "prediction_date": date(2024, 1, 1), "expected_date": date(2024, 6, 1),
        },
        {
            "id": 2, "prediction_text": "환율 하락",
            "predicted_direction": "down", "target_asset": "USD/KRW",
            "prediction_date": date(2024, 2, 1), "expected_date": None,
        },
    ]

    exported = run(v._export(preds))
    assert exported == 2

    files = list(tmp_path.glob("*.txt"))
    assert len(files) == 1

    content = files[0].read_text(encoding="utf-8")
    assert "코스피 3000" in content
    assert "환율 하락" in content

    mod.EXPORT_DIR = orig


def test_run_returns_zero_when_no_pending():
    """검증 대기 없으면 0 반환."""
    v = _make_verifier()
    v.conn.fetch = AsyncMock(return_value=[])
    result = run(v.run())
    assert result == 0


def test_notify_skips_without_token():
    """토큰 없으면 알림 건너뛰기."""
    v = _make_verifier()
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_TIER1_CHAT_ID": ""}):
        # Should not raise
        run(v._notify(5))
