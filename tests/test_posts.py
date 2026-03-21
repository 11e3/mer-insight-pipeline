"""collect/posts.py 단위 테스트."""

import os
import sys
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
from contextlib import asynccontextmanager

sys.path.insert(0, str(Path(__file__).parent.parent))

for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
    os.environ.setdefault(_var, "test")


def _mock_connect(mock_conn):
    @asynccontextmanager
    async def fake_connect(*args, **kwargs):
        yield mock_conn
    return fake_connect


async def test_load_posts_basic(tmp_path):
    """JSON 파일을 읽어 DB에 적재."""
    post_data = {
        "log_no": "1234567890",
        "title": "테스트 포스트",
        "date": "2024. 1. 15.",
        "url": "https://blog.naver.com/ranto28/1234567890",
        "content_text": "본문 내용",
        "images": [],
        "tags": ["경제"],
    }
    (tmp_path / "post1.json").write_text(json.dumps(post_data, ensure_ascii=False), encoding="utf-8")

    mock_conn = AsyncMock()

    with patch("src.db.connection.asyncpg.connect", AsyncMock(return_value=mock_conn)):
        mock_conn.close = AsyncMock()
        from src.collect.posts import load_posts
        await load_posts(str(tmp_path))

    mock_conn.execute.assert_called_once()
    args = mock_conn.execute.call_args[0]
    assert "INSERT INTO posts" in args[0]
    assert args[1] == "1234567890"


async def test_load_posts_invalid_json(tmp_path):
    """잘못된 JSON 파일은 에러로 처리."""
    (tmp_path / "bad.json").write_text("{invalid", encoding="utf-8")

    mock_conn = AsyncMock()

    with patch("src.db.connection.asyncpg.connect", AsyncMock(return_value=mock_conn)):
        mock_conn.close = AsyncMock()
        from src.collect.posts import load_posts
        await load_posts(str(tmp_path))

    mock_conn.execute.assert_not_called()


async def test_load_posts_db_error(tmp_path):
    """DB 에러 시 계속 진행."""
    post_data = {"log_no": "111", "content_text": "abc"}
    (tmp_path / "p1.json").write_text(json.dumps(post_data), encoding="utf-8")

    mock_conn = AsyncMock()
    mock_conn.execute.side_effect = Exception("DB error")

    with patch("src.db.connection.asyncpg.connect", AsyncMock(return_value=mock_conn)):
        mock_conn.close = AsyncMock()
        from src.collect.posts import load_posts
        await load_posts(str(tmp_path))

    mock_conn.execute.assert_called_once()


async def test_load_posts_empty_dir(tmp_path):
    """빈 디렉토리에서 실행."""
    mock_conn = AsyncMock()

    with patch("src.db.connection.asyncpg.connect", AsyncMock(return_value=mock_conn)):
        mock_conn.close = AsyncMock()
        from src.collect.posts import load_posts
        await load_posts(str(tmp_path))

    mock_conn.execute.assert_not_called()
