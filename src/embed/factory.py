"""Embedder 팩토리 + 유틸리티."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.config.settings import GCP_PROJECT_ID

if TYPE_CHECKING:
    from src.embed.protocol import Embedder

log = logging.getLogger(__name__)


def get_embedder() -> Embedder:
    """GCP 설정 없으면 LocalEmbedder(1024-dim, DB 호환), 있으면 VertexEmbedder(768-dim)."""
    if GCP_PROJECT_ID:
        log.warning(
            "GCP_PROJECT_ID 설정됨 → VertexEmbedder(768-dim) 사용. "
            "현재 DB는 1024-dim이므로 벡터 검색이 실패할 수 있습니다."
        )
        from src.embed.vertex import VertexEmbedder
        return VertexEmbedder()
    from src.embed.local import LocalEmbedder
    return LocalEmbedder()


def vec_str(vec: list[float]) -> str:
    """asyncpg용 pgvector 문자열 변환."""
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"
