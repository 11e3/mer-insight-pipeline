"""pipeline/event_dispatcher.py 단위 테스트."""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import asyncpg

sys.path.insert(0, str(Path(__file__).parent.parent))

for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
    os.environ.setdefault(_var, "test")

from src.collect.source_protocol import CollectedPost


class FakePoolAcquire:
    """Fake pool.acquire() that acts as an async context manager."""
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        return False


def _make_dispatcher():
    """Mock된 EventDispatcher 생성."""
    with patch("src.pipeline.event_dispatcher.AsyncIOScheduler"):
        from src.pipeline.event_dispatcher import EventDispatcher
        d = EventDispatcher()

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value = FakePoolAcquire(mock_conn)
    mock_pool.close = AsyncMock()
    d.pool = mock_pool

    d.embedder = MagicMock()

    d.bm25_index = MagicMock()
    d.bm25_index.load.return_value = True
    d.bm25_index.build = AsyncMock()
    d.bm25_index.save = MagicMock()

    return d, mock_conn


def _make_collector(source_name="mer_ranto28", posts=None):
    """Mock SourceCollector."""
    collector = MagicMock()
    collector.source_name = source_name
    collector.check_new = AsyncMock(return_value=posts or [])
    return collector


# ─── _run_pipeline ───────────────────────────────────────────────────────────

async def test_run_pipeline_no_new_posts():
    d, mock_conn = _make_dispatcher()

    collector = _make_collector(posts=[])
    mock_verifier = MagicMock()
    mock_verifier.run = AsyncMock(return_value={"auto_resolved": 0, "pending": 0, "errors": 0, "cost_usd": 0})

    with patch.object(d, "_get_active_collectors", AsyncMock(return_value=[collector])), \
         patch("src.pipeline.event_dispatcher.NewsCollector") as MockNews, \
         patch("src.pipeline.event_dispatcher.AutoVerifier", return_value=mock_verifier):
        MockNews.return_value.run_daily = AsyncMock(return_value=0)
        await d._run_pipeline()

    collector.check_new.assert_called_once()
    mock_verifier.run.assert_called_once()


async def test_run_pipeline_with_new_posts():
    d, mock_conn = _make_dispatcher()

    posts = [CollectedPost(
        external_id="111", title="테스트", content_text="내용",
        url="https://blog.naver.com/ranto28/111",
    )]
    collector = _make_collector(posts=posts)
    mock_conn.fetchrow = AsyncMock(return_value={"id": 1, "platform": "blog"})
    mock_conn.fetchval = AsyncMock(return_value=1)

    mock_verifier = MagicMock()
    mock_verifier.run = AsyncMock(return_value={"auto_resolved": 0, "pending": 0, "errors": 0, "cost_usd": 0})

    with patch.object(d, "_get_active_collectors", AsyncMock(return_value=[collector])), \
         patch("src.pipeline.event_dispatcher.extract_and_save",
               AsyncMock(return_value={"count": 3})) as mock_extract, \
         patch("src.pipeline.event_dispatcher.NewsCollector") as MockNews, \
         patch("src.pipeline.event_dispatcher.AutoVerifier", return_value=mock_verifier):
        MockNews.return_value.run_daily = AsyncMock(return_value=0)
        await d._run_pipeline()

    mock_extract.assert_called_once()
    d.bm25_index.build.assert_called_once()
    d.bm25_index.save.assert_called_once()


async def test_run_pipeline_monitor_error():
    d, mock_conn = _make_dispatcher()

    collector = _make_collector()
    collector.check_new = AsyncMock(side_effect=asyncpg.PostgresError("monitor error"))

    mock_verifier = MagicMock()
    mock_verifier.run = AsyncMock(return_value={"auto_resolved": 2, "pending": 0, "errors": 0, "cost_usd": 0})

    with patch.object(d, "_get_active_collectors", AsyncMock(return_value=[collector])), \
         patch("src.pipeline.event_dispatcher.NewsCollector") as MockNews, \
         patch("src.pipeline.event_dispatcher.AutoVerifier", return_value=mock_verifier):
        MockNews.return_value.run_daily = AsyncMock(return_value=0)
        await d._run_pipeline()

    mock_verifier.run.assert_called_once()


