"""
Eval Runner: 전체 평가 파이프라인 실행.

Usage:
    python -m src.eval.eval_runner
    python -m src.eval.eval_runner --dataset eval_data/gold.json --output results/ --mode full
    python -m src.eval.eval_runner --mode retrieval_only  # Claude 호출 없이 retrieval만 평가
"""

import argparse
import asyncio
import logging
from pathlib import Path

import asyncpg
from scipy import stats
from sentence_transformers import SentenceTransformer

from config.settings import DATABASE_URL, EMBEDDING_MODEL
from src.eval.eval_dataset import load_dataset, save_template, TestCase
from src.eval.metrics import CaseResult, GenerationMetrics, compute_retrieval, aggregate
from src.eval.llm_judge import (
    judge_context_relevance, judge_faithfulness, judge_answer_relevance,
    normalize_score,
)
from src.eval.report import generate_report
from src.search.bm25_index import BM25Index
from src.search.hybrid import HybridSearcher
from src.agent.agent import MerAgent
from src.guard.guard import HallucinationGuard

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_DEFAULT_DATASET = Path("eval_data/gold.json")
_DEFAULT_OUTPUT  = Path("results")
_K = 10


class EvalRunner:

    def __init__(
        self,
        conn: asyncpg.Connection,
        embedder: SentenceTransformer,
        bm25_index: BM25Index,
        k: int = _K,
        mode: str = "full",  # "full" | "retrieval_only"
    ):
        self._conn = conn
        self._searcher = HybridSearcher(conn, embedder, bm25_index, alpha=0.4)
        self._agent = MerAgent(conn, embedder, bm25_index) if mode == "full" else None
        self._guard = HallucinationGuard(conn)
        self._k = k
        self._mode = mode

    async def run(self, cases: list[TestCase]) -> list[CaseResult]:
        results = []
        for i, case in enumerate(cases):
            log.info(f"[{i+1}/{len(cases)}] {case.id}: {case.query[:40]}")
            result = await self._evaluate_case(case)
            results.append(result)
        return results

    async def _evaluate_case(self, case: TestCase) -> CaseResult:
        result = CaseResult(case_id=case.id, query=case.query)

        # 1. Retrieval
        search_results = await self._searcher.search(
            case.query, top_k=self._k * 2, fetch_content=True
        )
        retrieved_ids = [r["id"] for r in search_results]
        result.retrieved_ids = retrieved_ids
        result.retrieval = compute_retrieval(retrieved_ids, case.relevant_insight_ids, self._k)

        if self._mode == "retrieval_only":
            return result

        # 2. 분석 생성 (에이전트)
        try:
            post = {"title": case.query, "content_text": case.query, "url": "", "date": ""}
            analysis = await self._agent.run(post)
            result.analysis_text = analysis
        except Exception as e:
            log.warning(f"  에이전트 실패: {e}")
            result.analysis_text = ""
            return result

        # 3. Guard (hallucination rate)
        guard_result = await self._guard.verify(analysis)
        result.generation.hallucination_rate = guard_result.ungrounded_ratio

        # 4. LLM Judge
        source_texts = [r.get("content", "") for r in search_results[:5]]
        try:
            cr = await judge_context_relevance(case.query, source_texts)
            result.generation.context_relevance = normalize_score(cr.get("score", 0))

            ff = await judge_faithfulness(analysis, source_texts)
            result.generation.faithfulness = normalize_score(ff.get("score", 0))

            ar = await judge_answer_relevance(case.query, analysis)
            result.generation.answer_relevance = normalize_score(ar.get("score", 0))
        except Exception as e:
            log.warning(f"  LLM Judge 실패: {e}")

        return result


async def main(dataset_path: Path, output_dir: Path, mode: str, k: int):
    # 데이터셋 로드 (없으면 템플릿 생성)
    if not dataset_path.exists():
        log.warning(f"데이터셋 없음 — 템플릿 생성: {dataset_path}")
        save_template(dataset_path)
        log.info("템플릿을 수동으로 채워주세요.")
        return

    cases = load_dataset(dataset_path)
    if not cases:
        log.error("테스트 케이스 없음")
        return

    conn = await asyncpg.connect(DATABASE_URL)
    embedder = SentenceTransformer(EMBEDDING_MODEL)

    bm25_cache = Path("data/bm25_cache.pkl")
    bm25 = BM25Index()
    if not bm25.load(bm25_cache):
        await bm25.build(conn)
        bm25.save(bm25_cache)

    runner = EvalRunner(conn, embedder, bm25, k=k, mode=mode)
    results = await runner.run(cases)

    # ─── 통계 검증 ──────────────────────────────────────────────────────────
    if len(results) > 2:
        prec_vals = [r.retrieval.precision_at_k for r in results]
        faith_vals = [r.generation.faithfulness for r in results]

        if any(v > 0 for v in faith_vals):
            corr, corr_p = stats.spearmanr(prec_vals, faith_vals)
            log.info(f"Spearman(Precision, Faithfulness): ρ={corr:.3f}, p={corr_p:.4f}")

    # ─── 리포트 출력 ─────────────────────────────────────────────────────────
    agg = aggregate(results)
    print("\n" + "="*60)
    print(f"Eval 결과 (mode={mode}, K={k}, N={len(results)})")
    print("="*60)

    ret = agg.get("retrieval", {})
    gen = agg.get("generation", {})
    print(f"Precision@{k}:       {ret.get('precision_at_k', 0):.4f}")
    print(f"Recall@{k}:          {ret.get('recall_at_k', 0):.4f}")
    print(f"MRR:                 {ret.get('mrr', 0):.4f}")
    if mode == "full":
        print(f"Context Relevance:   {gen.get('context_relevance', 0):.4f}")
        print(f"Faithfulness:        {gen.get('faithfulness', 0):.4f}")
        print(f"Answer Relevance:    {gen.get('answer_relevance', 0):.4f}")
        print(f"Hallucination Rate:  {gen.get('hallucination_rate', 0):.4f}")
    print("="*60 + "\n")

    md_path = generate_report(results, output_dir)
    log.info(f"리포트 저장: {md_path}")

    await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Eval Runner")
    parser.add_argument("--dataset", default=str(_DEFAULT_DATASET))
    parser.add_argument("--output",  default=str(_DEFAULT_OUTPUT))
    parser.add_argument("--mode",    default="full",
                        choices=["full", "retrieval_only"],
                        help="full=전체 평가, retrieval_only=검색만")
    parser.add_argument("--k",       type=int, default=_K)
    args = parser.parse_args()

    asyncio.run(main(
        dataset_path=Path(args.dataset),
        output_dir=Path(args.output),
        mode=args.mode,
        k=args.k,
    ))
