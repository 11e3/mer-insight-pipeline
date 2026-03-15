"""
Embedder protocol + factory.

get_embedder() → LocalEmbedder(1024-dim) 기본. GCP_PROJECT_ID 설정 시 VertexEmbedder(768-dim, DB 비호환).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from config.settings import EMBEDDING_BATCH_SIZE, GCP_LOCATION, GCP_PROJECT_ID, VERTEX_EMBEDDING_MODEL

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


@runtime_checkable
class Embedder(Protocol):
    """임베더 공통 인터페이스."""
    def embed_passages_sync(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query_sync(self, text: str) -> list[float]: ...
    async def embed_passages(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...


class VertexEmbedder:
    """
    Vertex AI text-multilingual-embedding-002 임베더 (768-dim).

    주의: 현재 DB(1024-dim)와 호환 불가. DB 재인덱싱 후 사용.
    """

    def __init__(
        self,
        project_id: str = GCP_PROJECT_ID,
        location: str = GCP_LOCATION,
        model_name: str = VERTEX_EMBEDDING_MODEL,
    ):
        import vertexai
        vertexai.init(project=project_id, location=location)
        self._model_name = model_name
        self._model = None

    @property
    def model(self):
        from vertexai.language_models import TextEmbeddingModel
        if self._model is None:
            self._model = TextEmbeddingModel.from_pretrained(self._model_name)
        return self._model

    # ─── Sync ─────────────────────────────────────────────────────────────────

    def _embed_sync(self, texts: list[str], task_type: str) -> list[list[float]]:
        from vertexai.language_models import TextEmbeddingInput
        inputs = [TextEmbeddingInput(t, task_type=task_type) for t in texts]
        results = self.model.get_embeddings(inputs)
        return [list(r.values) for r in results]

    def embed_passages_sync(self, texts: list[str]) -> list[list[float]]:
        all_vecs: list[list[float]] = []
        for i in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = texts[i : i + EMBEDDING_BATCH_SIZE]
            all_vecs.extend(self._embed_sync(batch, "RETRIEVAL_DOCUMENT"))
        return all_vecs

    def embed_query_sync(self, text: str) -> list[float]:
        return self._embed_sync([text], "RETRIEVAL_QUERY")[0]

    # ─── Async ────────────────────────────────────────────────────────────────

    async def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self.embed_passages_sync, texts)

    async def embed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.embed_query_sync, text)


def get_embedder() -> Embedder:
    """GCP 설정 없으면 LocalEmbedder(1024-dim, DB 호환), 있으면 VertexEmbedder(768-dim)."""
    if GCP_PROJECT_ID:
        log.warning(
            "GCP_PROJECT_ID 설정됨 → VertexEmbedder(768-dim) 사용. "
            "현재 DB는 1024-dim이므로 벡터 검색이 실패할 수 있습니다."
        )
        return VertexEmbedder()
    from src.extract.local_embedder import LocalEmbedder
    return LocalEmbedder()


def vec_str(vec: list[float]) -> str:
    """asyncpg용 pgvector 문자열 변환."""
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"
