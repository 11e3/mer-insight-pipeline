"""Eval 패키지 — lazy import (sentence_transformers 의존성 회피)."""


def __getattr__(name):
    if name == "EvalRunner":
        from src.eval.eval_runner import EvalRunner
        return EvalRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["EvalRunner"]
