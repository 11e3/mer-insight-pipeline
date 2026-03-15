"""
파이프라인 핵심 데이터 구조 타입 정의.
"""

from __future__ import annotations

from datetime import datetime
import sys
if sys.version_info >= (3, 11):
    from typing import NotRequired, TypedDict
else:
    from typing import TypedDict
    from typing_extensions import NotRequired

from src.pipeline.event_types import EventType


class Post(TypedDict):
    """MER 블로그 포스트. title과 url은 항상 존재."""
    title: str
    url: str
    content_text: NotRequired[str]
    source: NotRequired[str]   # url과 혼용되는 레거시 키
    id: NotRequired[int]


class Event(TypedDict):
    event_type: EventType
    title: str
    content: str
    source: str
    event_date: datetime


class RelatedPost(TypedDict):
    title: str
    date: str
    url: str
    similarity: int


class EnrichResult(TypedDict, total=False):
    related_posts: list[RelatedPost]
    past_position: str | None
    action_signal: str | None
