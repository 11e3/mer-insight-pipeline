"""
Eval 지표 계산 모듈.

지표:
1. Precision@K   — 검색 결과 중 실제 관련 비율
2. Recall@K      — 관련 결과 중 검색으로 찾은 비율
3. MRR           — Mean Reciprocal Rank
4. Context Relevance  — LLM-as-judge (llm_judge.py)
5. Faithfulness       — LLM-as-judge (llm_judge.py)
6. Answer Relevance   — LLM-as-judge (llm_judge.py)
"""

from dataclasses import dataclass, field


@dataclass
class RetrievalMetrics:
    precision_at_k: float = 0.0
    recall_at_k: float = 0.0
    mrr: float = 0.0
    k: int = 10


@dataclass
class GenerationMetrics:
    context_relevance: float = 0.0   # 1-5 → normalize 0-1
    faithfulness: float = 0.0        # 1-5 → normalize 0-1
    answer_relevance: float = 0.0    # 1-5 → normalize 0-1
    hallucination_rate: float = 0.0  # LLM judge 평가 (미사용)


@dataclass
class CaseResult:
    case_id: str
    query: str
    retrieval: RetrievalMetrics = field(default_factory=RetrievalMetrics)
    generation: GenerationMetrics = field(default_factory=GenerationMetrics)
    retrieved_ids: list[int] = field(default_factory=list)
    analysis_text: str = ""


# ─── Retrieval 지표 ───────────────────────────────────────────────────────────

def precision_at_k(retrieved: list[int], relevant: set[int], k: int) -> float:
    top_k = retrieved[:k]
    hits = sum(1 for r in top_k if r in relevant)
    return hits / k if k > 0 else 0.0


def recall_at_k(retrieved: list[int], relevant: set[int], k: int) -> float:
    top_k = retrieved[:k]
    hits = sum(1 for r in top_k if r in relevant)
    return hits / len(relevant) if relevant else 0.0


def mean_reciprocal_rank(retrieved: list[int], relevant: set[int]) -> float:
    for rank, doc_id in enumerate(retrieved, start=1):
        if doc_id in relevant:
            return 1.0 / rank
    return 0.0


def compute_retrieval(
    retrieved_ids: list[int],
    relevant_ids: list[int],
    k: int = 10,
) -> RetrievalMetrics:
    relevant = set(relevant_ids)
    return RetrievalMetrics(
        precision_at_k=precision_at_k(retrieved_ids, relevant, k),
        recall_at_k=recall_at_k(retrieved_ids, relevant, k),
        mrr=mean_reciprocal_rank(retrieved_ids, relevant),
        k=k,
    )


# ─── 집계 ─────────────────────────────────────────────────────────────────────

def aggregate(results: list[CaseResult]) -> dict:
    """전체 CaseResult 리스트에서 평균 지표 계산."""
    if not results:
        return {}

    n = len(results)

    def avg(vals):
        return sum(vals) / n if n > 0 else 0.0

    return {
        "n": n,
        "retrieval": {
            "precision_at_k": avg([r.retrieval.precision_at_k for r in results]),
            "recall_at_k":    avg([r.retrieval.recall_at_k    for r in results]),
            "mrr":            avg([r.retrieval.mrr            for r in results]),
            "k":              results[0].retrieval.k,
        },
        "generation": {
            "context_relevance": avg([r.generation.context_relevance for r in results]),
            "faithfulness":      avg([r.generation.faithfulness      for r in results]),
            "answer_relevance":  avg([r.generation.answer_relevance  for r in results]),
            "hallucination_rate":avg([r.generation.hallucination_rate for r in results]),
        },
    }
