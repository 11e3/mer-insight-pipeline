"""
Retrieval A/B 실험: Vector-only vs BM25-only vs Hybrid

골드 데이터셋(eval_data/gold.json)을 이용해 Precision@K, Recall@K, MRR을 비교하고
paired t-test로 통계적 유의성을 검증한다.

Usage:
    python -m src.search.experiment
    python -m src.search.experiment --dataset eval_data/gold.json --k 10
"""

import asyncio
import argparse
import json
import logging
import os
from pathlib import Path

import asyncpg
from scipy import stats
from sentence_transformers import SentenceTransformer

from config.settings import DATABASE_URL, EMBEDDING_MODEL
from src.search.bm25_index import BM25Index
from src.search.vector_index import VectorIndex
from src.search.hybrid import HybridSearcher

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_DEFAULT_DATASET = Path(__file__).parent.parent.parent / "eval_data" / "gold.json"


# ─── 지표 계산 ────────────────────────────────────────────────────────────────

def precision_at_k(retrieved: list[int], relevant: set[int], k: int) -> float:
    top_k = retrieved[:k]
    hits = sum(1 for r in top_k if r in relevant)
    return hits / k if k > 0 else 0.0


def recall_at_k(retrieved: list[int], relevant: set[int], k: int) -> float:
    top_k = retrieved[:k]
    hits = sum(1 for r in top_k if r in relevant)
    return hits / len(relevant) if relevant else 0.0


def reciprocal_rank(retrieved: list[int], relevant: set[int]) -> float:
    for rank, doc_id in enumerate(retrieved, start=1):
        if doc_id in relevant:
            return 1.0 / rank
    return 0.0


# ─── 실험 실행 ────────────────────────────────────────────────────────────────

async def run_experiment(dataset_path: Path, k: int = 10):
    with open(dataset_path, encoding="utf-8") as f:
        data = json.load(f)
    test_cases = data["test_cases"]
    log.info(f"테스트 케이스: {len(test_cases)}개, K={k}")

    conn = await asyncpg.connect(DATABASE_URL)
    embedder = SentenceTransformer(EMBEDDING_MODEL)

    # BM25 인덱스 빌드 (캐시 활용)
    cache = Path("data/bm25_cache.pkl")
    bm25 = BM25Index()
    if not bm25.load(cache):
        await bm25.build(conn)
        bm25.save(cache)

    vector_idx = VectorIndex(conn, embedder)
    hybrid_idx = HybridSearcher(conn, embedder, bm25, alpha=0.4)

    results = {
        "vector": {"p": [], "r": [], "mrr": []},
        "bm25":   {"p": [], "r": [], "mrr": []},
        "hybrid": {"p": [], "r": [], "mrr": []},
    }

    for case in test_cases:
        query = case["query"]
        relevant = set(case["relevant_insight_ids"])

        # Vector-only
        v_res = await vector_idx.search(query, top_k=k * 2)
        v_ids = [doc_id for doc_id, _ in v_res]

        # BM25-only
        b_res = bm25.search(query, top_k=k * 2)
        b_ids = [doc_id for doc_id, _ in b_res]

        # Hybrid
        h_res = await hybrid_idx.search(query, top_k=k * 2, fetch_content=False)
        h_ids = [r["id"] for r in h_res]

        for name, ids in [("vector", v_ids), ("bm25", b_ids), ("hybrid", h_ids)]:
            results[name]["p"].append(precision_at_k(ids, relevant, k))
            results[name]["r"].append(recall_at_k(ids, relevant, k))
            results[name]["mrr"].append(reciprocal_rank(ids, relevant))

    await conn.close()

    # ─── 결과 출력 ──────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"Retrieval 실험 결과 (K={k}, N={len(test_cases)})")
    print(f"{'─'*60}")
    print(f"{'방식':<10} {'Precision@K':>12} {'Recall@K':>12} {'MRR':>12}")
    print(f"{'─'*60}")

    avg = {}
    for name in ["vector", "bm25", "hybrid"]:
        p  = sum(results[name]["p"])  / len(results[name]["p"])
        r  = sum(results[name]["r"])  / len(results[name]["r"])
        mrr = sum(results[name]["mrr"]) / len(results[name]["mrr"])
        avg[name] = {"precision": p, "recall": r, "mrr": mrr}
        print(f"{name:<10} {p:>12.4f} {r:>12.4f} {mrr:>12.4f}")

    # ─── Paired t-test: vector vs hybrid ────────────────────────────────────
    print(f"\n{'─'*60}")
    print("통계 검증 (Hybrid vs Vector, Precision@K, paired t-test)")
    t_stat, p_val = stats.ttest_rel(results["hybrid"]["p"], results["vector"]["p"])
    sig = "OK 유의 (p<0.05)" if p_val < 0.05 else "NG 유의하지 않음"
    print(f"  t={t_stat:.3f}, p={p_val:.4f}  {sig}")

    corr, corr_p = stats.spearmanr(results["hybrid"]["p"], results["hybrid"]["mrr"])
    print(f"  Spearman(Precision, MRR): ρ={corr:.3f}, p={corr_p:.4f}")
    print(f"{'='*60}\n")

    # JSON 저장
    out_path = Path("results/search_experiment.json")
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"k": k, "n": len(test_cases), "averages": avg}, f, ensure_ascii=False, indent=2)
    log.info(f"결과 저장: {out_path}")

    return avg


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Retrieval A/B 실험")
    parser.add_argument("--dataset", default=str(_DEFAULT_DATASET))
    parser.add_argument("--k", type=int, default=10)
    args = parser.parse_args()
    asyncio.run(run_experiment(Path(args.dataset), k=args.k))
