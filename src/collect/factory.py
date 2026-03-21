"""소스 수집기 팩토리."""
from __future__ import annotations

from src.collect.source_protocol import SourceCollector


_REGISTRY: dict[str, type] = {}


def register_collector(source_type: str, cls: type) -> None:
    _REGISTRY[source_type] = cls


def get_collector(source_type: str, source_name: str, config: dict | None = None) -> SourceCollector:
    """source_type에 맞는 Collector 인스턴스 생성.

    config에 feed_url이 있으면 RSSCollector 사용 (Substack, 일반 블로그).
    없으면 source_type 기본 등록된 Collector 사용.
    """
    config = config or {}

    # feed_url이 있으면 범용 RSS 수집기
    if config.get("feed_url"):
        from src.collect.rss_collector import RSSCollector
        return RSSCollector(source_name=source_name, feed_url=config["feed_url"])

    cls = _REGISTRY.get(source_type)
    if cls is None:
        raise ValueError(f"Unknown source_type: {source_type}")
    return cls(source_name=source_name, config=config)


# 기본 등록 — import 시 자동
def _register_defaults():
    try:
        from src.collect.mer_monitor import MerMonitor
        register_collector("blog", MerMonitor)
    except ImportError:
        pass


_register_defaults()
