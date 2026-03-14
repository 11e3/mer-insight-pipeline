"""
Vertex AI Embeddings wrapper — text-multilingual-embedding-002 (768dim).

로컬 intfloat/multilingual-e5-large (1024dim) 대체.
task_type으로 query/passage를 구분하므로 prefix 불필요.
"""

import asyncio

import vertexai
from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel

from config.settings import EMBEDDING_BATCH_SIZE, GCP_LOCATION, GCP_PROJECT_ID, VERTEX_EMBEDDING_MODEL


class VertexEmbedder:
    """
    Vertex AI text-multilingual-embedding-002 임베더.

    embed_passages() : 문서 저장용 (RETRIEVAL_DOCUMENT)
    embed_query()    : 검색 쿼리용 (RETRIEVAL_QUERY)
    """

    def __init__(
        self,
        project_id: str = GCP_PROJECT_ID,
        location: str = GCP_LOCATION,
        model_name: str = VERTEX_EMBEDDING_MODEL,
    ):
        vertexai.init(project=project_id, location=location)
        self._model_name = model_name
        self._model: TextEmbeddingModel | None = None

    @property
    def model(self) -> TextEmbeddingModel:
        if self._model is None:
            self._model = TextEmbeddingModel.from_pretrained(self._model_name)
        return self._model

    # ─── Sync ─────────────────────────────────────────────────────────────────

    def _embed_sync(self, texts: list[str], task_type: str) -> list[list[float]]:
        inputs = [TextEmbeddingInput(t, task_type=task_type) for t in texts]
        results = self.model.get_embeddings(inputs)
        return [list(r.values) for r in results]

    def embed_passages_sync(self, texts: list[str]) -> list[list[float]]:
        """문서 임베딩 배치 (동기). 배치 크기 초과 시 자동 분할."""
        all_vecs: list[list[float]] = []
        for i in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = texts[i : i + EMBEDDING_BATCH_SIZE]
            all_vecs.extend(self._embed_sync(batch, "RETRIEVAL_DOCUMENT"))
        return all_vecs

    def embed_query_sync(self, text: str) -> list[float]:
        """쿼리 임베딩 단건 (동기)."""
        return self._embed_sync([text], "RETRIEVAL_QUERY")[0]

    # ─── Async ────────────────────────────────────────────────────────────────

    async def embed_passages(self, texts: list[str]) -> list[list[float]]:
        """문서 임베딩 배치 (비동기)."""
        return await asyncio.to_thread(self.embed_passages_sync, texts)

    async def embed_query(self, text: str) -> list[float]:
        """쿼리 임베딩 단건 (비동기)."""
        return await asyncio.to_thread(self.embed_query_sync, text)


def get_embedder() -> VertexEmbedder:
    return VertexEmbedder()


def vec_str(vec: list[float]) -> str:
    """asyncpg용 pgvector 문자열 변환."""
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"
