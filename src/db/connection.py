"""Async context managers for DB connections."""

from contextlib import asynccontextmanager

import asyncpg

from src.config.settings import DATABASE_URL


@asynccontextmanager
async def connect(url: str | None = None):
    """단일 연결 context manager."""
    conn = await asyncpg.connect(url or DATABASE_URL)
    try:
        yield conn
    finally:
        await conn.close()


@asynccontextmanager
async def get_pool(url: str | None = None, *, min_size: int = 2, max_size: int = 10):
    """커넥션 풀 context manager."""
    pool = await asyncpg.create_pool(url or DATABASE_URL, min_size=min_size, max_size=max_size)
    try:
        yield pool
    finally:
        await pool.close()
