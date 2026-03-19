"""verifier.py 추가 테스트 — _notify with token, _export header details."""

import os
import sys
import asyncio
from pathlib import Path
from datetime import date
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
    os.environ.setdefault(_var, "test")

from src.verify.verifier import PredictionVerifier


def run(coro):
    return asyncio.run(coro)


def _make_verifier():
    conn = MagicMock()
    return PredictionVerifier(conn)


def test_notify_with_token():
    """텔레그램 토큰이 있으면 알림 전송."""
    v = _make_verifier()
    mock_requests_mod = MagicMock()
    mock_requests_mod.post.return_value = MagicMock(status_code=200)

    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "fake_token",
        "TELEGRAM_TIER1_CHAT_ID": "fake_chat_id",
    }):
        # requests is imported inside _notify, so patch it in sys.modules
        with patch.dict("sys.modules", {"requests": mock_requests_mod}):
            run(v._notify(10))

    mock_requests_mod.post.assert_called_once()
    call_args = mock_requests_mod.post.call_args
    assert "fake_token" in call_args[0][0]
    assert call_args[1]["json"]["chat_id"] == "fake_chat_id"
    assert "10" in call_args[1]["json"]["text"]


def test_notify_request_error():
    """텔레그램 요청 실패 시 예외 없이 로깅."""
    v = _make_verifier()
    mock_requests_mod = MagicMock()
    mock_requests_mod.post.side_effect = Exception("network error")

    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "tok",
        "TELEGRAM_TIER1_CHAT_ID": "cid",
    }):
        with patch.dict("sys.modules", {"requests": mock_requests_mod}):
            run(v._notify(5))  # should not raise


def test_export_header_content(tmp_path):
    """내보내기 파일의 헤더에 필수 정보 포함."""
    v = _make_verifier()
    import src.verify.verifier as mod
    orig = mod.EXPORT_DIR
    mod.EXPORT_DIR = tmp_path

    preds = [{
        "id": 1,
        "prediction_text": "테스트 예측",
        "predicted_direction": "up",
        "target_asset": "KOSPI",
        "prediction_date": date(2024, 1, 1),
        "expected_date": date(2024, 6, 1),
    }]

    run(v._export(preds))

    files = list(tmp_path.glob("*.txt"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "수동 검증 대기" in content
    assert "CORRECT" in content
    assert "INCORRECT" in content
    assert "PENDING" in content

    mod.EXPORT_DIR = orig


def test_run_with_pending_predictions(tmp_path):
    """검증 대기 예측이 있을 때 전체 흐름."""
    v = _make_verifier()
    import src.verify.verifier as mod
    orig = mod.EXPORT_DIR
    mod.EXPORT_DIR = tmp_path

    preds = [
        {
            "id": 1, "prediction_text": "금리 인하",
            "predicted_direction": "down", "target_asset": "기준금리",
            "prediction_date": date(2024, 3, 1), "expected_date": None,
        },
    ]
    v.conn.fetch = AsyncMock(return_value=preds)

    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_TIER1_CHAT_ID": ""}):
        result = run(v.run())

    assert result == 1
    mod.EXPORT_DIR = orig


def test_export_multiple_batches(tmp_path):
    """BATCH_SIZE 초과 시 여러 파일 생성."""
    v = _make_verifier()
    import src.verify.verifier as mod
    orig_dir = mod.EXPORT_DIR
    orig_batch = mod.BATCH_SIZE
    mod.EXPORT_DIR = tmp_path
    mod.BATCH_SIZE = 2  # small batch size

    preds = [
        {
            "id": i, "prediction_text": f"예측 {i}",
            "predicted_direction": "up", "target_asset": None,
            "prediction_date": None, "expected_date": None,
        }
        for i in range(5)
    ]

    exported = run(v._export(preds))
    assert exported == 5

    files = list(tmp_path.glob("*.txt"))
    assert len(files) == 3  # 5 preds / batch 2 = 3 files

    mod.EXPORT_DIR = orig_dir
    mod.BATCH_SIZE = orig_batch
