"""
insights 테이블의 embedding 컬럼 채우기.

Usage:
    python -m src.embed.backfill
"""

import asyncio

from tqdm import tqdm

from src.config.settings import EMBEDDING_BATCH_SIZE
from src.embed.factory import get_embedder, vec_str


async def fill_embeddings():
    from src.db import connect

    embedder = get_embedder()

    async with connect() as conn:
        rows = await conn.fetch("""
            SELECT id, content FROM insights
            WHERE embedding IS NULL
            ORDER BY id
        """)

        if not rows:
            print("모든 인사이트에 embedding이 존재합니다.")
            return

        print(f"embedding 생성 대상: {len(rows)}개")

        batch_ids, batch_texts = [], []
        total = 0

        async def flush():
            nonlocal total
            if not batch_ids:
                return
            vecs = await embedder.embed_passages(batch_texts)
            for rid, vec in zip(batch_ids, vecs):
                await conn.execute(
                    "UPDATE insights SET embedding = $1::vector WHERE id = $2",
                    vec_str(vec), rid,
                )
            total += len(batch_ids)
            batch_ids.clear()
            batch_texts.clear()

        for row in tqdm(rows, desc="embedding 생성"):
            batch_ids.append(row["id"])
            batch_texts.append(row["content"])
            if len(batch_ids) >= EMBEDDING_BATCH_SIZE:
                await flush()

        await flush()
        print(f"\n완료: {total}개 embedding 저장")


if __name__ == "__main__":
    asyncio.run(fill_embeddings())
