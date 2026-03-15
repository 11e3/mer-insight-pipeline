"""
Hybrid Search: BM25 + 벡터 검색 → Reciprocal Rank Fusion (RRF)

사용법:
    searcher = HybridSearcher(conn, embedder, bm25_index)
    results = await searcher.search("금리 인하 전망", top_k=10)
    # → [{"id": int, "content": str, "score": float, ...}, ...]

편의 함수:
    results = await hybrid_search(query, top_k, conn, embedder, bm25_index)
"""

import asyncpg

from src.extract.vertex_embedder import Embedder
from src.search.bm25_index import BM25Index
from src.search.vector_index import VectorIndex

# RRF 상수 (k=60 은 표준 설정)
_RRF_K = 60


def _rrf_fuse(
    bm25_results: list[tuple[int, float]],
    vector_results: list[tuple[int, float]],
    alpha: float,
) -> list[tuple[int, float]]:
    """
    Reciprocal Rank Fusion.

    alpha: BM25 가중치 (0~1). alpha=0.5 → 동일 가중치
           alpha=0 → 벡터만, alpha=1 → BM25만
    """
    scores: dict[int, float] = {}

    for rank, (doc_id, _) in enumerate(bm25_results):
        scores[doc_id] = scores.get(doc_id, 0.0) + alpha / (_RRF_K + rank)

    for rank, (doc_id, _) in enumerate(vector_results):
        scores[doc_id] = scores.get(doc_id, 0.0) + (1 - alpha) / (_RRF_K + rank)

    return sorted(scores.items(), key=lambda x: -x[1])


class HybridSearcher:
    """BM25 + 벡터 RRF 검색기."""

    def __init__(
        self,
        conn: asyncpg.Connection,
        embedder: Embedder,
        bm25_index: BM25Index,
        alpha: float = 0.4,
    ):
        self._vector = VectorIndex(conn, embedder)
        self._bm25 = bm25_index
        self._conn = conn
        self.alpha = alpha

    async def search(
        self,
        query: str,
        top_k: int = 10,
        fetch_content: bool = True,
    ) -> list[dict]:
        """
        하이브리드 검색.

        Args:
            query: 검색 쿼리
            top_k: 반환할 결과 수
            fetch_content: True면 DB에서 content/structured_data까지 조회

        Returns:
            [{"id", "score", "content"(선택), "structured_data"(선택)}, ...]
        """
        pool_k = top_k * 3  # RRF 풀 크기

        bm25_res = (
            self._bm25.search(query, top_k=pool_k)
            if self._bm25.is_ready
            else []
        )
        vector_res = await self._vector.search(query, top_k=pool_k)

        fused = _rrf_fuse(bm25_res, vector_res, self.alpha)[:top_k]

        if not fetch_content or not fused:
            return [{"id": doc_id, "score": score} for doc_id, score in fused]

        # DB에서 content 일괄 조회
        ids = [doc_id for doc_id, _ in fused]
        score_map = {doc_id: score for doc_id, score in fused}

        rows = await self._conn.fetch("""
            SELECT mi.id, mi.content, mi.structured_data, mi.insight_type, mp.date
            FROM mer_insights mi
            JOIN mer_posts mp ON mi.post_id = mp.id
            WHERE mi.id = ANY($1::int[])
        """, ids)

        row_map = {r["id"]: dict(r) for r in rows}
        results = []
        for doc_id, score in fused:
            if doc_id in row_map:
                item = row_map[doc_id]
                item["score"] = score
                results.append(item)

        return results


async def hybrid_search(
    query: str,
    top_k: int,
    conn: asyncpg.Connection,
    embedder: Embedder,
    bm25_index: BM25Index,
    alpha: float = 0.4,
) -> list[dict]:
    """HybridSearcher 편의 래퍼."""
    searcher = HybridSearcher(conn, embedder, bm25_index, alpha=alpha)
    return await searcher.search(query, top_k=top_k)
