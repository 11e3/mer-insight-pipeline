"""
통합 테스트 — 실제 PostgreSQL + Claude mock.

실행 조건:
    DATABASE_URL 환경변수가 설정된 경우에만 실행.
    CI: GitHub Actions의 services.postgres 사용.
    로컬: docker-compose up db 후 실행.

실행 방법:
    DATABASE_URL=postgresql://mer:pass@localhost:5432/mer_test pytest tests/test_integration_dispatcher.py -v
"""

import asyncio
import json
import os
import pytest
import pytest_asyncio

# DB 없으면 전체 스킵
pytestmark = pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="TEST_DATABASE_URL 미설정 — 통합 테스트 스킵",
)


@pytest_asyncio.fixture
async def pg_conn():
    """테스트용 PostgreSQL 연결. 각 테스트 종료 시 데이터 정리."""
    import asyncpg
    conn = await asyncpg.connect(os.environ["TEST_DATABASE_URL"])
    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id         SERIAL PRIMARY KEY,
            event_type VARCHAR(20) NOT NULL,
            source     TEXT,
            title      TEXT,
            content    TEXT,
            event_date TIMESTAMP,
            embedding  vector(768),
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS auto_analyses (
            id            SERIAL PRIMARY KEY,
            event_id      INTEGER REFERENCES events(id) ON DELETE CASCADE,
            analysis_text TEXT,
            rules_used    INTEGER[],
            macro_context JSONB,
            telegram_sent BOOLEAN DEFAULT FALSE,
            telegram_sent_at TIMESTAMP,
            created_at    TIMESTAMP DEFAULT NOW()
        )
    """)
    yield conn
    # 정리
    await conn.execute("DELETE FROM auto_analyses")
    await conn.execute("DELETE FROM events")
    await conn.close()


@pytest.mark.asyncio
async def test_event_insert_and_query(pg_conn):
    """events 테이블 삽입 및 조회."""
    from datetime import datetime
    event_id = await pg_conn.fetchval("""
        INSERT INTO events (event_type, source, title, content, event_date)
        VALUES ('mer_new_post', 'https://example.com', '테스트 글', '내용', $1)
        RETURNING id
    """, datetime.now())

    assert event_id is not None

    row = await pg_conn.fetchrow("SELECT * FROM events WHERE id = $1", event_id)
    assert row["title"] == "테스트 글"
    assert row["event_type"] == "mer_new_post"


@pytest.mark.asyncio
async def test_auto_analyses_insert_with_fk(pg_conn):
    """auto_analyses가 events FK를 올바르게 참조."""
    from datetime import datetime
    event_id = await pg_conn.fetchval("""
        INSERT INTO events (event_type, title, event_date)
        VALUES ('dart', 'DART 공시', $1)
        RETURNING id
    """, datetime.now())

    analysis_id = await pg_conn.fetchval("""
        INSERT INTO auto_analyses
            (event_id, analysis_text, rules_used, macro_context, telegram_sent)
        VALUES ($1, '분석 결과', $2, $3, FALSE)
        RETURNING id
    """, event_id, [], json.dumps({}))

    assert analysis_id is not None

    row = await pg_conn.fetchrow(
        "SELECT aa.analysis_text FROM auto_analyses aa WHERE aa.event_id = $1",
        event_id
    )
    assert row["analysis_text"] == "분석 결과"


@pytest.mark.asyncio
async def test_cascade_delete(pg_conn):
    """events 삭제 시 auto_analyses도 cascade 삭제."""
    from datetime import datetime
    event_id = await pg_conn.fetchval("""
        INSERT INTO events (event_type, title, event_date)
        VALUES ('news', '뉴스', $1) RETURNING id
    """, datetime.now())
    await pg_conn.execute("""
        INSERT INTO auto_analyses (event_id, analysis_text, rules_used, macro_context)
        VALUES ($1, '분석', $2, $3)
    """, event_id, [], json.dumps({}))

    count_before = await pg_conn.fetchval(
        "SELECT COUNT(*) FROM auto_analyses WHERE event_id = $1", event_id
    )
    assert count_before == 1

    await pg_conn.execute("DELETE FROM events WHERE id = $1", event_id)

    count_after = await pg_conn.fetchval(
        "SELECT COUNT(*) FROM auto_analyses WHERE event_id = $1", event_id
    )
    assert count_after == 0


@pytest.mark.asyncio
async def test_post_enricher_related_posts_empty_db(pg_conn):
    """DB가 비어 있을 때 related_posts는 빈 리스트."""
    import sys
    from unittest.mock import AsyncMock, MagicMock, patch

    # vertexai가 없는 환경(CI)에서는 mock으로 대체
    if "vertexai" not in sys.modules:
        sys.modules["vertexai"] = MagicMock()
        sys.modules["vertexai.language_models"] = MagicMock()

    with patch("src.pipeline.post_enricher.anthropic.AsyncAnthropic"):
        from src.pipeline.post_enricher import PostEnricher

    mock_embedder = MagicMock()
    mock_embedder.embed_passages = AsyncMock(return_value=[[0.0] * 768])

    enricher = PostEnricher(pg_conn, mock_embedder)
    result = await enricher._related_posts([0.0] * 768, "https://example.com")
    assert result == []
