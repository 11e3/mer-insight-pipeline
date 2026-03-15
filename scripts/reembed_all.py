"""
기존 mer_insights, events 테이블의 embedding을 Vertex AI로 재생성.

migrate_vector_dim.sql 실행 후 반드시 이 스크립트를 실행할 것.

Usage:
    python -m scripts.reembed_all
    python -m scripts.reembed_all --table insights   # mer_insights만
    python -m scripts.reembed_all --table events     # events만
"""

import asyncio
import argparse
import logging

import asyncpg
from tqdm import tqdm

from config.settings import DATABASE_URL, EMBEDDING_BATCH_SIZE
from src.extract.embedder import VertexEmbedder, vec_str

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)


async def reembed_insights(conn: asyncpg.Connection, embedder: VertexEmbedder) -> int:
    rows = await conn.fetch(
        "SELECT id, content FROM mer_insights ORDER BY id"
    )
    if not rows:
        log.info("mer_insights: 행 없음")
        return 0

    log.info(f"mer_insights 재임베딩: {len(rows)}개")
    total = 0
    batch_ids, batch_texts = [], []

    async def flush():
        nonlocal total
        if not batch_ids:
            return
        vecs = await embedder.embed_passages(batch_texts)
        for rid, vec in zip(batch_ids, vecs):
            await conn.execute(
                "UPDATE mer_insights SET embedding = $1::vector WHERE id = $2",
                vec_str(vec), rid,
            )
        total += len(batch_ids)
        batch_ids.clear()
        batch_texts.clear()

    for row in tqdm(rows, desc="insights"):
        batch_ids.append(row["id"])
        batch_texts.append(row["content"])
        if len(batch_ids) >= EMBEDDING_BATCH_SIZE:
            await flush()

    await flush()
    return total


async def reembed_events(conn: asyncpg.Connection, embedder: VertexEmbedder) -> int:
    rows = await conn.fetch(
        "SELECT id, title, content FROM events ORDER BY id"
    )
    if not rows:
        log.info("events: 행 없음")
        return 0

    log.info(f"events 재임베딩: {len(rows)}개")
    total = 0
    batch_ids, batch_texts = [], []

    async def flush():
        nonlocal total
        if not batch_ids:
            return
        vecs = await embedder.embed_passages(batch_texts)
        for rid, vec in zip(batch_ids, vecs):
            await conn.execute(
                "UPDATE events SET embedding = $1::vector WHERE id = $2",
                vec_str(vec), rid,
            )
        total += len(batch_ids)
        batch_ids.clear()
        batch_texts.clear()

    for row in tqdm(rows, desc="events"):
        batch_ids.append(row["id"])
        batch_texts.append(f"{row['title']} {(row['content'] or '')[:300]}")
        if len(batch_ids) >= EMBEDDING_BATCH_SIZE:
            await flush()

    await flush()
    return total


async def main(table: str):
    conn = await asyncpg.connect(DATABASE_URL)
    embedder = VertexEmbedder()

    try:
        if table in ("insights", "all"):
            n = await reembed_insights(conn, embedder)
            log.info(f"mer_insights 완료: {n}개")
        if table in ("events", "all"):
            n = await reembed_events(conn, embedder)
            log.info(f"events 완료: {n}개")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--table",
        choices=["all", "insights", "events"],
        default="all",
        help="재임베딩할 테이블 (기본값: all)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.table))