async def test_run_pipeline_verifier_error():
    d, mock_conn = _make_dispatcher()

    collector = _make_collector(posts=[])

    mock_verifier = MagicMock()
    mock_verifier.run = AsyncMock(side_effect=anthropic.APIError(message="verify error", request=MagicMock(), body=None))

    with patch.object(d, "_get_active_collectors", AsyncMock(return_value=[collector])), \
         patch("src.pipeline.event_dispatcher.NewsCollector") as MockNews, \
         patch("src.pipeline.event_dispatcher.AutoVerifier", return_value=mock_verifier):
        MockNews.return_value.run_daily = AsyncMock(return_value=0)
        await d._run_pipeline()  # should not raise


# ─── run ─────────────────────────────────────────────────────────────────────

async def test_run_full_cycle():
    d, mock_conn = _make_dispatcher()

    d._init = AsyncMock()
    d._run_pipeline = AsyncMock()

    await d.run()

    d._init.assert_called_once()
    d._run_pipeline.assert_called_once()
    d.pool.close.assert_called_once()


# ─── _init ────────────────────────────────────────────────────────────────────

async def test_init_loads_bm25_from_cache():
    with patch("src.pipeline.event_dispatcher.AsyncIOScheduler"):
        from src.pipeline.event_dispatcher import EventDispatcher
        d = EventDispatcher()

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value = FakePoolAcquire(mock_conn)

    mock_bm25 = MagicMock()
    mock_bm25.load.return_value = True

    with patch("src.pipeline.event_dispatcher.asyncpg.create_pool",
               AsyncMock(return_value=mock_pool)), \
         patch("src.pipeline.event_dispatcher.get_embedder", return_value=MagicMock()), \
         patch("src.pipeline.event_dispatcher.BM25Index", return_value=mock_bm25):
        await d._init()

    mock_bm25.load.assert_called_once()
    mock_bm25.build.assert_not_called()


async def test_init_builds_bm25_on_cache_miss():
    with patch("src.pipeline.event_dispatcher.AsyncIOScheduler"):
        from src.pipeline.event_dispatcher import EventDispatcher
        d = EventDispatcher()

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value = FakePoolAcquire(mock_conn)

    mock_bm25 = MagicMock()
    mock_bm25.load.return_value = False
    mock_bm25.build = AsyncMock()

    with patch("src.pipeline.event_dispatcher.asyncpg.create_pool",
               AsyncMock(return_value=mock_pool)), \
         patch("src.pipeline.event_dispatcher.get_embedder", return_value=MagicMock()), \
         patch("src.pipeline.event_dispatcher.BM25Index", return_value=mock_bm25):
        await d._init()

    mock_bm25.build.assert_called_once()
    mock_bm25.save.assert_called_once()


# ─── _get_active_collectors ──────────────────────────────────────────────────

async def test_get_active_collectors():
    d, mock_conn = _make_dispatcher()
    mock_conn.fetch = AsyncMock(return_value=[
        {"source_type": "blog", "name": "mer_ranto28", "config": "{}"},
    ])

    with patch("src.pipeline.event_dispatcher.get_collector") as mock_factory:
        mock_factory.return_value = _make_collector()
        collectors = await d._get_active_collectors(mock_conn)

    assert len(collectors) == 1
    mock_factory.assert_called_once_with("blog", "mer_ranto28", {})


async def test_get_active_collectors_unknown_type():
    d, mock_conn = _make_dispatcher()
    mock_conn.fetch = AsyncMock(return_value=[
        {"source_type": "unknown", "name": "test", "config": "{}"},
    ])

    with patch("src.pipeline.event_dispatcher.get_collector", side_effect=ValueError("Unknown")):
        collectors = await d._get_active_collectors(mock_conn)

    assert len(collectors) == 0
