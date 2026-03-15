def __getattr__(name: str):
    """Lazy import — hybrid.py → embedder.py 체인이 GCP 없이도 크래시하지 않도록."""
    if name in ("hybrid_search", "HybridSearcher"):
        from src.search.hybrid import hybrid_search, HybridSearcher
        _exports = {"hybrid_search": hybrid_search, "HybridSearcher": HybridSearcher}
        return _exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["hybrid_search", "HybridSearcher"]
