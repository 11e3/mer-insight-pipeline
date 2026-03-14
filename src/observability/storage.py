"""
Observability Storage: PostgreSQL traces/spans 테이블 CRUD.

스키마는 scripts/init_db.sql에 정의.
"""

import logging
from datetime import datetime

import asyncpg

log = logging.getLogger(__name__)


async def insert_trace(
    conn: asyncpg.Connection,
    trace_id: str,
    name: str,
    start_time: datetime,
    metadata: dict | None = None,
) -> None:
    await conn.execute("""
        INSERT INTO traces (id, name, start_time, metadata)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (id) DO NOTHING
    """, trace_id, name, start_time,
        __import__("json").dumps(metadata or {}, default=str))


async def finish_trace(
    conn: asyncpg.Connection,
    trace_id: str,
    end_time: datetime,
    total_input_tokens: int,
    total_output_tokens: int,
    total_cost_usd: float,
    status: str = "ok",
) -> None:
    await conn.execute("""
        UPDATE traces
        SET end_time=$2, total_input_tokens=$3, total_output_tokens=$4,
            total_cost_usd=$5, status=$6
        WHERE id=$1
    """, trace_id, end_time, total_input_tokens, total_output_tokens,
        total_cost_usd, status)


async def insert_span(
    conn: asyncpg.Connection,
    span_id: str,
    trace_id: str,
    name: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    cost_usd: float,
    tool_calls: list[str] | None = None,
    error: str | None = None,
    metadata: dict | None = None,
) -> None:
    import json
    await conn.execute("""
        INSERT INTO spans
            (id, trace_id, name, model, input_tokens, output_tokens,
             latency_ms, cost_usd, tool_calls, error, metadata)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
    """, span_id, trace_id, name, model, input_tokens, output_tokens,
        latency_ms, cost_usd, tool_calls or [], error,
        json.dumps(metadata or {}, default=str))


# ─── 조회 쿼리 (대시보드용) ─────────────────────────────────────────────────

async def daily_cost_summary(conn: asyncpg.Connection, days: int = 30) -> list[dict]:
    rows = await conn.fetch("""
        SELECT DATE(start_time) AS day,
               SUM(total_cost_usd)      AS cost,
               SUM(total_input_tokens)  AS in_tokens,
               SUM(total_output_tokens) AS out_tokens,
               COUNT(*)                 AS trace_count
        FROM traces
        WHERE start_time >= NOW() - ($1 || ' days')::interval
        GROUP BY day
        ORDER BY day
    """, str(days))
    return [dict(r) for r in rows]


async def latency_summary(conn: asyncpg.Connection, days: int = 7) -> list[dict]:
    rows = await conn.fetch("""
        SELECT name,
               AVG(latency_ms)::int AS avg_ms,
               PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)::int AS p95_ms,
               COUNT(*) AS count
        FROM spans
        WHERE created_at >= NOW() - ($1 || ' days')::interval
          AND error IS NULL
        GROUP BY name
        ORDER BY avg_ms DESC
    """, str(days))
    return [dict(r) for r in rows]


async def error_rate(conn: asyncpg.Connection, days: int = 7) -> dict:
    row = await conn.fetchrow("""
        SELECT COUNT(*) FILTER (WHERE status = 'error') AS errors,
               COUNT(*) AS total
        FROM traces
        WHERE start_time >= NOW() - ($1 || ' days')::interval
    """, str(days))
    total = row["total"] or 1
    return {"error_count": row["errors"], "total": total, "rate": row["errors"] / total}


async def recent_traces(conn: asyncpg.Connection, limit: int = 20) -> list[dict]:
    rows = await conn.fetch("""
        SELECT id, name, start_time, end_time, total_cost_usd,
               total_input_tokens + total_output_tokens AS total_tokens,
               status
        FROM traces
        ORDER BY start_time DESC
        LIMIT $1
    """, limit)
    return [dict(r) for r in rows]


async def trace_spans(conn: asyncpg.Connection, trace_id: str) -> list[dict]:
    rows = await conn.fetch("""
        SELECT id, name, model, input_tokens, output_tokens,
               latency_ms, cost_usd, tool_calls, error, created_at
        FROM spans
        WHERE trace_id = $1
        ORDER BY created_at
    """, trace_id)
    return [dict(r) for r in rows]
