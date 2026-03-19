"""search/__init__.py lazy import 테스트."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
    os.environ.setdefault(_var, "test")

import pytest


def test_lazy_import_hybrid_search():
    """hybrid_search가 lazy import로 접근 가능."""
    import src.search as search_mod
    # Access via __getattr__
    hs = search_mod.hybrid_search
    assert callable(hs)


def test_lazy_import_hybrid_searcher():
    """HybridSearcher가 lazy import로 접근 가능."""
    import src.search as search_mod
    cls = search_mod.HybridSearcher
    assert cls is not None


def test_lazy_import_invalid_attr():
    """존재하지 않는 속성에 AttributeError 발생."""
    import src.search as search_mod
    with pytest.raises(AttributeError):
        _ = search_mod.nonexistent_attribute
