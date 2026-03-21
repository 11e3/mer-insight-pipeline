"""
Eval 데이터셋 확장 스크립트.

BM25 인덱스 + Claude Haiku로 gold.json을 200+개 케이스로 자동 확장한다.

알고리즘:
  1. DB에서 인사이트 stratified sampling (type별, cluster별 최대 3개)
  2. Claude Haiku로 각 인사이트에 대한 자연스러운 한국어 검색 쿼리 생성
  3. BM25 검색으로 해당 insight_id가 top-K에 있으면 채택
  4. eval_data/gold_extended.json 저장 (gold.json과 동일 schema)

Usage:
    python scripts/expand_eval_dataset.py                 # 기본: 200개 목표
    python scripts/expand_eval_dataset.py --target 300
    python scripts/expand_eval_dataset.py --dry-run 10    # 10개만 테스트
    python scripts/expand_eval_dataset.py --k 10          # 검증 top-K 변경 (기본 10)
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

import asyncpg

# config.settings는 GCP_PROJECT_ID를 강제 요구하므로 직접 env에서 읽음
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
DATABASE_URL      = os.environ["DATABASE_URL"]
MODEL_HAIKU       = "claude-haiku-4-5-20251001"

from src.search.bm25_index import BM25Index

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_OUT_PATH = _ROOT / "eval_data" / "gold_extended.json"
_CACHE_PATH = _ROOT / "data" / "bm25_cache.pkl"

# BM25 인덱스와 동일한 필터 조건 (prediction 제외)
_ELIGIBLE_TYPES = ("rule", "evaluation", "macro_view")
_MAX_PER_CLUSTER = 3   # 같은 cluster_id에서 최대 3개 (topic diversity)
_RATE_LIMIT_SEC = 0.1  # Haiku API 호출 간격

_DIFFICULTY = {
    "macro_view": "easy",
    "evaluation": "medium",
    "rule": "hard",
}

_HAIKU_SYSTEM = (
    "당신은 경제·금융 리서치 검색 시스템의 쿼리 생성 전문가입니다. "
    "주어진 인사이트를 찾기 위해 사용자가 입력할 법한 자연스러운 한국어 검색 쿼리를 "
    "한 문장으로 생성하세요. 쿼리만 출력하고 다른 설명은 쓰지 마세요."
)


# ─── 샘플링 ─────────────────────────────────────────────────────────────────

async def sample_insights(conn: asyncpg.Connection, target_n: int) -> list[dict]:
    """
    insight_type × cluster_id 기준 stratified sampling.

    반환: [{"id", "content", "insight_type", "cluster_id", "date"}, ...]
    """
    # cluster_id 없는 경우 -1로 통일
    rows = await conn.fetch("""
        SELECT mi.id, mi.content, mi.insight_type,
               COALESCE(mi.cluster_id, -1) AS cluster_id,
               mp.date
        FROM insights mi
        JOIN posts mp ON mi.post_id = mp.id
        WHERE mi.insight_type = ANY($1::text[])
          AND (mi.is_canonical IS NULL OR mi.is_canonical = TRUE)
        ORDER BY mi.insight_type, cluster_id, RANDOM()
    """, list(_ELIGIBLE_TYPES))

    log.info(f"DB에서 {len(rows)}개 후보 인사이트 로드")

    # cluster별 최대 _MAX_PER_CLUSTER개 선택 후 type별 균등 배분
    per_type: dict[str, list[dict]] = {t: [] for t in _ELIGIBLE_TYPES}
    cluster_count: dict[tuple, int] = {}  # (type, cluster_id) → count

    for r in rows:
        t = r["insight_type"]
        key = (t, r["cluster_id"])
        if cluster_count.get(key, 0) >= _MAX_PER_CLUSTER:
            continue
        per_type[t].append(dict(r))
        cluster_count[key] = cluster_count.get(key, 0) + 1

    # type당 목표 개수 (총 target_n의 1.5배 확보해 yield 손실 흡수)
    per_type_target = int(target_n * 1.5 / len(_ELIGIBLE_TYPES)) + 1
    sampled: list[dict] = []
    for t in _ELIGIBLE_TYPES:
        sampled.extend(per_type[t][:per_type_target])

    log.info(f"샘플링 완료: {len(sampled)}개 (type별 최대 {per_type_target}개)")
    return sampled


# ─── 쿼리 생성 ───────────────────────────────────────────────────────────────

async def generate_query(client, insight_content: str) -> str:
    """Claude Haiku로 인사이트 → 검색 쿼리 생성."""
    resp = await client.messages.create(
        model=MODEL_HAIKU,
        max_tokens=100,
        system=_HAIKU_SYSTEM,
        messages=[{"role": "user", "content": insight_content[:800]}],
    )
    return resp.content[0].text.strip()


# ─── BM25 검증 ───────────────────────────────────────────────────────────────

def verify_bm25(bm25: BM25Index, query: str, insight_id: int, k: int) -> bool:
    """쿼리로 BM25 검색 시 해당 insight_id가 top-K에 있으면 True."""
    results = bm25.search(query, top_k=k)
    retrieved_ids = {doc_id for doc_id, _ in results}
    return insight_id in retrieved_ids


# ─── 메인 ────────────────────────────────────────────────────────────────────

async def expand(target_n: int, k: int, dry_run: int | None) -> None:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    conn   = await asyncpg.connect(DATABASE_URL)

    # BM25 인덱스 로드 (캐시 우선)
    bm25 = BM25Index()
    if not bm25.load(_CACHE_PATH):
        log.info("BM25 캐시 없음 — DB에서 빌드...")
        await bm25.build(conn)
        bm25.save(_CACHE_PATH)

    # 후보 인사이트 샘플링
    candidates = await sample_insights(conn, target_n)
    await conn.close()

    if dry_run:
        candidates = candidates[:dry_run]
        log.info(f"dry-run 모드: {dry_run}개만 처리")

    accepted: list[dict] = []
    rejected = 0

    for i, ins in enumerate(candidates):
        if not dry_run and len(accepted) >= target_n:
            break

        try:
            query = await generate_query(client, ins["content"])
            await asyncio.sleep(_RATE_LIMIT_SEC)
        except Exception as exc:
            log.warning(f"  쿼리 생성 실패 (id={ins['id']}): {exc}")
            continue

        found = verify_bm25(bm25, query, ins["id"], k=k)
        if found:
            accepted.append({
                "id": f"ext_{len(accepted) + 1:03d}",
                "query": query,
                "relevant_insight_ids": [ins["id"]],
                "expected_topics": [],
                "expected_stance": "",
                "difficulty": _DIFFICULTY.get(ins["insight_type"], "medium"),
                "source_insight_type": ins["insight_type"],
            })
            log.info(f"  [{i+1}/{len(candidates)}] ✓ id={ins['id']}  query={query[:60]}")
        else:
            rejected += 1
            log.debug(f"  [{i+1}/{len(candidates)}] ✗ id={ins['id']} (top-{k}에 없음)")

    # 저장
    _OUT_PATH.parent.mkdir(exist_ok=True)
    output = {"test_cases": accepted}
    with open(_OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    yield_rate = len(accepted) / (len(accepted) + rejected) * 100 if (accepted or rejected) else 0
    log.info(
        f"\n완료: {len(accepted)}개 저장 ({rejected}개 기각, yield={yield_rate:.0f}%)\n"
        f"출력: {_OUT_PATH}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Eval 데이터셋 자동 확장")
    parser.add_argument("--target", type=int, default=200, help="목표 케이스 수 (기본 200)")
    parser.add_argument("--k", type=int, default=10, help="BM25 검증 top-K (기본 10)")
    parser.add_argument("--dry-run", type=int, default=None, metavar="N",
                        help="N개만 처리하고 종료 (테스트용)")
    args = parser.parse_args()
    asyncio.run(expand(target_n=args.target, k=args.k, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
