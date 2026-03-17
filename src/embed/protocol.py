"""Embedder 공통 인터페이스."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """임베더 공통 인터페이스."""
    def embed_passages_sync(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query_sync(self, text: str) -> list[float]: ...
    async def embed_passages(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...
