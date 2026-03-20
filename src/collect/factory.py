"""소스 수집기 팩토리."""
from __future__ import annotations

from src.collect.source_protocol import SourceCollector


_REGISTRY: dict[str, type] = {}


def register_collector(source_type: str, cls: type) -> None:
    _REGISTRY[source_type] = cls


def get_collector(source_type: str, source_name: str, config: dict | None = None) -> SourceCollector:
    """source_type에 맞는 Collector 인스턴스 생성."""
    cls = _REGISTRY.get(source_type)
    if cls is None:
        raise ValueError(f"Unknown source_type: {source_type}")
    return cls(source_name=source_name, config=config or {})


# 기본 등록 — import 시 자동
def _register_defaults():
    try:
        from src.collect.mer_monitor import MerMonitor
        register_collector("blog", MerMonitor)
    except ImportError:
        pass


_register_defaults()
