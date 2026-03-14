"""
벡터 검색 래퍼: context_assembler의 pgvector 검색 로직을 동일 인터페이스로 노출.

Returns: [(insight_id, similarity_score), ...] top_k 개
"""

import asyncpg
from sentence_transformers import SentenceTransformer


def _vec_str(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"


class VectorIndex:
    """pgvector HNSW 코사인 유사도 검색."""

    def __init__(self, conn: asyncpg.Connection, embedder: SentenceTransformer):
        self.conn = conn
        self.embedder = embedder

    async def search(self, query: str, top_k: int = 20) -> list[tuple[int, float]]:
        """
        벡터 유사도 검색.

        Returns:
            [(insight_id, cosine_similarity), ...] top_k 개, 유사도 내림차순
        """
        vec = _vec_str(
            self.embedder.encode(
                [f"query: {query[:500]}"],
                normalize_embeddings=True,
            )[0].tolist()
        )

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
