"""
인사이트 클러스터링 + 중복 제거.

임베딩 기반 DBSCAN으로 유사 인사이트를 클러스터링하고,
클러스터 내에서 가장 상세한 것을 canonical로 지정.

Usage:
    python scripts/cluster_insights.py
    python scripts/cluster_insights.py --type rule --eps 0.15
    python scripts/cluster_insights.py --type all --eps 0.15 --dry-run
"""

import asyncio
import argparse
import os
import sys
from pathlib import Path

import numpy as np
from sklearn.cluster import DBSCAN

sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncpg
from dotenv import load_dotenv

load_dotenv()


INSIGHT_TYPES = ["rule", "evaluation", "prediction", "macro_view"]


async def load_embeddings(conn: asyncpg.Connection, insight_type: str | None):
    """DB에서 임베딩 + 메타데이터 로드."""
    where = "WHERE embedding IS NOT NULL"
    if insight_type and insight_type != "all":
        where += f" AND insight_type = '{insight_type}'"

    rows = await conn.fetch(f"""
        SELECT id, insight_type, content, embedding
        FROM insights
        {where}
        ORDER BY id
    """)
    print(f"  로드: {len(rows)}개 인사이트")
    return rows


def parse_embedding(emb_str: str) -> np.ndarray:
    """pgvector 문자열 → numpy array."""
    # asyncpg returns as string like '[0.1,0.2,...]'
    s = str(emb_str).strip("[]")
    return np.array([float(x) for x in s.split(",")], dtype=np.float32)


def run_dbscan(embeddings: np.ndarray, eps: float, min_samples: int) -> np.ndarray:
    """코사인 거리 기반 DBSCAN."""
    # 이미 정규화된 임베딩이면 cosine distance = 1 - dot product
    # DBSCAN metric='cosine' 사용
    print(f"  DBSCAN 실행 (eps={eps}, min_samples={min_samples}, n={len(embeddings)})...")
    db = DBSCAN(
        eps=eps,
        min_samples=min_samples,
        metric="cosine",
        n_jobs=-1,
    ).fit(embeddings)
    return db.labels_


def select_canonical(group: list[dict]) -> int:
    """클러스터 내에서 canonical 선택: 가장 긴 content."""
    return max(group, key=lambda x: len(x["content"]))["id"]


async def update_db(
    conn: asyncpg.Connection,
    id_to_cluster: dict[int, int],
    id_to_canonical: dict[int, bool],
    dry_run: bool,
):
    """cluster_id, is_canonical 업데이트."""
    if dry_run:
        print("  [dry-run] DB 미반영")
        return

    # batch update
    records = [
        (row_id, id_to_cluster[row_id], id_to_canonical[row_id])
        for row_id in id_to_cluster
    ]
    await conn.executemany(
        "UPDATE insights SET cluster_id=$2, is_canonical=$3 WHERE id=$1",
        records,
    )
    print(f"  DB 업데이트: {len(records)}개")


async def cluster_insights(
    insight_type: str = "rule",
    eps: float = 0.15,
    min_samples: int = 2,
    dry_run: bool = False,
):
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    types_to_run = INSIGHT_TYPES if insight_type == "all" else [insight_type]

    total_clusters = 0
    total_dupes = 0

    for itype in types_to_run:
        print(f"\n{'='*50}")
        print(f"타입: {itype}")
        rows = await load_embeddings(conn, itype)
        if not rows:
            print("  데이터 없음, 건너뜀")
            continue

        # 임베딩 파싱
        print("  임베딩 파싱 중...")
        ids = [r["id"] for r in rows]
        contents = [r["content"] for r in rows]
        embeddings = np.vstack([parse_embedding(r["embedding"]) for r in rows])

        # L2 정규화 (cosine distance 정확도 향상)
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        embeddings = embeddings / np.where(norms == 0, 1, norms)

        # DBSCAN
        labels = run_dbscan(embeddings, eps=eps, min_samples=min_samples)

        # 통계
        n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
        n_noise    = int((labels == -1).sum())
        n_in_cluster = int((labels >= 0).sum())
        print(f"  클러스터: {n_clusters}개 | 클러스터 내: {n_in_cluster}개 | 노이즈(단독): {n_noise}개")

        # canonical 선택
        cluster_groups: dict[int, list[dict]] = {}
        for i, label in enumerate(labels):
            if label == -1:
                continue  # 노이즈 = 단독 → canonical=True 유지
            cluster_groups.setdefault(label, []).append({
                "id": ids[i],
                "content": contents[i],
                "label": label,
            })

        id_to_cluster: dict[int, int] = {}
        id_to_canonical: dict[int, bool] = {}

        for label, group in cluster_groups.items():
            canonical_id = select_canonical(group)
            n_dupes = len(group) - 1
            total_dupes += n_dupes

            for item in group:
                id_to_cluster[item["id"]] = int(label)
                id_to_canonical[item["id"]] = (item["id"] == canonical_id)

        # 노이즈 항목 (label=-1) → is_canonical=True 유지
        for i, label in enumerate(labels):
            if label == -1:
                id_to_cluster[ids[i]]   = -1   # 클러스터 없음
                id_to_canonical[ids[i]] = True

        # 중복 제거 미리보기
        n_removed = sum(1 for v in id_to_canonical.values() if not v)
        print(f"  canonical: {sum(v for v in id_to_canonical.values())}개 | 중복 제거: {n_removed}개")

        # 클러스터 샘플 출력
        _print_cluster_samples(cluster_groups, contents, ids, n=3)

        total_clusters += n_clusters
        await update_db(conn, id_to_cluster, id_to_canonical, dry_run)

    await conn.close()

    print(f"\n{'='*50}")
    print(f"전체 클러스터: {total_clusters}개")
    print(f"전체 중복 제거: {total_dupes}개")


def _print_cluster_samples(
    cluster_groups: dict[int, list[dict]],
    contents: list[str],
    ids: list[int],
    n: int = 3,
):
    """큰 클러스터 샘플 출력."""
    large = sorted(cluster_groups.items(), key=lambda x: len(x[1]), reverse=True)[:n]
    for label, group in large:
        canonical_id = select_canonical(group)
        print(f"\n  [클러스터 #{label}] {len(group)}개:")
        for item in group[:4]:
            marker = "★" if item["id"] == canonical_id else " "
            print(f"    {marker} [{item['id']}] {item['content'][:80]}...")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", default="rule",
                        choices=["rule", "evaluation", "prediction", "macro_view", "all"])
    parser.add_argument("--eps", type=float, default=0.15,
                        help="DBSCAN eps (코사인 거리). 낮을수록 엄격 (0.10~0.20)")
    parser.add_argument("--min-samples", type=int, default=2,
                        help="클러스터 최소 멤버 수")
    parser.add_argument("--dry-run", action="store_true",
                        help="DB 반영 없이 통계만 출력")
    args = parser.parse_args()

    asyncio.run(cluster_insights(
        insight_type=args.type,
        eps=args.eps,
        min_samples=args.min_samples,
        dry_run=args.dry_run,
    ))
