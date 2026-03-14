"""
Eval Report: 평가 결과를 마크다운 + JSON으로 저장.
"""

import json
from datetime import datetime
from pathlib import Path

from src.eval.metrics import CaseResult, aggregate


def generate_report(results: list[CaseResult], output_dir: str | Path) -> Path:
    """
    평가 결과를 마크다운 + JSON으로 저장.

    Returns:
        저장된 마크다운 파일 경로
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    agg = aggregate(results)
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")

    # JSON 저장
    json_path = out / f"eval_{ts}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": ts,
            "summary": agg,
            "cases": [
                {
                    "id": r.case_id,
                    "query": r.query,
                    "retrieval": {
                        "precision_at_k": r.retrieval.precision_at_k,
                        "recall_at_k":    r.retrieval.recall_at_k,
                        "mrr":            r.retrieval.mrr,
                    },
                    "generation": {
                        "context_relevance": r.generation.context_relevance,
                        "faithfulness":      r.generation.faithfulness,
                        "answer_relevance":  r.generation.answer_relevance,
                        "hallucination_rate":r.generation.hallucination_rate,
                    },
                }
                for r in results
            ],
        }, f, ensure_ascii=False, indent=2)

    # Markdown 저장
    md_path = out / f"eval_{ts}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(_render_markdown(agg, results, ts))

    return md_path


def _render_markdown(agg: dict, results: list[CaseResult], ts: str) -> str:
    ret = agg.get("retrieval", {})
    gen = agg.get("generation", {})
    n   = agg.get("n", 0)
    k   = ret.get("k", 10)

    lines = [
        f"# Eval Report — {ts}",
        f"",
        f"테스트 케이스: **{n}개** | K={k}",
        f"",
        f"## Retrieval 지표",
        f"",
        f"| 지표 | 값 |",
        f"|------|----|",
        f"| Precision@{k} | {ret.get('precision_at_k', 0):.4f} |",
        f"| Recall@{k}    | {ret.get('recall_at_k', 0):.4f}    |",
        f"| MRR           | {ret.get('mrr', 0):.4f}             |",
        f"",
        f"## Generation 지표",
        f"",
        f"| 지표 | 값 |",
        f"|------|----|",
        f"| Context Relevance | {gen.get('context_relevance', 0):.4f} |",
        f"| Faithfulness      | {gen.get('faithfulness', 0):.4f}      |",
        f"| Answer Relevance  | {gen.get('answer_relevance', 0):.4f}  |",
        f"| Hallucination Rate| {gen.get('hallucination_rate', 0):.4f}|",
        f"",
        f"## Worst Cases (Precision@K 하위 3개)",
        f"",
    ]

    # worst cases
    sorted_by_precision = sorted(results, key=lambda r: r.retrieval.precision_at_k)[:3]
    for r in sorted_by_precision:
        lines += [
            f"### {r.case_id}",
            f"- Query: {r.query}",
            f"- Precision@K: {r.retrieval.precision_at_k:.4f}",
            f"- Faithfulness: {r.generation.faithfulness:.4f}",
            f"",
        ]

    return "\n".join(lines)
