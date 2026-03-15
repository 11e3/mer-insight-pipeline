"""
Retrieval A/B 실험: Vector-only vs BM25-only vs Hybrid

골드 데이터셋(eval_data/gold.json)을 이용해 Precision@K, Recall@K, MRR을 비교하고
paired t-test로 통계적 유의성을 검증한다.

Usage:
    python -m src.search.experiment
    python -m src.search.experiment --dataset eval_data/gold.json --k 10
    python -m src.search.experiment --mode ablation --k 5
    python -m src.search.experiment --mode ablation --alphas 0.0 0.2 0.4 0.6 0.8 1.0
"""

import asyncio
import argparse
import json
import logging
from pathlib import Path

import asyncpg
import numpy as np
from scipy import stats

from config.settings import DATABASE_URL
from src.eval.metrics import precision_at_k, recall_at_k, mean_reciprocal_rank as reciprocal_rank
from src.extract.vertex_embedder import vec_str
from src.search.bm25_index import BM25Index

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_DEFAULT_DATASET = Path(__file__).parent.parent.parent / "eval_data" / "gold.json"


# ─── 쿼리 벡터 (DB 저장 임베딩 활용) ──────────────────────────────────────────

async def _avg_embedding(conn: asyncpg.Connection, relevant_ids: list[int]) -> str | None:
    """
    관련 인사이트들의 평균 임베딩을 쿼리 벡터로 사용.

    VertexEmbedder 없이 로컬 실험 가능 — DB 저장 벡터를 그대로 활용하므로
    dimension 불일치 없음.
    """
    rows = await conn.fetch(
        "SELECT embedding FROM mer_insights WHERE id = ANY($1) AND embedding IS NOT NULL",
        relevant_ids,
    )
    if not rows:
        return None
    def _parse(v):
        return json.loads(v) if isinstance(v, str) else list(v)

    vecs = np.array([_parse(r["embedding"]) for r in rows], dtype=np.float32)
    avg = vecs.mean(axis=0)
    return vec_str(avg.tolist())


async def _vector_search_by_vec(
    conn: asyncpg.Connection, query_vec: str, top_k: int
) -> list[tuple[int, float]]:
    """미리 계산된 벡터 문자열로 pgvector 검색."""
    rows = await conn.fetch("""
        SELECT id, 1 - (embedding <=> $1::vector) AS similarity
        FROM mer_insights
        WHERE embedding IS NOT NULL
          AND insight_type IN ('rule', 'evaluation', 'macro_view')
          AND (is_canonical IS NULL OR is_canonical = TRUE)
        ORDER BY embedding <=> $1::vector
        LIMIT $2
    """, query_vec, top_k)
    return [(r["id"], float(r["similarity"])) for r in rows]


# ─── 실험 실행 ────────────────────────────────────────────────────────────────

async def run_experiment(dataset_path: Path, k: int = 10):
    with open(dataset_path, encoding="utf-8") as f:
        data = json.load(f)
    test_cases = data["test_cases"]
    log.info(f"테스트 케이스: {len(test_cases)}개, K={k}")

    conn = await asyncpg.connect(DATABASE_URL)

    # BM25 인덱스 빌드 (캐시 활용)
    cache = Path("data/bm25_cache.pkl")
    bm25 = BM25Index()
    if not bm25.load(cache):
        await bm25.build(conn)
        bm25.save(cache)

    results = {
        "vector": {"p": [], "r": [], "mrr": []},
        "bm25":   {"p": [], "r": [], "mrr": []},
        "hybrid": {"p": [], "r": [], "mrr": []},
    }

    for case in test_cases:
        query    = case["query"]
        relevant = set(case["relevant_insight_ids"])

        # 관련 인사이트 평균 임베딩 → 쿼리 벡터 (VertexEmbedder 없이 로컬 실험 가능)
        qvec = await _avg_embedding(conn, list(relevant))

        # Vector-only
        if qvec:
            v_res = await _vector_search_by_vec(conn, qvec, top_k=k * 2)
        else:
            v_res = []
        v_ids = [doc_id for doc_id, _ in v_res]

        # BM25-only
        b_res = bm25.search(query, top_k=k * 2)
        b_ids = [doc_id for doc_id, _ in b_res]

        # Hybrid (RRF)
        from src.search.hybrid import _rrf_fuse
        fused = _rrf_fuse(b_res, v_res, alpha=0.4)[:k * 2]
        h_ids = [doc_id for doc_id, _ in fused]

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

    # JSON 저장 (기존 ablation 키 보존)
    out_path = Path("results/search_experiment.json")
    out_path.parent.mkdir(exist_ok=True)
    existing = {}
    if out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            existing = json.load(f)
    existing.update({"k": k, "n": len(test_cases), "averages": avg, "dataset": str(dataset_path)})
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    log.info(f"결과 저장: {out_path}")

    return avg


# ─── Alpha Ablation 실험 ──────────────────────────────────────────────────────

