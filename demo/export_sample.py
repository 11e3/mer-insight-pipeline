"""
프로덕션 DB에서 샘플 데이터를 export해 demo/sample_data.json으로 저장.

한 번만 실행하면 된다. demo/app.py는 이 파일을 정적으로 읽는다.
kiwipiepy로 사전 토크나이징해 demo 앱에서 kiwipiepy 불필요.

Usage:
    python demo/export_sample.py
    python demo/export_sample.py --limit 200 --out demo/sample_data.json
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, date
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import asyncpg

from config.settings import DATABASE_URL


# kiwipiepy 선택적 import
try:
    from kiwipiepy import Kiwi
    _kiwi = Kiwi()

    def _tokenize(text: str) -> list[str]:
        return [t.form for t in _kiwi.tokenize(text) if len(t.form) > 1]

except ImportError:
    def _tokenize(text: str) -> list[str]:
        return [t for t in text.split() if len(t) > 1]


def _serialize(obj):
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


async def export(limit: int, out_path: Path) -> None:
    conn = await asyncpg.connect(DATABASE_URL)

    # ── 인사이트 (최신순, rule/evaluation/macro_view만) ─────────────────────
    insight_rows = await conn.fetch("""
        SELECT mi.id, mi.insight_type, mi.content, mp.date
        FROM mer_insights mi
        JOIN mer_posts mp ON mi.post_id = mp.id
        WHERE mi.insight_type IN ('rule', 'evaluation', 'macro_view')
          AND (mi.is_canonical IS NULL OR mi.is_canonical = TRUE)
        ORDER BY mp.date DESC
        LIMIT $1
    """, limit)

    insights = []
    for r in insight_rows:
        insights.append({
            "id":      r["id"],
            "type":    r["insight_type"],
            "content": r["content"],
            "date":    r["date"].isoformat() if r["date"] else "",
            "tokens":  _tokenize(r["content"]),
        })
    print(f"인사이트: {len(insights)}개")

    # ── 자동 분석 (최신 10개) ───────────────────────────────────────────────
    analysis_rows = await conn.fetch("""
        SELECT e.title, e.created_at::date AS date, aa.analysis_text
        FROM auto_analyses aa
        JOIN events e ON aa.event_id = e.id
        WHERE aa.analysis_text IS NOT NULL
        ORDER BY e.created_at DESC
        LIMIT 10
    """)

    analyses = []
    for r in analysis_rows:
        analyses.append({
            "title":         r["title"] or "",
            "date":          r["date"].isoformat() if r["date"] else "",
            "analysis_text": (r["analysis_text"] or "")[:1000],
        })
    print(f"분석: {len(analyses)}개")

    # ── 예측 정확도 통계 ────────────────────────────────────────────────────
    stats_row = await conn.fetchrow("""
        SELECT
            COUNT(*)                                        AS total,
            COUNT(*) FILTER (WHERE is_correct IS NOT NULL) AS verified,
            COUNT(*) FILTER (WHERE is_correct = TRUE)       AS correct,
            COUNT(*) FILTER (WHERE is_correct = FALSE)      AS incorrect,
            COUNT(*) FILTER (WHERE is_correct IS NULL)      AS pending
        FROM mer_predictions
    """)

    verified = stats_row["verified"] or 0
    correct  = stats_row["correct"]  or 0
    accuracy = round(correct / verified * 100, 1) if verified > 0 else 0.0
    prediction_stats = {
        "total":       stats_row["total"],
        "verified":    verified,
        "correct":     correct,
        "incorrect":   stats_row["incorrect"] or 0,
        "pending":     stats_row["pending"]   or 0,
        "accuracy_pct": accuracy,
    }
    print(f"예측 정확도: {accuracy:.1f}% ({correct}/{verified}건 검증)")

    await conn.close()

    output = {
        "insights":          insights,
        "analyses":          analyses,
        "prediction_stats":  prediction_stats,
        "exported_at":       datetime.utcnow().isoformat() + "Z",
        "insight_count_total": 25090,   # 전체 DB 기준 (포트폴리오 표시용)
        "post_count_total":    2193,
    }

    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=_serialize)
    print(f"\n저장 완료: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="DB → sample_data.json export")
    parser.add_argument("--limit", type=int, default=100, help="인사이트 export 건수 (기본 100)")
    parser.add_argument("--out", default="demo/sample_data.json")
    args = parser.parse_args()
    asyncio.run(export(limit=args.limit, out_path=ROOT / args.out))


if __name__ == "__main__":
    main()
