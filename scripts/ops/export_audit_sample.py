"""검증 데이터 오염률 측정 — 랜덤 50건 샘플링 + 블라인드 내보내기.

사용법:
    python scripts/ops/export_audit_sample.py          # 50건 (기본)
    python scripts/ops/export_audit_sample.py --n 30   # 30건
"""

import argparse
import asyncio
import json
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv()

AUDIT_DIR = Path("data/audit")


def _verdict_label(is_correct: bool | None) -> str:
    if is_correct is True:
        return "CORRECT"
    if is_correct is False:
        return "INCORRECT"
    return "PENDING"


async def main(n: int = 50) -> None:
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        rows = await conn.fetch(
            """
            SELECT p.id, p.prediction_text, p.expected_date,
                   p.is_correct, p.actual_outcome, p.source_url
            FROM mer_predictions p
            WHERE p.is_correct IS NOT NULL
            ORDER BY RANDOM()
            LIMIT $1
            """,
            n,
        )

        if not rows:
            print("검증 완료된 예측이 없습니다.")
            return

        print(f"검증 완료 건 중 {len(rows)}건 샘플링 완료")

        # --- sample_50.json ---
        samples = []
        for r in rows:
            samples.append(
                {
                    "id": r["id"],
                    "content": r["prediction_text"],
                    "expected_date": (
                        r["expected_date"].isoformat() if r["expected_date"] else None
                    ),
                    "original_verdict": _verdict_label(r["is_correct"]),
                    "original_reason": r["actual_outcome"] or "",
                    "audit_verdict": "",
                    "audit_reason": "",
                    "match": None,
                }
            )

        AUDIT_DIR.mkdir(parents=True, exist_ok=True)

        sample_path = AUDIT_DIR / "sample_50.json"
        sample_path.write_text(
            json.dumps(samples, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"저장: {sample_path} ({len(samples)}건)")

        # --- audit_batch.md (블라인드) ---
        md_lines = [
            "# 검증 데이터 오염률 감사 — {}건\n".format(len(samples)),
            "아래 예측들의 판정을 검증해주세요. 각 건마다:",
            "1. 웹 검색으로 실제 결과를 확인",
            "2. CORRECT / INCORRECT / PENDING 판정",
            "3. 근거 URL 1-2개 제공\n",
            "원래 판정은 의도적으로 가려져 있습니다. 편향 없이 독립적으로 판정해주세요.\n",
            "---\n",
        ]

        for idx, s in enumerate(samples, 1):
            md_lines.append(f"## {idx}. [ID: {s['id']}]")
            md_lines.append(f"**예측**: {s['content']}")
            md_lines.append(
                f"**기한**: {s['expected_date'] or '없음'}\n"
            )
            md_lines.append("**판정**: ")
            md_lines.append("**근거 URL**: ")
            md_lines.append("**사유 (1줄)**: \n")
            md_lines.append("---\n")

        batch_path = AUDIT_DIR / "audit_batch.md"
        batch_path.write_text("\n".join(md_lines), encoding="utf-8")
        print(f"저장: {batch_path}")

    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="검증 데이터 오염률 감사 샘플링")
    parser.add_argument("--n", type=int, default=50, help="샘플 크기 (기본 50)")
    args = parser.parse_args()
    asyncio.run(main(args.n))
