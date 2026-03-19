"""db/connection.py 단위 테스트."""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
    os.environ.setdefault(_var, "test")

from src.db.connection import connect, get_pool


# ─── connect ─────────────────────────────────────────────────────────────────

async def test_connect_yields_connection():
    mock_conn = AsyncMock()
    mock_conn.close = AsyncMock()

    with patch("src.db.connection.asyncpg.connect", AsyncMock(return_value=mock_conn)):
        async with connect("postgresql://test") as conn:
            assert conn is mock_conn

    mock_conn.close.assert_called_once()


async def test_connect_uses_default_url():
    mock_conn = AsyncMock()
    mock_conn.close = AsyncMock()

    with patch("src.db.connection.asyncpg.connect", AsyncMock(return_value=mock_conn)) as mock_connect_fn:
        async with connect() as conn:
            pass

    # Should use DATABASE_URL default
    mock_connect_fn.assert_called_once()
    args = mock_connect_fn.call_args[0]
    assert args[0] == os.environ["DATABASE_URL"]


async def test_connect_closes_on_error():
    mock_conn = AsyncMock()
    mock_conn.close = AsyncMock()

    with patch("src.db.connection.asyncpg.connect", AsyncMock(return_value=mock_conn)):
        try:
            async with connect("postgresql://test") as conn:
                raise ValueError("test error")
        except ValueError:
            pass

    mock_conn.close.assert_called_once()


# ─── get_pool ────────────────────────────────────────────────────────────────

async def test_get_pool_yields_pool():
    mock_pool = AsyncMock()
    mock_pool.close = AsyncMock()

    with patch("src.db.connection.asyncpg.create_pool", AsyncMock(return_value=mock_pool)):
        async with get_pool("postgresql://test") as pool:
            assert pool is mock_pool

    mock_pool.close.assert_called_once()


async def test_get_pool_custom_sizes():
    mock_pool = AsyncMock()
    mock_pool.close = AsyncMock()

    with patch("src.db.connection.asyncpg.create_pool", AsyncMock(return_value=mock_pool)) as mock_create:
        async with get_pool("postgresql://test", min_size=1, max_size=5) as pool:
            pass

    mock_create.assert_called_once_with("postgresql://test", min_size=1, max_size=5)


async def test_get_pool_closes_on_error():
    mock_pool = AsyncMock()
    mock_pool.close = AsyncMock()

    with patch("src.db.connection.asyncpg.create_pool", AsyncMock(return_value=mock_pool)):
        try:
            async with get_pool("postgresql://test") as pool:
                raise RuntimeError("pool error")
        except RuntimeError:
            pass

    mock_pool.close.assert_called_once()
