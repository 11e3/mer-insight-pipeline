"""소스 수집기 공통 인터페이스."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import asyncpg


@dataclass
class CollectedPost:
    """소스에서 수집된 단일 컨텐츠."""

    external_id: str       # 소스 내 고유 ID (log_no, tweet_id 등)
    title: str
    content_text: str
    url: str
    published_at: str | None = None  # ISO format date string or None
    metadata: dict = field(default_factory=dict)


@runtime_checkable
class SourceCollector(Protocol):
    """소스 수집기 인터페이스."""

    source_name: str  # sources 테이블의 name과 매칭

    async def check_new(self, conn: asyncpg.Connection) -> list[CollectedPost]:
        """DB에 없는 신규 컨텐츠를 수집하여 반환."""
        ...