async def run_ablation(
    dataset_path: Path,
    k: int = 5,
    alphas: list[float] | None = None,
):
    """alpha sweep: 각 alpha 값에서 Hybrid P@K, R@K, MRR을 측정한다.

    alpha=0.0 → vector-only, alpha=1.0 → BM25-only.
    결과는 results/search_experiment.json의 "ablation" 키에 추가된다.
    """
    if alphas is None:
        alphas = [0.0, 0.2, 0.3, 0.4, 0.6, 0.8, 1.0]

    with open(dataset_path, encoding="utf-8") as f:
        data = json.load(f)
    test_cases = data["test_cases"]
    log.info(f"Alpha ablation: {len(test_cases)}개 케이스, K={k}, alphas={alphas}")

    conn = await asyncpg.connect(DATABASE_URL)

    cache = Path("data/bm25_cache.pkl")
    bm25 = BM25Index()
    if not bm25.load(cache):
        await bm25.build(conn)
        bm25.save(cache)

    from src.search.hybrid import _rrf_fuse
    from sentence_transformers import SentenceTransformer

    # DB와 동일한 모델(intfloat/multilingual-e5-large, 1024-dim)로 쿼리 임베딩
    st_model = SentenceTransformer("intfloat/multilingual-e5-large")
    embed_mode = "multilingual-e5-large (1024-dim)"
    log.info(f"임베딩 모델 로드 완료: {embed_mode}")

    def _embed(text: str) -> str:
        # multilingual-e5 규격: retrieval 쿼리는 "query: " 접두어
        vec = st_model.encode(f"query: {text}", normalize_embeddings=True).tolist()
        return vec_str(vec)

    # 케이스별 사전 계산 (BM25 + 벡터) — alpha sweep에서 재사용
    case_data: list[tuple[list, list, set]] = []
    for i, case in enumerate(test_cases):
        query    = case["query"]
        relevant = set(case["relevant_insight_ids"])
        b_res = bm25.search(query, top_k=k * 9)
        qvec  = _embed(query)
        v_res = await _vector_search_by_vec(conn, qvec, top_k=k * 9)
        case_data.append((b_res, v_res, relevant))
        if (i + 1) % 50 == 0:
            log.info(f"  임베딩 진행: {i+1}/{len(test_cases)}")

    log.info(f"임베딩 모드: {embed_mode}")

    ablation_results: list[dict] = []

    for alpha in alphas:
        p_list, r_list, mrr_list = [], [], []

        for b_res, v_res, relevant in case_data:
            fused = _rrf_fuse(b_res, v_res, alpha=alpha)[:k * 3]
            ids = [doc_id for doc_id, _ in fused]
            p_list.append(precision_at_k(ids, relevant, k))
            r_list.append(recall_at_k(ids, relevant, k))
            mrr_list.append(reciprocal_rank(ids, relevant))

        avg_p   = sum(p_list)   / len(p_list)
        avg_r   = sum(r_list)   / len(r_list)
        avg_mrr = sum(mrr_list) / len(mrr_list)
        ablation_results.append({"alpha": alpha, "precision": avg_p, "recall": avg_r, "mrr": avg_mrr})
        log.info(f"  alpha={alpha:.1f}  P@{k}={avg_p:.4f}  R@{k}={avg_r:.4f}  MRR={avg_mrr:.4f}")

    await conn.close()

    # best alpha per metric
    best = {
        metric: max(ablation_results, key=lambda x: x[metric])
        for metric in ("precision", "recall", "mrr")
    }
    best_summary = {
        m: {"alpha": best[m]["alpha"], "value": best[m][m]}
        for m in ("precision", "recall", "mrr")
    }

    # 출력
    print(f"\n{'='*65}")
    print(f"Alpha Ablation 결과 (K={k}, N={len(test_cases)})")
    print(f"{'─'*65}")
    print(f"{'alpha':>8} {'Precision@K':>14} {'Recall@K':>12} {'MRR':>10}")
    print(f"{'─'*65}")
    for row in ablation_results:
        marker = " ★" if row["alpha"] == best["precision"]["alpha"] else ""
        print(f"{row['alpha']:>8.1f} {row['precision']:>14.4f} {row['recall']:>12.4f} {row['mrr']:>10.4f}{marker}")
    print(f"{'='*65}\n")
    print(f"Best alpha - Precision: {best_summary['precision']}, Recall: {best_summary['recall']}, MRR: {best_summary['mrr']}")

    # JSON 저장 (기존 averages 키 보존)
    out_path = Path("results/search_experiment.json")
    out_path.parent.mkdir(exist_ok=True)
    existing = {}
    if out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            existing = json.load(f)
    existing["ablation"] = {
        "alphas_tested": alphas,
        "k": k,
        "dataset": str(dataset_path),
        "results": ablation_results,
        "best": best_summary,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    log.info(f"Ablation 결과 저장: {out_path}")

    return ablation_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Retrieval 실험")
    parser.add_argument("--dataset", default=str(_DEFAULT_DATASET))
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--mode", default="comparison", choices=["comparison", "ablation"],
                        help="comparison=3-way A/B 비교 (기본), ablation=alpha sweep")
    parser.add_argument("--alphas", nargs="*", type=float,
                        default=[0.0, 0.2, 0.3, 0.4, 0.6, 0.8, 1.0],
                        help="ablation 모드에서 테스트할 alpha 값 목록")
    args = parser.parse_args()

    if args.mode == "ablation":
        asyncio.run(run_ablation(Path(args.dataset), k=args.k, alphas=args.alphas))
    else:
        asyncio.run(run_experiment(Path(args.dataset), k=args.k))
