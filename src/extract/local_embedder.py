"""
Local Embedder — intfloat/multilingual-e5-large (1024-dim).

DB에 저장된 기존 임베딩과 동일 모델/차원. GCP 없이 동작.
VertexEmbedder와 동일 인터페이스 (embed_passages / embed_query).
"""

import asyncio
import logging

from config.settings import EMBEDDING_BATCH_SIZE, LOCAL_EMBEDDING_MODEL

log = logging.getLogger(__name__)


class LocalEmbedder:
    """
    sentence-transformers 기반 로컬 임베더.

    모델: intfloat/multilingual-e5-large (1024-dim)
    query prefix: "query: "  /  passage prefix: "passage: "
    """

    def __init__(self, model_name: str = LOCAL_EMBEDDING_MODEL):
        self._model_name = model_name
        self._model = None

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self._model_name)
            log.info(f"LocalEmbedder 로드 완료: {self._model_name}")
        return self._model

    # ─── Sync ─────────────────────────────────────────────────────────────────

    def embed_passages_sync(self, texts: list[str]) -> list[list[float]]:
        """문서 임베딩 배치 (동기). passage: prefix 자동 추가."""
        prefixed = [f"passage: {t}" for t in texts]
        all_vecs: list[list[float]] = []
        for i in range(0, len(prefixed), EMBEDDING_BATCH_SIZE):
            batch = prefixed[i : i + EMBEDDING_BATCH_SIZE]
            embeddings = self.model.encode(batch, normalize_embeddings=True)
            all_vecs.extend(e.tolist() for e in embeddings)
        return all_vecs

    def embed_query_sync(self, text: str) -> list[float]:
        """쿼리 임베딩 단건 (동기). query: prefix 자동 추가."""
        prefixed = f"query: {text}"
        embedding = self.model.encode([prefixed], normalize_embeddings=True)
        return embedding[0].tolist()

    # ─── Async ────────────────────────────────────────────────────────────────

    async def embed_passages(self, texts: list[str]) -> list[list[float]]:
        """문서 임베딩 배치 (비동기)."""
        return await asyncio.to_thread(self.embed_passages_sync, texts)

    async def embed_query(self, text: str) -> list[float]:
        """쿼리 임베딩 단건 (비동기)."""
        return await asyncio.to_thread(self.embed_query_sync, text)
