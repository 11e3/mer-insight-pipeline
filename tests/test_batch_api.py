"""extract/batch_api.py 단위 테스트."""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
    os.environ.setdefault(_var, "test")

from src.extract.batch_api import make_request, fetch_unprocessed_posts


# ─── make_request (순수 함수) ────────────────────────────────────────────────

def test_make_request_basic():
    post = {
        "log_no": "1234567890",
        "title": "테스트",
        "date": "2024-01-15",
        "url": "https://example.com",
        "content_text": "본문",
    }
    result = make_request(post, "claude-sonnet-4-6")

    assert result["custom_id"] == "mer-1234567890"
    assert result["params"]["model"] == "claude-sonnet-4-6"
    assert result["params"]["max_tokens"] == 4096
    assert len(result["params"]["messages"]) == 1
    assert result["params"]["messages"][0]["role"] == "user"


def test_make_request_custom_max_tokens():
    post = {"log_no": "111", "title": "t", "content_text": "c"}
    result = make_request(post, "claude-haiku-4-5-20251001", max_tokens=8192)
    assert result["params"]["max_tokens"] == 8192


def test_make_request_truncates_content():
    post = {
        "log_no": "111",
        "title": "t",
        "content_text": "x" * 20000,
    }
    result = make_request(post, "claude-sonnet-4-6")
    # Content should be truncated to 8000 chars
    content = result["params"]["messages"][0]["content"]
    assert len(content) < 20000


def test_make_request_none_fields():
    post = {
        "log_no": "111",
        "title": "t",
        "date": None,
        "url": None,
        "content_text": None,
    }
    result = make_request(post, "claude-sonnet-4-6")
    assert result["custom_id"] == "mer-111"


# ─── fetch_unprocessed_posts ─────────────────────────────────────────────────

async def test_fetch_unprocessed_posts():
    mock_conn = AsyncMock()
    mock_rows = [
        {"log_no": "111", "title": "A", "date": "2024-01-01", "url": "u1", "content_text": "c1"},
        {"log_no": "222", "title": "B", "date": "2024-01-02", "url": "u2", "content_text": "c2"},
    ]
    mock_conn.fetch.return_value = mock_rows
    result = await fetch_unprocessed_posts(mock_conn)
    assert len(result) == 2
    assert result[0]["log_no"] == "111"


async def test_fetch_unprocessed_posts_empty():
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []
    result = await fetch_unprocessed_posts(mock_conn)
    assert result == []


# ─── check_status ────────────────────────────────────────────────────────────

def test_check_status():
    mock_counts = MagicMock()
    mock_counts.processing = 0
    mock_counts.succeeded = 10
    mock_counts.errored = 1
    mock_counts.expired = 0
    mock_counts.canceled = 0

    mock_batch = MagicMock()
    mock_batch.processing_status = "ended"
    mock_batch.request_counts = mock_counts

    with patch("src.extract.batch_api.client") as mock_client:
        mock_client.messages.batches.retrieve.return_value = mock_batch
        from src.extract.batch_api import check_status
        result = check_status("batch_123")

    assert result is True


def test_check_status_processing():
    mock_counts = MagicMock()
    mock_counts.processing = 5
    mock_counts.succeeded = 3
    mock_counts.errored = 0
    mock_counts.expired = 0
    mock_counts.canceled = 0

    mock_batch = MagicMock()
    mock_batch.processing_status = "in_progress"
    mock_batch.request_counts = mock_counts

    with patch("src.extract.batch_api.client") as mock_client:
        mock_client.messages.batches.retrieve.return_value = mock_batch
        from src.extract.batch_api import check_status
        result = check_status("batch_456")

    assert result is False


# ─── download_results ────────────────────────────────────────────────────────

async def test_create_batch(tmp_path):
    """create_batch가 배치 생성 + ID 저장."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [
        {"log_no": "111", "title": "A", "date": "2024-01-01", "url": "u1", "content_text": "c1"},
    ]
    mock_conn.close = AsyncMock()

    mock_batch = MagicMock()
    mock_batch.id = "batch_test_123"

    with patch("src.extract.batch_api.asyncpg.connect", AsyncMock(return_value=mock_conn)), \
         patch("src.extract.batch_api.client") as mock_client, \
         patch("src.extract.batch_api.BATCH_DIR", str(tmp_path)):
        mock_client.messages.batches.create.return_value = mock_batch
        from src.extract.batch_api import create_batch
        batch_id = await create_batch(limit=1)

    assert batch_id == "batch_test_123"
    id_file = tmp_path / "batch_ids.jsonl"
    assert id_file.exists()


async def test_create_batch_with_limit(tmp_path):
    """limit 파라미터가 포스트 수를 제한."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [
        {"log_no": str(i), "title": f"T{i}", "date": "", "url": "", "content_text": "c"}
        for i in range(10)
    ]
    mock_conn.close = AsyncMock()

    mock_batch = MagicMock()
    mock_batch.id = "batch_limited"

    with patch("src.extract.batch_api.asyncpg.connect", AsyncMock(return_value=mock_conn)), \
         patch("src.extract.batch_api.client") as mock_client, \
         patch("src.extract.batch_api.BATCH_DIR", str(tmp_path)):
        mock_client.messages.batches.create.return_value = mock_batch
        from src.extract.batch_api import create_batch
        await create_batch(limit=3)

    # Should have created batch with only 3 requests
    call_args = mock_client.messages.batches.create.call_args
    assert len(call_args[1]["requests"]) == 3


def test_download_results(tmp_path):
    mock_result1 = MagicMock()
    mock_result1.model_dump_json.return_value = '{"custom_id": "mer-1"}'
    mock_result2 = MagicMock()
    mock_result2.model_dump_json.return_value = '{"custom_id": "mer-2"}'

    with patch("src.extract.batch_api.client") as mock_client, \
         patch("src.extract.batch_api.BATCH_DIR", str(tmp_path)):
        mock_client.messages.batches.results.return_value = [mock_result1, mock_result2]
        from src.extract.batch_api import download_results
        out_path = download_results("batch_789")

    assert os.path.exists(out_path)
    with open(out_path, encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == 2
