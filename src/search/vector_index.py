"""
벡터 검색 래퍼: pgvector HNSW 검색을 동일 인터페이스로 노출.

Returns: [(insight_id, similarity_score), ...] top_k 개
"""

import asyncpg

from src.embed import Embedder, vec_str


class VectorIndex:
    """pgvector HNSW 코사인 유사도 검색."""

    def __init__(self, conn: asyncpg.Connection, embedder: Embedder):
        self.conn = conn
        self.embedder = embedder

    async def search(self, query: str, top_k: int = 20) -> list[tuple[int, float]]:
        """
        벡터 유사도 검색.

        Returns:
            [(insight_id, cosine_similarity), ...] top_k 개, 유사도 내림차순
        """
        vec = vec_str(await self.embedder.embed_query(query[:500]))

        rows = await self.conn.fetch("""
            SELECT id,
                   1 - (embedding <=> $1::vector) AS similarity
            FROM mer_insights
            WHERE embedding IS NOT NULL
              AND insight_type IN ('rule', 'evaluation', 'macro_view')
              AND (is_canonical IS NULL OR is_canonical = TRUE)
            ORDER BY embedding <=> $1::vector
            LIMIT $2
        """, vec, top_k)

        return [(r["id"], float(r["similarity"])) for r in rows]
