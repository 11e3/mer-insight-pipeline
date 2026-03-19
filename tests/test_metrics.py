"""eval/metrics.py 단위 테스트 — 순수 함수, 의존성 없음."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
    os.environ.setdefault(_var, "test")

from src.eval.metrics import (
    precision_at_k,
    recall_at_k,
    mean_reciprocal_rank,
    compute_retrieval,
    aggregate,
    CaseResult,
    RetrievalMetrics,
    GenerationMetrics,
)


# ─── precision_at_k ──────────────────────────────────────────────────────────

def test_precision_all_relevant():
    assert precision_at_k([1, 2, 3], {1, 2, 3}, 3) == 1.0


def test_precision_none_relevant():
    assert precision_at_k([4, 5, 6], {1, 2, 3}, 3) == 0.0


def test_precision_partial():
    assert precision_at_k([1, 4, 2], {1, 2, 3}, 3) == 2 / 3


def test_precision_k_zero():
    assert precision_at_k([1, 2], {1, 2}, 0) == 0.0


def test_precision_k_larger_than_retrieved():
    # k=5 but only 3 retrieved → uses top_k = retrieved[:5] = [1,2,3]
    assert precision_at_k([1, 2, 3], {1, 2, 3}, 5) == 3 / 5


# ─── recall_at_k ─────────────────────────────────────────────────────────────

def test_recall_all_found():
    assert recall_at_k([1, 2, 3], {1, 2, 3}, 3) == 1.0


def test_recall_none_found():
    assert recall_at_k([4, 5, 6], {1, 2, 3}, 3) == 0.0


def test_recall_partial():
    assert recall_at_k([1, 4, 5], {1, 2, 3}, 3) == 1 / 3


def test_recall_empty_relevant():
    assert recall_at_k([1, 2], set(), 2) == 0.0


# ─── mean_reciprocal_rank ────────────────────────────────────────────────────

def test_mrr_first_position():
    assert mean_reciprocal_rank([1, 2, 3], {1}) == 1.0


def test_mrr_second_position():
    assert mean_reciprocal_rank([4, 1, 3], {1}) == 0.5


def test_mrr_not_found():
    assert mean_reciprocal_rank([4, 5, 6], {1}) == 0.0


def test_mrr_empty_retrieved():
    assert mean_reciprocal_rank([], {1}) == 0.0


# ─── compute_retrieval ───────────────────────────────────────────────────────

def test_compute_retrieval_basic():
    m = compute_retrieval([1, 2, 3, 4, 5], [1, 3], k=5)
    assert isinstance(m, RetrievalMetrics)
    assert m.precision_at_k == 2 / 5
    assert m.recall_at_k == 1.0
    assert m.mrr == 1.0
    assert m.k == 5


def test_compute_retrieval_no_relevant():
    m = compute_retrieval([1, 2], [], k=2)
    assert m.recall_at_k == 0.0


# ─── aggregate ────────────────────────────────────────────────────────────────

def test_aggregate_empty():
    assert aggregate([]) == {}


def test_aggregate_single():
    r = CaseResult(
        case_id="c1",
        query="q1",
        retrieval=RetrievalMetrics(precision_at_k=0.5, recall_at_k=1.0, mrr=0.5, k=5),
        generation=GenerationMetrics(context_relevance=0.8, faithfulness=0.6,
                                     answer_relevance=0.4, hallucination_rate=0.1),
    )
    agg = aggregate([r])
    assert agg["n"] == 1
    assert agg["retrieval"]["precision_at_k"] == 0.5
    assert agg["generation"]["faithfulness"] == 0.6


def test_aggregate_averages():
    r1 = CaseResult(
        case_id="c1", query="q1",
        retrieval=RetrievalMetrics(precision_at_k=0.4, recall_at_k=1.0, mrr=1.0, k=5),
        generation=GenerationMetrics(),
    )
    r2 = CaseResult(
        case_id="c2", query="q2",
        retrieval=RetrievalMetrics(precision_at_k=0.6, recall_at_k=0.5, mrr=0.5, k=5),
        generation=GenerationMetrics(),
    )
    agg = aggregate([r1, r2])
    assert agg["n"] == 2
    assert abs(agg["retrieval"]["precision_at_k"] - 0.5) < 1e-9
    assert abs(agg["retrieval"]["recall_at_k"] - 0.75) < 1e-9
